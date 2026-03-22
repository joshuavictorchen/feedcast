"""Report generation for current forecasts and model backtests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from forecasting import (
    DATA_FLOOR,
    DISPLAY_DAYS,
    BacktestCase,
    ForecastPoint,
    ModelRun,
    PipelineResult,
    availability_adjusted_first_feed_error,
    hour_of_day,
)


def write_reports(
    result: PipelineResult,
    output_root: Path = Path("reports"),
) -> Path:
    """Write the full report set for a pipeline result.

    Args:
        result: Completed pipeline result.
        output_root: Root reports directory.

    Returns:
        The newly created run directory.
    """
    output_root.mkdir(exist_ok=True)
    run_dir = output_root / result.run_id
    models_dir = run_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=False)

    previous_metrics = load_previous_metrics(output_root, result.run_id)
    metrics = build_metrics_payload(result)

    headliner_plot_path = run_dir / "headliner_schedule.png"
    plot_schedule(
        events=[event for event in result.headliner.events if event.time <= result.analysis_time],
        forecast=result.headliner.forecast.points,
        analysis_time=result.analysis_time,
        output_path=headliner_plot_path,
        title="Headliner Forecast",
        subtitle=f"{result.headliner.definition.title} · {result.snapshot.export_path.name}",
    )

    scores_plot_path = run_dir / "model_scores.png"
    plot_model_scores(
        model_runs=result.model_runs,
        headliner_slug=result.headliner_slug,
        output_path=scores_plot_path,
    )

    for model_run in result.model_runs:
        model_plot_path = models_dir / f"{model_run.definition.slug}.png"
        plot_schedule(
            events=[event for event in model_run.events if event.time <= result.analysis_time],
            forecast=model_run.forecast.points,
            analysis_time=result.analysis_time,
            output_path=model_plot_path,
            title=model_run.definition.title,
            subtitle=model_run.definition.description,
        )
        write_model_report(
            model_run=model_run,
            output_path=models_dir / f"{model_run.definition.slug}.md",
            plot_filename=f"{model_run.definition.slug}.png",
        )

    summary_path = run_dir / "summary.md"
    summary_path.write_text(build_summary_markdown(result, previous_metrics), encoding="utf-8")

    metrics_path = run_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return run_dir


def build_metrics_payload(result: PipelineResult) -> dict[str, Any]:
    """Build the machine-readable metrics payload for a run."""
    return {
        "run_id": result.run_id,
        "generated_at": result.generated_at.isoformat(),
        "export_path": str(result.snapshot.export_path),
        "analysis_time": result.analysis_time.isoformat(),
        "data_floor": DATA_FLOOR.isoformat(),
        "headliner_slug": result.headliner_slug,
        "models": {
            model_run.definition.slug: {
                "title": model_run.definition.title,
                "description": model_run.definition.description,
                "merge_window_minutes": model_run.definition.merge_window_minutes,
                "notes": model_run.definition.notes,
                "forecast": [point.to_dict() for point in model_run.forecast.points],
                "forecast_notes": model_run.forecast.notes,
                "diagnostics": model_run.forecast.diagnostics,
                "backtest_summary": model_run.backtest_summary.to_dict(),
                "backtest_cases": [case.to_dict() for case in model_run.backtest_cases],
            }
            for model_run in result.model_runs
        },
    }


def load_previous_metrics(output_root: Path, current_run_id: str) -> dict[str, Any] | None:
    """Load the most recent prior metrics payload."""
    candidates = sorted(
        path for path in output_root.glob("*/metrics.json") if path.parent.name != current_run_id
    )
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def plot_schedule(
    events: list[Any],
    forecast: list[ForecastPoint],
    analysis_time: datetime,
    output_path: Path,
    title: str,
    subtitle: str,
) -> None:
    """Render the schedule-view plot used throughout the reports."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.spines.bottom": False,
            "figure.facecolor": "#FAFAFA",
            "axes.facecolor": "#FAFAFA",
            "text.color": "#1D1D1F",
            "axes.labelcolor": "#555555",
            "xtick.color": "#86868B",
            "ytick.color": "#86868B",
        }
    )

    blue = "#007AFF"
    orange = "#FF9500"
    orange_soft = "#FFCC80"
    red = "#FF3B30"
    card = "#FFFFFF"
    background = "#FAFAFA"
    night_fill = "#F0F0F5"
    projection_fill = "#FFF7ED"
    separator = "#E5E5EA"
    secondary = "#86868B"

    display_start = max(
        DATA_FLOOR,
        (analysis_time - timedelta(days=DISPLAY_DAYS)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ),
    )
    projection_end = analysis_time + timedelta(hours=24)
    display_end = projection_end.replace(hour=0, minute=0, second=0, microsecond=0)

    all_dates = []
    current_date = display_start.date()
    while current_date <= display_end.date():
        all_dates.append(current_date)
        current_date += timedelta(days=1)

    date_to_x = {date: index for index, date in enumerate(all_dates)}
    projection_dates = {point.time.date() for point in forecast}

    fig, axis = plt.subplots(figsize=(16, 9.5))
    for x_position, date in enumerate(all_dates):
        axis.axvspan(
            x_position - 0.42,
            x_position + 0.42,
            color=projection_fill if date in projection_dates else card,
            zorder=0,
            linewidth=0,
        )
        axis.axvspan(
            x_position - 0.42,
            x_position + 0.42,
            ymin=1 - 6 / 24,
            ymax=1.0,
            color=night_fill,
            zorder=1,
            linewidth=0,
        )
        axis.axvspan(
            x_position - 0.42,
            x_position + 0.42,
            ymin=0,
            ymax=3 / 24,
            color=night_fill,
            zorder=1,
            linewidth=0,
        )

    for x_position in range(len(all_dates) + 1):
        axis.axvline(
            x_position - 0.5,
            color=separator,
            linewidth=0.5,
            alpha=0.5,
            zorder=2,
        )

    now_x = date_to_x[analysis_time.date()]
    now_y = hour_of_day(analysis_time)
    axis.plot(
        [now_x - 0.42, now_x + 0.42],
        [now_y, now_y],
        color=red,
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
        edgecolors=red,
        linewidths=1.5,
    )
    axis.annotate(
        "NOW",
        (now_x + 0.42, now_y),
        fontsize=6.5,
        color=red,
        fontweight="bold",
        va="center",
        ha="left",
        xytext=(4, 0),
        textcoords="offset points",
    )

    history = [event for event in events if display_start <= event.time <= analysis_time]
    if history:
        history_x = np.array([date_to_x[event.time.date()] for event in history], dtype=float)
        history_y = np.array([hour_of_day(event.time) for event in history], dtype=float)
        history_volumes = np.array([event.volume_oz for event in history], dtype=float)
        history_sizes = np.array([volume_to_marker_size(volume) for volume in history_volumes], dtype=float)
        ages = np.array([(analysis_time - event.time).total_seconds() / 3600 for event in history], dtype=float)
        max_age = max(float(np.max(ages)), 1.0)
        alphas = 0.25 + (0.60 * (1 - (ages / max_age)))

        for index in range(len(history)):
            axis.scatter(
                history_x[index],
                history_y[index],
                s=history_sizes[index],
                c=blue,
                alpha=alphas[index],
                edgecolors="white",
                linewidths=0.6,
                zorder=5,
            )

    if forecast:
        forecast_x = np.array([date_to_x[point.time.date()] for point in forecast], dtype=float)
        forecast_y = np.array([hour_of_day(point.time) for point in forecast], dtype=float)
        forecast_volumes = np.array([point.volume_oz for point in forecast], dtype=float)
        forecast_sizes = np.array([volume_to_marker_size(volume) for volume in forecast_volumes], dtype=float)

        for index in range(len(forecast)):
            axis.scatter(
                forecast_x[index],
                forecast_y[index],
                s=forecast_sizes[index] * 2.5,
                c=orange_soft,
                alpha=0.2,
                zorder=3,
                linewidths=0,
            )

        axis.scatter(
            forecast_x,
            forecast_y,
            s=forecast_sizes,
            c=orange,
            alpha=0.85,
            edgecolors="white",
            linewidths=0.8,
            zorder=6,
            marker="D",
        )

        for index, point in enumerate(forecast):
            label = f"{point.volume_oz:.1f} oz\n{point.time.strftime('%-I:%M %p').lower()}"
            axis.annotate(
                label,
                (forecast_x[index], forecast_y[index]),
                textcoords="offset points",
                xytext=(14, 0),
                fontsize=7,
                color="#B35A00",
                ha="left",
                va="center",
                fontweight="medium",
                linespacing=1.3,
            )

    axis.set_xticks(range(len(all_dates)))
    axis.set_xticklabels(
        [datetime.combine(date, datetime.min.time()).strftime("%a\n%-m/%d") for date in all_dates],
        fontsize=9,
        fontweight="medium",
    )
    axis.set_ylim(24, 0)
    axis.set_yticks(range(0, 25, 3))
    axis.set_yticklabels(
        [format_hour_label(hour) for hour in range(0, 25, 3)],
        fontsize=9,
    )
    axis.yaxis.set_minor_locator(mticker.MultipleLocator(1))
    axis.grid(True, which="major", axis="y", alpha=0.2, color=separator, linewidth=0.5)
    axis.grid(True, which="minor", axis="y", alpha=0.08, color=separator, linewidth=0.3)
    axis.tick_params(axis="both", which="both", length=0)
    axis.set_xlim(-0.55, len(all_dates) - 0.45)

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
        f"{subtitle} · analysis cutoff {analysis_time.strftime('%B %-d, %Y %-I:%M %p')}",
        fontsize=10.5,
        color=secondary,
        va="top",
        ha="left",
    )

    from matplotlib.lines import Line2D

    legend_items = [
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor=blue,
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
            markerfacecolor=orange,
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
            markeredgecolor=red,
            markeredgewidth=1.2,
            label="Now",
        ),
    ]
    for ounces in [1, 3, 5]:
        legend_items.append(
            Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor="#D1D1D6",
                markeredgecolor="#C7C7CC",
                markersize=np.sqrt(volume_to_marker_size(ounces)) / 3.0,
                markeredgewidth=0.3,
                label=f"{ounces} fl oz",
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
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor=background, edgecolor="none")
    plt.close(fig)


def plot_model_scores(
    model_runs: list[ModelRun],
    headliner_slug: str,
    output_path: Path,
) -> None:
    """Render a backtest comparison plot for all models."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"],
            "figure.facecolor": "#FAFAFA",
            "axes.facecolor": "#FAFAFA",
            "text.color": "#1D1D1F",
            "axes.labelcolor": "#555555",
            "xtick.color": "#86868B",
            "ytick.color": "#86868B",
        }
    )

    ordered_models = sorted(
        model_runs,
        key=lambda model_run: (
            availability_adjusted_first_feed_error(model_run.backtest_summary),
            sortable_metric(model_run.backtest_summary.mean_first_feed_error_minutes),
        ),
    )
    labels = [model_run.definition.title for model_run in ordered_models]
    overall = np.array(
        [
            sortable_metric(model_run.backtest_summary.mean_first_feed_error_minutes, fallback=0.0)
            for model_run in ordered_models
        ],
        dtype=float,
    )
    recent = np.array(
        [
            sortable_metric(model_run.backtest_summary.recent_first_feed_error_minutes, fallback=overall[index])
            for index, model_run in enumerate(ordered_models)
        ],
        dtype=float,
    )
    volume = [
        model_run.backtest_summary.mean_volume_mae_oz for model_run in ordered_models
    ]
    y_positions = np.arange(len(ordered_models))

    fig, axis = plt.subplots(figsize=(11, 5.5))
    axis.barh(y_positions, overall, color="#D1D1D6", height=0.60, label="Overall first-feed MAE")
    colors = [
        "#FF9500" if model_run.definition.slug == headliner_slug else "#007AFF"
        for model_run in ordered_models
    ]
    axis.barh(y_positions, recent, color=colors, height=0.36, label="Recent first-feed MAE")

    for index, model_run in enumerate(ordered_models):
        volume_text = "n/a" if volume[index] is None else f"{volume[index]:.2f} oz"
        axis.text(
            max(overall[index], recent[index]) + 2,
            y_positions[index],
            f"volume MAE {volume_text}",
            va="center",
            ha="left",
            fontsize=9,
            color="#6E6E73",
        )

    axis.set_yticks(y_positions)
    axis.set_yticklabels(labels, fontsize=10)
    axis.invert_yaxis()
    axis.set_xlabel("Minutes")
    axis.set_title("Backtest Comparison", fontsize=17, fontweight="bold", loc="left", pad=16)
    axis.text(
        0,
        1.04,
        "Lower is better. Recent = last 48 hours of available cutoffs.",
        transform=axis.transAxes,
        fontsize=10,
        color="#86868B",
    )
    axis.grid(axis="x", alpha=0.15, linewidth=0.6)
    axis.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=200, bbox_inches="tight", facecolor="#FAFAFA", edgecolor="none")
    plt.close(fig)


def write_model_report(
    model_run: ModelRun,
    output_path: Path,
    plot_filename: str,
) -> None:
    """Write a model-specific Markdown report."""
    lines: list[str] = [
        f"# {model_run.definition.title}",
        "",
        model_run.definition.description,
        "",
        f"![{model_run.definition.title}]({plot_filename})",
        "",
        "## Current Forecast",
        "",
    ]

    lines.extend(build_forecast_table(model_run.forecast.points))
    lines.extend(
        [
            "",
            "## Backtest Summary",
            "",
            f"- First-feed MAE: {format_minutes(model_run.backtest_summary.mean_first_feed_error_minutes)}",
            f"- Recent first-feed MAE: {format_minutes(model_run.backtest_summary.recent_first_feed_error_minutes)}",
            f"- Availability-adjusted recent first-feed score: {format_minutes(availability_adjusted_first_feed_error(model_run.backtest_summary))}",
            f"- Cutoff coverage: {format_ratio(model_run.backtest_summary.cutoff_coverage_ratio)} "
            f"({model_run.backtest_summary.total_cutoffs}/{model_run.backtest_summary.potential_cutoffs})",
            f"- Full-24h timing MAE: {format_minutes(model_run.backtest_summary.mean_timing_mae_minutes)}",
            f"- Full-24h volume MAE: {format_ounces(model_run.backtest_summary.mean_volume_mae_oz)}",
            f"- Full-24h cases: {model_run.backtest_summary.full_horizon_cases}",
            "",
            "## Notes",
            "",
        ]
    )
    for note in dedupe_preserve_order(model_run.definition.notes + model_run.forecast.notes):
        lines.append(f"- {note}")

    lines.extend(["", "## Diagnostics", ""])
    for key, value in model_run.forecast.diagnostics.items():
        lines.append(f"- `{key}`: {value}")

    lines.extend(["", "## Recent Cutoffs", ""])
    lines.extend(build_recent_cutoff_table(model_run.backtest_cases))
    output_path.write_text("\n".join(lines), encoding="utf-8")


def build_summary_markdown(
    result: PipelineResult,
    previous_metrics: dict[str, Any] | None,
) -> str:
    """Build the main Markdown summary report."""
    lines: list[str] = [
        "# Feeding Forecast Summary",
        "",
        f"Export: `{result.snapshot.export_path.name}`",
        "",
        f"Generated: `{result.generated_at.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        f"Analysis cutoff: `{result.analysis_time.strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        f"Data floor: `{DATA_FLOOR.strftime('%Y-%m-%d')}`",
        "",
        "![Headliner Forecast](headliner_schedule.png)",
        "",
        "![Model Scores](model_scores.png)",
        "",
        "## Headliner",
        "",
        f"**{result.headliner.definition.title}** is the current headliner. "
        f"It was selected by availability-adjusted recent first-feed accuracy first, "
        f"then broader first-feed accuracy, then volume accuracy.",
        "",
        result.headliner.definition.description,
        "",
    ]

    for note in dedupe_preserve_order(result.headliner.definition.notes + result.headliner.forecast.notes):
        lines.append(f"- {note}")

    lines.extend(["", "## Next 24 Hours", ""])
    lines.extend(build_forecast_table(result.headliner.forecast.points))

    lines.extend(["", "## Leaderboard", ""])
    lines.extend(build_leaderboard_table(result.model_runs))

    lines.extend(["", "## Delta Vs Prior Run", ""])
    lines.extend(build_delta_section(result, previous_metrics))

    lines.extend(["", "## Model Pages", ""])
    for model_run in result.model_runs:
        lines.append(f"- [{model_run.definition.title}](models/{model_run.definition.slug}.md)")

    return "\n".join(lines)


def build_forecast_table(forecast: list[ForecastPoint]) -> list[str]:
    """Build a forecast table."""
    lines = [
        "| Feed | Time | Volume | Range | Gap |",
        "|---|---|---|---|---|",
    ]
    total_volume = 0.0
    for index, point in enumerate(forecast, start=1):
        total_volume += point.volume_oz
        lines.append(
            f"| {index} | **{point.time.strftime('%-I:%M %p')}** | "
            f"{point.volume_oz:.1f} oz | "
            f"{point.low_volume_oz:.1f}-{point.high_volume_oz:.1f} | "
            f"{point.gap_hours:.1f}h |"
        )
    lines.append("")
    lines.append(f"> Projected total: **{total_volume:.1f} oz** across **{len(forecast)} feeds**")
    return lines


def build_leaderboard_table(model_runs: list[ModelRun]) -> list[str]:
    """Build the comparison table for all models."""
    ordered_models = sorted(
        model_runs,
        key=lambda model_run: (
            availability_adjusted_first_feed_error(model_run.backtest_summary),
            sortable_metric(model_run.backtest_summary.mean_first_feed_error_minutes),
        ),
    )
    lines = [
        "| Model | Adjusted recent score | Recent first-feed MAE | Coverage | Overall first-feed MAE | Full-24h timing MAE | Volume MAE | First current feed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model_run in ordered_models:
        first_point = model_run.forecast.points[0] if model_run.forecast.points else None
        first_current = "n/a"
        if first_point is not None:
            first_current = f"{first_point.time.strftime('%-I:%M %p')} / {first_point.volume_oz:.1f} oz"
        lines.append(
            f"| {model_run.definition.title} | "
            f"{format_minutes(availability_adjusted_first_feed_error(model_run.backtest_summary))} | "
            f"{format_minutes(model_run.backtest_summary.recent_first_feed_error_minutes)} | "
            f"{format_ratio(model_run.backtest_summary.cutoff_coverage_ratio)} "
            f"({model_run.backtest_summary.total_cutoffs}/{model_run.backtest_summary.potential_cutoffs}) | "
            f"{format_minutes(model_run.backtest_summary.mean_first_feed_error_minutes)} | "
            f"{format_minutes(model_run.backtest_summary.mean_timing_mae_minutes)} | "
            f"{format_ounces(model_run.backtest_summary.mean_volume_mae_oz)} | "
            f"{first_current} |"
        )
    return lines


def build_recent_cutoff_table(cases: list[BacktestCase]) -> list[str]:
    """Build a small table of the latest backtest cases."""
    recent_cases = sorted(cases, key=lambda case: case.cutoff, reverse=True)[:8]
    if not recent_cases:
        return ["No backtest cases available."]

    lines = [
        "| Cutoff | Predicted first feed | Actual first feed | Error | 24h timing MAE | Volume MAE |",
        "|---|---|---|---|---|---|",
    ]
    for case in recent_cases:
        predicted = "n/a" if case.first_predicted_time is None else case.first_predicted_time.strftime("%m/%d %-I:%M %p")
        actual = "n/a" if case.first_actual_time is None else case.first_actual_time.strftime("%m/%d %-I:%M %p")
        lines.append(
            f"| {case.cutoff.strftime('%m/%d %-I:%M %p')} | "
            f"{predicted} | "
            f"{actual} | "
            f"{format_minutes(case.first_feed_error_minutes)} | "
            f"{format_minutes(case.timing_mae_minutes)} | "
            f"{format_ounces(case.volume_mae_oz)} |"
        )
    return lines


def build_delta_section(
    result: PipelineResult,
    previous_metrics: dict[str, Any] | None,
) -> list[str]:
    """Build the delta-vs-prior-run section."""
    if previous_metrics is None:
        return ["No prior run found under `reports/`, so this is the baseline."]

    lines = [
        f"Prior run: `{previous_metrics['run_id']}` using `{Path(previous_metrics['export_path']).name}`",
        "",
    ]
    current_headliner = result.headliner
    previous_headliner_slug = previous_metrics.get("headliner_slug")
    lines.append(
        f"- Headliner: `{current_headliner.definition.slug}`"
        + (
            " (unchanged)"
            if previous_headliner_slug == current_headliner.definition.slug
            else f" (was `{previous_headliner_slug}`)"
        )
    )

    previous_headliner = previous_metrics["models"].get(current_headliner.definition.slug)
    if previous_headliner is not None:
        previous_summary = previous_headliner["backtest_summary"]
        current_summary = current_headliner.backtest_summary
        delta_first_feed = delta_text(
            current_summary.mean_first_feed_error_minutes,
            previous_summary.get("mean_first_feed_error_minutes"),
            "minutes",
        )
        delta_volume = delta_text(
            current_summary.mean_volume_mae_oz,
            previous_summary.get("mean_volume_mae_oz"),
            "oz",
        )
        lines.append(f"- {current_headliner.definition.title} first-feed MAE delta: {delta_first_feed}")
        lines.append(f"- {current_headliner.definition.title} volume MAE delta: {delta_volume}")

    previous_headliner_forecast = previous_metrics["models"].get(previous_headliner_slug, {}).get("forecast", [])
    current_forecast = current_headliner.forecast.points
    if previous_headliner_forecast and current_forecast:
        previous_first = previous_headliner_forecast[0]
        previous_time = datetime.fromisoformat(previous_first["time"])
        current_time = current_forecast[0].time
        shift_minutes = (current_time - previous_time).total_seconds() / 60
        volume_shift = current_forecast[0].volume_oz - previous_first["volume_oz"]
        lines.append(
            f"- First headliner forecast shifted by {shift_minutes:+.0f} minutes and {volume_shift:+.2f} oz."
        )

    return lines


def format_minutes(value: float | None) -> str:
    """Format minutes for tables."""
    if value is None:
        return "n/a"
    return f"{value:.0f} min"


def format_ounces(value: float | None) -> str:
    """Format ounces for tables."""
    if value is None:
        return "n/a"
    return f"{value:.2f} oz"


def format_ratio(value: float | None) -> str:
    """Format a ratio as a percentage."""
    if value is None:
        return "n/a"
    return f"{value * 100:.0f}%"


def delta_text(current: float | None, previous: float | None, unit: str) -> str:
    """Format a delta against a prior run."""
    if current is None or previous is None:
        return "n/a"
    delta = current - previous
    if abs(delta) < 0.005:
        delta = 0.0
    return f"{delta:+.2f} {unit}"


def sortable_metric(value: float | None, fallback: float | None = None) -> float:
    """Convert None to a sortable value."""
    if value is None:
        if fallback is not None:
            return fallback
        return float("inf")
    return value


def dedupe_preserve_order(values: list[str]) -> list[str]:
    """Return a list with duplicate strings removed in order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def volume_to_marker_size(volume_oz: float) -> float:
    """Map ounces to plot marker size."""
    return 50 + ((volume_oz / 5.0) * 350)


def format_hour_label(hour: int) -> str:
    """Format an integer hour for the chart axis."""
    if hour in {0, 24}:
        return "12 AM"
    if hour == 12:
        return "12 PM"
    if hour < 12:
        return f"{hour} AM"
    return f"{hour - 12} PM"
