"""Plot generation helpers for the Markdown report.

The report uses two charts only: a featured schedule view and a compact
trajectory comparison chart across all forecast sources.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

_mpl_config_dir = Path(".mpl-cache")
_mpl_config_dir.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_mpl_config_dir.resolve()))

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import numpy as np  # noqa: E402

from feedcast.data import (
    DATA_FLOOR,
    HORIZON_HOURS,
    FeedEvent,
    Forecast,
    ForecastPoint,
    hour_of_day,
)

BLUE = "#007AFF"
ORANGE = "#FF9500"
RED = "#FF3B30"
GREEN = "#34C759"
PURPLE = "#AF52DE"
TEAL = "#5AC8FA"
BG = "#FAFAFA"
SEPARATOR = "#E5E5EA"
LABEL_SECONDARY = "#86868B"
ORANGE_SOFT = "#FFCC80"
CARD = "#FFFFFF"
NIGHT_FILL = "#F0F0F5"
PROJ_FILL = "#FFF7ED"
DISPLAY_DAYS = 7

MODEL_COLORS = {
    "slot_drift": BLUE,
    "analog_trajectory": ORANGE,
    "latent_hunger": PURPLE,
    "survival_hazard": TEAL,
    "consensus_blend": RED,
    "claude_forecast": "#8E8E93",
    "codex_forecast": "#64D2FF",
}
FEATURED_COLOR = GREEN


def write_spaghetti_plot(
    output_path: Path,
    all_forecasts: list[Forecast],
    featured_slug: str,
    events: list[FeedEvent],
    cutoff: datetime,
    history_tail_hours: float = 12,
) -> None:
    """Render the compact trajectory comparison chart."""
    _apply_plot_style()
    figure, axis = plt.subplots(figsize=(16, 7))

    history_start = cutoff - timedelta(hours=history_tail_hours)
    recent_events = [event for event in events if history_start <= event.time <= cutoff]
    if recent_events:
        times = [event.time for event in recent_events]
        axis.plot(
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

    y_level = 1
    for forecast in all_forecasts:
        if (
            forecast.slug == featured_slug
            or not forecast.available
            or not forecast.points
        ):
            continue
        times = [cutoff] + [point.time for point in forecast.points]
        axis.plot(
            times,
            [y_level] * len(times),
            "o-",
            color=MODEL_COLORS.get(forecast.slug, "#AEAEB2"),
            markersize=4,
            linewidth=1.0,
            alpha=0.3,
            zorder=3,
            label=forecast.name,
        )

    featured = _find_forecast(all_forecasts, featured_slug)
    times = [cutoff] + [point.time for point in featured.points]
    axis.plot(
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
    for point in featured.points:
        axis.annotate(
            point.time.strftime("%-I:%M"),
            (point.time, y_level),
            textcoords="offset points",
            xytext=(0, 14),
            fontsize=7.5,
            ha="center",
            color=FEATURED_COLOR,
            fontweight="bold",
        )

    axis.axvline(cutoff, color=RED, linewidth=1.2, alpha=0.5, linestyle="--", zorder=8)
    axis.annotate(
        "NOW",
        (cutoff, y_level),
        textcoords="offset points",
        xytext=(0, -20),
        fontsize=8,
        color=RED,
        fontweight="bold",
        ha="center",
    )

    axis.set_yticks([])
    axis.set_ylim(0.5, 1.5)
    axis.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axis.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%-I %p"))
    axis.tick_params(axis="both", which="both", length=0)
    axis.grid(True, which="major", axis="x", alpha=0.15, color=SEPARATOR, linewidth=0.5)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.legend(loc="upper right", fontsize=9, frameon=False)

    figure.text(
        0.04,
        0.96,
        "Forecast Trajectories",
        fontsize=20,
        fontweight="bold",
        color="#1D1D1F",
        va="top",
    )
    figure.text(
        0.04,
        0.92,
        f"All models · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}",
        fontsize=10,
        color=LABEL_SECONDARY,
        va="top",
    )
    figure.subplots_adjust(top=0.85, bottom=0.08, left=0.04, right=0.96)
    figure.savefig(
        output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none"
    )
    plt.close(figure)


def write_schedule_plot(
    events: list[FeedEvent],
    forecast_points: list[ForecastPoint],
    cutoff: datetime,
    output_path: Path,
    title: str,
    subtitle: str,
    forecast_color: str = ORANGE,
) -> None:
    """Render the featured schedule chart."""
    _apply_plot_style()

    display_start = max(
        DATA_FLOOR,
        (cutoff - timedelta(days=DISPLAY_DAYS)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ),
    )
    projection_end = cutoff + timedelta(hours=HORIZON_HOURS)
    display_end = projection_end.replace(hour=0, minute=0, second=0, microsecond=0)

    all_dates = []
    current_date = display_start.date()
    while current_date <= display_end.date():
        all_dates.append(current_date)
        current_date += timedelta(days=1)
    date_to_x = {date: index for index, date in enumerate(all_dates)}
    projected_dates = {point.time.date() for point in forecast_points}

    figure, axis = plt.subplots(figsize=(16, 9.5))

    for x_position, date in enumerate(all_dates):
        axis.axvspan(
            x_position - 0.42,
            x_position + 0.42,
            color=PROJ_FILL if date in projected_dates else CARD,
            zorder=0,
            linewidth=0,
        )
        axis.axvspan(
            x_position - 0.42,
            x_position + 0.42,
            ymin=1 - 6 / 24,
            ymax=1.0,
            color=NIGHT_FILL,
            zorder=1,
            linewidth=0,
        )
        axis.axvspan(
            x_position - 0.42,
            x_position + 0.42,
            ymin=0,
            ymax=3 / 24,
            color=NIGHT_FILL,
            zorder=1,
            linewidth=0,
        )

    for x_position in range(len(all_dates) + 1):
        axis.axvline(
            x_position - 0.5, color=SEPARATOR, linewidth=0.5, alpha=0.5, zorder=2
        )

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

    history = [event for event in events if display_start <= event.time <= cutoff]
    if history:
        history_x = np.array(
            [date_to_x[event.time.date()] for event in history], dtype=float
        )
        history_y = np.array(
            [hour_of_day(event.time) for event in history], dtype=float
        )
        history_volumes = np.array([event.volume_oz for event in history], dtype=float)
        history_sizes = np.array(
            [_volume_to_marker_size(volume) for volume in history_volumes], dtype=float
        )
        ages = np.array(
            [(cutoff - event.time).total_seconds() / 3600 for event in history]
        )
        max_age = max(float(np.max(ages)), 1.0)
        alphas = 0.25 + (0.60 * (1 - (ages / max_age)))
        for index in range(len(history)):
            axis.scatter(
                history_x[index],
                history_y[index],
                s=history_sizes[index],
                c=BLUE,
                alpha=float(alphas[index]),
                edgecolors="white",
                linewidths=0.6,
                zorder=5,
            )

    forecast_x = np.array(
        [date_to_x.get(point.time.date(), 0) for point in forecast_points], dtype=float
    )
    forecast_y = np.array(
        [hour_of_day(point.time) for point in forecast_points], dtype=float
    )
    forecast_volumes = np.array(
        [point.volume_oz for point in forecast_points], dtype=float
    )
    forecast_sizes = np.array(
        [_volume_to_marker_size(volume) for volume in forecast_volumes], dtype=float
    )
    for index in range(len(forecast_points)):
        axis.scatter(
            forecast_x[index],
            forecast_y[index],
            s=forecast_sizes[index] * 2.5,
            c=ORANGE_SOFT,
            alpha=0.2,
            zorder=3,
            linewidths=0,
        )
    axis.scatter(
        forecast_x,
        forecast_y,
        s=forecast_sizes,
        c=forecast_color,
        alpha=0.85,
        edgecolors="white",
        linewidths=0.8,
        zorder=6,
        marker="D",
    )
    label_color = _darken_color(forecast_color)
    for index, point in enumerate(forecast_points):
        axis.annotate(
            f"{point.volume_oz:.1f} oz\n{point.time.strftime('%-I:%M %p').lower()}",
            (forecast_x[index], forecast_y[index]),
            textcoords="offset points",
            xytext=(14, 0),
            fontsize=7,
            color=label_color,
            ha="left",
            va="center",
            fontweight="medium",
            linespacing=1.3,
        )

    axis.set_xticks(range(len(all_dates)))
    axis.set_xticklabels(
        [
            datetime.combine(date, datetime.min.time()).strftime("%a\n%-m/%d")
            for date in all_dates
        ],
        fontsize=9,
        fontweight="medium",
    )
    axis.set_ylim(24, 0)
    axis.set_yticks(range(0, 25, 3))
    axis.set_yticklabels([_format_hour(hour) for hour in range(0, 25, 3)], fontsize=9)
    axis.yaxis.set_minor_locator(mticker.MultipleLocator(1))
    axis.grid(True, which="major", axis="y", alpha=0.2, color=SEPARATOR, linewidth=0.5)
    axis.grid(True, which="minor", axis="y", alpha=0.08, color=SEPARATOR, linewidth=0.3)
    axis.tick_params(axis="both", which="both", length=0)
    axis.set_xlim(-0.55, len(all_dates) - 0.45)
    for spine in axis.spines.values():
        spine.set_visible(False)

    figure.text(
        0.04,
        0.965,
        title,
        fontsize=22,
        fontweight="bold",
        color="#1D1D1F",
        va="top",
        ha="left",
    )
    figure.text(
        0.04,
        0.93,
        f"{subtitle} · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}",
        fontsize=10.5,
        color=LABEL_SECONDARY,
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
    for ounces in [1, 3, 5]:
        legend_items.append(
            Line2D(
                [0],
                [0],
                marker="o",
                color="none",
                markerfacecolor="#D1D1D6",
                markeredgecolor="#C7C7CC",
                markersize=np.sqrt(_volume_to_marker_size(ounces)) / 3.0,
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

    figure.subplots_adjust(top=0.89, bottom=0.10, left=0.06, right=0.96)
    figure.savefig(
        output_path, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none"
    )
    plt.close(figure)


def _find_forecast(forecasts: list[Forecast], slug: str) -> Forecast:
    """Return the forecast matching one slug."""
    for forecast in forecasts:
        if forecast.slug == slug:
            return forecast
    raise KeyError(f"No forecast with slug {slug!r}.")


def _apply_plot_style() -> None:
    """Set matplotlib rcParams for a clean, readable report aesthetic."""
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


def _volume_to_marker_size(volume_oz: float) -> float:
    """Map feed volume to a scatter marker area."""
    return 50 + (volume_oz / 5.0) * 350


def _format_hour(hour: int) -> str:
    """Format an integer hour for the schedule chart Y axis."""
    if hour in {0, 24}:
        return "12 AM"
    if hour == 12:
        return "12 PM"
    return f"{hour} AM" if hour < 12 else f"{hour - 12} PM"


def _darken_color(hex_color: str) -> str:
    """Return a darker version of a hex color for forecast labels."""
    hex_color = hex_color.lstrip("#")
    red, green, blue = (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )
    factor = 0.6
    red, green, blue = int(red * factor), int(green * factor), int(blue * factor)
    return f"#{red:02x}{green:02x}{blue:02x}"
