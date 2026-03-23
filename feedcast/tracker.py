"""Persist run history and evaluate prior predictions against new actuals.

This module intentionally avoids historical cutoff replay. The only accuracy
signal tracked here is retrospective performance: how the previous run's
predictions compared to the next export's observed bottle feeds.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from feedcast.data import (
    ExportSnapshot,
    Forecast,
    ForecastPoint,
    HORIZON_HOURS,
    build_feed_events,
)

UNMATCHED_PENALTY_MINUTES = 180.0


@dataclass(frozen=True)
class RetrospectiveResult:
    """Comparison between one prior forecast and the newly observed actuals."""

    name: str
    slug: str
    first_feed_error_minutes: float | None
    timing_mae_minutes: float | None
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
    mean_first_feed_error_minutes: float | None
    mean_timing_mae_minutes: float | None


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
    """Append one completed run entry to tracker history."""
    tracker = load_tracker(path)
    tracker["runs"].append(run_entry)
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
    actual_after_cutoff = [
        event for event in actual_events if event.time > prior_cutoff
    ]

    observed_horizon_hours = 0.0
    if actual_after_cutoff:
        observed_horizon_hours = max(
            0.0,
            min(
                HORIZON_HOURS,
                (actual_after_cutoff[-1].time - prior_cutoff).total_seconds() / 3600,
            ),
        )

    actual_within_horizon = [
        event
        for event in actual_after_cutoff
        if event.time <= prior_cutoff + timedelta(hours=HORIZON_HOURS)
    ]

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
                    first_feed_error_minutes=None,
                    timing_mae_minutes=None,
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
                    first_feed_error_minutes=None,
                    timing_mae_minutes=None,
                    status="No predictions emitted",
                )
            )
            continue

        first_feed_error_minutes = None
        if actual_within_horizon:
            first_feed_error_minutes = (
                abs(
                    (
                        predicted_points[0].time - actual_within_horizon[0].time
                    ).total_seconds()
                )
                / 60
            )

        timing_mae_minutes = None
        status = "No bottle feeds observed yet"
        if actual_within_horizon:
            if observed_horizon_hours >= HORIZON_HOURS:
                timing_mae_minutes, _, _ = _align_forecast_to_actual(
                    predicted_points,
                    actual_within_horizon,
                )
                status = "Full 24h observed"
            else:
                status = f"Partial horizon ({observed_horizon_hours:.1f}h observed)"

        results.append(
            RetrospectiveResult(
                name=name,
                slug=slug,
                first_feed_error_minutes=first_feed_error_minutes,
                timing_mae_minutes=timing_mae_minutes,
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
    """Aggregate stored retrospective results into model-level accuracy rows."""
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
                    "first_feed_errors": [],
                    "timing_errors": [],
                },
            )
            first_feed_error = _float_or_none(result.get("first_feed_error_minutes"))
            timing_error = _float_or_none(result.get("timing_mae_minutes"))
            if first_feed_error is not None:
                aggregate["first_feed_errors"].append(first_feed_error)
            if timing_error is not None:
                aggregate["timing_errors"].append(timing_error)

    summaries: list[HistoricalAccuracySummary] = []
    for slug, aggregate in aggregates.items():
        first_feed_errors = aggregate["first_feed_errors"]
        timing_errors = aggregate["timing_errors"]
        if not first_feed_errors and not timing_errors:
            continue

        summaries.append(
            HistoricalAccuracySummary(
                name=str(aggregate["name"]),
                slug=slug,
                comparison_count=len(first_feed_errors),
                full_horizon_count=len(timing_errors),
                mean_first_feed_error_minutes=_mean_or_none(first_feed_errors),
                mean_timing_mae_minutes=_mean_or_none(timing_errors),
            )
        )

    return sorted(
        summaries,
        key=lambda summary: (
            _sortable_metric(summary.mean_first_feed_error_minutes),
            _sortable_metric(summary.mean_timing_mae_minutes),
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
                "first_feed_error_minutes": _round_or_none(
                    result.first_feed_error_minutes
                ),
                "timing_mae_minutes": _round_or_none(result.timing_mae_minutes),
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


def _align_forecast_to_actual(
    predicted: list[ForecastPoint],
    actual: list[Any],
) -> tuple[float | None, int, int]:
    """Align two ordered feed sequences with an order-preserving dynamic program.

    This is an edit-distance style alignment over two time-ordered feed lists.
    At each cell (i, j) the DP chooses the cheapest of three moves:

      - Match: pair predicted[i] with actual[j]; cost = abs time difference.
      - Skip predicted: leave predicted[i] unmatched; cost = UNMATCHED_PENALTY.
      - Skip actual: leave actual[j] unmatched; cost = UNMATCHED_PENALTY.

    After filling the table, the traceback recovers the optimal alignment and
    returns the mean timing error across matched pairs.
    """
    if not predicted and not actual:
        return None, 0, 0

    predicted_count = len(predicted)
    actual_count = len(actual)

    # dp[i][j] = minimum total cost to align predicted[:i] with actual[:j].
    # step[i][j] records which move was taken to reach (i, j).
    dp = np.full((predicted_count + 1, actual_count + 1), np.inf)
    step = np.empty((predicted_count + 1, actual_count + 1), dtype=object)
    dp[0, 0] = 0.0

    # Forward pass: fill the cost table.
    for predicted_index in range(predicted_count + 1):
        for actual_index in range(actual_count + 1):
            base_cost = dp[predicted_index, actual_index]
            if np.isinf(base_cost):
                continue

            if predicted_index < predicted_count and actual_index < actual_count:
                match_cost = (
                    abs(
                        (
                            predicted[predicted_index].time - actual[actual_index].time
                        ).total_seconds()
                    )
                    / 60
                )
                if base_cost + match_cost < dp[predicted_index + 1, actual_index + 1]:
                    dp[predicted_index + 1, actual_index + 1] = base_cost + match_cost
                    step[predicted_index + 1, actual_index + 1] = "match"

            if predicted_index < predicted_count:
                skip_predicted_cost = base_cost + UNMATCHED_PENALTY_MINUTES
                if skip_predicted_cost < dp[predicted_index + 1, actual_index]:
                    dp[predicted_index + 1, actual_index] = skip_predicted_cost
                    step[predicted_index + 1, actual_index] = "skip_predicted"

            if actual_index < actual_count:
                skip_actual_cost = base_cost + UNMATCHED_PENALTY_MINUTES
                if skip_actual_cost < dp[predicted_index, actual_index + 1]:
                    dp[predicted_index, actual_index + 1] = skip_actual_cost
                    step[predicted_index, actual_index + 1] = "skip_actual"

    # Backward pass: trace back through the step table to recover the alignment.
    predicted_index = predicted_count
    actual_index = actual_count
    matched_time_errors: list[float] = []
    unmatched_predicted = 0
    unmatched_actual = 0

    while predicted_index > 0 or actual_index > 0:
        action = step[predicted_index, actual_index]
        if action == "match":
            matched_time_errors.append(
                abs(
                    (
                        predicted[predicted_index - 1].time
                        - actual[actual_index - 1].time
                    ).total_seconds()
                )
                / 60
            )
            predicted_index -= 1
            actual_index -= 1
            continue
        if action == "skip_predicted":
            unmatched_predicted += 1
            predicted_index -= 1
            continue
        if action == "skip_actual":
            unmatched_actual += 1
            actual_index -= 1
            continue
        break

    matched_time_errors.reverse()
    return _mean_or_none(matched_time_errors), unmatched_predicted, unmatched_actual


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
    return float(np.mean(values))


def _round_or_none(value: float | None) -> float | None:
    """Round a float for compact tracker storage when present."""
    if value is None:
        return None
    return round(value, 3)


def _sortable_metric(value: float | None) -> float:
    """Convert a missing metric to infinity for stable sorting."""
    return float("inf") if value is None else value
