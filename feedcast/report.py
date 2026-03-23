"""Render the Markdown report and publish the latest report directory.

This module owns report assembly: Markdown rendering, diagnostics artifact
generation, and the atomic swap that keeps `report/` consistent on failure.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from feedcast.data import (
    BIRTH_DATE,
    DATA_FLOOR,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    DEFAULT_BREASTFEED_OZ_PER_30_MIN,
    ExportSnapshot,
    FeedEvent,
    Forecast,
)
from feedcast.plots import write_schedule_plot, write_spaghetti_plot
from feedcast.tracker import HistoricalAccuracySummary, Retrospective


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

    featured = _find_forecast(all_forecasts, featured_slug)
    featured_points = [_prepare_point(point) for point in featured.points]
    context = {
        "date_display": cutoff.strftime("%A, %B %-d, %Y"),
        "age_days": (cutoff.date() - BIRTH_DATE.date()).days,
        "cutoff_display": cutoff.strftime("%-I:%M %p"),
        "featured_name": featured.name,
        "featured_points": featured_points,
        "featured_total_oz": f"{sum(point.volume_oz for point in featured.points):.1f}",
        "comparison_rows": [
            _prepare_comparison_row(forecast, featured_slug)
            for forecast in all_forecasts
        ],
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
                "first_feed_display": _fmt_minutes(row.mean_first_feed_error_minutes),
                "timing_mae_display": _fmt_minutes(row.mean_timing_mae_minutes),
            }
            for row in historical_accuracy
        ],
        "history_days": (cutoff - DATA_FLOOR).days,
        "data_floor_display": DATA_FLOOR.strftime("%B %-d, %Y"),
        "bf_heuristic": (
            f"{DEFAULT_BREASTFEED_OZ_PER_30_MIN} oz per 30 min breastfeeding, "
            f"merged within {DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES} min"
        ),
        "source_file": snapshot.export_path.name,
        "dataset_id_short": snapshot.dataset_id[:15] + "...",
        "git_commit_display": _git_commit_display(tracker_meta),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    rendered = template.render(context)
    (output_dir / "report.md").write_text(rendered, encoding="utf-8")


def _prepare_point(point: Any) -> dict[str, str]:
    """Prepare one forecast point for template rendering."""
    return {
        "time_display": point.time.strftime("%-I:%M %p"),
        "gap_display": f"{point.gap_hours:.1f}h",
        "volume_display": f"{point.volume_oz:.1f} oz",
    }


def _prepare_comparison_row(forecast: Forecast, featured_slug: str) -> dict[str, str]:
    """Prepare a compact comparison row for one forecast source."""
    if not forecast.available:
        return {
            "name": forecast.name,
            "status": "Unavailable",
            "first_feed_display": "n/a",
            "feed_times_display": forecast.error_message or "Unavailable",
        }

    feed_times = ", ".join(
        point.time.strftime("%-I:%M %p") for point in forecast.points
    )
    status = "Featured" if forecast.slug == featured_slug else "Available"
    first_feed_display = (
        forecast.points[0].time.strftime("%-I:%M %p") if forecast.points else "n/a"
    )
    return {
        "name": forecast.name,
        "status": status,
        "first_feed_display": first_feed_display,
        "feed_times_display": feed_times or "n/a",
    }


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
        "body": forecast.methodology.strip(),
    }


def _prepare_retrospective(retrospective: Retrospective) -> dict[str, Any]:
    """Convert the retrospective dataclass into template-ready fields."""
    return {
        "available": retrospective.available,
        "same_dataset": retrospective.same_dataset,
        "dataset_id_short": retrospective.dataset_id_short,
        "prior_run_id": retrospective.prior_run_id,
        "observed_horizon_hours": retrospective.observed_horizon_hours,
        "results": [
            {
                "name": result.name,
                "first_feed_display": _fmt_minutes(result.first_feed_error_minutes),
                "timing_mae_display": _fmt_minutes(result.timing_mae_minutes),
                "status": result.status,
            }
            for result in retrospective.results
        ],
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
            {
                "name": forecast.name,
                "slug": forecast.slug,
                "available": forecast.available,
                "error_message": forecast.error_message,
                "diagnostics": _clean_value(forecast.diagnostics),
            }
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
                    "first_feed_error_minutes": result.first_feed_error_minutes,
                    "timing_mae_minutes": result.timing_mae_minutes,
                    "status": result.status,
                }
                for result in retrospective.results
            ],
        },
    }
    output_path.write_text(
        _to_yaml(payload),
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
    """Swap the staged report directory into place and archive the prior one."""
    backup_dir: Path | None = None
    if output_dir.exists() and any(output_dir.iterdir()):
        backup_dir = output_dir.with_name(output_dir.name + ".bak")
        output_dir.rename(backup_dir)

    try:
        staging_dir.rename(output_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists():
            backup_dir.rename(output_dir)
        raise

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


def _fmt_minutes(value: float | None) -> str:
    """Format a minutes metric for display."""
    return "n/a" if value is None else f"{value:.0f} min"


def _git_commit_display(tracker_meta: dict[str, Any]) -> str:
    """Return a footer-friendly commit label."""
    git_commit = tracker_meta.get("git_commit", "n/a")
    if tracker_meta.get("git_dirty"):
        return f"{git_commit} (dirty)"
    return str(git_commit)


def _to_yaml(value: Any) -> str:
    """Serialize a small nested payload into readable YAML."""
    return "\n".join(_yaml_lines(value)) + "\n"


def _yaml_lines(value: Any, indent: int = 0) -> list[str]:
    """Return YAML lines for one nested structure."""
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}{{}}"]
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _yaml_scalar(value: Any) -> str:
    """Return one scalar value encoded safely for YAML output."""
    return json.dumps(value, ensure_ascii=True)
