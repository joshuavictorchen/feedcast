"""Render the Markdown report and publish the latest report directory.

This module owns report assembly: Markdown rendering, diagnostics artifact
generation, and the atomic swap that keeps `report/` consistent on failure.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
import yaml

from feedcast.clustering import group_into_episodes
from feedcast.data import (
    BIRTH_DATE,
    ExportSnapshot,
    FeedEvent,
    Forecast,
    HORIZON_HOURS,
)
from feedcast.plots import write_schedule_plot, write_spaghetti_plot
from feedcast.tracker import HistoricalAccuracySummary, Retrospective

_LEADING_HEADING_PATTERN = re.compile(
    r"^\s{0,3}#{1,6}[ \t]+[^\n]+(?:\n+|$)"
)


def generate_report(
    snapshot: ExportSnapshot,
    all_forecasts: list[Forecast],
    featured_slug: str,
    events: list[FeedEvent],
    cutoff: datetime,
    run_id: str,
    retrospective: Retrospective,
    historical_accuracy: list[HistoricalAccuracySummary],
    tracker_meta: dict[str, Any],
    output_dir: Path = Path("report"),
    archive_dir: Path = Path(".report-archive"),
    agent_insights: str | None = None,
) -> Path:
    """Render and publish the latest report atomically."""
    featured = _find_forecast(all_forecasts, featured_slug)
    if not featured.available or not featured.points:
        raise ValueError(f"Featured forecast {featured_slug!r} is not renderable.")

    output_dir = Path(output_dir)
    archive_dir = Path(archive_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix="feedcast-report-", dir=str(output_dir.parent))
    )

    try:
        _render_report(
            output_dir=staging_dir,
            snapshot=snapshot,
            all_forecasts=all_forecasts,
            featured_slug=featured_slug,
            cutoff=cutoff,
            retrospective=retrospective,
            historical_accuracy=historical_accuracy,
            tracker_meta=tracker_meta,
            agent_insights=agent_insights,
        )
        write_schedule_plot(
            events=events,
            forecast_points=featured.points,
            cutoff=cutoff,
            output_path=staging_dir / "schedule.png",
            title="Next 24 Hours",
            subtitle=featured.name,
        )
        write_spaghetti_plot(
            output_path=staging_dir / "spaghetti.png",
            all_forecasts=all_forecasts,
            featured_slug=featured_slug,
            events=events,
            cutoff=cutoff,
        )
        _write_diagnostics(
            output_path=staging_dir / "diagnostics.yaml",
            all_forecasts=all_forecasts,
            featured_slug=featured_slug,
            cutoff=cutoff,
            tracker_meta=tracker_meta,
            retrospective=retrospective,
        )

        _validate_staged_report(staging_dir)
        _swap_report_directory(
            staging_dir=staging_dir,
            output_dir=output_dir,
            archive_dir=archive_dir,
            run_id=run_id,
        )
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return output_dir


def _render_report(
    output_dir: Path,
    snapshot: ExportSnapshot,
    all_forecasts: list[Forecast],
    featured_slug: str,
    cutoff: datetime,
    retrospective: Retrospective,
    historical_accuracy: list[HistoricalAccuracySummary],
    tracker_meta: dict[str, Any],
    agent_insights: str | None = None,
) -> None:
    """Render `report.md` from the package template."""
    template_dir = Path(__file__).resolve().parent / "templates"
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("report.md.j2")

    context = {
        "date_display": cutoff.strftime("%A, %B %-d, %Y"),
        "age_days": (cutoff.date() - BIRTH_DATE.date()).days,
        "cutoff_display": cutoff.strftime("%-I:%M %p"),
        "methodologies": [
            _prepare_methodology_row(forecast, featured_slug)
            for forecast in all_forecasts
        ],
        "retrospective": _prepare_retrospective(retrospective),
        "historical_accuracy": [
            {
                "name": row.name,
                "comparisons": row.comparison_count,
                "full_horizon": row.full_horizon_count,
                "score_display": _fmt_score(row.mean_score),
                "count_display": _fmt_score(row.mean_count_score),
                "timing_display": _fmt_score(row.mean_timing_score),
                "coverage_display": _fmt_ratio(row.mean_coverage_ratio),
            }
            for row in historical_accuracy
        ],
        "agent_insights": _strip_leading_heading(agent_insights),
        "source_file": snapshot.export_path.name,
        "dataset_id_short": snapshot.dataset_id[:15] + "...",
        "git_commit_display": _git_commit_display(tracker_meta),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    rendered = template.render(context)
    (output_dir / "report.md").write_text(rendered, encoding="utf-8")

    # Publish agent insights as a standalone report artifact during the
    # atomic swap. The renderer never reads this from disk — it flows
    # through the template context above.
    if agent_insights:
        (output_dir / "agent-insights.md").write_text(
            agent_insights + "\n", encoding="utf-8",
        )


def _prepare_methodology_row(forecast: Forecast, featured_slug: str) -> dict[str, str]:
    """Prepare one methodology section row."""
    title = forecast.name
    if forecast.slug == featured_slug:
        title = f"{title} (featured)"
    if not forecast.available:
        return {
            "title": title,
            "body": forecast.error_message or "Unavailable.",
        }
    return {
        "title": title,
        "body": _strip_leading_heading(forecast.methodology),
    }


def _strip_leading_heading(markdown: str | None) -> str | None:
    """Remove one leading markdown heading when embedding content in the report."""
    if markdown is None:
        return None

    normalized = markdown.strip()
    if not normalized:
        return None

    without_heading = _LEADING_HEADING_PATTERN.sub("", normalized, count=1)
    return without_heading.strip() or normalized


def _prepare_retrospective(retrospective: Retrospective) -> dict[str, Any]:
    """Convert the retrospective dataclass into template-ready fields."""
    return {
        "available": retrospective.available,
        "same_dataset": retrospective.same_dataset,
        "dataset_id_short": retrospective.dataset_id_short,
        "prior_run_id": retrospective.prior_run_id,
        "observed_horizon_hours": retrospective.observed_horizon_hours,
        "coverage_display": _fmt_ratio(
            retrospective.observed_horizon_hours / HORIZON_HOURS
            if retrospective.available
            else None
        ),
        "results": [
            {
                "name": result.name,
                "score_display": _fmt_score(result.score),
                "count_display": _fmt_score(result.count_score),
                "timing_display": _fmt_score(result.timing_score),
                "feeds_display": (
                    f"{result.predicted_episode_count}/{result.actual_episode_count}/{result.matched_episode_count}"
                    if result.score is not None
                    else "n/a"
                ),
                "status": result.status,
            }
            for result in retrospective.results
        ],
    }


def _forecast_diagnostics_entry(forecast: Forecast) -> dict[str, Any]:
    """Build one diagnostics entry for a forecast, including episode counts."""
    raw_count = len(forecast.points)
    episode_count = len(group_into_episodes(forecast.points)) if forecast.points else 0
    return {
        "name": forecast.name,
        "slug": forecast.slug,
        "available": forecast.available,
        "error_message": forecast.error_message,
        "raw_point_count": raw_count,
        "episode_count": episode_count,
        "collapsed_attachments": raw_count - episode_count,
        "diagnostics": _clean_value(forecast.diagnostics),
    }


def _write_diagnostics(
    output_path: Path,
    all_forecasts: list[Forecast],
    featured_slug: str,
    cutoff: datetime,
    tracker_meta: dict[str, Any],
    retrospective: Retrospective,
) -> None:
    """Write structured forecast diagnostics alongside the report."""
    payload = {
        "run_id": tracker_meta.get("run_id"),
        "cutoff": cutoff.isoformat(timespec="seconds"),
        "featured_slug": featured_slug,
        "forecasts": [
            _forecast_diagnostics_entry(forecast)
            for forecast in all_forecasts
        ],
        "retrospective": {
            "available": retrospective.available,
            "same_dataset": retrospective.same_dataset,
            "dataset_id_short": retrospective.dataset_id_short,
            "prior_run_id": retrospective.prior_run_id,
            "observed_horizon_hours": retrospective.observed_horizon_hours,
            "results": [
                {
                    "name": result.name,
                    "slug": result.slug,
                    "score": result.score,
                    "count_score": result.count_score,
                    "timing_score": result.timing_score,
                    "predicted_episode_count": result.predicted_episode_count,
                    "actual_episode_count": result.actual_episode_count,
                    "matched_episode_count": result.matched_episode_count,
                    "status": result.status,
                }
                for result in retrospective.results
            ],
        },
    }
    output_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def _validate_staged_report(staging_dir: Path) -> None:
    """Fail fast if the staged report directory is incomplete."""
    required_paths = [
        staging_dir / "report.md",
        staging_dir / "schedule.png",
        staging_dir / "spaghetti.png",
        staging_dir / "diagnostics.yaml",
    ]
    for path in required_paths:
        if not path.exists():
            raise AssertionError(f"Missing staged report artifact: {path.name}")


def _swap_report_directory(
    staging_dir: Path,
    output_dir: Path,
    archive_dir: Path,
    run_id: str,
) -> None:
    """Swap the staged report directory into place and archive the prior one.

    The sequence is designed so that ``report/`` is always valid:

      1. Rename the current ``report/`` to a backup.
      2. Rename the staging directory into ``report/``.
      3. If step 2 fails, restore the backup so the old report survives.
      4. Best-effort archive the backup into ``.report-archive/<run_id>/``.

    ``tracker.json`` is only updated after this swap succeeds, so the two
    are always in sync.
    """
    backup_dir: Path | None = None
    if output_dir.exists() and any(output_dir.iterdir()):
        backup_dir = output_dir.with_name(output_dir.name + ".bak")
        output_dir.rename(backup_dir)

    try:
        staging_dir.rename(output_dir)
    except Exception:
        # Restore the prior report so the repo stays consistent.
        if backup_dir is not None and backup_dir.exists():
            backup_dir.rename(output_dir)
        raise

    # The new report is live. Archive the old one (best-effort).
    if backup_dir is None or not backup_dir.exists():
        return

    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_target = archive_dir / run_id
        if archive_target.exists():
            shutil.rmtree(archive_target)
        backup_dir.rename(archive_target)
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _find_forecast(forecasts: list[Forecast], slug: str) -> Forecast:
    """Return the forecast matching one slug."""
    for forecast in forecasts:
        if forecast.slug == slug:
            return forecast
    raise KeyError(f"No forecast with slug {slug!r}.")


def _clean_value(value: Any) -> Any:
    """Convert diagnostics values into plain YAML-serializable structures."""
    if isinstance(value, dict):
        return {str(key): _clean_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_value(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) or hasattr(value, "__float__"):
        return round(float(value), 3)
    return str(value)


def _fmt_score(value: float | None) -> str:
    """Format a 0-100 score for display."""
    return "n/a" if value is None else f"{value:.1f}"


def _fmt_ratio(value: float | None) -> str:
    """Format a unit interval as a percentage."""
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _git_commit_display(tracker_meta: dict[str, Any]) -> str:
    """Return a footer-friendly commit label."""
    git_commit = tracker_meta.get("git_commit", "n/a")
    if tracker_meta.get("git_dirty"):
        return f"{git_commit} (dirty)"
    return str(git_commit)
