"""Replay scoring and tuning with multi-window evaluation.

The replay harness generates retrospective cutoff points from observed data,
reruns a model at each cutoff, scores each forecast against the now-known
actuals, and aggregates results with recency weighting. For tuning, it
evaluates the cross-product of candidate parameter values and ranks them
by availability tier first, then weighted aggregate headline score.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime
from importlib import import_module
from itertools import product
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from feedcast.clustering import group_into_episodes
from feedcast.data import (
    Activity,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    ExportSnapshot,
    FeedEvent,
    Forecast,
    HORIZON_HOURS,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.evaluation.windows import (
    MultiWindowResult,
    WindowResult,
    evaluate_multi_window,
    generate_episode_boundary_cutoffs,
    generate_fixed_step_cutoffs,
)
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
    lookback_hours: float = 96.0,
    half_life_hours: float = 36.0,
    cutoff_mode: str = "episode",
    step_hours: float = 12.0,
    parallel: bool = False,
) -> dict[str, Any]:
    """Score one model across multiple retrospective windows.

    Args:
        model_slug: Target model slug (scripted or consensus_blend).
        overrides: Optional parameter overrides for scripted models.
            Module-level constants are temporarily replaced for the run.
        export_path: Explicit export CSV. Defaults to the latest file.
        output_dir: Where replay artifacts are written.
        lookback_hours: Maximum lookback for cutoff generation.
        half_life_hours: Recency decay half-life for window weighting.
        cutoff_mode: "episode" for episode-boundary cutoffs, "fixed" for
            fixed-interval cutoffs.
        step_hours: Step size for fixed-interval cutoffs.
        parallel: If True, evaluate windows concurrently.

    Returns:
        The replay result payload (also persisted as JSON).
    """
    if overrides and get_model_spec(model_slug) is None:
        raise ValueError(
            f"Parameter overrides only work with scripted models; "
            f"got {model_slug!r}."
        )

    snapshot = load_export_snapshot(export_path=export_path)
    scoring_events = build_feed_events(snapshot.activities, merge_window_minutes=None)
    cutoffs = _generate_cutoffs(
        scoring_events=scoring_events,
        snapshot=snapshot,
        lookback_hours=lookback_hours,
        cutoff_mode=cutoff_mode,
        step_hours=step_hours,
    )
    model_name = _resolve_model_name(model_slug)

    def forecast_fn(cutoff: datetime) -> Forecast:
        return _run_forecast(model_slug, snapshot.activities, cutoff)

    # override_constants must wrap the entire evaluate_multi_window call,
    # not just closure construction — the closure reads module-level
    # constants at execution time.
    context = (
        override_constants(f"feedcast.models.{model_slug}.model", overrides)
        if overrides
        else nullcontext()
    )
    with context:
        mw_result = evaluate_multi_window(
            forecast_fn=forecast_fn,
            scoring_events=scoring_events,
            cutoffs=cutoffs,
            latest_activity_time=snapshot.latest_activity_time,
            half_life_hours=half_life_hours,
            parallel=parallel,
        )

    payload: dict[str, Any] = {
        "mode": "score",
        "validation": "multi_window_directional_replay",
        "model": {"slug": model_slug, "name": model_name},
        "export_path": str(snapshot.export_path),
        "dataset_id": snapshot.dataset_id,
        "replay_windows": _serialize_multi_window(
            mw_result, lookback_hours, cutoff_mode, step_hours,
        ),
    }
    if overrides:
        payload["overrides"] = _json_safe_params(overrides)

    save_results(
        mode="score", model_slug=model_slug, payload=payload, output_dir=output_dir,
    )
    return payload


def tune_model(
    model_slug: str,
    candidates_by_name: dict[str, list[Any]],
    *,
    export_path: Path | None = None,
    output_dir: Path = DEFAULT_RESULTS_DIR,
    lookback_hours: float = 96.0,
    half_life_hours: float = 36.0,
    cutoff_mode: str = "episode",
    step_hours: float = 12.0,
    parallel: bool = False,
) -> dict[str, Any]:
    """Tune one scripted model across multiple retrospective windows.

    Evaluates the full cross-product of candidate values, plus the current
    baseline, and ranks by availability tier first (highest
    scored_window_count), then by weighted aggregate headline score.

    Args:
        model_slug: Target scripted model slug.
        candidates_by_name: Maps parameter names to lists of candidate values.
            The harness evaluates the full cross-product.
        export_path: Explicit export CSV. Defaults to the latest file.
        output_dir: Where replay artifacts are written.
        lookback_hours: Maximum lookback for cutoff generation.
        half_life_hours: Recency decay half-life for window weighting.
        cutoff_mode: "episode" for episode-boundary cutoffs, "fixed" for
            fixed-interval cutoffs.
        step_hours: Step size for fixed-interval cutoffs.
        parallel: If True, evaluate windows concurrently.

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
    scoring_events = build_feed_events(snapshot.activities, merge_window_minutes=None)
    cutoffs = _generate_cutoffs(
        scoring_events=scoring_events,
        snapshot=snapshot,
        lookback_hours=lookback_hours,
        cutoff_mode=cutoff_mode,
        step_hours=step_hours,
    )
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

    def forecast_fn(cutoff: datetime) -> Forecast:
        return _run_forecast(model_slug, snapshot.activities, cutoff)

    # Evaluate baseline with current production constants
    baseline_mw = evaluate_multi_window(
        forecast_fn=forecast_fn,
        scoring_events=scoring_events,
        cutoffs=cutoffs,
        latest_activity_time=snapshot.latest_activity_time,
        half_life_hours=half_life_hours,
        parallel=parallel,
    )

    # Generate full cross-product of pre-validated candidate values
    all_candidates = [
        dict(zip(param_names, values))
        for values in product(
            *(coerced_candidates_by_name[name] for name in param_names)
        )
    ]

    # Evaluate each candidate across all windows. Per-window errors and
    # unavailability are recorded inside evaluate_multi_window(); we do not
    # catch around the whole call because that would discard diagnostics.
    results: list[tuple[dict[str, Any], MultiWindowResult]] = []
    for params in all_candidates:
        with override_constants(module_name, params):
            mw_result = evaluate_multi_window(
                forecast_fn=forecast_fn,
                scoring_events=scoring_events,
                cutoffs=cutoffs,
                latest_activity_time=snapshot.latest_activity_time,
                half_life_hours=half_life_hours,
                parallel=parallel,
            )
        results.append((params, mw_result))

    # Rank sweep candidates.
    _rank_key = lambda r: (-r[1].scored_window_count, -r[1].headline_score, str(r[0]))
    results.sort(key=_rank_key)

    # Baseline competes for "best" so we never recommend a regression,
    # but it does not appear in the candidates list (it is already
    # reported separately as "baseline").
    best_params, best_mw = results[0] if results else (baseline_params, baseline_mw)
    if _rank_key((baseline_params, baseline_mw)) <= _rank_key((best_params, best_mw)):
        best_params, best_mw = baseline_params, baseline_mw

    def serialize(mw: MultiWindowResult) -> dict[str, Any]:
        return _serialize_multi_window(mw, lookback_hours, cutoff_mode, step_hours)

    payload = {
        "mode": "tune",
        "validation": "multi_window_directional_replay",
        "model": {"slug": model_slug, "name": spec.name},
        "export_path": str(snapshot.export_path),
        "dataset_id": snapshot.dataset_id,
        "replay_windows": _serialize_multi_window_config(
            baseline_mw, lookback_hours, cutoff_mode, step_hours,
        ),
        "search": {
            "total_candidates": len(all_candidates),
            "evaluated": len(results),
        },
        "baseline": {
            "params": _json_safe_params(baseline_params),
            "replay_windows": serialize(baseline_mw),
        },
        "best": {
            "params": _json_safe_params(best_params),
            "replay_windows": serialize(best_mw),
            "availability_delta": (
                best_mw.scored_window_count - baseline_mw.scored_window_count
            ),
            "headline_delta": round(
                best_mw.headline_score - baseline_mw.headline_score, 3,
            ),
        },
        "candidates": [
            {
                "params": _json_safe_params(params),
                "replay_windows": serialize(mw),
            }
            for params, mw in results
        ],
    }
    save_results(
        mode="tune", model_slug=model_slug, payload=payload, output_dir=output_dir,
    )
    return payload


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_param(name: str, value: Any, original: Any) -> Any:
    """Coerce an override value to match the original constant's type.

    Handles the common cases: same type passthrough, int->float promotion,
    list->ndarray conversion, and string->scalar parsing. Raises ValueError
    with a clear message if coercion fails.
    """
    if isinstance(original, type(value)):
        return value

    # int -> float promotion
    if isinstance(original, float) and isinstance(value, int):
        return float(value)

    # list -> numpy array
    if isinstance(original, np.ndarray) and isinstance(value, list):
        return np.array(value, dtype=original.dtype)

    # Attempt generic conversion (covers str->int, str->float, etc.)
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


def _generate_cutoffs(
    *,
    scoring_events: list[FeedEvent],
    snapshot: ExportSnapshot,
    lookback_hours: float,
    cutoff_mode: str,
    step_hours: float,
) -> list[datetime]:
    """Generate retrospective cutoffs based on the chosen mode.

    Episode mode places cutoffs at feeding episode boundaries derived from
    bottle-only scoring events (matching the scorer's episode view). Fixed
    mode places cutoffs at regular intervals, anchored at
    max(earliest_activity, latest_activity - lookback).

    When the dataset does not span the full lookback range, the fixed-step
    grid shifts with data availability and can yield fewer windows than the
    episode-boundary mode.
    """
    if cutoff_mode == "episode":
        episodes = group_into_episodes(scoring_events)
        return generate_episode_boundary_cutoffs(
            episodes=episodes,
            latest_activity_time=snapshot.latest_activity_time,
            lookback_hours=lookback_hours,
        )
    if cutoff_mode == "fixed":
        earliest = min(activity.start for activity in snapshot.activities)
        return generate_fixed_step_cutoffs(
            latest_activity_time=snapshot.latest_activity_time,
            earliest_activity_time=earliest,
            lookback_hours=lookback_hours,
            step_hours=step_hours,
        )
    raise ValueError(f"cutoff_mode must be 'episode' or 'fixed'; got {cutoff_mode!r}.")


def _resolve_model_name(model_slug: str) -> str:
    """Get the display name for a model slug."""
    spec = get_model_spec(model_slug)
    if spec is not None:
        return spec.name
    if model_slug == CONSENSUS_BLEND_SLUG:
        return "Consensus Blend"
    raise ValueError(
        f"Replay supports scripted model slugs and {CONSENSUS_BLEND_SLUG}; "
        f"got {model_slug!r}."
    )


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


def _serialize_multi_window_config(
    result: MultiWindowResult,
    lookback_hours: float,
    cutoff_mode: str,
    step_hours: float,
) -> dict[str, Any]:
    """Serialize the shared multi-window configuration (no per-window detail).

    Used as the top-level replay_windows in tune payloads where per-window
    data is nested under each candidate.
    """
    payload: dict[str, Any] = {
        "lookback_hours": lookback_hours,
        "half_life_hours": result.half_life_hours,
        "cutoff_mode": cutoff_mode,
        "window_count": result.window_count,
    }
    if cutoff_mode == "fixed":
        payload["step_hours"] = step_hours
    return payload


def _serialize_multi_window(
    result: MultiWindowResult,
    lookback_hours: float,
    cutoff_mode: str,
    step_hours: float,
) -> dict[str, Any]:
    """Convert a MultiWindowResult to a JSON-serializable dict."""
    payload: dict[str, Any] = {
        "lookback_hours": lookback_hours,
        "half_life_hours": result.half_life_hours,
        "cutoff_mode": cutoff_mode,
        "window_count": result.window_count,
        "scored_window_count": result.scored_window_count,
        "availability_ratio": result.availability_ratio,
        "aggregate": {
            "headline": result.headline_score,
            "count": result.count_score,
            "timing": result.timing_score,
        },
        "per_window": [_serialize_window_result(w) for w in result.per_window],
    }
    if cutoff_mode == "fixed":
        payload["step_hours"] = step_hours
    return payload


def _serialize_window_result(window: WindowResult) -> dict[str, Any]:
    """Convert one WindowResult to a JSON-serializable dict."""
    entry: dict[str, Any] = {
        "cutoff": window.cutoff.isoformat(timespec="seconds"),
        "observed_until": window.observed_until.isoformat(timespec="seconds"),
        "weight": round(window.weight, 6),
        "status": window.status,
        "error_message": window.error_message,
    }
    if window.score is not None:
        entry["score"] = {
            "headline": window.score.score,
            "count": window.score.count_score,
            "timing": window.score.timing_score,
            "predicted_episode_count": window.score.predicted_episode_count,
            "actual_episode_count": window.score.actual_episode_count,
            "matched_episode_count": window.score.matched_episode_count,
            "observed_horizon_hours": window.score.observed_horizon_hours,
            "coverage_ratio": window.score.coverage_ratio,
        }
    else:
        entry["score"] = None
    return entry
