"""Latent Hunger State forecast model.

Predicts the next 24 hours by modeling hunger as a hidden state that
rises over time and is partially reset by each feed. Larger feeds
produce deeper resets, so the next feed comes later. See methodology.md
and design.md in this directory for research and design decisions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from feedcast.clustering import episodes_as_events
from feedcast.data import (
    Activity,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
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

MODEL_NAME = "Latent Hunger State"
MODEL_SLUG = "latent_hunger"
MODEL_METHODOLOGY = load_methodology(__file__)

# --- Tuning parameters (model-specific) ---

# Fixed threshold normalizes the hunger scale. Only the ratio between
# growth rate and satiety rate matters, so fixing this eliminates a
# redundant degree of freedom.
HUNGER_THRESHOLD = 1.0

# Multiplicative satiety rate: hunger_after = threshold * exp(-rate * volume).
# Selected by canonical multi-window tuning on the 20260410(2) export.
# The canonical optimum shifted from 0.55 (earlier 20260410 export) to a
# plateau at 0.12–0.20. Interior value chosen for robustness.
# See research.md for the sweep evidence.
SATIETY_RATE = 0.18

# How many days of recent history to use for growth rate estimation.
LOOKBACK_DAYS = 7

# Minimum observed gaps (consecutive event pairs) in the lookback window
# required to produce a forecast. N events yield N-1 gaps.
MIN_FIT_GAPS = 5

# Recency half-life for weighting events in growth rate estimation.
# Set to LOOKBACK_DAYS × 24 so the oldest events in the window get ~50%
# weight. Broad averaging works because episode-level history is clean —
# all gaps are real inter-episode gaps, not cluster-internal noise.
RECENCY_HALF_LIFE_HOURS = 168

# Circadian modulation of the hunger growth rate. amplitude=0 means no
# modulation (constant growth). Research found amplitude=0.0 optimal for
# the multiplicative model because volume already correlates with time of
# day. The infrastructure is kept for future use as day/night patterns
# strengthen.
CIRCADIAN_AMPLITUDE = 0.0
CIRCADIAN_PHASE_HOUR = 0.0

# Numerical integration step for circadian simulation (hours).
SIM_STEP_HOURS = 0.25

# Floor on predicted gaps to prevent degenerate zero-gap cascades.
MIN_GAP_HOURS = 0.25

# Volume floor for forecast points.
MIN_VOLUME_OZ = 0.5


# ---------------------------------------------------------------------------
# Core hunger simulation
# ---------------------------------------------------------------------------


def _hunger_after_feed(volume_oz: float) -> float:
    """Compute the hunger level immediately after a feed.

    Uses multiplicative satiety: hunger drops to threshold * exp(-rate * volume).
    A larger feed drives hunger lower, so the next feed takes longer.
    Unlike additive satiety, this guarantees partial resets — a feed never
    fully zeroes hunger, preserving meaningful volume sensitivity.
    """
    return HUNGER_THRESHOLD * math.exp(-SATIETY_RATE * volume_oz)


def _simulate_gap(
    volume_oz: float,
    growth_rate: float,
    feed_hour: float = 0.0,
) -> float:
    """Predict the gap (hours) until the next feed after a feed of given volume.

    Hunger starts at the post-feed level and grows at growth_rate
    (optionally modulated by a circadian term) until it crosses the
    threshold.
    """
    hunger_after = _hunger_after_feed(volume_oz)
    remaining = HUNGER_THRESHOLD - hunger_after

    if remaining <= 0:
        return MIN_GAP_HOURS

    if CIRCADIAN_AMPLITUDE == 0.0:
        # Constant growth: closed-form solution.
        return max(remaining / growth_rate, MIN_GAP_HOURS)

    # Circadian modulation: numerically integrate growth.
    # rate(t) = growth_rate * (1 + amp * cos(2π(hour - phase) / 24))
    accumulated = 0.0
    t = 0.0
    while accumulated < remaining and t < 24.0:
        current_hour = (feed_hour + t) % 24.0
        rate = growth_rate * (
            1.0 + CIRCADIAN_AMPLITUDE * math.cos(
                2.0 * math.pi * (current_hour - CIRCADIAN_PHASE_HOUR) / 24.0
            )
        )
        accumulated += max(rate, 0.01) * SIM_STEP_HOURS
        t += SIM_STEP_HOURS
    return max(t, MIN_GAP_HOURS)


def _time_to_threshold(
    current_hunger: float,
    growth_rate: float,
    current_hour: float = 0.0,
) -> float:
    """Hours until hunger crosses the threshold from a given level.

    Used for the first predicted feed, where some time has already elapsed
    since the last observed feed.
    """
    remaining = HUNGER_THRESHOLD - current_hunger
    if remaining <= 0:
        return MIN_GAP_HOURS

    if CIRCADIAN_AMPLITUDE == 0.0:
        return max(remaining / growth_rate, MIN_GAP_HOURS)

    accumulated = 0.0
    t = 0.0
    while accumulated < remaining and t < 24.0:
        hour = (current_hour + t) % 24.0
        rate = growth_rate * (
            1.0 + CIRCADIAN_AMPLITUDE * math.cos(
                2.0 * math.pi * (hour - CIRCADIAN_PHASE_HOUR) / 24.0
            )
        )
        accumulated += max(rate, 0.01) * SIM_STEP_HOURS
        t += SIM_STEP_HOURS
    return max(t, MIN_GAP_HOURS)


# ---------------------------------------------------------------------------
# Growth rate estimation
# ---------------------------------------------------------------------------


def _estimate_growth_rate(
    events: list[FeedEvent],
    cutoff: datetime,
) -> tuple[float, list[dict]]:
    """Estimate the current hunger growth rate from recent observed gaps.

    For each recent event, we know the volume and the actual gap to the
    next event. The multiplicative model predicts:
        gap = (threshold - threshold * exp(-satiety_rate * volume)) / growth_rate
    So:
        implied_growth_rate = (1 - exp(-satiety_rate * volume)) / actual_gap

    We take a recency-weighted average of the implied growth rates. This
    adapts the model to the baby's current feeding pace without needing an
    expensive grid search at forecast time.

    Returns the estimated growth rate and a details list for diagnostics.
    """
    decay = math.log(2) / RECENCY_HALF_LIFE_HOURS
    lookback_start = cutoff - timedelta(days=LOOKBACK_DAYS)

    implied_rates = []
    weights = []
    details = []

    for i in range(len(events) - 1):
        event = events[i]
        next_event = events[i + 1]

        if event.time < lookback_start:
            continue
        if next_event.time > cutoff:
            break

        actual_gap = (next_event.time - event.time).total_seconds() / 3600
        if actual_gap <= 0:
            continue

        # Implied growth rate from this event's volume and observed gap.
        satiety_effect = 1.0 - math.exp(-SATIETY_RATE * event.volume_oz)
        implied_gr = satiety_effect / actual_gap

        age_hours = (cutoff - event.time).total_seconds() / 3600
        weight = math.exp(-decay * max(age_hours, 0))

        implied_rates.append(implied_gr)
        weights.append(weight)
        details.append({
            "time": event.time.isoformat(),
            "volume_oz": round(event.volume_oz, 2),
            "gap_hours": round(actual_gap, 2),
            "implied_gr": round(implied_gr, 4),
            "weight": round(weight, 3),
        })

    if not implied_rates:
        raise ForecastUnavailable("No recent events for growth rate estimation")

    # Recency-weighted average of implied growth rates.
    growth_rate = float(np.average(implied_rates, weights=weights))

    # Clamp to reasonable range.
    growth_rate = max(0.05, min(growth_rate, 2.0))

    return growth_rate, details


# ---------------------------------------------------------------------------
# Public forecast function
# ---------------------------------------------------------------------------


def forecast_latent_hunger(
    activities: list[Activity],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Predict feeds by simulating a latent hunger state forward.

    Args:
        activities: Raw feeding activities from the export.
        cutoff: The latest observed activity time.
        horizon_hours: How many hours ahead to forecast.

    Returns:
        A Forecast with predicted feed times, volumes, and diagnostics.

    Raises:
        ForecastUnavailable: If there are too few recent events.
    """
    # Build breastfeed-merged events. Latent Hunger uses volume directly
    # in timing logic, so breastfeed volume attribution matters here.
    raw_events = [
        e for e in build_feed_events(
            activities,
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
        )
        if e.time <= cutoff
    ]
    events = episodes_as_events(raw_events)
    if len(events) < MIN_FIT_GAPS + 1:
        raise ForecastUnavailable(
            f"Need at least {MIN_FIT_GAPS + 1} events, have {len(events)}"
        )

    # Estimate growth rate from recent events.
    growth_rate, fit_details = _estimate_growth_rate(events, cutoff)

    if len(fit_details) < MIN_FIT_GAPS:
        raise ForecastUnavailable(
            f"Need at least {MIN_FIT_GAPS} gaps in lookback window, "
            f"have {len(fit_details)}"
        )

    # Compute simulation volume: median of recent events in lookback window.
    lookback_start = cutoff - timedelta(days=LOOKBACK_DAYS)
    recent_events = [e for e in events if e.time >= lookback_start]
    sim_volume = float(np.median([e.volume_oz for e in recent_events]))
    sim_volume = max(sim_volume, MIN_VOLUME_OZ)

    # Current hunger state.
    last_event = events[-1]
    elapsed_since_last = (cutoff - last_event.time).total_seconds() / 3600
    hunger_after_last = _hunger_after_feed(last_event.volume_oz)
    current_hunger = hunger_after_last + growth_rate * elapsed_since_last
    current_hunger = min(current_hunger, HUNGER_THRESHOLD)

    # Simulate forward from cutoff.
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []
    sim_time = cutoff

    # First feed: time from current hunger level to threshold.
    time_to_next = _time_to_threshold(
        current_hunger, growth_rate, hour_of_day(cutoff),
    )
    feed_time = sim_time + timedelta(hours=time_to_next)

    while feed_time < horizon_end:
        # Volume for the first predicted feed uses the actual last event's
        # "natural continuation." Subsequent feeds use the simulation volume.
        if not points:
            feed_volume = sim_volume
        else:
            feed_volume = sim_volume

        previous_time = points[-1].time if points else last_event.time
        gap_hours = (feed_time - previous_time).total_seconds() / 3600

        points.append(ForecastPoint(
            time=feed_time,
            volume_oz=max(feed_volume, MIN_VOLUME_OZ),
            gap_hours=max(gap_hours, 0.1),
        ))

        # Simulate the next gap from this feed.
        sim_time = feed_time
        gap = _simulate_gap(
            sim_volume, growth_rate, hour_of_day(sim_time),
        )
        feed_time = sim_time + timedelta(hours=gap)

    points = normalize_forecast_points(points, cutoff, horizon_hours)

    diagnostics = _build_diagnostics(
        growth_rate, sim_volume, current_hunger,
        elapsed_since_last, fit_details, recent_events,
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
    growth_rate: float,
    sim_volume: float,
    current_hunger: float,
    elapsed_since_last: float,
    fit_details: list[dict],
    recent_events: list[FeedEvent],
) -> dict:
    """Build diagnostics dict for the report and debugging."""
    implied_rates = [d["implied_gr"] for d in fit_details]
    return {
        "growth_rate": round(growth_rate, 4),
        "satiety_rate": SATIETY_RATE,
        "circadian_amplitude": CIRCADIAN_AMPLITUDE,
        "circadian_phase_hour": CIRCADIAN_PHASE_HOUR,
        "hunger_threshold": HUNGER_THRESHOLD,
        "sim_volume_oz": round(sim_volume, 2),
        "current_hunger": round(current_hunger, 4),
        "elapsed_since_last_hours": round(elapsed_since_last, 3),
        "recent_episodes_in_window": len(recent_events),
        "fit_episodes_used": len(fit_details),
        "implied_growth_rate_range": {
            "min": round(min(implied_rates), 4),
            "max": round(max(implied_rates), 4),
            "std": round(float(np.std(implied_rates)), 4),
        },
        "lookback_days": LOOKBACK_DAYS,
        "recency_half_life_hours": RECENCY_HALF_LIFE_HOURS,
    }
