"""Report generation: Jinja2 Markdown rendering and matplotlib plots."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Matplotlib needs a writable config directory. Set this BEFORE importing
# matplotlib so the library picks it up during its module-level init.
_mpl_config_dir = Path(".mpl-cache")
_mpl_config_dir.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config_dir.resolve()))

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from jinja2 import Environment, FileSystemLoader  # noqa: E402

from data import (  # noqa: E402
    BIRTH_DATE,
    DATA_FLOOR,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    DEFAULT_BREASTFEED_OZ_PER_30_MIN,
    HORIZON_HOURS,
    ExportSnapshot,
    FeedEvent,
    Forecast,
)

# ── Apple-inspired color palette ──────────────────────────────────────────

BLUE = "#007AFF"
ORANGE = "#FF9500"
RED = "#FF3B30"
GREEN = "#34C759"
PURPLE = "#AF52DE"
TEAL = "#5AC8FA"
PINK = "#FF2D55"
INDIGO = "#5856D6"
BG = "#FAFAFA"
SEPARATOR = "#E5E5EA"
LABEL_SECONDARY = "#86868B"

MODEL_COLORS = {
    "recent_cadence": "#8E8E93",
    "phase_nowcast": BLUE,
    "gap_conditional": ORANGE,
    "consensus_blend": RED,
    "claude_forecast": PURPLE,
    "codex_forecast": TEAL,
}

FEATURED_COLOR = GREEN


# ── Public entry point ────────────────────────────────────────────────────


def generate_report(
    snapshot: ExportSnapshot,
    all_forecasts: list[Forecast],
    featured_slug: str,
    backtest_results: list[Any],
    events: list[FeedEvent],
    cutoff: datetime,
    run_id: str,
    retrospective: Any | None = None,
    output_dir: Path = Path("report"),
    archive_dir: Path = Path(".report-archive"),
) -> Path:
    """Render the full report set atomically.

    Args:
        snapshot: Export metadata.
        all_forecasts: All forecasts (scripted + agents + blend).
        featured_slug: Slug of the featured forecast.
        backtest_results: List of ModelBacktest results.
        events: Feed events for the spaghetti plot history tail.
        cutoff: Analysis cutoff time.
        run_id: Timestamp-based run identifier.
        retrospective: Optional retrospective comparison data.
        output_dir: Target directory for the latest report.
        archive_dir: Directory to move prior reports into.

    Returns:
        Path to the report directory after a successful swap.
    """
    # Stage into a sibling temp directory next to output_dir so the final
    # rename is on the same filesystem (required for atomic os.rename).
    output_dir = Path(output_dir)
    archive_dir = Path(archive_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(prefix="nara-report-", dir=str(output_dir.parent))
    )
    try:
        # Step 1: Render into staging directory
        _render_summary(
            staging_dir,
            snapshot=snapshot,
            all_forecasts=all_forecasts,
            featured_slug=featured_slug,
            backtest_results=backtest_results,
            cutoff=cutoff,
            retrospective=retrospective,
        )
        _plot_spaghetti(
            staging_dir / "spaghetti.png",
            all_forecasts=all_forecasts,
            featured_slug=featured_slug,
            events=events,
            cutoff=cutoff,
        )

        # Step 2: Validate staged output
        assert (staging_dir / "summary.md").exists(), "summary.md missing"
        assert (staging_dir / "spaghetti.png").exists(), "spaghetti.png missing"

        # Step 3: Atomic swap with restore-on-failure.
        #
        # Sequence:
        #   a) Rename report/ to a sibling backup
        #   b) Rename staging/ to report/
        #   c) Move backup into .report-archive/
        #
        # If (b) fails, restore the backup back to report/ so the repo
        # always has a valid latest report.
        backup_dir: Path | None = None
        if output_dir.exists() and any(output_dir.iterdir()):
            backup_dir = output_dir.with_name(output_dir.name + ".bak")
            output_dir.rename(backup_dir)

        try:
            staging_dir.rename(output_dir)
        except Exception:
            # Restore the previous report so the repo stays consistent
            if backup_dir is not None and backup_dir.exists():
                backup_dir.rename(output_dir)
            raise

        # Swap succeeded — archive the backup. This is best-effort cleanup:
        # the new report is already live, so a failure here must not raise
        # or leave stale state behind.
        if backup_dir is not None and backup_dir.exists():
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_target = archive_dir / run_id
                # Handle target collision (e.g., same run_id from a re-run)
                if archive_target.exists():
                    shutil.rmtree(archive_target)
                backup_dir.rename(archive_target)
            except Exception:
                # Archive failed — clean up the backup so it doesn't linger
                shutil.rmtree(backup_dir, ignore_errors=True)

    except Exception:
        # If rendering/validation failed, clean up the staging directory
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return output_dir


# ── Jinja2 rendering ─────────────────────────────────────────────────────


def _render_summary(
    output_dir: Path,
    snapshot: ExportSnapshot,
    all_forecasts: list[Forecast],
    featured_slug: str,
    backtest_results: list[Any],
    cutoff: datetime,
    retrospective: Any | None,
) -> None:
    """Render summary.md from the Jinja2 template."""
    template_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("summary.md.j2")

    featured = _find_forecast(all_forecasts, featured_slug)
    baby_age = (cutoff.date() - BIRTH_DATE.date()).days
    history_days = (cutoff - DATA_FLOOR).days

    context = {
        "date_display": cutoff.strftime("%A, %B %-d, %Y"),
        "age_days": baby_age,
        "cutoff_display": cutoff.strftime("%-I:%M %p"),
        # Featured forecast
        "featured": _prepare_forecast(featured),
        "featured_total_oz": f"{sum(p.volume_oz for p in featured.points):.1f}",
        # All forecasts for per-model sections
        "all_forecasts": [_prepare_forecast(f) for f in all_forecasts],
        # Backtest results
        "backtest_results": [_prepare_backtest(bt) for bt in backtest_results],
        # Retrospective
        "retrospective": retrospective,
        # Limitations
        "history_days": history_days,
        "data_floor_display": DATA_FLOOR.strftime("%B %-d, %Y"),
        "bf_heuristic": (
            f"{DEFAULT_BREASTFEED_OZ_PER_30_MIN} oz per 30 min breastfeeding, "
            f"merged within {DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES} min"
        ),
        # Footer
        "source_file": snapshot.export_path.name,
        "dataset_id_short": snapshot.dataset_id[:15] + "...",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    rendered = template.render(context)
    (output_dir / "summary.md").write_text(rendered, encoding="utf-8")


def _find_forecast(forecasts: list[Forecast], slug: str) -> Forecast:
    """Find a forecast by slug."""
    for forecast in forecasts:
        if forecast.slug == slug:
            return forecast
    raise KeyError(f"No forecast with slug '{slug}'")


def _prepare_forecast(forecast: Forecast) -> dict[str, Any]:
    """Prepare a forecast for template rendering."""
    points = []
    for point in forecast.points:
        points.append(
            {
                "time": point.time,
                "time_display": point.time.strftime("%-I:%M %p"),
                "volume_display": f"{point.volume_oz:.1f} oz",
                "gap_display": f"{point.gap_hours:.1f}h",
            }
        )
    return {
        "name": forecast.name,
        "slug": forecast.slug,
        "points": points,
        "methodology": forecast.methodology,
        "diagnostics": _clean_diagnostics(forecast.diagnostics),
        "available": forecast.available,
        "error_message": forecast.error_message,
    }


def _prepare_backtest(backtest: Any) -> dict[str, str]:
    """Prepare a ModelBacktest for template rendering."""
    summary = backtest.summary
    return {
        "name": backtest.name,
        "slug": backtest.slug,
        "recent_first_feed_display": _fmt_min(summary.recent_first_feed_error_minutes),
        "coverage_display": _fmt_pct(summary.cutoff_coverage_ratio),
        "overall_first_feed_display": _fmt_min(summary.mean_first_feed_error_minutes),
        "timing_mae_display": _fmt_min(summary.mean_timing_mae_minutes),
    }


# ── Spaghetti plot ───────────────────────────────────────────────────────


def _plot_spaghetti(
    output_path: Path,
    all_forecasts: list[Forecast],
    featured_slug: str,
    events: list[FeedEvent],
    cutoff: datetime,
    history_tail_hours: float = 12,
) -> None:
    """Hero spaghetti plot: all trajectories, featured forecast emphasized."""
    _apply_plot_style()
    fig, ax = plt.subplots(figsize=(16, 7))

    # Recent actuals
    history_start = cutoff - timedelta(hours=history_tail_hours)
    recent_events = [e for e in events if history_start <= e.time <= cutoff]
    if recent_events:
        times = [e.time for e in recent_events]
        ax.plot(
            times,
            [1] * len(times),
            "o-",
            color="#1D1D1F",
            markersize=7,
            linewidth=1.5,
            alpha=0.8,
            zorder=7,
            label="Actual (recent)",
        )

    # Non-featured forecasts — faded
    y_level = 1
    for forecast in all_forecasts:
        if forecast.slug == featured_slug or not forecast.available:
            continue
        if not forecast.points:
            continue
        times = [cutoff] + [p.time for p in forecast.points]
        color = MODEL_COLORS.get(forecast.slug, "#AEAEB2")
        ax.plot(
            times,
            [y_level] * len(times),
            "o-",
            color=color,
            markersize=4,
            linewidth=1.0,
            alpha=0.3,
            zorder=3,
            label=forecast.name,
        )

    # Featured forecast — bold
    featured = _find_forecast(all_forecasts, featured_slug)
    if featured.available and featured.points:
        times = [cutoff] + [p.time for p in featured.points]
        ax.plot(
            times,
            [y_level] * len(times),
            "D-",
            color=FEATURED_COLOR,
            markersize=9,
            linewidth=2.5,
            alpha=0.9,
            zorder=6,
            label=f"{featured.name} (featured)",
        )
        # Time labels on featured points
        for point in featured.points:
            ax.annotate(
                point.time.strftime("%-I:%M"),
                (point.time, y_level),
                textcoords="offset points",
                xytext=(0, 14),
                fontsize=7.5,
                ha="center",
                color=FEATURED_COLOR,
                fontweight="bold",
            )

    # NOW marker
    ax.axvline(cutoff, color=RED, linewidth=1.2, alpha=0.5, linestyle="--", zorder=8)
    ax.annotate(
        "NOW",
        (cutoff, y_level),
        textcoords="offset points",
        xytext=(0, -20),
        fontsize=8,
        color=RED,
        fontweight="bold",
        ha="center",
    )

    # Axis formatting
    ax.set_yticks([])
    ax.set_ylim(0.5, 1.5)
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-I %p"))
    ax.tick_params(axis="both", which="both", length=0)
    ax.grid(True, which="major", axis="x", alpha=0.15, color=SEPARATOR, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(loc="upper right", fontsize=9, frameon=False)

    fig.text(
        0.04,
        0.96,
        "Forecast Trajectories",
        fontsize=20,
        fontweight="bold",
        color="#1D1D1F",
        va="top",
    )
    fig.text(
        0.04,
        0.92,
        f"All models \u00b7 cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}",
        fontsize=10,
        color=LABEL_SECONDARY,
        va="top",
    )

    fig.subplots_adjust(top=0.85, bottom=0.08, left=0.04, right=0.96)
    fig.savefig(
        output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none"
    )
    plt.close(fig)


# ── Helpers ───────────────────────────────────────────────────────────────


def _apply_plot_style() -> None:
    """Set matplotlib rcParams for a clean, Apple-inspired aesthetic."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Helvetica Neue",
                "Helvetica",
                "Arial",
                "DejaVu Sans",
            ],
            "figure.facecolor": BG,
            "axes.facecolor": BG,
            "text.color": "#1D1D1F",
            "axes.labelcolor": "#555555",
            "xtick.color": LABEL_SECONDARY,
            "ytick.color": LABEL_SECONDARY,
        }
    )


def _clean_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Convert numpy types and nested dicts to clean display strings."""
    cleaned: dict[str, Any] = {}
    for key, value in diagnostics.items():
        if isinstance(value, dict):
            # Flatten nested dicts into a readable string
            inner = ", ".join(f"{k}={_clean_value(v)}" for k, v in value.items())
            cleaned[key] = f"{{{inner}}}"
        else:
            cleaned[key] = _clean_value(value)
    return cleaned


def _clean_value(value: Any) -> Any:
    """Convert a single value to a clean display type."""
    # Preserve native Python ints (don't coerce to float)
    if isinstance(value, (int, bool)):
        return value
    if isinstance(value, float) or hasattr(value, "__float__"):
        return round(float(value), 3)
    if isinstance(value, (list, tuple)):
        return [_clean_value(v) for v in value]
    return value


def _fmt_min(value: float | None) -> str:
    """Format a minutes value for display."""
    return "n/a" if value is None else f"{value:.0f} min"


def _fmt_pct(value: float | None) -> str:
    """Format a ratio as a percentage."""
    return "n/a" if value is None else f"{value:.0%}"
