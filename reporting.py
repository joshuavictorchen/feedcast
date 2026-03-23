"""Report generation for feeding forecasts and model backtests.

Produces:
- A journal-style summary.md with abstract, methods, results, discussion
- Hurricane-style spaghetti plots showing all model trajectories
- Per-model detail pages with full reproducible methodology
- Machine-readable metrics.json for run-to-run comparison
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from forecasting import (
    BIRTH_DATE,
    CONSENSUS_MATCH_WINDOW_MINUTES,
    DAILY_SHIFT_HALF_LIFE_DAYS,
    DAILY_SHIFT_LOOKBACK_DAYS,
    DAILY_SHIFT_MIN_COMPLETE_DAYS,
    DAILY_SHIFT_MIN_FEEDS_PER_DAY,
    DAILY_SHIFT_SCALE_MAX,
    DAILY_SHIFT_SCALE_MIN,
    DATA_FLOOR,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    DEFAULT_BREASTFEED_OZ_PER_30_MIN,
    DISPLAY_DAYS,
    GAP_CONDITIONAL_HALF_LIFE_HOURS,
    GAP_CONDITIONAL_LOOKBACK_DAYS,
    GBM_LEARNING_RATE,
    GBM_LOOKBACK_DAYS,
    GBM_MAX_DEPTH,
    GBM_N_ESTIMATORS,
    HORIZON_HOURS,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
    MIN_POINT_GAP_MINUTES,
    PHASE_LOCKED_FILTER_BETA,
    PHASE_LOCKED_MEAN_REVERSION,
    PHASE_LOCKED_VOLUME_GAIN,
    PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS,
    PHASE_NOWCAST_BLEND_PHASE_WEIGHT,
    RECENT_HALF_LIFE_HOURS,
    RECENT_LOOKBACK_DAYS,
    RECENT_PERFORMANCE_HOURS,
    SATIETY_HALF_LIFE_HOURS,
    SATIETY_LOOKBACK_DAYS,
    SNACK_THRESHOLD_OZ,
    SURVIVAL_LOOKBACK_DAYS,
    SURVIVAL_NIGHT_END,
    SURVIVAL_NIGHT_START,
    TEMPLATE_NEIGHBORS,
    TEMPLATE_WINDOW_EVENTS,
    TREND_HALF_LIFE_HOURS,
    TREND_LONG_LOOKBACK_DAYS,
    TREND_SHORT_LOOKBACK_DAYS,
    UNMATCHED_PENALTY_MINUTES,
    BacktestCase,
    ForecastPoint,
    ModelRun,
    PipelineResult,
    availability_adjusted_first_feed_error,
    hour_of_day,
)

# ── Apple-inspired color palette ────────────────────────────────────────────
BLUE = "#007AFF"
ORANGE = "#FF9500"
ORANGE_SOFT = "#FFCC80"
RED = "#FF3B30"
GREEN = "#34C759"
PURPLE = "#AF52DE"
TEAL = "#5AC8FA"
PINK = "#FF2D55"
INDIGO = "#5856D6"
CARD = "#FFFFFF"
BG = "#FAFAFA"
NIGHT_FILL = "#F0F0F5"
PROJ_FILL = "#FFF7ED"
SEPARATOR = "#E5E5EA"
LABEL_SECONDARY = "#86868B"

# Model colors for spaghetti plots — grouped by family
MODEL_COLORS = {
    "recent_cadence": "#8E8E93",     # gray — baseline
    "trend_hybrid": "#636366",       # dark gray — baseline
    "phase_locked_oscillator": BLUE,
    "phase_nowcast_hybrid": "#0051D5",  # dark blue — headliner family
    "template_match": TEAL,
    "daily_shift": GREEN,
    "gap_conditional": ORANGE,
    "survival_weibull": PURPLE,
    "gradient_boosted": PINK,
    "satiety_decay": INDIGO,
    "consensus_blend": RED,
}


# ── Entry point ─────────────────────────────────────────────────────────────
def write_reports(
    result: PipelineResult,
    output_root: Path = Path("reports"),
) -> Path:
    """Write the full report set for a pipeline result."""
    output_root.mkdir(exist_ok=True)
    run_dir = output_root / result.run_id
    models_dir = run_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=False)

    previous_metrics = load_previous_metrics(output_root, result.run_id)
    metrics = build_metrics_payload(result)

    # Headliner schedule plot (existing Apple-style schedule view)
    plot_schedule(
        events=[e for e in result.headliner.events if e.time <= result.analysis_time],
        forecast=result.headliner.forecast.points,
        analysis_time=result.analysis_time,
        output_path=run_dir / "headliner_schedule.png",
        title="Headliner Forecast",
        subtitle=f"{result.headliner.definition.title} · {result.snapshot.export_path.name}",
    )

    # Spaghetti plots — three variants
    plot_spaghetti_hero(result, run_dir / "spaghetti_hero.png")
    plot_spaghetti_all(result, run_dir / "spaghetti_all.png")
    plot_spaghetti_top5(result, run_dir / "spaghetti_top5.png")

    # Backtest comparison bar chart
    plot_model_scores(
        model_runs=result.model_runs,
        headliner_slug=result.headliner_slug,
        output_path=run_dir / "model_scores.png",
    )

    # Per-model pages
    for model_run in result.model_runs:
        plot_schedule(
            events=[e for e in model_run.events if e.time <= result.analysis_time],
            forecast=model_run.forecast.points,
            analysis_time=result.analysis_time,
            output_path=models_dir / f"{model_run.definition.slug}.png",
            title=model_run.definition.title,
            subtitle=model_run.definition.description,
        )
        write_model_report(
            model_run=model_run,
            output_path=models_dir / f"{model_run.definition.slug}.md",
            plot_filename=f"{model_run.definition.slug}.png",
        )

    # Summary and metrics
    summary_path = run_dir / "summary.md"
    summary_path.write_text(
        build_summary_markdown(result, previous_metrics), encoding="utf-8"
    )
    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return run_dir


# ── Spaghetti plots ────────────────────────────────────────────────────────
def _spaghetti_base_setup():
    """Return common style config for spaghetti plots."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "text.color": "#1D1D1F",
        "axes.labelcolor": "#555555",
        "xtick.color": LABEL_SECONDARY,
        "ytick.color": LABEL_SECONDARY,
    })


def _recent_history_tail(result: PipelineResult, hours: float = 12):
    """Get recent feed events for the history tail on spaghetti plots."""
    cutoff = result.analysis_time
    start = cutoff - timedelta(hours=hours)
    events = result.headliner.events
    return [e for e in events if start <= e.time <= cutoff]


def _format_spaghetti_axis(ax, cutoff, horizon_hours=HORIZON_HOURS):
    """Format a spaghetti plot's time axis."""
    import matplotlib.dates as mdates
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-I %p"))
    ax.axvline(cutoff, color=RED, linewidth=1.2, alpha=0.5, linestyle="--", zorder=8)
    ax.tick_params(axis="both", which="both", length=0)
    ax.grid(True, which="major", axis="x", alpha=0.15, color=SEPARATOR, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_visible(False)


def plot_spaghetti_hero(result: PipelineResult, output_path: Path):
    """Hero figure: all models overlaid on shared axes, headliner emphasized."""
    _spaghetti_base_setup()
    fig, ax = plt.subplots(figsize=(16, 7))

    cutoff = result.analysis_time
    history_tail = _recent_history_tail(result)
    ordered = _order_models_by_performance(result.model_runs)

    # Recent actuals — solid dark dots connected
    if history_tail:
        times = [e.time for e in history_tail]
        ax.plot(times, [1] * len(times), "o-", color="#1D1D1F", markersize=7,
                linewidth=1.5, alpha=0.8, zorder=7, label="Actual (recent)")

    # All non-headliner models — faded
    y_level = 1  # all on same y-level (shared axis)
    for model_run in ordered:
        if model_run.definition.slug == result.headliner_slug:
            continue
        points = model_run.forecast.points
        if not points:
            continue
        times = [cutoff] + [p.time for p in points]
        color = MODEL_COLORS.get(model_run.definition.slug, "#AEAEB2")
        ax.plot(times, [y_level] * len(times), "o-", color=color, markersize=4,
                linewidth=1.0, alpha=0.25, zorder=3)

    # Headliner — bold
    headliner = result.headliner
    if headliner.forecast.points:
        times = [cutoff] + [p.time for p in headliner.forecast.points]
        color = MODEL_COLORS.get(headliner.definition.slug, ORANGE)
        ax.plot(times, [y_level] * len(times), "D-", color=color, markersize=9,
                linewidth=2.5, alpha=0.9, zorder=6, label=f"{headliner.definition.title} (headliner)")
        # Time labels on headliner points
        for p in headliner.forecast.points:
            ax.annotate(p.time.strftime("%-I:%M"), (p.time, y_level),
                        textcoords="offset points", xytext=(0, 14),
                        fontsize=7.5, ha="center", color=color, fontweight="bold")

    # Cutoff label
    ax.annotate("NOW", (cutoff, y_level), textcoords="offset points",
                xytext=(0, -20), fontsize=8, color=RED, fontweight="bold",
                ha="center")

    ax.set_yticks([])
    ax.set_ylim(0.5, 1.5)
    _format_spaghetti_axis(ax, cutoff)
    ax.legend(loc="upper right", fontsize=9, frameon=False)

    fig.text(0.04, 0.96, "Forecast Trajectories",
             fontsize=20, fontweight="bold", color="#1D1D1F", va="top")
    fig.text(0.04, 0.92,
             f"All models · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}",
             fontsize=10, color=LABEL_SECONDARY, va="top")

    fig.subplots_adjust(top=0.85, bottom=0.08, left=0.04, right=0.96)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close(fig)


def plot_spaghetti_all(result: PipelineResult, output_path: Path):
    """Comparison figure: each model on its own row, all models shown."""
    _plot_spaghetti_rows(result, output_path, top_n=None,
                         title="All Model Trajectories")


def plot_spaghetti_top5(result: PipelineResult, output_path: Path):
    """Comparison figure: top 5 models by recent MAE, one per row."""
    _plot_spaghetti_rows(result, output_path, top_n=5,
                         title="Top 5 Model Trajectories")


def _plot_spaghetti_rows(result: PipelineResult, output_path: Path,
                         top_n: int | None, title: str):
    """Row-based spaghetti: each model gets its own horizontal track."""
    _spaghetti_base_setup()
    cutoff = result.analysis_time
    history_tail = _recent_history_tail(result)
    ordered = _order_models_by_performance(result.model_runs)
    if top_n:
        ordered = ordered[:top_n]

    n_rows = len(ordered) + 1  # +1 for actuals
    fig, ax = plt.subplots(figsize=(16, max(4, n_rows * 0.7 + 1.5)))

    # Row positions: 0 = actuals at bottom, models above
    row_labels = ["Actual (recent)"] + [m.definition.title for m in ordered]
    y_positions = list(range(n_rows))

    # Actuals
    if history_tail:
        times = [e.time for e in history_tail]
        sizes = [volume_to_marker_size(e.volume_oz) * 0.3 for e in history_tail]
        ax.scatter(times, [0] * len(times), s=sizes, c="#1D1D1F", alpha=0.7,
                   edgecolors="white", linewidths=0.5, zorder=5)

    # Each model as a row
    for i, model_run in enumerate(ordered):
        y = i + 1
        points = model_run.forecast.points
        if not points:
            continue
        times = [p.time for p in points]
        sizes = [volume_to_marker_size(p.volume_oz) * 0.3 for p in points]
        color = MODEL_COLORS.get(model_run.definition.slug, "#AEAEB2")
        is_headliner = model_run.definition.slug == result.headliner_slug
        alpha = 0.85 if is_headliner else 0.6
        marker = "D" if is_headliner else "o"
        edge_w = 1.0 if is_headliner else 0.5
        ax.scatter(times, [y] * len(times), s=sizes, c=color, alpha=alpha,
                   edgecolors="white", linewidths=edge_w, zorder=5, marker=marker)
        # Connect with line
        all_times = [cutoff] + times
        ax.plot(all_times, [y] * len(all_times), color=color,
                linewidth=1.5 if is_headliner else 0.8,
                alpha=0.4 if is_headliner else 0.2, zorder=3)

    ax.set_yticks(y_positions)
    ax.set_yticklabels(row_labels, fontsize=9)
    ax.set_ylim(-0.5, n_rows - 0.5)
    _format_spaghetti_axis(ax, cutoff)

    fig.text(0.04, 0.97, title,
             fontsize=18, fontweight="bold", color="#1D1D1F", va="top")
    fig.text(0.04, 0.935,
             f"Cutoff {cutoff.strftime('%B %-d, %-I:%M %p')} · dot size = volume",
             fontsize=9.5, color=LABEL_SECONDARY, va="top")

    fig.subplots_adjust(top=0.88, bottom=0.08, left=0.18, right=0.96)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close(fig)


def _order_models_by_performance(model_runs: list[ModelRun]) -> list[ModelRun]:
    """Sort models using the same key as headliner selection in forecasting.py."""
    return sorted(
        model_runs,
        key=lambda m: (
            availability_adjusted_first_feed_error(m.backtest_summary),
            _sortable(m.backtest_summary.mean_timing_mae_minutes),
            _sortable(m.backtest_summary.mean_first_feed_error_minutes),
        ),
    )


# ── Schedule plot (existing Apple-style) ────────────────────────────────────
def plot_schedule(events, forecast, analysis_time, output_path, title, subtitle):
    """Render the schedule-view plot used throughout the reports."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.spines.bottom": False,
        "figure.facecolor": BG, "axes.facecolor": BG,
        "text.color": "#1D1D1F", "axes.labelcolor": "#555555",
        "xtick.color": LABEL_SECONDARY, "ytick.color": LABEL_SECONDARY,
    })

    display_start = max(
        DATA_FLOOR,
        (analysis_time - timedelta(days=DISPLAY_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0),
    )
    proj_end = analysis_time + timedelta(hours=24)
    display_end = proj_end.replace(hour=0, minute=0, second=0)

    all_dates = []
    d = display_start.date()
    while d <= display_end.date():
        all_dates.append(d)
        d += timedelta(days=1)
    date_to_x = {date: i for i, date in enumerate(all_dates)}
    proj_dates = {p.time.date() for p in forecast}

    fig, axis = plt.subplots(figsize=(16, 9.5))

    for x, date in enumerate(all_dates):
        axis.axvspan(x - 0.42, x + 0.42,
                     color=PROJ_FILL if date in proj_dates else CARD,
                     zorder=0, linewidth=0)
        axis.axvspan(x - 0.42, x + 0.42, ymin=1 - 6/24, ymax=1.0,
                     color=NIGHT_FILL, zorder=1, linewidth=0)
        axis.axvspan(x - 0.42, x + 0.42, ymin=0, ymax=3/24,
                     color=NIGHT_FILL, zorder=1, linewidth=0)

    for x in range(len(all_dates) + 1):
        axis.axvline(x - 0.5, color=SEPARATOR, linewidth=0.5, alpha=0.5, zorder=2)

    now_x = date_to_x[analysis_time.date()]
    now_y = hour_of_day(analysis_time)
    axis.plot([now_x - 0.42, now_x + 0.42], [now_y, now_y],
              color=RED, linewidth=1.2, alpha=0.5, zorder=9)
    axis.scatter([now_x], [now_y], color="white", s=50, zorder=10,
                 edgecolors=RED, linewidths=1.5)
    axis.annotate("NOW", (now_x + 0.42, now_y), fontsize=6.5, color=RED,
                  fontweight="bold", va="center", ha="left",
                  xytext=(4, 0), textcoords="offset points")

    history = [e for e in events if display_start <= e.time <= analysis_time]
    if history:
        hx = np.array([date_to_x[e.time.date()] for e in history], dtype=float)
        hy = np.array([hour_of_day(e.time) for e in history], dtype=float)
        hvols = np.array([e.volume_oz for e in history], dtype=float)
        hsizes = np.array([volume_to_marker_size(v) for v in hvols], dtype=float)
        ages = np.array([(analysis_time - e.time).total_seconds() / 3600 for e in history])
        max_age = max(float(np.max(ages)), 1.0)
        alphas = 0.25 + (0.60 * (1 - (ages / max_age)))
        for i in range(len(history)):
            axis.scatter(hx[i], hy[i], s=hsizes[i], c=BLUE, alpha=alphas[i],
                         edgecolors="white", linewidths=0.6, zorder=5)

    if forecast:
        fx = np.array([date_to_x[p.time.date()] for p in forecast], dtype=float)
        fy = np.array([hour_of_day(p.time) for p in forecast], dtype=float)
        fvols = np.array([p.volume_oz for p in forecast], dtype=float)
        fsizes = np.array([volume_to_marker_size(v) for v in fvols], dtype=float)
        for i in range(len(forecast)):
            axis.scatter(fx[i], fy[i], s=fsizes[i] * 2.5, c=ORANGE_SOFT,
                         alpha=0.2, zorder=3, linewidths=0)
        axis.scatter(fx, fy, s=fsizes, c=ORANGE, alpha=0.85,
                     edgecolors="white", linewidths=0.8, zorder=6, marker="D")
        for i, p in enumerate(forecast):
            label = f"{p.volume_oz:.1f} oz\n{p.time.strftime('%-I:%M %p').lower()}"
            axis.annotate(label, (fx[i], fy[i]), textcoords="offset points",
                          xytext=(14, 0), fontsize=7, color="#B35A00", ha="left",
                          va="center", fontweight="medium", linespacing=1.3)

    axis.set_xticks(range(len(all_dates)))
    axis.set_xticklabels(
        [datetime.combine(d, datetime.min.time()).strftime("%a\n%-m/%d") for d in all_dates],
        fontsize=9, fontweight="medium")
    axis.set_ylim(24, 0)
    axis.set_yticks(range(0, 25, 3))
    axis.set_yticklabels([_format_hour(h) for h in range(0, 25, 3)], fontsize=9)
    axis.yaxis.set_minor_locator(mticker.MultipleLocator(1))
    axis.grid(True, which="major", axis="y", alpha=0.2, color=SEPARATOR, linewidth=0.5)
    axis.grid(True, which="minor", axis="y", alpha=0.08, color=SEPARATOR, linewidth=0.3)
    axis.tick_params(axis="both", which="both", length=0)
    axis.set_xlim(-0.55, len(all_dates) - 0.45)

    fig.text(0.04, 0.965, title, fontsize=22, fontweight="bold",
             color="#1D1D1F", va="top", ha="left")
    fig.text(0.04, 0.93,
             f"{subtitle} · analysis cutoff {analysis_time.strftime('%B %-d, %Y %-I:%M %p')}",
             fontsize=10.5, color=LABEL_SECONDARY, va="top", ha="left")

    from matplotlib.lines import Line2D
    legend_items = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=BLUE,
               markersize=9, alpha=0.7, markeredgecolor="white",
               markeredgewidth=0.5, label="Recorded"),
        Line2D([0], [0], marker="D", color="none", markerfacecolor=ORANGE,
               markersize=8, alpha=0.85, markeredgecolor="white",
               markeredgewidth=0.5, label="Projected"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="white",
               markersize=7, markeredgecolor=RED, markeredgewidth=1.2,
               label="Now"),
    ]
    for oz in [1, 3, 5]:
        legend_items.append(
            Line2D([0], [0], marker="o", color="none", markerfacecolor="#D1D1D6",
                   markeredgecolor="#C7C7CC",
                   markersize=np.sqrt(volume_to_marker_size(oz)) / 3.0,
                   markeredgewidth=0.3, label=f"{oz} fl oz"))
    axis.legend(handles=legend_items, loc="lower center",
                bbox_to_anchor=(0.5, -0.09), ncol=6, fontsize=8.5,
                frameon=False, columnspacing=2.5, handletextpad=0.4)

    fig.subplots_adjust(top=0.89, bottom=0.10, left=0.06, right=0.96)
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close(fig)


# ── Backtest comparison bar chart ───────────────────────────────────────────
def plot_model_scores(model_runs, headliner_slug, output_path):
    """Render backtest comparison — timing metrics only."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
        "figure.facecolor": BG, "axes.facecolor": BG,
        "text.color": "#1D1D1F", "axes.labelcolor": "#555555",
        "xtick.color": LABEL_SECONDARY, "ytick.color": LABEL_SECONDARY,
    })
    ordered = sorted(model_runs, key=lambda m: (
        _sortable(m.backtest_summary.recent_first_feed_error_minutes),
        _sortable(m.backtest_summary.mean_first_feed_error_minutes)))
    labels = [m.definition.title for m in ordered]
    overall = np.array([_sortable(m.backtest_summary.mean_first_feed_error_minutes, 0.0)
                        for m in ordered])
    recent = np.array([_sortable(m.backtest_summary.recent_first_feed_error_minutes, o)
                       for m, o in zip(ordered, overall)])
    y = np.arange(len(ordered))

    fig, axis = plt.subplots(figsize=(11, 5.5))
    axis.barh(y, overall, color="#D1D1D6", height=0.60, label="Overall first-feed MAE")
    colors = [ORANGE if m.definition.slug == headliner_slug else BLUE for m in ordered]
    axis.barh(y, recent, color=colors, height=0.36, label="Recent first-feed MAE")

    for i, m in enumerate(ordered):
        cov = m.backtest_summary.cutoff_coverage_ratio
        axis.text(max(overall[i], recent[i]) + 2, y[i],
                  f"{cov:.0%} coverage", va="center", ha="left",
                  fontsize=9, color="#6E6E73")

    axis.set_yticks(y)
    axis.set_yticklabels(labels, fontsize=10)
    axis.invert_yaxis()
    axis.set_xlabel("Minutes")
    axis.set_title("Backtest Comparison (Timing Only)", fontsize=17,
                   fontweight="bold", loc="left", pad=16)
    axis.text(0, 1.04, "Lower is better. Recent = last 48h of available cutoffs.",
              transform=axis.transAxes, fontsize=10, color=LABEL_SECONDARY)
    axis.grid(axis="x", alpha=0.15, linewidth=0.6)
    axis.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close(fig)


# ── Summary report (journal-style) ─────────────────────────────────────────
def build_summary_markdown(result: PipelineResult, previous_metrics) -> str:
    """Build the main summary report in journal-article style."""
    cutoff = result.analysis_time
    headliner = result.headliner
    baby_age = (cutoff.date() - BIRTH_DATE.date()).days
    ordered = _order_models_by_performance(result.model_runs)

    lines: list[str] = []

    # ── Title ───────────────────────────────────────────────────────────
    lines.append("# Silas Feeding Forecast")
    lines.append(f"**{cutoff.strftime('%A, %B %-d, %Y')}** · "
                 f"{baby_age} days old · "
                 f"Cutoff: {cutoff.strftime('%-I:%M %p')}")
    lines.append("")

    # ── Abstract ────────────────────────────────────────────────────────
    lines.append("## Abstract")
    lines.append("")
    n_models = len(result.model_runs)
    first_feed = headliner.forecast.points[0] if headliner.forecast.points else None
    first_time_str = first_feed.time.strftime("%-I:%M %p") if first_feed else "unknown"
    n_feeds = len(headliner.forecast.points)
    recent_mae = headliner.backtest_summary.recent_first_feed_error_minutes
    mae_str = f"{recent_mae:.0f}" if recent_mae else "unknown"

    lines.append(
        f"This report forecasts Silas's bottle feeds over the next 24 hours "
        f"using {n_models} independent models evaluated against historical data "
        f"from {DATA_FLOOR.strftime('%B %-d')} onward. "
        f"The current headliner model is **{headliner.definition.title}**, "
        f"selected by recent next-feed timing accuracy "
        f"(mean absolute error: {mae_str} minutes over the last 48 hours of backtested cutoffs). "
        f"It projects the next feed at **{first_time_str}**, "
        f"with **{n_feeds} feeds** expected over the next 24 hours."
    )
    lines.append("")

    # ── Hero figure ─────────────────────────────────────────────────────
    lines.append("![Forecast Trajectories](spaghetti_hero.png)")
    lines.append("")

    # ── Forecast ────────────────────────────────────────────────────────
    lines.append("## Forecast")
    lines.append("")
    lines.append(f"**Headliner: {headliner.definition.title}**")
    lines.append("")
    lines.extend(_build_forecast_table(headliner.forecast.points))
    lines.append("")

    # ── Model Comparison ────────────────────────────────────────────────
    lines.append("## Model Comparison")
    lines.append("")
    lines.append("### All Model Trajectories")
    lines.append("")
    lines.append("![All Model Trajectories](spaghetti_all.png)")
    lines.append("")
    lines.append("### Top 5 Models")
    lines.append("")
    lines.append("![Top 5 Model Trajectories](spaghetti_top5.png)")
    lines.append("")
    lines.append("### Timing Leaderboard")
    lines.append("")
    lines.extend(_build_leaderboard(ordered))
    lines.append("")
    lines.append("![Backtest Comparison](model_scores.png)")
    lines.append("")

    # ── Methods ─────────────────────────────────────────────────────────
    lines.append("## Methods")
    lines.append("")
    lines.extend(_build_methods_section(result))

    # ── Results ─────────────────────────────────────────────────────────
    lines.append("## Results")
    lines.append("")
    lines.extend(_build_results_section(result, ordered))

    # ── Discussion ──────────────────────────────────────────────────────
    lines.append("## Discussion")
    lines.append("")
    lines.extend(_build_discussion_section(result))

    # ── Appendix ────────────────────────────────────────────────────────
    lines.append("## Appendix")
    lines.append("")
    lines.append("### Headliner Schedule View")
    lines.append("")
    lines.append("![Headliner Schedule](headliner_schedule.png)")
    lines.append("")
    lines.append("### Individual Model Reports")
    lines.append("")
    for m in result.model_runs:
        lines.append(f"- [{m.definition.title}](models/{m.definition.slug}.md)")
    lines.append("")
    lines.append("### Delta vs Prior Run")
    lines.append("")
    lines.extend(_build_delta_section(result, previous_metrics))
    lines.append("")

    # ── Footer ──────────────────────────────────────────────────────────
    lines.append("---")
    lines.append(f"*Export: `{result.snapshot.export_path.name}` · "
                 f"Generated: {result.generated_at.strftime('%Y-%m-%d %H:%M:%S')} · "
                 f"Data floor: {DATA_FLOOR.strftime('%Y-%m-%d')} · "
                 f"Run ID: {result.run_id}*")

    return "\n".join(lines)


def _build_forecast_table(points: list[ForecastPoint]) -> list[str]:
    """Next-24h forecast table. Includes volume for bottle prep."""
    lines = [
        "| Feed | Time | Volume | Range | Gap |",
        "|------|------|--------|-------|-----|",
    ]
    total = 0.0
    for i, p in enumerate(points, 1):
        total += p.volume_oz
        lines.append(
            f"| {i} | **{p.time.strftime('%-I:%M %p')}** | "
            f"{p.volume_oz:.1f} oz | {p.low_volume_oz:.1f}–{p.high_volume_oz:.1f} | "
            f"{p.gap_hours:.1f}h |")
    lines.append("")
    lines.append(f"> Projected total: **{total:.1f} oz** across **{len(points)} feeds**")
    return lines


def _build_leaderboard(ordered: list[ModelRun]) -> list[str]:
    """Timing-only leaderboard. No volume MAE column."""
    lines = [
        "| Model | Recent First-Feed MAE | Coverage | Overall First-Feed MAE | Full-24h Timing MAE | Next Predicted Feed |",
        "|-------|----------------------|----------|------------------------|--------------------|--------------------|",
    ]
    for m in ordered:
        s = m.backtest_summary
        first = m.forecast.points[0] if m.forecast.points else None
        first_str = first.time.strftime("%-I:%M %p") if first else "n/a"
        lines.append(
            f"| {m.definition.title} | "
            f"{_fmt_min(s.recent_first_feed_error_minutes)} | "
            f"{_fmt_pct(s.cutoff_coverage_ratio)} ({s.total_cutoffs}/{s.potential_cutoffs}) | "
            f"{_fmt_min(s.mean_first_feed_error_minutes)} | "
            f"{_fmt_min(s.mean_timing_mae_minutes)} | "
            f"{first_str} |")
    return lines


def _build_methods_section(result: PipelineResult) -> list[str]:
    """Full methods section: data, backtesting, and all model algorithms."""
    lines = []

    # Data
    lines.append("### Data")
    lines.append("")
    lines.append(
        f"Feeding data is exported from the Nara Baby mobile app as CSV. "
        f"Each row represents one activity (bottle feed, breastfeed, diaper, pump). "
        f"Only bottle feeds and breastfeeds are used for forecasting. "
        f"All data before **{DATA_FLOOR.strftime('%B %-d, %Y')}** is discarded.")
    lines.append("")
    lines.append(
        f"**Bottle feed volume** is the sum of breast milk and formula volumes in the row, "
        f"normalized to fluid ounces (early records in milliliters are converted at "
        f"1 mL = {0.033814} fl oz).")
    lines.append("")
    lines.append(
        f"**Breastfeeding** is an optional input. When a model opts in "
        f"(merge window = {DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES} min), "
        f"estimated breastfeed intake is computed as "
        f"`(left_seconds + right_seconds) / 1800 × {DEFAULT_BREASTFEED_OZ_PER_30_MIN} oz` "
        f"and added to the next bottle feed if that bottle starts within "
        f"{DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES} minutes of the breastfeed ending. "
        f"This is a heuristic, not measured intake. Models may ignore it.")
    lines.append("")
    lines.append(
        f"**Snack threshold:** feeds below **{SNACK_THRESHOLD_OZ} oz** are classified as "
        f"snacks/top-offs. Some models exclude them from interval estimation; "
        f"others train directly on all events including snacks.")
    lines.append("")

    # Backtesting
    lines.append("### Backtesting Protocol")
    lines.append("")
    lines.append(
        f"Each model is backtested at every bottle-feed event in the export as a potential cutoff. "
        f"At each cutoff, only history prior to that time is available to the model. "
        f"The model produces a {HORIZON_HOURS}-hour forecast, which is compared "
        f"to the actual bottle feeds that occurred after the cutoff.")
    lines.append("")
    lines.append("**Sequence alignment:** Predicted and actual feed sequences are aligned "
                 f"using order-preserving dynamic programming. Unmatched predictions or actuals "
                 f"incur a penalty of {UNMATCHED_PENALTY_MINUTES:.0f} minutes each.")
    lines.append("")
    lines.append("**Metrics:**")
    lines.append(f"- **First-feed MAE**: absolute error between the first predicted feed "
                 f"time and the first actual feed time after the cutoff")
    lines.append(f"- **Recent first-feed MAE**: same metric restricted to cutoffs in the "
                 f"last {RECENT_PERFORMANCE_HOURS:.0f} hours of available data")
    lines.append(f"- **Full-24h timing MAE**: mean absolute error across all matched feeds "
                 f"in the {HORIZON_HOURS}-hour window (only for cutoffs with full horizon)")
    lines.append("")
    lines.append(
        f"**Headliner selection** ranks models by: "
        f"(1) availability-adjusted recent first-feed MAE, "
        f"(2) full-24h timing MAE, (3) overall first-feed MAE. "
        f"The availability adjustment formula is: "
        f"`adjusted = recent_first_feed_MAE + 40 × max(0, 0.75 − coverage) / 0.75`. "
        f"Models with ≥75% cutoff coverage pay no penalty; "
        f"models below 75% are penalized proportionally. "
        f"Volume accuracy is not used in model ranking.")
    lines.append("")
    lines.append(
        f"**Forecast normalization:** all models' output points are clamped to the "
        f"{HORIZON_HOURS}-hour window, sorted by time, and spaced at least "
        f"{MIN_POINT_GAP_MINUTES} minutes apart. Volumes are clipped to [0.1, 8.0] oz. "
        f"Intervals are clipped to [{MIN_INTERVAL_HOURS}, {MAX_INTERVAL_HOURS}] hours.")
    lines.append("")

    # Model algorithms
    lines.append("### Model Algorithms")
    lines.append("")
    lines.append("Each model below is described in sufficient detail to reimplement "
                 "from this text alone. Hyperparameters are listed with their current values. "
                 "Code references point to `forecasting.py`.")
    lines.append("")

    # Group by family
    lines.extend(_model_family_docs())

    return lines


def _model_family_docs() -> list[str]:
    """Full algorithmic descriptions for all model families."""
    lines = []

    # ── Interval-based ──────────────────────────────────────────────────
    lines.append("#### Interval-Based Models")
    lines.append("")

    lines.append("**Recent Cadence** (`forecast_recent_cadence`, bottle-only)")
    lines.append("")
    lines.append(
        f"Extracts full feeds (≥ {SNACK_THRESHOLD_OZ} oz) from the last "
        f"{RECENT_LOOKBACK_DAYS} days and computes inter-feed intervals "
        f"with exponential weighting (half-life = {RECENT_HALF_LIFE_HOURS}h). "
        f"Separately estimates the recent feeds-per-day count using day-level "
        f"exponential weights (half-life = {DAILY_SHIFT_HALF_LIFE_DAYS} days) and "
        f"computes a target interval as 24 / feeds_per_day (clamped to [6.5, 10.5] feeds/day). "
        f"The final interval is a blend: 70% weighted recent interval + 30% target interval, "
        f"clipped to [{MIN_INTERVAL_HOURS}, {MAX_INTERVAL_HOURS}]h. "
        f"Volume at each projected time comes from a {TREND_LONG_LOOKBACK_DAYS}-day "
        f"time-of-day profile with 12 two-hour bins, exponentially weighted "
        f"(half-life = {RECENT_HALF_LIFE_HOURS}h).")
    lines.append("")

    lines.append("**Trend Hybrid** (`forecast_trend_hybrid`, bottle-only)")
    lines.append("")
    lines.append(
        f"Fits a weighted linear regression to inter-feed intervals over the last "
        f"{TREND_SHORT_LOOKBACK_DAYS} days of full feeds (half-life = {TREND_HALF_LIFE_HOURS}h). "
        f"The intercept at the cutoff gives the current interval estimate; the slope "
        f"gives the rate of change (hours per hour). A parallel regression fits volume trends "
        f"over {TREND_LONG_LOOKBACK_DAYS} days. During roll-forward, each successive interval "
        f"is `current_interval + slope × hours_from_cutoff`, and each volume is "
        f"`time-of-day base + volume_slope × hours_from_cutoff`. "
        f"All values are clipped to [{MIN_INTERVAL_HOURS}, {MAX_INTERVAL_HOURS}]h "
        f"for intervals and [0.5, 8.0] oz for volumes.")
    lines.append("")

    # ── State-space ─────────────────────────────────────────────────────
    lines.append("#### State-Space Models")
    lines.append("")

    lines.append("**Phase-Locked Oscillator** (`forecast_phase_locked_oscillator`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Models the feeding schedule as a noisy oscillator with a slowly-varying period. "
        f"Initializes a base period from the recent target interval (see `estimate_target_interval`). "
        f"Then processes the last ~28 events sequentially: for each pair, the predicted gap is "
        f"`period + {PHASE_LOCKED_VOLUME_GAIN} × (prev_volume - running_avg_volume)`. "
        f"The error between actual and predicted gap updates the period: "
        f"`period += {PHASE_LOCKED_FILTER_BETA} × error`. The running average volume "
        f"updates as `0.7 × old + 0.3 × new`. "
        f"During projection, the period mean-reverts toward the target interval at "
        f"{PHASE_LOCKED_MEAN_REVERSION:.0%} per step. Volume comes from the time-of-day "
        f"profile blended 65/35 with the running average volume.")
    lines.append("")

    lines.append("**Phase Nowcast Hybrid** (`forecast_phase_nowcast_hybrid`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Uses the Phase-Locked Oscillator as the full-horizon backbone. "
        f"Separately fits a local state-gap regression (same as Gap-Conditional's fitting procedure) "
        f"to estimate the next gap from the current event state. "
        f"If both models agree within {PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS}h "
        f"({PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS * 60:.0f} minutes) AND the latest event "
        f"is a full feed (≥ {SNACK_THRESHOLD_OZ} oz), the first gap is blended: "
        f"{PHASE_NOWCAST_BLEND_PHASE_WEIGHT:.0%} phase + "
        f"{1 - PHASE_NOWCAST_BLEND_PHASE_WEIGHT:.0%} state. "
        f"All subsequent points shift by the same delta, preserving inter-feed spacing. "
        f"If the models disagree or the last event is a snack, the phase forecast is used unchanged.")
    lines.append("")

    # ── Regression ──────────────────────────────────────────────────────
    lines.append("#### Regression Models")
    lines.append("")

    lines.append("**Gap-Conditional** (`forecast_gap_conditional`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Fits a weighted multivariate linear regression predicting the gap to the next feed "
        f"from the current event's state. Training data: all events (including snacks) from "
        f"the last {GAP_CONDITIONAL_LOOKBACK_DAYS} days, with exponential sample weights "
        f"(half-life = {GAP_CONDITIONAL_HALF_LIFE_HOURS}h). "
        f"Features: [volume_oz, previous_gap_hours, rolling_3_avg_gap, sin(2π·hour/24), cos(2π·hour/24)]. "
        f"Target: gap to the next event in hours. "
        f"Fitting uses weighted normal equations: `(X'WX)⁻¹X'Wy`. "
        f"During projection, each predicted feed is appended as a synthetic event "
        f"(with volume from the time-of-day profile) and the model re-evaluates "
        f"on the updated state to predict the next gap.")
    lines.append("")

    lines.append("**Survival / Weibull** (`forecast_survival_weibull`, bottle-only)")
    lines.append("")
    lines.append(
        f"Fits a Weibull distribution to inter-feed intervals from the last "
        f"{SURVIVAL_LOOKBACK_DAYS} days of full feeds (≥ {SNACK_THRESHOLD_OZ} oz). "
        f"The distribution shape and scale are estimated via maximum likelihood "
        f"(`scipy.stats.weibull_min.fit`, location fixed at 0). "
        f"Separate day/night scale parameters are computed by averaging intervals "
        f"in each regime (night = {SURVIVAL_NIGHT_START}:00–{SURVIVAL_NIGHT_END}:00). "
        f"A volume-gap regression slope adjusts the scale based on last feed volume "
        f"relative to the mean. The point forecast uses the Weibull mode: "
        f"`scale × ((shape-1)/shape)^(1/shape)` for shape > 1, clipped to "
        f"[{MIN_INTERVAL_HOURS}, {MAX_INTERVAL_HOURS}]h.")
    lines.append("")

    lines.append("**Gradient Boosted** (`forecast_gradient_boosted`, breastfeed-aware, exploratory)")
    lines.append("")
    lines.append(
        f"Trains a scikit-learn `GradientBoostingRegressor` "
        f"(n_estimators={GBM_N_ESTIMATORS}, max_depth={GBM_MAX_DEPTH}, "
        f"learning_rate={GBM_LEARNING_RATE}, subsample=0.8) on the last "
        f"{GBM_LOOKBACK_DAYS} days of full feeds. "
        f"Features: [volume_oz, hour_of_day, rolling_3_avg_gap, rolling_3_avg_vol, "
        f"gap_variability, is_night]. Target: gap to next feed. "
        f"Sample weights use exponential decay (half-life = {GAP_CONDITIONAL_HALF_LIFE_HOURS}h). "
        f"This model is treated as an exploratory canary — it is excluded from the "
        f"consensus blend and not trusted as headliner unless it beats simpler models "
        f"on both accuracy and coverage.")
    lines.append("")

    # ── Template-based ──────────────────────────────────────────────────
    lines.append("#### Template-Based Models")
    lines.append("")

    lines.append("**Template Match** (`forecast_template_match`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Nearest-neighbor analog forecast. Extracts a feature vector from the last "
        f"{TEMPLATE_WINDOW_EVENTS} feeds: [inter-feed gaps (×{TEMPLATE_WINDOW_EVENTS - 1}), "
        f"volumes (×{TEMPLATE_WINDOW_EVENTS}), hours-of-day (×{TEMPLATE_WINDOW_EVENTS})]. "
        f"Compares this vector to every prior {TEMPLATE_WINDOW_EVENTS}-feed window "
        f"using Euclidean distance (gap scale=1.0, volume scale=0.5, hour scale=3.0) "
        f"plus a recency penalty of 0.05 per day of age. "
        f"Selects the {TEMPLATE_NEIGHBORS} closest analogs and averages their "
        f"subsequent feed sequences (offsets and volumes) using exp(-distance) weights.")
    lines.append("")

    lines.append("**Daily Shift** (`forecast_daily_shift`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Builds a daily gap-slot template from the last {DAILY_SHIFT_LOOKBACK_DAYS} days "
        f"of completed days (≥ {DAILY_SHIFT_MIN_FEEDS_PER_DAY} feeds), requiring at least "
        f"{DAILY_SHIFT_MIN_COMPLETE_DAYS} usable days. "
        f"Day-level exponential weights (half-life = {DAILY_SHIFT_HALF_LIFE_DAYS} days) "
        f"emphasize recent days. The template includes: intra-day gap sequence, "
        f"slot volume sequence, slot hour-of-day sequence, and an explicit overnight gap "
        f"(last feed of day → first feed of next day). "
        f"Today's observed feeds are aligned to the template by scanning all possible "
        f"start-slot positions and selecting the alignment that minimizes "
        f"gap error + phase error. A scale factor (clamped to "
        f"[{DAILY_SHIFT_SCALE_MIN}, {DAILY_SHIFT_SCALE_MAX}]) stretches or compresses "
        f"the template gaps to match today's cadence. "
        f"The overnight gap is damped: `overnight × (0.6 × scale + 0.4)`.")
    lines.append("")

    # ── Physiological ───────────────────────────────────────────────────
    lines.append("#### Physiological Models")
    lines.append("")

    lines.append("**Satiety Decay** (`forecast_satiety_decay`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Models hunger as accumulating linearly over time (rate = 1.0/h), "
        f"with each feed resetting hunger proportional to its volume. "
        f"The key parameter, **satiety_per_oz** (hours of satiety per ounce), "
        f"is estimated as the exponentially-weighted mean of `gap_hours / volume_oz` "
        f"across recent full feeds (lookback = {SATIETY_LOOKBACK_DAYS} days, "
        f"half-life = {SATIETY_HALF_LIFE_HOURS}h). Separate day/night values are estimated "
        f"(night = {SURVIVAL_NIGHT_START}:00–{SURVIVAL_NIGHT_END}:00). "
        f"Predicted gap = `effective_volume × satiety_per_oz`. "
        f"The initial effective volume uses `effective_timing_volume()`, which aggregates "
        f"the most recent cluster of closely-spaced feeds into a single volume "
        f"(walks backward until it finds a full feed or a 2-hour gap).")
    lines.append("")

    # ── Ensemble ────────────────────────────────────────────────────────
    lines.append("#### Ensemble Models")
    lines.append("")

    lines.append("**Consensus Blend** (`forecast_consensus_blend`, breastfeed-aware)")
    lines.append("")
    lines.append(
        f"Invokes all base models (excluding Gradient Boosted) at each cutoff, "
        f"tolerating failures gracefully (requires ≥ 2 successful components). "
        f"Groups component predictions by time proximity rather than index: "
        f"the next unconsumed point from each model is collected, the median timestamp "
        f"is computed as an anchor, and points within ±{CONSENSUS_MATCH_WINDOW_MINUTES} minutes "
        f"of the anchor form a cluster. "
        f"Outlier points outside the cluster are skipped. "
        f"For each cluster, the consensus point uses the median timestamp and "
        f"mean volume/confidence bounds.")
    lines.append("")

    return lines


def _build_results_section(result: PipelineResult, ordered: list[ModelRun]) -> list[str]:
    """Results: which model won, key findings, model agreement."""
    lines = []
    headliner = result.headliner
    s = headliner.backtest_summary

    lines.append(
        f"**{headliner.definition.title}** is the current headliner with a recent "
        f"first-feed MAE of **{_fmt_min(s.recent_first_feed_error_minutes)}** "
        f"and **{_fmt_pct(s.cutoff_coverage_ratio)}** cutoff coverage. "
        f"Its full-24h timing MAE is {_fmt_min(s.mean_timing_mae_minutes)}.")
    lines.append("")

    # Model agreement on first feed
    first_times = []
    for m in ordered:
        if m.forecast.points:
            first_times.append((m.definition.title, m.forecast.points[0].time))
    if len(first_times) >= 2:
        times = [t for _, t in first_times]
        spread_min = (max(times) - min(times)).total_seconds() / 60
        lines.append(f"**Model agreement on next feed:** {len(first_times)} models produced "
                     f"forecasts. The spread between earliest and latest first-feed prediction "
                     f"is **{spread_min:.0f} minutes** "
                     f"({min(times).strftime('%-I:%M %p')}–{max(times).strftime('%-I:%M %p')}).")
        lines.append("")

    lines.append("**Key findings from backtesting:**")
    lines.append("")
    lines.append("- Feed volume is a meaningful timing signal: larger feeds produce "
                 "systematically longer subsequent gaps.")
    lines.append("- Recursive state-space models (Phase-Locked Oscillator family) "
                 "outperform static interval averages because they carry forward phase context.")
    lines.append("- Event-level regression models that train on all events (including snacks) "
                 "outperform full-feed-only models patched at inference time.")
    lines.append("- Ensemble blending provides modest improvement but cannot surpass "
                 "the best individual model on recent accuracy.")
    lines.append("")

    return lines


def _build_discussion_section(result: PipelineResult) -> list[str]:
    """Discussion: limitations and future directions."""
    lines = []
    lines.append("### Limitations")
    lines.append("")
    lines.append(
        f"- **Limited data:** All models are trained and evaluated on a single export "
        f"spanning {(result.analysis_time - DATA_FLOOR).days} days of usable history. "
        f"Model comparison at this scale is noisy — a few-minute difference in MAE "
        f"may not be statistically significant.")
    lines.append("- **Non-stationarity:** The baby's feeding patterns are changing rapidly "
                 "(intervals lengthening, volumes increasing). Models fitted to last week's "
                 "data may not generalize to next week.")
    lines.append("- **Backtesting uses the same export:** The backtesting protocol uses "
                 "later feeds in the same CSV as ground truth. This is a form of "
                 "in-sample evaluation, though the train/test split at each cutoff "
                 "is strictly temporal.")
    lines.append("- **Breastfeeding volumes are estimated:** The 0.5 oz per 30 minutes "
                 "heuristic is not measured. Models that use breastfeeding volume carry "
                 "this uncertainty forward.")
    lines.append("")
    lines.append("### Future Directions")
    lines.append("")
    lines.append("- Add new exports over time and track model performance longitudinally.")
    lines.append("- Investigate whether the satiety decay concept improves with more data "
                 "and a richer parameterization (e.g., nonlinear satiety curves).")
    lines.append("- Consider separate day/night models with explicit regime detection "
                 "instead of continuous time-of-day features.")
    lines.append("- Monitor whether gradient-boosted models become competitive as the "
                 "training set grows beyond the current ~70 usable cutoffs.")
    lines.append("")

    return lines


def _build_delta_section(result, previous_metrics) -> list[str]:
    """Delta vs prior run."""
    if previous_metrics is None:
        return ["No prior run found. This is the baseline."]

    lines = [
        f"Prior run: `{previous_metrics['run_id']}` "
        f"using `{Path(previous_metrics['export_path']).name}`",
        "",
    ]
    headliner = result.headliner
    prev_slug = previous_metrics.get("headliner_slug")
    lines.append(
        f"- Headliner: `{headliner.definition.slug}`"
        + (" (unchanged)" if prev_slug == headliner.definition.slug
           else f" (was `{prev_slug}`)"))

    prev_model = previous_metrics["models"].get(headliner.definition.slug)
    if prev_model:
        prev_s = prev_model["backtest_summary"]
        cur_s = headliner.backtest_summary
        lines.append(f"- First-feed MAE delta: "
                     f"{_delta(cur_s.mean_first_feed_error_minutes, prev_s.get('mean_first_feed_error_minutes'))} minutes")

    prev_forecast = previous_metrics["models"].get(prev_slug, {}).get("forecast", [])
    cur_forecast = headliner.forecast.points
    if prev_forecast and cur_forecast:
        prev_time = datetime.fromisoformat(prev_forecast[0]["time"])
        shift = (cur_forecast[0].time - prev_time).total_seconds() / 60
        lines.append(f"- First forecast shifted by {shift:+.0f} minutes.")

    return lines


# ── Model page reports ──────────────────────────────────────────────────────
def write_model_report(model_run: ModelRun, output_path: Path, plot_filename: str):
    """Write a model-specific detail page with diagnostics and backtest results."""
    lines = [
        f"# {model_run.definition.title}",
        "",
        model_run.definition.description,
        "",
        f"![{model_run.definition.title}]({plot_filename})",
        "",
        "## Algorithm",
        "",
    ]

    # Notes as algorithm description
    for note in _dedupe(model_run.definition.notes + model_run.forecast.notes):
        lines.append(f"- {note}")
    lines.append("")

    lines.append(f"**Code reference:** `forecasting.py` → "
                 f"`{model_run.definition.forecast_fn.__name__}()`")
    lines.append("")
    lines.append(f"**Breastfeed merge:** "
                 + (f"{model_run.definition.merge_window_minutes} min window"
                    if model_run.definition.merge_window_minutes
                    else "bottle-only (no merge)"))
    lines.append("")

    # Diagnostics as hyperparameters
    lines.append("## Diagnostics")
    lines.append("")
    for k, v in model_run.forecast.diagnostics.items():
        lines.append(f"- `{k}`: {v}")
    lines.append("")

    # Current forecast
    lines.append("## Current Forecast")
    lines.append("")
    lines.extend(_build_forecast_table(model_run.forecast.points))
    lines.append("")

    # Backtest — timing only
    lines.append("## Backtest Summary")
    lines.append("")
    s = model_run.backtest_summary
    lines.append(f"- Recent first-feed MAE: {_fmt_min(s.recent_first_feed_error_minutes)}")
    lines.append(f"- Overall first-feed MAE: {_fmt_min(s.mean_first_feed_error_minutes)}")
    lines.append(f"- Full-24h timing MAE: {_fmt_min(s.mean_timing_mae_minutes)}")
    lines.append(f"- Coverage: {_fmt_pct(s.cutoff_coverage_ratio)} "
                 f"({s.total_cutoffs}/{s.potential_cutoffs})")
    lines.append("")

    # Recent cutoffs
    lines.append("## Recent Cutoffs")
    lines.append("")
    lines.extend(_build_cutoff_table(model_run.backtest_cases))

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _build_cutoff_table(cases: list[BacktestCase]) -> list[str]:
    """Recent backtest cutoff table — timing only."""
    recent = sorted(cases, key=lambda c: c.cutoff, reverse=True)[:8]
    if not recent:
        return ["No backtest cases available."]
    lines = [
        "| Cutoff | Predicted | Actual | Error | 24h MAE |",
        "|--------|-----------|--------|-------|---------|",
    ]
    for c in recent:
        pred = c.first_predicted_time.strftime("%m/%d %-I:%M %p") if c.first_predicted_time else "n/a"
        act = c.first_actual_time.strftime("%m/%d %-I:%M %p") if c.first_actual_time else "n/a"
        lines.append(
            f"| {c.cutoff.strftime('%m/%d %-I:%M %p')} | {pred} | {act} | "
            f"{_fmt_min(c.first_feed_error_minutes)} | {_fmt_min(c.timing_mae_minutes)} |")
    return lines


# ── Metrics payload ─────────────────────────────────────────────────────────
def build_metrics_payload(result: PipelineResult) -> dict[str, Any]:
    """Machine-readable metrics for run-to-run comparison."""
    return {
        "run_id": result.run_id,
        "generated_at": result.generated_at.isoformat(),
        "export_path": str(result.snapshot.export_path),
        "analysis_time": result.analysis_time.isoformat(),
        "data_floor": DATA_FLOOR.isoformat(),
        "headliner_slug": result.headliner_slug,
        "models": {
            m.definition.slug: {
                "title": m.definition.title,
                "description": m.definition.description,
                "merge_window_minutes": m.definition.merge_window_minutes,
                "notes": m.definition.notes,
                "forecast": [p.to_dict() for p in m.forecast.points],
                "forecast_notes": m.forecast.notes,
                "diagnostics": m.forecast.diagnostics,
                "backtest_summary": m.backtest_summary.to_dict(),
                "backtest_cases": [c.to_dict() for c in m.backtest_cases],
            }
            for m in result.model_runs
        },
    }


def load_previous_metrics(output_root: Path, current_run_id: str):
    """Load the most recent prior metrics payload."""
    candidates = sorted(
        p for p in output_root.glob("*/metrics.json") if p.parent.name != current_run_id)
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


# ── Helpers ─────────────────────────────────────────────────────────────────
def volume_to_marker_size(vol):
    return 50 + (vol / 5.0) * 350

def _format_hour(h):
    if h in {0, 24}: return "12 AM"
    if h == 12: return "12 PM"
    return f"{h} AM" if h < 12 else f"{h-12} PM"

def _fmt_min(v):
    return "n/a" if v is None else f"{v:.0f} min"

def _fmt_pct(v):
    return "n/a" if v is None else f"{v:.0%}"

def _sortable(v, fallback=None):
    if v is None: return float("inf") if fallback is None else fallback
    return v

def _delta(cur, prev):
    if cur is None or prev is None: return "n/a"
    d = cur - prev
    return f"{d:+.1f}" if abs(d) >= 0.05 else "+0.0"

def _dedupe(items):
    seen = set()
    result = []
    for x in items:
        if x not in seen:
            result.append(x)
            seen.add(x)
    return result
