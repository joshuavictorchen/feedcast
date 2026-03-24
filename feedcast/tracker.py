"""Persist run history and evaluate prior predictions against new actuals.

This module intentionally avoids historical cutoff replay. The only accuracy
signal tracked here is retrospective performance: how the previous run's
predictions compared to the next export's observed bottle feeds.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from feedcast.data import (
    ExportSnapshot,
    Forecast,
    ForecastPoint,
    HORIZON_HOURS,
    build_feed_events,
)
from feedcast.scoring import (
    DEFAULT_HORIZON_WEIGHT_HALF_LIFE_HOURS,
    score_forecast,
)


@dataclass(frozen=True)
class RetrospectiveResult:
    """Comparison between one prior forecast and the newly observed actuals."""

    name: str
    slug: str
    score: float | None
    count_score: float | None
    timing_score: float | None
    predicted_feed_count: int
    actual_feed_count: int
    matched_feed_count: int
    status: str


@dataclass(frozen=True)
class Retrospective:
    """Report-ready summary for the most recent prior run."""

    available: bool
    same_dataset: bool = False
    dataset_id_short: str | None = None
    prior_run_id: str | None = None
    observed_horizon_hours: float = 0.0
    results: list[RetrospectiveResult] = field(default_factory=list)


@dataclass(frozen=True)
class HistoricalAccuracySummary:
    """Aggregate retrospective accuracy across stored tracker history."""

    name: str
    slug: str
    comparison_count: int
    full_horizon_count: int
    mean_score: float | None
    mean_count_score: float | None
    mean_timing_score: float | None
    mean_coverage_ratio: float | None


def load_tracker(path: Path = Path("tracker.json")) -> dict[str, list[dict[str, Any]]]:
    """Load tracker history from disk."""
    if not path.exists():
        return {"runs": []}

    payload = json.loads(path.read_text(encoding="utf-8"))
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise ValueError(f"Tracker at {path} does not contain a 'runs' list.")
    return {"runs": runs}


def save_run(path: Path, run_entry: dict[str, Any]) -> None:
    """Persist one completed run entry to tracker history.

    Consecutive reruns against the same dataset replace the prior entry
    instead of appending. This keeps committed history focused on new
    observations rather than iterative retries on the same export.
    """
    tracker = load_tracker(path)
    tracker["runs"] = _compact_run_history([*tracker["runs"], run_entry])
    path.write_text(json.dumps(tracker, indent=2) + "\n", encoding="utf-8")


def build_run_entry(
    run_id: str,
    snapshot: ExportSnapshot,
    cutoff: datetime,
    forecasts: list[Forecast],
    featured_slug: str,
    retrospective: Retrospective,
    prompt_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the tracker manifest for one completed pipeline run."""
    predictions: dict[str, list[dict[str, str | float]]] = {}
    model_names = {forecast.slug: forecast.name for forecast in forecasts}

    for forecast in forecasts:
        if forecast.available:
            predictions[forecast.slug] = [point.to_dict() for point in forecast.points]

    return {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "dataset_id": snapshot.dataset_id,
        "source_file": snapshot.export_path.name,
        "source_hash": snapshot.source_hash,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "featured_slug": featured_slug,
        "model_slugs": [forecast.slug for forecast in forecasts],
        "model_names": model_names,
        "prompt_hashes": prompt_hashes or {},
        "predictions": predictions,
        "retrospective": _serialize_retrospective(retrospective),
    }


def _compact_run_history(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive reruns of the same dataset to the latest entry."""
    compacted: list[dict[str, Any]] = []
    for run in runs:
        dataset_id = run.get("dataset_id")
        if (
            compacted
            and isinstance(dataset_id, str)
            and compacted[-1].get("dataset_id") == dataset_id
        ):
            compacted[-1] = run
            continue
        compacted.append(run)
    return compacted


def compute_retrospective(
    tracker_path: Path,
    current_snapshot: ExportSnapshot,
) -> Retrospective:
    """Compare the most recent prior run against new actual bottle events."""
    tracker = load_tracker(tracker_path)
    if not tracker["runs"]:
        return Retrospective(available=False)

    prior_run = tracker["runs"][-1]
    if prior_run["dataset_id"] == current_snapshot.dataset_id:
        return Retrospective(
            available=False,
            same_dataset=True,
            dataset_id_short=_short_dataset_id(current_snapshot.dataset_id),
            prior_run_id=prior_run["run_id"],
        )

    prior_cutoff = datetime.fromisoformat(prior_run["cutoff"])
    actual_events = build_feed_events(
        current_snapshot.activities,
        merge_window_minutes=None,
    )
    observed_horizon_hours = max(
        0.0,
        min(
            HORIZON_HOURS,
            (current_snapshot.latest_activity_time - prior_cutoff).total_seconds()
            / 3600.0,
        ),
    )

    results: list[RetrospectiveResult] = []
    model_names = prior_run.get("model_names", {})
    predictions = prior_run.get("predictions", {})

    for slug in prior_run.get("model_slugs", []):
        name = model_names.get(slug, slug.replace("_", " ").title())
        serialized_points = predictions.get(slug)
        if serialized_points is None:
            results.append(
                RetrospectiveResult(
                    name=name,
                    slug=slug,
                    score=None,
                    count_score=None,
                    timing_score=None,
                    predicted_feed_count=0,
                    actual_feed_count=0,
                    matched_feed_count=0,
                    status="Unavailable in prior run",
                )
            )
            continue

        predicted_points = _deserialize_forecast_points(serialized_points)
        if not predicted_points:
            results.append(
                RetrospectiveResult(
                    name=name,
                    slug=slug,
                    score=None,
                    count_score=None,
                    timing_score=None,
                    predicted_feed_count=0,
                    actual_feed_count=0,
                    matched_feed_count=0,
                    status="No predictions emitted",
                )
            )
            continue

        if observed_horizon_hours <= 0:
            results.append(
                RetrospectiveResult(
                    name=name,
                    slug=slug,
                    score=None,
                    count_score=None,
                    timing_score=None,
                    predicted_feed_count=0,
                    actual_feed_count=0,
                    matched_feed_count=0,
                    status="No observed horizon yet",
                )
            )
            continue

        forecast_score = score_forecast(
            predicted_points=predicted_points,
            actual_events=actual_events,
            prediction_time=prior_cutoff,
            observed_until=current_snapshot.latest_activity_time,
        )
        status = "Full 24h observed"
        if observed_horizon_hours < HORIZON_HOURS:
            status = f"Partial horizon ({observed_horizon_hours:.1f}h observed)"

        results.append(
            RetrospectiveResult(
                name=name,
                slug=slug,
                score=forecast_score.score,
                count_score=forecast_score.count_score,
                timing_score=forecast_score.timing_score,
                predicted_feed_count=forecast_score.predicted_count,
                actual_feed_count=forecast_score.actual_count,
                matched_feed_count=forecast_score.matched_count,
                status=status,
            )
        )

    return Retrospective(
        available=True,
        prior_run_id=prior_run["run_id"],
        observed_horizon_hours=observed_horizon_hours,
        results=results,
    )


def summarize_retrospective_history(
    tracker_path: Path,
    additional_retrospective: Retrospective | None = None,
) -> list[HistoricalAccuracySummary]:
    """Aggregate stored retrospective results into model-level accuracy rows.

    Historical means are weighted by observed-horizon evidence so thin partial
    windows contribute less than near-complete or full 24-hour retrospectives.
    """
    tracker = load_tracker(tracker_path)
    retrospective_blocks = [
        run.get("retrospective") for run in tracker["runs"] if isinstance(run, dict)
    ]
    if additional_retrospective is not None:
        retrospective_blocks.append(_serialize_retrospective(additional_retrospective))

    aggregates: dict[str, dict[str, Any]] = {}
    for retrospective in retrospective_blocks:
        if not isinstance(retrospective, dict):
            continue
        if not retrospective.get("available") or retrospective.get("same_dataset"):
            continue

        for result in retrospective.get("results", []):
            if not isinstance(result, dict):
                continue
            slug = result.get("slug")
            name = result.get("name")
            if not isinstance(slug, str) or not isinstance(name, str):
                continue

            aggregate = aggregates.setdefault(
                slug,
                {
                    "name": name,
                    "score_samples": [],
                    "count_samples": [],
                    "timing_samples": [],
                    "coverage_ratios": [],
                    "full_horizon_count": 0,
                },
            )
            score = _float_or_none(result.get("score"))
            count_score = _float_or_none(result.get("count_score"))
            timing_score = _float_or_none(result.get("timing_score"))
            observed_horizon_hours = min(
                HORIZON_HOURS,
                max(
                    0.0,
                    _float_or_none(retrospective.get("observed_horizon_hours")) or 0.0,
                ),
            )
            coverage_ratio = observed_horizon_hours / HORIZON_HOURS
            evidence_weight = _history_evidence_weight(observed_horizon_hours)

            if (
                score is not None
                and count_score is not None
                and timing_score is not None
            ):
                aggregate["score_samples"].append((score, evidence_weight))
                aggregate["count_samples"].append((count_score, evidence_weight))
                aggregate["timing_samples"].append((timing_score, evidence_weight))
                aggregate["coverage_ratios"].append(coverage_ratio)
                if coverage_ratio >= 1.0:
                    aggregate["full_horizon_count"] += 1

    summaries: list[HistoricalAccuracySummary] = []
    for slug, aggregate in aggregates.items():
        score_samples = aggregate["score_samples"]
        count_samples = aggregate["count_samples"]
        timing_samples = aggregate["timing_samples"]
        coverage_ratios = aggregate["coverage_ratios"]
        if not score_samples:
            continue

        summaries.append(
            HistoricalAccuracySummary(
                name=str(aggregate["name"]),
                slug=slug,
                comparison_count=len(score_samples),
                full_horizon_count=int(aggregate["full_horizon_count"]),
                mean_score=_weighted_mean_or_none(score_samples),
                mean_count_score=_weighted_mean_or_none(count_samples),
                mean_timing_score=_weighted_mean_or_none(timing_samples),
                mean_coverage_ratio=_mean_or_none(coverage_ratios),
            )
        )

    return sorted(
        summaries,
        key=lambda summary: (
            -_sortable_score(summary.mean_score),
            -_sortable_score(summary.mean_timing_score),
            -_sortable_score(summary.mean_count_score),
            summary.name,
        ),
    )


def _serialize_retrospective(retrospective: Retrospective) -> dict[str, Any]:
    """Convert a retrospective dataclass into tracker JSON."""
    return {
        "available": retrospective.available,
        "same_dataset": retrospective.same_dataset,
        "dataset_id_short": retrospective.dataset_id_short,
        "prior_run_id": retrospective.prior_run_id,
        "observed_horizon_hours": _round_or_none(retrospective.observed_horizon_hours),
        "results": [
            {
                "name": result.name,
                "slug": result.slug,
                "score": _round_or_none(result.score),
                "count_score": _round_or_none(result.count_score),
                "timing_score": _round_or_none(result.timing_score),
                "predicted_feed_count": result.predicted_feed_count,
                "actual_feed_count": result.actual_feed_count,
                "matched_feed_count": result.matched_feed_count,
                "status": result.status,
            }
            for result in retrospective.results
        ],
    }


def _deserialize_forecast_points(
    serialized_points: list[dict[str, Any]],
) -> list[ForecastPoint]:
    """Deserialize tracker prediction payloads into forecast points."""
    points: list[ForecastPoint] = []
    for item in serialized_points:
        points.append(
            ForecastPoint(
                time=datetime.fromisoformat(item["time"]),
                volume_oz=float(item["volume_oz"]),
                gap_hours=float(item.get("gap_hours", 0.0)),
            )
        )
    return points


def _git_commit() -> str:
    """Return the current short git commit SHA, if available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "n/a"
    return result.stdout.strip() or "n/a"


def _git_dirty() -> bool:
    """Return whether tracked repo files are dirty, ignoring untracked inputs."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return bool(result.stdout.strip())


def _repo_root() -> Path:
    """Return the repository root used for git metadata commands."""
    return Path(__file__).resolve().parent.parent


def _short_dataset_id(dataset_id: str) -> str:
    """Return the short display form used in reports."""
    return dataset_id[:15] + "..."


def _float_or_none(value: Any) -> float | None:
    """Return a float for numeric values, otherwise None."""
    if value is None:
        return None
    return float(value)


def _mean_or_none(values: list[float]) -> float | None:
    """Return the arithmetic mean or None for empty input."""
    if not values:
        return None
    return float(fmean(values))


def _weighted_mean_or_none(samples: list[tuple[float, float]]) -> float | None:
    """Return the weighted mean or None for empty input."""
    if not samples:
        return None

    total_weight = sum(weight for _, weight in samples)
    if total_weight <= 0:
        return None

    weighted_total = sum(value * weight for value, weight in samples)
    return weighted_total / total_weight


def _history_evidence_weight(observed_horizon_hours: float) -> float:
    """Return normalized retrospective evidence weight for one observed window.

    The weighting mirrors the scorer's horizon decay, so early hours count more
    heavily in history aggregation too.
    """
    if observed_horizon_hours <= 0:
        return 0.0

    numerator = 1.0 - 2.0 ** (
        -observed_horizon_hours / DEFAULT_HORIZON_WEIGHT_HALF_LIFE_HOURS
    )
    denominator = 1.0 - 2.0 ** (-HORIZON_HOURS / DEFAULT_HORIZON_WEIGHT_HALF_LIFE_HOURS)
    return numerator / denominator


def _round_or_none(value: float | None) -> float | None:
    """Round a float for compact tracker storage when present."""
    if value is None:
        return None
    return round(value, 3)


def _sortable_score(value: float | None) -> float:
    """Convert missing scores to a sentinel that sorts last."""
    return float("-inf") if value is None else value
