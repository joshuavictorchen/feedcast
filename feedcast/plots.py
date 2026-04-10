"""Plot generation helpers for the Markdown report.

The report uses two charts:

  * A featured schedule chart (``write_schedule_plot``), days as horizontal
    rows, time-of-day along the x axis. Historical bottle feeds are blue;
    forecasted feeds are orange with "oz / time" labels below each dot.
  * A trajectory comparison chart (``write_spaghetti_plot``), models as
    horizontal lanes with the featured model and Agent Inference emphasized
    near the top, time flowing left-to-right. The x axis is suppressed
    because every marker carries its own label.

Both charts are sized for narrow-column rendering (GitHub markdown, mobile
portrait), roughly 0.6-0.7 width-to-height ratio.
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
from matplotlib.colors import to_rgba  # noqa: E402
from matplotlib.offsetbox import (  # noqa: E402
    AnchoredOffsetbox,
    DrawingArea,
    HPacker,
    TextArea,
)
from matplotlib.patches import Circle, Rectangle  # noqa: E402
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
BG = "#E0E0E6"
SEPARATOR = "#C8C8CE"
LABEL_SECONDARY = "#78787E"
ORANGE_SOFT = "#E8CDA0"
CARD = "#F0F0F4"
PROJ_FILL = "#EDE8E1"
HISTORY_COLOR = "#8E8E93"
# Pastel gold reserved for the Agent Inference lane in the trajectory chart.
# Distinct from the Prior 24h gray and the other pastel model colors so the
# agent-driven forecast is immediately identifiable.
AGENT_INFERENCE_COLOR = "#D4B86A"
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

# Muted pastel palette for the trajectory comparison chart.
SPAGHETTI_COLORS = {
    "slot_drift": "#6BBF7E",
    "analog_trajectory": "#7EB3D8",
    "latent_hunger": "#B88ED4",
    "survival_hazard": "#D98A8A",
    "claude_forecast": "#A0A0AA",
    "codex_forecast": "#88C8E0",
}

# Compact lane labels for the rotated y-tick layout in the trajectory chart.
# Long names (for example, "Latent Hunger State") would blow out the left
# margin. "Latent Hunger" is two lines so that when rotated 90 degrees the
# label becomes two short side-by-side columns instead of one tall column
# that nearly fills the lane.
_SHORT_LANE_NAMES = {
    "consensus_blend": "Consensus",
    "slot_drift": "Slot Drift",
    "analog_trajectory": "Analog",
    "latent_hunger": "Latent\nHunger",
    "survival_hazard": "Survival",
    "agent_inference": "Agent",
    "claude_forecast": "Claude",
    "codex_forecast": "Codex",
}


def write_spaghetti_plot(
    output_path: Path,
    all_forecasts: list[Forecast],
    featured_slug: str,
    events: list[FeedEvent],
    cutoff: datetime,
    history_tail_hours: float = 24,
    agent_inference_color: str | None = None,
) -> None:
    """Render the trajectory comparison chart.

    Each model occupies a horizontal lane with volume-sized markers at
    predicted feed times. A "Prior 24h" lane at the top shows actual feeds
    shifted +24h so times-of-day align with the forecast axis, enabling
    direct day-over-day comparison.

    Lane order (top to bottom): Prior 24h, Agent Inference (if present),
    the featured model, then the remaining scripted models in input order.

    Every marker carries its own "oz / time" label, so the bottom x axis is
    suppressed entirely. The temporal information lives on the markers, not
    on a tick row.

    Args:
        output_path: Where to write the PNG.
        all_forecasts: Every model forecast.
        featured_slug: The slug of the featured model.
        events: Historical events for the Prior 24h lane.
        cutoff: Cutoff "now" time.
        history_tail_hours: How far back the Prior 24h lane reaches.
        agent_inference_color: Optional hex override for the Agent Inference
            lane color. When ``None``, uses the module-level default.
    """
    _apply_plot_style()

    agent_color = agent_inference_color or AGENT_INFERENCE_COLOR

    def lane_color(slug: str) -> str:
        if slug == "agent_inference":
            return agent_color
        return _spaghetti_color(slug)

    featured = _find_forecast(all_forecasts, featured_slug)
    # Lane order: Agent Inference first (if present and not featured), then
    # the featured model, then the remaining scripted models in input order.
    # Mirrors the methodology section ordering in report.py, where
    # agent_inference and the featured model lead the cross-model outputs.
    agent = next(
        (
            forecast
            for forecast in all_forecasts
            if forecast.slug == "agent_inference"
            and forecast.available
            and forecast.points
        ),
        None,
    )
    scripted_others = [
        forecast
        for forecast in all_forecasts
        if forecast.slug != featured_slug
        and forecast.slug != "agent_inference"
        and forecast.available
        and forecast.points
    ]
    if agent is not None and agent.slug != featured_slug:
        model_list = [agent, featured] + scripted_others
    else:
        model_list = [featured] + scripted_others
    num_models = len(model_list)
    num_rows = num_models + 1  # +1 for the Prior 24h lane at the top

    figure, axis = plt.subplots(figsize=(6.5, 0.7 + num_rows * 1.15))

    # Time range rounded to hour boundaries.
    hour_start = cutoff.replace(minute=0, second=0, microsecond=0)
    hour_end = (cutoff + timedelta(hours=HORIZON_HOURS + 1)).replace(
        minute=0, second=0, microsecond=0
    )

    # History: prior 24h events, shifted forward by 24h to align with forecasts.
    history_start = cutoff - timedelta(hours=history_tail_hours)
    recent_events = [event for event in events if history_start <= event.time <= cutoff]
    shifted_events = [
        (event, event.time + timedelta(hours=24)) for event in recent_events
    ]

    earliest_point = min(point.time for forecast in model_list for point in forecast.points)
    # Lane rectangles (and xlim) extend an extra hour on each side beyond
    # the data points. At this figsize the scatter marker radius is roughly
    # 40 minutes of x-axis space, so without the pad, dots at the lane
    # edges, especially Prior 24h's first and last entries, get clipped.
    x_left = earliest_point - timedelta(minutes=45)
    x_right = hour_end + timedelta(minutes=15)
    lane_pad = timedelta(hours=1)
    lane_x_left = x_left - lane_pad
    lane_x_right = x_right + lane_pad

    history_y = num_rows
    lane_height = 0.84

    # History lane, gray tint.
    red, green, blue, _ = to_rgba(HISTORY_COLOR)
    axis.add_patch(Rectangle(
        (mdates.date2num(lane_x_left), history_y - lane_height / 2),
        mdates.date2num(lane_x_right) - mdates.date2num(lane_x_left),
        lane_height,
        color=(red, green, blue, 0.08), zorder=0, linewidth=0,
    ))

    # Model lane backgrounds tinted with each model's color.
    for index, forecast in enumerate(model_list):
        y_position = num_rows - 1 - index
        base_color = (
            ORANGE if forecast.slug == featured_slug else lane_color(forecast.slug)
        )
        red, green, blue, _ = to_rgba(base_color)
        axis.add_patch(Rectangle(
            (mdates.date2num(lane_x_left), y_position - lane_height / 2),
            mdates.date2num(lane_x_right) - mdates.date2num(lane_x_left),
            lane_height,
            color=(red, green, blue, 0.10), zorder=0, linewidth=0,
        ))

    # Lane separators.
    for index in range(num_rows + 1):
        axis.axhline(
            index + 0.5, color=SEPARATOR, linewidth=0.5, alpha=0.5, zorder=2,
        )

    # Dotted vertical gridlines at each hour.
    current_hour = hour_start
    while current_hour <= hour_end:
        axis.axvline(
            current_hour,
            color="#A0A0A8",
            linewidth=0.4,
            alpha=0.5,
            linestyle=":",
            zorder=2,
        )
        current_hour += timedelta(hours=1)

    # Prior 24h markers, gray with halo and volume/time label.
    history_label_color = _darken_color(HISTORY_COLOR)
    for event, shifted_time in shifted_events:
        if shifted_time < lane_x_left or shifted_time > lane_x_right:
            continue
        size = _volume_to_marker_size(event.volume_oz)
        axis.scatter(
            shifted_time, history_y,
            s=size * 2.5, c=HISTORY_COLOR, alpha=0.1,
            zorder=3, linewidths=0,
        )
        axis.scatter(
            shifted_time, history_y,
            s=size, c=HISTORY_COLOR, alpha=0.55,
            edgecolors="white", linewidths=0.6, zorder=4,
        )
        axis.annotate(
            f"{event.volume_oz:.1f} oz\n{event.time.strftime('%-I:%M %p').lower()}",
            (shifted_time, history_y),
            textcoords="offset points",
            xytext=(0, 12),
            fontsize=5,
            ha="center",
            color=history_label_color,
            fontweight="medium",
            linespacing=1.3,
        )

    # Per-model markers and labels.
    for index, forecast in enumerate(model_list):
        y_position = num_rows - 1 - index
        is_featured = forecast.slug == featured_slug
        color = ORANGE if is_featured else lane_color(forecast.slug)
        label_color = _darken_color(color)

        for point in forecast.points:
            size = _volume_to_marker_size(point.volume_oz)
            halo_color = ORANGE_SOFT if is_featured else color
            halo_alpha = 0.2 if is_featured else 0.15
            axis.scatter(
                point.time, y_position,
                s=size * 2.5, c=halo_color, alpha=halo_alpha,
                zorder=3, linewidths=0,
            )
            axis.scatter(
                point.time, y_position,
                s=size, c=color,
                alpha=0.85 if is_featured else 0.7,
                edgecolors="white",
                linewidths=0.8 if is_featured else 0.6,
                zorder=5 if is_featured else 4,
            )
            axis.annotate(
                f"{point.volume_oz:.1f} oz\n"
                f"{point.time.strftime('%-I:%M %p').lower()}",
                (point.time, y_position),
                textcoords="offset points",
                xytext=(0, 12),
                fontsize=5,
                ha="center",
                color=label_color,
                fontweight="medium",
                linespacing=1.3,
            )

    # Y axis: compact lane names rotated 90 degrees. Rotation brings the
    # label width down to roughly fontsize instead of label-length, which
    # is what lets the left margin shrink without truncating any text.
    axis.set_yticks(range(1, num_rows + 1))
    y_labels = list(reversed([_short_lane_name(forecast) for forecast in model_list])) + [
        "Prior 24h"
    ]
    y_colors = list(
        reversed([
            ORANGE if forecast.slug == featured_slug else lane_color(forecast.slug)
            for forecast in model_list
        ])
    ) + [HISTORY_COLOR]
    axis.set_yticklabels(
        y_labels, fontsize=8, fontweight="bold", rotation=90, va="center",
    )
    for tick_label, color in zip(axis.get_yticklabels(), y_colors):
        tick_label.set_color(_darken_color(color))

    axis.set_ylim(0.3, num_rows + 0.7)
    axis.set_xlim(lane_x_left, lane_x_right)

    # X axis is hidden because every marker already carries its own
    # "oz / time" label, so a bottom tick row would be redundant.
    axis.set_xticks([])
    axis.tick_params(axis="both", which="both", length=0)
    for spine in axis.spines.values():
        spine.set_visible(False)

    figure.text(
        0.04, 0.985, "Forecast Trajectories",
        fontsize=16, fontweight="bold", color="#1D1D1F", va="top", ha="left",
    )
    figure.text(
        0.04, 0.945,
        f"All models · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}"
        "  ·  prior day shifted +24h",
        fontsize=7.5, color=LABEL_SECONDARY, va="top", ha="left",
    )

    # Left margin is tight because rotated y-tick labels only need about
    # one font size of width. Bottom margin is minimal because there are
    # no x-axis ticks to accommodate.
    figure.subplots_adjust(top=0.92, bottom=0.03, left=0.08, right=0.97)
    figure.savefig(
        output_path, dpi=200, bbox_inches="tight",
        facecolor=BG, edgecolor="none", pad_inches=0.3,
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
    """Render the featured schedule chart.

    Days become horizontal rows, oldest at top, latest at bottom. Time of
    day flows left-to-right along the x axis, 12 AM to 12 AM. Historical
    bottle events are blue dots with alpha fading by age. Forecast events
    are orange dots with two-line "oz / time" labels below.

    Forecast days get a pale orange card tint so the forecast band is
    visually distinct from the history rows.

    Args:
        events: Historical feed events to plot.
        forecast_points: Forecast dots for the featured model.
        cutoff: The "now" boundary between history and forecast.
        output_path: Where to write the PNG.
        title: Main title.
        subtitle: Subtitle, usually the featured model name.
        forecast_color: Base color for forecast dots.
    """
    _apply_plot_style()

    display_start = max(
        DATA_FLOOR,
        (cutoff - timedelta(days=DISPLAY_DAYS)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ),
    )
    projection_end = cutoff + timedelta(hours=HORIZON_HOURS)
    display_end = projection_end.replace(hour=0, minute=0, second=0, microsecond=0)

    all_dates = []
    current_date = display_start.date()
    while current_date <= display_end.date():
        all_dates.append(current_date)
        current_date += timedelta(days=1)

    # Trim leading/trailing days that have no data.
    dates_with_data = (
        {event.time.date() for event in events if display_start <= event.time <= cutoff}
        | {point.time.date() for point in forecast_points}
    )
    while all_dates and all_dates[-1] not in dates_with_data:
        all_dates.pop()
    while all_dates and all_dates[0] not in dates_with_data:
        all_dates.pop(0)

    projected_dates = {point.time.date() for point in forecast_points}

    # Row 0 = top = oldest day; last row = bottom = latest (today + forecast).
    num_rows = len(all_dates)
    date_to_y = {date: index for index, date in enumerate(all_dates)}

    figure, axis = plt.subplots(figsize=(7.2, 0.85 + num_rows * 1.0))

    # Card bounds extend 1 hour past the 0-24 tick range on both sides so
    # that scatter markers at x=0 (12 AM) and x=24 (12 AM next day) sit
    # fully inside the card. At this figsize the marker display radius is
    # roughly 0.5 data units, which would overhang the tick range without
    # the pad.
    card_x_left = -1.0
    card_x_right = 25.0
    card_width = card_x_right - card_x_left
    # Cards fill most of the row, leaving a visible gap between rows for
    # the separator line. They are tall enough to still contain the
    # two-line forecast label below each dot given the offset/fontsize below.
    lane_height = 0.92
    for y_position, date in enumerate(all_dates):
        axis.add_patch(Rectangle(
            (card_x_left, y_position - lane_height / 2),
            card_width,
            lane_height,
            color=PROJ_FILL if date in projected_dates else CARD,
            zorder=0,
            linewidth=0,
        ))

    # Day separators.
    for index in range(num_rows + 1):
        axis.axhline(
            index - 0.5, color=SEPARATOR, linewidth=0.5, alpha=0.5, zorder=2,
        )

    # Faint vertical gridlines every 3 hours for scannability.
    for hour in range(0, 25, 3):
        axis.axvline(
            hour,
            color="#A0A0A8",
            linewidth=0.4,
            alpha=0.5,
            linestyle=":",
            zorder=2,
        )

    # Historical dots, blue and volume-sized, alpha fades with age.
    display_history = [event for event in events if display_start <= event.time <= cutoff]
    if display_history:
        history_x = np.array([hour_of_day(event.time) for event in display_history], dtype=float)
        history_y = np.array([date_to_y[event.time.date()] for event in display_history], dtype=float)
        history_sizes = np.array(
            [_volume_to_marker_size(event.volume_oz) for event in display_history],
            dtype=float,
        )
        ages = np.array(
            [(cutoff - event.time).total_seconds() / 3600 for event in display_history]
        )
        max_age = max(float(np.max(ages)), 1.0)
        alphas = 0.25 + 0.60 * (1 - (ages / max_age))
        for index in range(len(display_history)):
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

    # Forecast dots with halos.
    forecast_x = np.array([hour_of_day(point.time) for point in forecast_points], dtype=float)
    forecast_y = np.array(
        [date_to_y.get(point.time.date(), 0) for point in forecast_points],
        dtype=float,
    )
    forecast_sizes = np.array(
        [_volume_to_marker_size(point.volume_oz) for point in forecast_points],
        dtype=float,
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

    # Forecast labels sit below each dot. Y axis is inverted (row 0 on top),
    # so a negative display-space offset moves the label visually down. The
    # offset and fontsize are chosen so the two-line label stays inside the
    # forecast card given the lane_height above.
    label_color = _darken_color(forecast_color)
    for index, point in enumerate(forecast_points):
        axis.annotate(
            f"{point.volume_oz:.1f} oz\n{point.time.strftime('%-I:%M %p').lower()}",
            (forecast_x[index], forecast_y[index]),
            textcoords="offset points",
            xytext=(0, -11),
            fontsize=6,
            color=label_color,
            ha="center",
            va="top",
            fontweight="medium",
            linespacing=1.2,
        )

    # X axis: hours of day. xlim matches the card bounds so dots at the
    # edges never overhang into axis margin territory.
    axis.set_xticks(range(0, 25, 3))
    axis.set_xticklabels(
        [_format_hour(hour) for hour in range(0, 25, 3)],
        fontsize=8,
        fontweight="medium",
    )
    axis.set_xlim(card_x_left, card_x_right)

    # Y axis: day labels (row 0 on top). ylim is inverted so day 0 sits at
    # the top of the figure.
    axis.set_yticks(range(num_rows))
    axis.set_yticklabels(
        [
            datetime.combine(date, datetime.min.time()).strftime("%a %-m/%d")
            for date in all_dates
        ],
        fontsize=8.5,
        fontweight="bold",
    )
    axis.set_ylim(num_rows - 0.5 + 0.3, -0.5 - 0.3)
    axis.tick_params(axis="both", which="both", length=0)
    for spine in axis.spines.values():
        spine.set_visible(False)

    _draw_schedule_header(
        figure=figure,
        axis=axis,
        title=title,
        subtitle=subtitle,
        cutoff=cutoff,
        forecast_color=forecast_color,
    )

    figure.subplots_adjust(top=0.93, bottom=0.07, left=0.13, right=0.97)
    figure.savefig(
        output_path, dpi=200, bbox_inches="tight",
        facecolor=BG, edgecolor="none", pad_inches=0.35,
    )
    plt.close(figure)


def _draw_schedule_header(
    figure,
    axis,
    title: str,
    subtitle: str,
    cutoff: datetime,
    forecast_color: str,
) -> None:
    """Draw the schedule title, subtitle, and inline Recorded/Projected key."""
    figure.text(
        0.04, 0.985, title,
        fontsize=17, fontweight="bold", color="#1D1D1F", va="top", ha="left",
    )
    subtitle_str = (
        f"{subtitle} · cutoff {cutoff.strftime('%B %-d, %Y %-I:%M %p')}  ·  "
    )
    key_items: list = [
        TextArea(subtitle_str, textprops=dict(fontsize=8, color=LABEL_SECONDARY)),
    ]
    for color, label in [(BLUE, "Recorded"), (forecast_color, "Projected")]:
        dot = DrawingArea(8, 10)
        dot.add_artist(Circle((4, 5), 3.5, fc=color, ec="white", lw=0.5, alpha=0.85))
        key_items.append(dot)
        key_items.append(
            TextArea(
                f" {label}   ",
                textprops=dict(fontsize=7.5, color=LABEL_SECONDARY),
            )
        )
    box = HPacker(children=key_items, align="center", pad=0, sep=1)
    axis.add_artist(
        AnchoredOffsetbox(
            loc="upper left",
            child=box,
            bbox_to_anchor=(0.04, 0.955),
            bbox_transform=figure.transFigure,
            frameon=False,
            pad=0,
        )
    )


def _spaghetti_color(slug: str) -> str:
    """Return the muted pastel color for a model in the trajectory chart."""
    return SPAGHETTI_COLORS.get(slug, "#AEAEB2")


def _short_lane_name(forecast: Forecast) -> str:
    """Return a compact lane label for narrow-column rendering."""
    return _SHORT_LANE_NAMES.get(forecast.slug, forecast.name)


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
    """Format an integer hour for the schedule chart x axis."""
    if hour in {0, 24}:
        return "12 AM"
    if hour == 12:
        return "12 PM"
    return f"{hour} AM" if hour < 12 else f"{hour - 12} PM"


def _darken_color(hex_color: str) -> str:
    """Return a darker version of a hex color for label text."""
    hex_color = hex_color.lstrip("#")
    red, green, blue = (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )
    factor = 0.6
    red, green, blue = int(red * factor), int(green * factor), int(blue * factor)
    return f"#{red:02x}{green:02x}{blue:02x}"
