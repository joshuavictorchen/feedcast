"""Tracker persistence and prior-run retrospective comparison."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backtest import align_forecast_to_actual
from data import (
    ExportSnapshot,
    Forecast,
    ForecastPoint,
    HORIZON_HOURS,
    build_feed_events,
)


@dataclass(frozen=True)
class RetrospectiveResult:
    """Comparison between one prior forecast and new actuals."""

    name: str
    slug: str
    first_feed_error_minutes: float | None
    timing_mae_minutes: float | None
    status: str


@dataclass(frozen=True)
class Retrospective:
    """Report-ready retrospective summary for the most recent prior run."""

    available: bool
    same_dataset: bool = False
    dataset_id_short: str | None = None
    prior_run_id: str | None = None
    observed_horizon_hours: float = 0.0
    results: list[RetrospectiveResult] = field(default_factory=list)


def load_tracker(path: Path = Path("tracker.json")) -> dict[str, list[dict[str, Any]]]:
    """Load tracker history from disk.

    Args:
        path: Tracker JSON path.

    Returns:
        Tracker payload. Missing files return an empty run list.
    """
    if not path.exists():
        return {"runs": []}

    payload = json.loads(path.read_text(encoding="utf-8"))
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise ValueError(f"Tracker at {path} does not contain a 'runs' list.")
    return {"runs": runs}


def save_run(path: Path, run_entry: dict[str, Any]) -> None:
    """Append one run entry to tracker history.

    Args:
        path: Tracker JSON path.
        run_entry: Serialized run manifest.
    """
    tracker = load_tracker(path)
    tracker["runs"].append(run_entry)
    path.write_text(json.dumps(tracker, indent=2) + "\n", encoding="utf-8")


def build_run_entry(
    run_id: str,
    snapshot: ExportSnapshot,
    cutoff: datetime,
    forecasts: list[Forecast],
    prompt_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the tracker manifest for one completed report run.

    Args:
        run_id: Stable run identifier used for report/archive naming.
        snapshot: Export metadata for the current dataset.
        cutoff: Forecast cutoff used for this run.
        forecasts: All forecasts considered in the report.
        prompt_hashes: Optional prompt hashes for agent forecasts.

    Returns:
        JSON-serializable run entry.
    """
    git_commit = _git_commit()
    git_dirty = _git_dirty()
    model_slugs = [forecast.slug for forecast in forecasts]
    model_names = {forecast.slug: forecast.name for forecast in forecasts}
    predictions: dict[str, list[dict[str, str | float]]] = {}

    for forecast in forecasts:
        if not forecast.available:
            continue
        predictions[forecast.slug] = [point.to_dict() for point in forecast.points]

    return {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "dataset_id": snapshot.dataset_id,
        "source_file": snapshot.export_path.name,
        "source_hash": snapshot.source_hash,
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "model_slugs": model_slugs,
        "model_names": model_names,
        "prompt_hashes": prompt_hashes or {},
        "predictions": predictions,
    }


def compute_retrospective(
    tracker_path: Path,
    current_snapshot: ExportSnapshot,
) -> Retrospective:
    """Compare the most recent prior run against new actuals.

    Args:
        tracker_path: Tracker JSON path.
        current_snapshot: Newly loaded export snapshot.

    Returns:
        Retrospective summary for report rendering.
    """
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
        current_snapshot.activities, merge_window_minutes=None
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

    model_names = prior_run.get("model_names", {})
    predictions = prior_run.get("predictions", {})
    results: list[RetrospectiveResult] = []

    for slug in prior_run.get("model_slugs", []):
        name = model_names.get(slug, slug.replace("_", " ").title())
        if slug not in predictions:
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

        predicted_points = _deserialize_forecast_points(predictions[slug])
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
                timing_mae_minutes, _, _ = align_forecast_to_actual(
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
    """Return the current short git commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=_repo_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_dirty() -> bool:
    """Return whether tracked repo files are dirty.

    Untracked files are ignored so raw export drops do not mark every run dirty.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=_repo_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _repo_root() -> Path:
    """Return the repository root used for git metadata commands."""
    return Path(__file__).resolve().parent


def _short_dataset_id(dataset_id: str) -> str:
    """Return the short display form used in reports."""
    return dataset_id[:15] + "..."
