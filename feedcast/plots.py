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
from matplotlib.patches import Rectangle  # noqa: E402
import numpy as np  # noqa: E402

from feedcast.data import (
    DATA_FLOOR,
    HORIZON_HOURS,
    FeedEvent,
    Forecast,
    ForecastPoint,
    hour_of_day,
)

BLUE = "#4A8EC2"
ORANGE = "#D98B3A"
RED = "#FF3B30"
GREEN = "#34C759"
PURPLE = "#AF52DE"
TEAL = "#5AC8FA"
BG = "#EAEAEF"
SEPARATOR = "#C8C8CE"
LABEL_SECONDARY = "#78787E"
ORANGE_SOFT = "#E8CDA0"
CARD = "#F0F0F4"
NIGHT_FILL = "#E0E0E6"
PROJ_FILL = "#EDE8E1"
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

    # Remove leading/trailing columns that have no data
    dates_with_data = (
        {e.time.date() for e in events if display_start <= e.time <= cutoff}
        | {p.time.date() for p in forecast_points}
    )
    while all_dates and all_dates[-1] not in dates_with_data:
        all_dates.pop()
    while all_dates and all_dates[0] not in dates_with_data:
        all_dates.pop(0)

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
        # Night bands in data coordinates (immune to ylim padding changes)
        axis.add_patch(Rectangle(
            (x_position - 0.42, -0.5), 0.84, 6.5,
            color=NIGHT_FILL, zorder=1, linewidth=0,
        ))
        axis.add_patch(Rectangle(
            (x_position - 0.42, 21), 0.84, 3.5,
            color=NIGHT_FILL, zorder=1, linewidth=0,
        ))

    for x_position in range(len(all_dates) + 1):
        axis.axvline(
            x_position - 0.5, color=SEPARATOR, linewidth=0.5, alpha=0.5, zorder=2
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
        marker="o",
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
    axis.set_ylim(24.5, -0.5)
    axis.set_yticks(range(0, 25))
    axis.set_yticklabels([_format_hour(hour) for hour in range(0, 25)], fontsize=7.5)
    axis.grid(
        True, which="major", axis="y",
        alpha=0.6, color="#A0A0A8", linewidth=0.5, linestyle=":",
    )
    # Emphasize 3-hour tick labels for scannability
    for index, tick_label in enumerate(axis.get_yticklabels()):
        if index % 3 == 0:
            tick_label.set_fontsize(8.5)
            tick_label.set_fontweight("medium")
        else:
            tick_label.set_alpha(0.6)
    axis.tick_params(axis="both", which="both", length=0)
    axis.set_xlim(-0.55, len(all_dates) - 0.45)
    for spine in axis.spines.values():
        spine.set_visible(False)

    # Compact hour labels on every other column separator for mid-chart readability
    separator_positions = [i + 0.5 for i in range(0, len(all_dates) - 1, 2)]
    for x_sep in separator_positions:
        for hour in range(0, 25, 3):
            axis.text(
                x_sep, hour, _short_hour(hour),
                fontsize=5.5, color="#A0A0A6", ha="center", va="center",
                fontweight="medium", zorder=2,
            )

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
    # Subtitle with inline key: Model · cutoff Time · ● Recorded ● Projected
    from matplotlib.offsetbox import AnchoredOffsetbox, DrawingArea, HPacker, TextArea
    from matplotlib.patches import Circle

    subtitle_str = (
        f"{subtitle} · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}  ·  "
    )
    key_items: list = [
        TextArea(subtitle_str, textprops=dict(fontsize=10.5, color=LABEL_SECONDARY)),
    ]
    for color, label in [(BLUE, "Recorded"), (forecast_color, "Projected")]:
        dot = DrawingArea(8, 10)
        dot.add_artist(Circle((4, 5), 3.5, fc=color, ec="white", lw=0.5, alpha=0.85))
        key_items.append(dot)
        key_items.append(
            TextArea(f" {label}   ", textprops=dict(fontsize=10, color=LABEL_SECONDARY))
        )
    box = HPacker(children=key_items, align="center", pad=0, sep=1)
    axis.add_artist(AnchoredOffsetbox(
        loc="upper left",
        child=box,
        bbox_to_anchor=(0.04, 0.935),
        bbox_transform=figure.transFigure,
        frameon=False,
        pad=0,
    ))

    figure.subplots_adjust(top=0.88, bottom=0.10, left=0.07, right=0.95)
    figure.savefig(
        output_path, dpi=200, bbox_inches="tight",
        facecolor=BG, edgecolor="none", pad_inches=0.4,
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


def _short_hour(hour: int) -> str:
    """Compact hour label for between-column markers (e.g. '6a', '12p')."""
    if hour in {0, 24}:
        return "12a"
    if hour == 12:
        return "12p"
    return f"{hour}a" if hour < 12 else f"{hour - 12}p"


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
