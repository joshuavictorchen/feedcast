"""Survival / Hazard forecast model.

Predicts the next 24 hours by modeling feeding as a probabilistic event
whose likelihood increases with elapsed time. Uses a Weibull hazard
with separate overnight and daytime shapes to capture the structurally
different feeding regimes. See methodology.md and design.md in this
directory for research and design decisions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from feedcast.clustering import episodes_as_events
from feedcast.data import (
    Activity,
    FeedEvent,
    Forecast,
    ForecastPoint,
    build_feed_events,
    hour_of_day,
)
from feedcast.models.shared import (
    ForecastUnavailable,
    load_methodology,
    normalize_forecast_points,
)

MODEL_NAME = "Survival Hazard"
MODEL_SLUG = "survival_hazard"
MODEL_METHODOLOGY = load_methodology(__file__)

# --- Tuning parameters (model-specific) ---

# Weibull shape parameters by day-part, fitted on episode-level data.
# Higher shape = more regular (tighter distribution around the median).
# Overnight feeds are very regular; daytime feeds are more variable.
# Episode-level fit removes cluster-internal gap contamination that
# previously depressed these values. See research.py Section 9.
OVERNIGHT_SHAPE = 6.54
DAYTIME_SHAPE = 3.04

# Day-part boundaries (hour of day).
# Overnight: 20:00 to 08:00. Daytime: 08:00 to 20:00.
OVERNIGHT_START = 20
DAYTIME_START = 8

# How many days of recent history to use for scale estimation.
LOOKBACK_DAYS = 7

# Minimum same-daypart gaps required for scale estimation.
# Falls back to all-gap estimation if fewer are available.
MIN_DAYPART_GAPS = 3

# Minimum total gaps in lookback window to produce a forecast.
MIN_FIT_GAPS = 5

# Recency half-life for weighting gaps in scale estimation.
# Set to LOOKBACK_DAYS × 24 so the oldest events in the window get ~50%
# weight. Broad averaging works because episode-level history is clean —
# all gaps are real inter-episode gaps, not cluster-internal noise.
RECENCY_HALF_LIFE_HOURS = 168

# Volume floor for forecast points.
MIN_VOLUME_OZ = 0.5


# ---------------------------------------------------------------------------
# Day-part helpers
# ---------------------------------------------------------------------------


def _is_overnight(hour: float) -> bool:
    """Whether an hour-of-day falls in the overnight period (20-08)."""
    return hour >= OVERNIGHT_START or hour < DAYTIME_START


def _shape_for_hour(hour: float) -> float:
    """Return the Weibull shape parameter for the given hour of day."""
    return OVERNIGHT_SHAPE if _is_overnight(hour) else DAYTIME_SHAPE


# ---------------------------------------------------------------------------
# Weibull helpers
# ---------------------------------------------------------------------------


def _weibull_median(shape: float, scale: float) -> float:
    """Median of a Weibull distribution: scale * (ln 2)^(1/shape)."""
    return scale * math.log(2) ** (1.0 / shape)


def _weibull_conditional_remaining(
    shape: float, scale: float, elapsed: float,
) -> float:
    """Additional time to median given we have already waited `elapsed` hours.

    Uses the conditional survival function:
      P(T > t0 + t_rem | T > t0) = 0.5
    => t_rem = scale * ((t0/scale)^shape + ln 2)^(1/shape) - t0
    """
    if elapsed <= 0:
        return _weibull_median(shape, scale)
    total = scale * ((elapsed / scale) ** shape + math.log(2)) ** (1.0 / shape)
    return max(total - elapsed, 0.01)


def _weibull_quantile(shape: float, scale: float, p: float) -> float:
    """p-th quantile of a Weibull: scale * (-ln(1-p))^(1/shape)."""
    return scale * (-math.log(1.0 - p)) ** (1.0 / shape)


def _estimate_scale(
    gaps: np.ndarray, weights: np.ndarray, shape: float,
) -> float:
    """Closed-form weighted MLE for Weibull scale given fixed shape.

    λ_hat = (sum(w_i * t_i^k) / sum(w_i))^(1/k)
    """
    w_norm = weights / weights.sum()
    return float(np.sum(w_norm * gaps ** shape) ** (1.0 / shape))


# ---------------------------------------------------------------------------
# Scale estimation from recent history
# ---------------------------------------------------------------------------


def _estimate_daypart_scales(
    events: list[FeedEvent],
    cutoff: datetime,
) -> tuple[float, float, list[dict]]:
    """Estimate Weibull scale for both day-parts from recent gaps.

    Returns (overnight_scale, daytime_scale, fit_details).
    Falls back to all-gap scale if too few same-daypart gaps.
    """
    decay = math.log(2) / RECENCY_HALF_LIFE_HOURS
    lookback_start = cutoff - timedelta(days=LOOKBACK_DAYS)

    overnight_gaps, overnight_weights = [], []
    daytime_gaps, daytime_weights = [], []
    all_gaps, all_weights = [], []
    details = []

    for i in range(len(events) - 1):
        event = events[i]
        next_event = events[i + 1]

        if event.time < lookback_start:
            continue
        if next_event.time > cutoff:
            break

        gap = (next_event.time - event.time).total_seconds() / 3600
        if gap <= 0:
            continue

        age = (cutoff - event.time).total_seconds() / 3600
        weight = math.exp(-decay * max(age, 0))
        hour = hour_of_day(event.time)

        all_gaps.append(gap)
        all_weights.append(weight)

        if _is_overnight(hour):
            overnight_gaps.append(gap)
            overnight_weights.append(weight)
        else:
            daytime_gaps.append(gap)
            daytime_weights.append(weight)

        details.append({
            "time": event.time.isoformat(),
            "gap_hours": round(gap, 2),
            "daypart": "overnight" if _is_overnight(hour) else "daytime",
            "weight": round(weight, 3),
        })

    if not all_gaps:
        raise ForecastUnavailable("No recent gaps for scale estimation")

    all_gaps_np = np.array(all_gaps)
    all_weights_np = np.array(all_weights)

    # Estimate overnight scale.
    if len(overnight_gaps) >= MIN_DAYPART_GAPS:
        overnight_scale = _estimate_scale(
            np.array(overnight_gaps), np.array(overnight_weights),
            OVERNIGHT_SHAPE,
        )
    else:
        # Fall back to all gaps with overnight shape.
        overnight_scale = _estimate_scale(
            all_gaps_np, all_weights_np, OVERNIGHT_SHAPE,
        )

    # Estimate daytime scale.
    if len(daytime_gaps) >= MIN_DAYPART_GAPS:
        daytime_scale = _estimate_scale(
            np.array(daytime_gaps), np.array(daytime_weights),
            DAYTIME_SHAPE,
        )
    else:
        daytime_scale = _estimate_scale(
            all_gaps_np, all_weights_np, DAYTIME_SHAPE,
        )

    return overnight_scale, daytime_scale, details


# ---------------------------------------------------------------------------
# Public forecast function
# ---------------------------------------------------------------------------


def forecast_survival_hazard(
    activities: list[Activity],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Predict feeds using a day-part Weibull survival model.

    Args:
        activities: Raw feeding activities from the export.
        cutoff: The latest observed activity time.
        horizon_hours: How many hours ahead to forecast.

    Returns:
        A Forecast with predicted feed times, volumes, and diagnostics.

    Raises:
        ForecastUnavailable: If there are too few recent events.
    """
    # Build bottle-only events, filter to cutoff, then collapse into episodes.
    # Episode-level history removes cluster-internal gaps that contaminate
    # Weibull scale estimation and conditional survival elapsed time.
    raw_events = [
        e for e in build_feed_events(activities, merge_window_minutes=None)
        if e.time <= cutoff
    ]
    events = episodes_as_events(raw_events)
    if len(events) < MIN_FIT_GAPS + 1:
        raise ForecastUnavailable(
            f"Need at least {MIN_FIT_GAPS + 1} events, have {len(events)}"
        )

    # Estimate scales for both day-parts.
    overnight_scale, daytime_scale, fit_details = _estimate_daypart_scales(
        events, cutoff,
    )

    if len(fit_details) < MIN_FIT_GAPS:
        raise ForecastUnavailable(
            f"Need at least {MIN_FIT_GAPS} gaps in lookback window, "
            f"have {len(fit_details)}"
        )

    # Simulation volume: recent median.
    lookback_start = cutoff - timedelta(days=LOOKBACK_DAYS)
    recent_events = [e for e in events if e.time >= lookback_start]
    sim_volume = max(
        float(np.median([e.volume_oz for e in recent_events])),
        MIN_VOLUME_OZ,
    )

    # Current state.
    last_event = events[-1]
    elapsed = (cutoff - last_event.time).total_seconds() / 3600

    # The first gap's day-part is anchored to when the gap started (the
    # last feed), not the current wall clock. This prevents a discontinuity
    # where waiting across an 08:00/20:00 boundary would jump to a
    # different Weibull and increase the predicted remaining time.
    last_feed_hour = hour_of_day(last_event.time)
    current_shape = _shape_for_hour(last_feed_hour)
    current_scale = (
        overnight_scale if _is_overnight(last_feed_hour) else daytime_scale
    )

    # First feed: conditional survival given elapsed time.
    time_to_first = _weibull_conditional_remaining(
        current_shape, current_scale, elapsed,
    )

    # Simulate forward from cutoff.
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []
    feed_time = cutoff + timedelta(hours=time_to_first)

    while feed_time < horizon_end:
        previous_time = points[-1].time if points else last_event.time
        gap_hours = (feed_time - previous_time).total_seconds() / 3600

        points.append(ForecastPoint(
            time=feed_time,
            volume_oz=sim_volume,
            gap_hours=max(gap_hours, 0.1),
        ))

        # Next gap: use unconditional median for the day-part at feed_time.
        feed_hour = hour_of_day(feed_time)
        shape = _shape_for_hour(feed_hour)
        scale = overnight_scale if _is_overnight(feed_hour) else daytime_scale
        gap = _weibull_median(shape, scale)
        feed_time = feed_time + timedelta(hours=gap)

    points = normalize_forecast_points(points, cutoff, horizon_hours)

    diagnostics = _build_diagnostics(
        overnight_scale, daytime_scale, elapsed,
        last_feed_hour, fit_details, recent_events,
    )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=points,
        methodology=MODEL_METHODOLOGY,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _build_diagnostics(
    overnight_scale: float,
    daytime_scale: float,
    elapsed_since_last: float,
    last_feed_hour: float,
    fit_details: list[dict],
    recent_events: list[FeedEvent],
) -> dict:
    """Build diagnostics dict for the report and debugging."""
    overnight_count = sum(1 for d in fit_details if d["daypart"] == "overnight")
    daytime_count = sum(1 for d in fit_details if d["daypart"] == "daytime")

    return {
        "overnight_shape": OVERNIGHT_SHAPE,
        "overnight_scale": round(overnight_scale, 4),
        "overnight_median_hours": round(_weibull_median(OVERNIGHT_SHAPE, overnight_scale), 3),
        "overnight_fit_gaps": overnight_count,
        "daytime_shape": DAYTIME_SHAPE,
        "daytime_scale": round(daytime_scale, 4),
        "daytime_median_hours": round(_weibull_median(DAYTIME_SHAPE, daytime_scale), 3),
        "daytime_fit_gaps": daytime_count,
        "elapsed_since_last_hours": round(elapsed_since_last, 3),
        "first_gap_daypart": "overnight" if _is_overnight(last_feed_hour) else "daytime",
        "sim_volume_oz": round(
            float(np.median([e.volume_oz for e in recent_events])), 2
        ),
        "total_fit_episode_gaps": len(fit_details),
        "lookback_days": LOOKBACK_DAYS,
        "recency_half_life_hours": RECENCY_HALF_LIFE_HOURS,
        "uncertainty_25_75_overnight": {
            "p25_hours": round(_weibull_quantile(OVERNIGHT_SHAPE, overnight_scale, 0.25), 3),
            "p75_hours": round(_weibull_quantile(OVERNIGHT_SHAPE, overnight_scale, 0.75), 3),
        },
        "uncertainty_25_75_daytime": {
            "p25_hours": round(_weibull_quantile(DAYTIME_SHAPE, daytime_scale, 0.25), 3),
            "p75_hours": round(_weibull_quantile(DAYTIME_SHAPE, daytime_scale, 0.75), 3),
        },
    }
