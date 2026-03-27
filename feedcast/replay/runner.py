"""Replay the latest observed 24 hours for scoring and tuning.

The replay harness rewinds the current export by 24 hours, reruns a model
from that synthetic cutoff, and scores the forecast against the now-known
actuals. For tuning, it evaluates the cross-product of invoker-supplied
candidate parameter values and ranks them by headline score.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from importlib import import_module
from itertools import product
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from feedcast.data import (
    Activity,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    FeedEvent,
    HORIZON_HOURS,
    ExportSnapshot,
    Forecast,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.evaluation.scoring import score_forecast
from feedcast.models import (
    CONSENSUS_BLEND_SLUG,
    get_model_spec,
    run_all_models,
    run_consensus_blend,
)
from feedcast.models.shared import ForecastUnavailable
from .results import DEFAULT_RESULTS_DIR, save_results


@contextmanager
def override_constants(
    module_name: str,
    overrides: Mapping[str, Any],
) -> Iterator[None]:
    """Temporarily override module-level constants for one replay run.

    Validates that every name exists on the module and coerces override
    values to match the original type. Restores originals on exit.
    """
    module = import_module(module_name)

    # Validate names and save originals
    originals: dict[str, Any] = {}
    for name in overrides:
        if not hasattr(module, name):
            raise ValueError(
                f"Module {module_name} has no constant {name!r}. "
                f"Check the model's model.py for available parameter names."
            )
        originals[name] = getattr(module, name)

    # Coerce each override to match the original's type
    coerced = {
        name: _coerce_param(name, overrides[name], originals[name])
        for name in overrides
    }

    try:
        for name, value in coerced.items():
            setattr(module, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(module, name, value)


def score_model(
    model_slug: str,
    *,
    overrides: dict[str, Any] | None = None,
    export_path: Path | None = None,
    output_dir: Path = DEFAULT_RESULTS_DIR,
) -> dict[str, Any]:
    """Replay one model against the latest observed 24 hours.

    Args:
        model_slug: Target model slug (scripted or consensus_blend).
        overrides: Optional parameter overrides for scripted models.
            Module-level constants are temporarily replaced for the run.
        export_path: Explicit export CSV. Defaults to the latest file.
        output_dir: Where replay artifacts are written.

    Returns:
        The replay result payload (also persisted as JSON).
    """
    if overrides and get_model_spec(model_slug) is None:
        raise ValueError(
            f"Parameter overrides only work with scripted models; "
            f"got {model_slug!r}."
        )

    snapshot = load_export_snapshot(export_path=export_path)
    window = _latest_replay_window(snapshot)
    # Bottle-only events for scoring actuals — built once, reused.
    scoring_events = build_feed_events(snapshot.activities, merge_window_minutes=None)

    context = (
        override_constants(f"feedcast.models.{model_slug}.model", overrides)
        if overrides
        else nullcontext()
    )
    with context:
        evaluation = _evaluate_model(
            model_slug=model_slug,
            activities=snapshot.activities,
            scoring_events=scoring_events,
            replay_cutoff=window["cutoff"],
            observed_until=window["observed_until"],
        )

    if overrides:
        evaluation["overrides"] = _json_safe_params(overrides)

    payload = {
        "mode": "score",
        "validation": "latest_24h_directional_replay_only",
        "model": {"slug": model_slug, "name": evaluation["model_name"]},
        "export_path": str(snapshot.export_path),
        "dataset_id": snapshot.dataset_id,
        "replay_window": _serialize_window(window),
        "result": evaluation,
    }
    save_results(
        mode="score", model_slug=model_slug, payload=payload, output_dir=output_dir
    )
    return payload


def tune_model(
    model_slug: str,
    candidates_by_name: dict[str, list[Any]],
    *,
    export_path: Path | None = None,
    output_dir: Path = DEFAULT_RESULTS_DIR,
) -> dict[str, Any]:
    """Tune one scripted model against the latest observed 24 hours.

    Evaluates the full cross-product of candidate values, plus the current
    baseline, and ranks by headline score.

    Args:
        model_slug: Target scripted model slug.
        candidates_by_name: Maps parameter names to lists of candidate values.
            The harness evaluates the full cross-product.
        export_path: Explicit export CSV. Defaults to the latest file.
        output_dir: Where replay artifacts are written.

    Returns:
        The tuning result payload (also persisted as JSON).
    """
    spec = get_model_spec(model_slug)
    if spec is None:
        raise ValueError(f"Only scripted models can be tuned; got {model_slug!r}.")
    if not candidates_by_name:
        raise ValueError(
            "Tuning requires at least one parameter with candidate values."
        )

    snapshot = load_export_snapshot(export_path=export_path)
    window = _latest_replay_window(snapshot)
    module_name = f"feedcast.models.{model_slug}.model"

    # Validate param names and read baseline values upfront so bad names
    # fail fast with a clear error instead of a raw AttributeError.
    param_names = sorted(candidates_by_name.keys())
    module = import_module(module_name)
    baseline_params: dict[str, Any] = {}
    for name in param_names:
        if not hasattr(module, name):
            raise ValueError(
                f"Module {module_name} has no constant {name!r}. "
                f"Check the model's model.py for available parameter names."
            )
        baseline_params[name] = getattr(module, name)

    # Pre-coerce all candidate values so type errors fail fast before we
    # spend time running evaluations.
    coerced_candidates_by_name: dict[str, list[Any]] = {}
    for name, values in candidates_by_name.items():
        coerced_candidates_by_name[name] = [
            _coerce_param(name, value, baseline_params[name]) for value in values
        ]

    # Bottle-only events for scoring actuals — built once, reused across candidates.
    scoring_events = build_feed_events(snapshot.activities, merge_window_minutes=None)

    # Evaluate baseline with current constants
    baseline = _evaluate_model(
        model_slug=model_slug,
        activities=snapshot.activities,
        scoring_events=scoring_events,
        replay_cutoff=window["cutoff"],
        observed_until=window["observed_until"],
    )
    baseline["params"] = _json_safe_params(baseline_params)

    # Generate full cross-product of pre-validated candidate values
    all_candidates = [
        dict(zip(param_names, values))
        for values in product(
            *(coerced_candidates_by_name[name] for name in param_names)
        )
    ]

    # Evaluate each candidate. The broad except here catches genuine model
    # runtime errors (e.g. insufficient history for a param combo), not
    # param validation errors — those are already caught above.
    results: list[dict[str, Any]] = []
    for params in all_candidates:
        try:
            with override_constants(module_name, params):
                evaluation = _evaluate_model(
                    model_slug=model_slug,
                    activities=snapshot.activities,
                    scoring_events=scoring_events,
                    replay_cutoff=window["cutoff"],
                    observed_until=window["observed_until"],
                )
        except Exception as error:
            evaluation = {
                "model_name": spec.name,
                "status": "error",
                "effective_score": 0.0,
                "error_message": str(error),
                "forecast_available": False,
                "forecast_points": [],
                "score": None,
                "diagnostics": {},
            }
        evaluation["params"] = _json_safe_params(params)
        results.append(evaluation)

    results.sort(key=lambda r: (-r["effective_score"], str(r["params"])))
    best = results[0] if results else baseline

    payload = {
        "mode": "tune",
        "validation": "latest_24h_directional_replay_only",
        "model": {"slug": model_slug, "name": spec.name},
        "export_path": str(snapshot.export_path),
        "dataset_id": snapshot.dataset_id,
        "replay_window": _serialize_window(window),
        "search": {
            "total_candidates": len(all_candidates),
            "evaluated": len(results),
        },
        "baseline": baseline,
        "best": {
            **best,
            "improvement_vs_baseline": round(
                best["effective_score"] - baseline["effective_score"], 3
            ),
        },
        "candidates": results,
    }
    save_results(
        mode="tune", model_slug=model_slug, payload=payload, output_dir=output_dir
    )
    return payload


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_param(name: str, value: Any, original: Any) -> Any:
    """Coerce an override value to match the original constant's type.

    Handles the common cases: same type passthrough, int→float promotion,
    list→ndarray conversion, and string→scalar parsing. Raises ValueError
    with a clear message if coercion fails.
    """
    if isinstance(original, type(value)):
        return value

    # int → float promotion
    if isinstance(original, float) and isinstance(value, int):
        return float(value)

    # list → numpy array
    if isinstance(original, np.ndarray) and isinstance(value, list):
        return np.array(value, dtype=original.dtype)

    # Attempt generic conversion (covers str→int, str→float, etc.)
    try:
        return type(original)(value)
    except (TypeError, ValueError):
        pass

    raise ValueError(
        f"Cannot convert {name}={value!r} ({type(value).__name__}) to "
        f"{type(original).__name__} (current value: {original!r})."
    )


def _json_safe(value: Any) -> Any:
    """Convert a value to a JSON-serializable type.

    Handles numpy arrays and scalar types that json.dumps cannot serialize.
    """
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _json_safe_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable copy of a parameter dict."""
    return {name: _json_safe(value) for name, value in params.items()}


def _latest_replay_window(snapshot: ExportSnapshot) -> dict[str, datetime]:
    """Return the single latest-24h replay window."""
    observed_until = snapshot.latest_activity_time
    cutoff = observed_until - timedelta(hours=HORIZON_HOURS)
    earliest = min(activity.start for activity in snapshot.activities)
    if cutoff < earliest:
        raise ValueError(
            "Replay needs at least 24 observed hours in the export snapshot."
        )
    return {"cutoff": cutoff, "observed_until": observed_until}


def _evaluate_model(
    *,
    model_slug: str,
    activities: list[Activity],
    scoring_events: list[FeedEvent],
    replay_cutoff: datetime,
    observed_until: datetime,
) -> dict[str, Any]:
    """Replay one model and score against the known last-24h actuals."""
    forecast = _run_forecast(model_slug, activities, replay_cutoff)

    if not forecast.available:
        return {
            "model_name": forecast.name,
            "status": "unavailable",
            "effective_score": 0.0,
            "error_message": forecast.error_message,
            "forecast_available": False,
            "forecast_points": [],
            "score": None,
            "diagnostics": forecast.diagnostics,
        }

    # Score against bottle-only events in the observed window.
    forecast_score = score_forecast(
        predicted_points=forecast.points,
        actual_events=scoring_events,
        prediction_time=replay_cutoff,
        observed_until=observed_until,
    )
    return {
        "model_name": forecast.name,
        "status": "scored",
        "effective_score": forecast_score.score,
        "error_message": None,
        "forecast_available": True,
        "forecast_points": [point.to_dict() for point in forecast.points],
        "score": {
            "headline": forecast_score.score,
            "count": forecast_score.count_score,
            "timing": forecast_score.timing_score,
            "predicted_episode_count": forecast_score.predicted_episode_count,
            "actual_episode_count": forecast_score.actual_episode_count,
            "matched_episode_count": forecast_score.matched_episode_count,
            "observed_horizon_hours": forecast_score.observed_horizon_hours,
            "coverage_ratio": forecast_score.coverage_ratio,
        },
        "diagnostics": forecast.diagnostics,
    }


def _run_forecast(
    model_slug: str,
    activities: list[Activity],
    replay_cutoff: datetime,
) -> Forecast:
    """Run one replayable forecaster at the replay cutoff."""
    spec = get_model_spec(model_slug)
    if spec is not None:
        try:
            return spec.forecast_fn(activities, replay_cutoff, HORIZON_HOURS)
        except ForecastUnavailable as error:
            return Forecast(
                name=spec.name,
                slug=spec.slug,
                points=[],
                methodology=spec.methodology,
                diagnostics={},
                available=False,
                error_message=str(error),
            )

    if model_slug == CONSENSUS_BLEND_SLUG:
        base_forecasts = run_all_models(activities, replay_cutoff, HORIZON_HOURS)
        pipeline_events = build_feed_events(
            activities,
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
        )
        return run_consensus_blend(
            base_forecasts,
            pipeline_events,
            replay_cutoff,
            HORIZON_HOURS,
        )

    raise ValueError(
        f"Replay supports scripted model slugs and {CONSENSUS_BLEND_SLUG}; "
        f"got {model_slug!r}."
    )


def _serialize_window(window: dict[str, datetime]) -> dict[str, Any]:
    """Convert replay window timestamps to JSON-ready strings."""
    return {
        "cutoff": window["cutoff"].isoformat(timespec="seconds"),
        "observed_until": window["observed_until"].isoformat(timespec="seconds"),
        "horizon_hours": HORIZON_HOURS,
    }
