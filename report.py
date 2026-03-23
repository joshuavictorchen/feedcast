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
import matplotlib.ticker as mticker  # noqa: E402
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
    ForecastPoint,
    hour_of_day,
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

# Schedule chart constants
ORANGE_SOFT = "#FFCC80"
CARD = "#FFFFFF"
NIGHT_FILL = "#F0F0F5"
PROJ_FILL = "#FFF7ED"
DISPLAY_DAYS = 7


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
    tracker_meta: dict[str, Any] | None = None,
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
        tracker_meta: Optional run metadata for the footer.
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
            tracker_meta=tracker_meta,
        )
        _plot_spaghetti(
            staging_dir / "spaghetti.png",
            all_forecasts=all_forecasts,
            featured_slug=featured_slug,
            events=events,
            cutoff=cutoff,
        )

        # Featured forecast schedule chart (Apple-style calendar view)
        featured = _find_forecast(all_forecasts, featured_slug)
        if featured.available and featured.points:
            _plot_schedule(
                events=events,
                forecast_points=featured.points,
                cutoff=cutoff,
                output_path=staging_dir / "schedule.png",
                title="Featured Forecast",
                subtitle=featured.name,
            )

        # Per-model schedule charts
        for forecast in all_forecasts:
            if not forecast.available or not forecast.points:
                continue
            color = MODEL_COLORS.get(forecast.slug, ORANGE)
            _plot_schedule(
                events=events,
                forecast_points=forecast.points,
                cutoff=cutoff,
                output_path=staging_dir / f"{forecast.slug}.png",
                title=forecast.name,
                subtitle=forecast.methodology[:80] if forecast.methodology else "",
                forecast_color=color,
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
    tracker_meta: dict[str, Any] | None,
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
        "retrospective": _prepare_retrospective(retrospective),
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
        "git_commit_display": _git_commit_display(tracker_meta),
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


def _prepare_retrospective(retrospective: Any | None) -> dict[str, Any] | None:
    """Prepare retrospective data for template rendering."""
    if retrospective is None:
        return None

    results = []
    for result in getattr(retrospective, "results", []):
        results.append(
            {
                "name": result.name,
                "slug": result.slug,
                "first_feed_display": _fmt_min(result.first_feed_error_minutes),
                "timing_mae_display": _fmt_min(result.timing_mae_minutes),
                "status": result.status,
            }
        )

    return {
        "available": retrospective.available,
        "same_dataset": retrospective.same_dataset,
        "dataset_id_short": retrospective.dataset_id_short,
        "prior_run_id": retrospective.prior_run_id,
        "observed_horizon_hours": retrospective.observed_horizon_hours,
        "results": results,
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


# ── Schedule chart (Apple-style calendar view) ──────────────────────────


def _plot_schedule(
    events: list[FeedEvent],
    forecast_points: list[ForecastPoint],
    cutoff: datetime,
    output_path: Path,
    title: str,
    subtitle: str,
    forecast_color: str = ORANGE,
) -> None:
    """Calendar-style schedule chart: daily columns, time-of-day on Y axis.

    Historical feeds are blue circles sized by volume. Predicted feeds are
    colored diamonds with time/volume labels. The forecast window is highlighted
    with a warm fill.
    """
    _apply_plot_style()

    # Date range: DISPLAY_DAYS of history through the end of the forecast
    display_start = max(
        DATA_FLOOR,
        (cutoff - timedelta(days=DISPLAY_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ),
    )
    proj_end = cutoff + timedelta(hours=HORIZON_HOURS)
    display_end = proj_end.replace(hour=0, minute=0, second=0)

    all_dates = []
    current_date = display_start.date()
    while current_date <= display_end.date():
        all_dates.append(current_date)
        current_date += timedelta(days=1)
    date_to_x = {date: i for i, date in enumerate(all_dates)}
    proj_dates = {p.time.date() for p in forecast_points}

    fig, axis = plt.subplots(figsize=(16, 9.5))

    # Day columns: white cards, warm fill for forecast days, night bands
    for x, date in enumerate(all_dates):
        axis.axvspan(
            x - 0.42,
            x + 0.42,
            color=PROJ_FILL if date in proj_dates else CARD,
            zorder=0,
            linewidth=0,
        )
        # Night bands (midnight–6am, 6pm–midnight)
        axis.axvspan(
            x - 0.42,
            x + 0.42,
            ymin=1 - 6 / 24,
            ymax=1.0,
            color=NIGHT_FILL,
            zorder=1,
            linewidth=0,
        )
        axis.axvspan(
            x - 0.42,
            x + 0.42,
            ymin=0,
            ymax=3 / 24,
            color=NIGHT_FILL,
            zorder=1,
            linewidth=0,
        )

    # Column separators
    for x in range(len(all_dates) + 1):
        axis.axvline(x - 0.5, color=SEPARATOR, linewidth=0.5, alpha=0.5, zorder=2)

    # NOW marker
    if cutoff.date() in date_to_x:
        now_x = date_to_x[cutoff.date()]
        now_y = hour_of_day(cutoff)
        axis.plot(
            [now_x - 0.42, now_x + 0.42],
            [now_y, now_y],
            color=RED,
            linewidth=1.2,
            alpha=0.5,
            zorder=9,
        )
        axis.scatter(
            [now_x],
            [now_y],
            color="white",
            s=50,
            zorder=10,
            edgecolors=RED,
            linewidths=1.5,
        )
        axis.annotate(
            "NOW",
            (now_x + 0.42, now_y),
            fontsize=6.5,
            color=RED,
            fontweight="bold",
            va="center",
            ha="left",
            xytext=(4, 0),
            textcoords="offset points",
        )

    # Historical feeds: blue circles sized by volume, fading with age
    history = [e for e in events if display_start <= e.time <= cutoff]
    if history:
        hx = np.array([date_to_x[e.time.date()] for e in history], dtype=float)
        hy = np.array([hour_of_day(e.time) for e in history], dtype=float)
        hvols = np.array([e.volume_oz for e in history], dtype=float)
        hsizes = np.array([_volume_to_marker_size(v) for v in hvols], dtype=float)
        ages = np.array([(cutoff - e.time).total_seconds() / 3600 for e in history])
        max_age = max(float(np.max(ages)), 1.0)
        alphas = 0.25 + (0.60 * (1 - (ages / max_age)))
        for i in range(len(history)):
            axis.scatter(
                hx[i],
                hy[i],
                s=hsizes[i],
                c=BLUE,
                alpha=float(alphas[i]),
                edgecolors="white",
                linewidths=0.6,
                zorder=5,
            )

    # Forecast points: colored diamonds with labels
    if forecast_points:
        fx = np.array(
            [date_to_x.get(p.time.date(), 0) for p in forecast_points], dtype=float
        )
        fy = np.array([hour_of_day(p.time) for p in forecast_points], dtype=float)
        fvols = np.array([p.volume_oz for p in forecast_points], dtype=float)
        fsizes = np.array([_volume_to_marker_size(v) for v in fvols], dtype=float)
        # Soft glow behind each diamond
        for i in range(len(forecast_points)):
            axis.scatter(
                fx[i],
                fy[i],
                s=fsizes[i] * 2.5,
                c=ORANGE_SOFT,
                alpha=0.2,
                zorder=3,
                linewidths=0,
            )
        axis.scatter(
            fx,
            fy,
            s=fsizes,
            c=forecast_color,
            alpha=0.85,
            edgecolors="white",
            linewidths=0.8,
            zorder=6,
            marker="D",
        )
        # Volume and time labels
        label_color = _darken_color(forecast_color)
        for i, point in enumerate(forecast_points):
            label = (
                f"{point.volume_oz:.1f} oz\n"
                f"{point.time.strftime('%-I:%M %p').lower()}"
            )
            axis.annotate(
                label,
                (fx[i], fy[i]),
                textcoords="offset points",
                xytext=(14, 0),
                fontsize=7,
                color=label_color,
                ha="left",
                va="center",
                fontweight="medium",
                linespacing=1.3,
            )

    # Axis formatting
    axis.set_xticks(range(len(all_dates)))
    axis.set_xticklabels(
        [
            datetime.combine(d, datetime.min.time()).strftime("%a\n%-m/%d")
            for d in all_dates
        ],
        fontsize=9,
        fontweight="medium",
    )
    axis.set_ylim(24, 0)
    axis.set_yticks(range(0, 25, 3))
    axis.set_yticklabels([_format_hour(h) for h in range(0, 25, 3)], fontsize=9)
    axis.yaxis.set_minor_locator(mticker.MultipleLocator(1))
    axis.grid(True, which="major", axis="y", alpha=0.2, color=SEPARATOR, linewidth=0.5)
    axis.grid(True, which="minor", axis="y", alpha=0.08, color=SEPARATOR, linewidth=0.3)
    axis.tick_params(axis="both", which="both", length=0)
    axis.set_xlim(-0.55, len(all_dates) - 0.45)
    for spine in axis.spines.values():
        spine.set_visible(False)

    # Title and subtitle
    fig.text(
        0.04,
        0.965,
        title,
        fontsize=22,
        fontweight="bold",
        color="#1D1D1F",
        va="top",
        ha="left",
    )
    fig.text(
        0.04,
        0.93,
        f"{subtitle} · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}",
        fontsize=10.5,
        color=LABEL_SECONDARY,
        va="top",
        ha="left",
    )

    # Legend
    from matplotlib.lines import Line2D

    legend_items = [
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor=BLUE,
            markersize=9,
            alpha=0.7,
            markeredgecolor="white",
            markeredgewidth=0.5,
            label="Recorded",
        ),
        Line2D(
            [0],
            [0],
            marker="D",
            color="none",
            markerfacecolor=forecast_color,
            markersize=8,
            alpha=0.85,
            markeredgecolor="white",
            markeredgewidth=0.5,
            label="Projected",
        ),
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor="white",
            markersize=7,
            markeredgecolor=RED,
            markeredgewidth=1.2,
            label="Now",
        ),
    ]
    for oz in [1, 3, 5]:
        legend_items.append(
            Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor="#D1D1D6",
                markeredgecolor="#C7C7CC",
                markersize=np.sqrt(_volume_to_marker_size(oz)) / 3.0,
                markeredgewidth=0.3,
                label=f"{oz} fl oz",
            )
        )
    axis.legend(
        handles=legend_items,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.09),
        ncol=6,
        fontsize=8.5,
        frameon=False,
        columnspacing=2.5,
        handletextpad=0.4,
    )

    fig.subplots_adjust(top=0.89, bottom=0.10, left=0.06, right=0.96)
    fig.savefig(
        output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none"
    )
    plt.close(fig)


def _volume_to_marker_size(volume_oz: float) -> float:
    """Map feed volume to a matplotlib scatter marker area."""
    return 50 + (volume_oz / 5.0) * 350


def _format_hour(hour: int) -> str:
    """Format an integer hour (0–24) for the schedule chart Y axis."""
    if hour in {0, 24}:
        return "12 AM"
    if hour == 12:
        return "12 PM"
    return f"{hour} AM" if hour < 12 else f"{hour - 12} PM"


def _darken_color(hex_color: str) -> str:
    """Return a darkened version of a hex color for text labels."""
    # Strip # and parse RGB
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    # Darken by 40%
    factor = 0.6
    r, g, b = int(r * factor), int(g * factor), int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


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


def _git_commit_display(tracker_meta: dict[str, Any] | None) -> str:
    """Return a footer-friendly commit label."""
    if tracker_meta is None:
        return "n/a"

    git_commit = tracker_meta.get("git_commit", "n/a")
    if tracker_meta.get("git_dirty"):
        return f"{git_commit} (dirty)"
    return str(git_commit)
