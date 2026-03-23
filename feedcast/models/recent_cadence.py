"""Recent cadence baseline model.

This model deliberately stays simple: estimate one recent interval, then roll
it forward with time-of-day volume lookups.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from feedcast.data import (
    FeedEvent,
    Forecast,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
    SNACK_THRESHOLD_OZ,
    daily_feed_counts,
)
from .shared import (
    DAILY_SHIFT_HALF_LIFE_DAYS,
    RECENT_HALF_LIFE_HOURS,
    RECENT_LOOKBACK_DAYS,
    TREND_LONG_LOOKBACK_DAYS,
    ForecastUnavailable,
    build_volume_profile,
    day_weights,
    exp_weights,
    roll_forward_constant_interval,
)

MODEL_NAME = "Recent Cadence"
MODEL_SLUG = "recent_cadence"
MODEL_METHODOLOGY = """\
Bottle-only interval baseline. It keeps only full feeds (>=1.5 oz) from the \
last 3 days, computes the gap between consecutive full feeds, and applies \
exponential recency weights to those gaps using the midpoint timestamp of each \
gap (half-life = 36h). Separately, it estimates a day-level prior from recent \
feeds-per-day counts using exponential day weights (half-life = 2 days), clamps \
that rate to 6.5-10.5 feeds/day, and converts it into a target interval \
24 / feeds_per_day. The final interval estimate is \
clip(0.7 * weighted_recent_gap + 0.3 * target_interval, 1.5h, 6.0h).

Projection is a constant-gap roll-forward from the latest observed bottle \
time. For projected volumes, it builds a 12-bin two-hour time-of-day profile \
over the last 7 days with exponential recency weighting. Each bin stores a \
weighted mean volume; empty bins fall back to the global weighted average. \
Each forecast point therefore combines a simple constant timing rule with a \
time-of-day volume lookup rather than trying to model volume causally."""


def forecast_recent_cadence(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Project feeds using recent intervals and time-of-day volume bins."""
    # This is the low-complexity baseline. It ignores recursive state and asks
    # whether recent full-feed cadence is already good enough.
    if len(history) < 4:
        raise ForecastUnavailable("Recent Cadence needs at least four events.")

    recent_start = cutoff - timedelta(days=RECENT_LOOKBACK_DAYS)
    recent_events = [event for event in history if recent_start <= event.time <= cutoff]
    full_events = [
        event for event in recent_events if event.volume_oz >= SNACK_THRESHOLD_OZ
    ]
    if len(full_events) < 3:
        raise ForecastUnavailable("Recent Cadence needs three recent full feeds.")

    intervals: list[float] = []
    interval_times: list[datetime] = []
    for previous, current in zip(full_events, full_events[1:]):
        intervals.append((current.time - previous.time).total_seconds() / 3600)
        interval_times.append(previous.time + (current.time - previous.time) / 2)

    interval_weights = exp_weights(interval_times, cutoff, RECENT_HALF_LIFE_HOURS)
    weighted_interval = float(np.average(intervals, weights=interval_weights))

    daily_counts = daily_feed_counts(full_events)
    count_dates = sorted(daily_counts)
    daily_count_weights = day_weights(
        count_dates, cutoff.date(), DAILY_SHIFT_HALF_LIFE_DAYS
    )
    average_feeds_per_day = float(
        np.average(
            [daily_counts[current_date] for current_date in count_dates],
            weights=daily_count_weights,
        )
    )
    target_interval = 24 / np.clip(average_feeds_per_day, 6.5, 10.5)
    blended_interval = float(
        np.clip(
            (0.7 * weighted_interval) + (0.3 * target_interval),
            MIN_INTERVAL_HOURS,
            MAX_INTERVAL_HOURS,
        )
    )

    volume_profile = build_volume_profile(
        recent_events,
        cutoff=cutoff,
        lookback_days=TREND_LONG_LOOKBACK_DAYS,
        half_life_hours=RECENT_HALF_LIFE_HOURS,
    )
    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=roll_forward_constant_interval(
            history=history,
            cutoff=cutoff,
            horizon_hours=horizon_hours,
            interval_hours=blended_interval,
            volume_profile=volume_profile,
            label_interval_hours=blended_interval,
        ),
        methodology=MODEL_METHODOLOGY,
        diagnostics={
            "recent_full_feeds": len(full_events),
            "weighted_interval_hours": round(weighted_interval, 3),
            "average_feeds_per_day": round(average_feeds_per_day, 3),
            "blended_interval_hours": round(blended_interval, 3),
        },
    )
