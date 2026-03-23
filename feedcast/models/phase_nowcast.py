"""Phase-based state-space forecast with first-gap nowcast blending.

This is the most stateful scripted model. It keeps a recursive phase estimate
for the full schedule, then optionally adjusts only the immediate next gap with
the shared local regression model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from feedcast.data import (
    FeedEvent,
    Forecast,
    ForecastPoint,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
    SNACK_THRESHOLD_OZ,
)
from .shared import (
    GAP_CONDITIONAL_LOOKBACK_DAYS,
    PHASE_LOCKED_FILTER_BETA,
    PHASE_LOCKED_MEAN_REVERSION,
    PHASE_LOCKED_VOLUME_GAIN,
    PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS,
    PHASE_NOWCAST_BLEND_PHASE_WEIGHT,
    RECENT_HALF_LIFE_HOURS,
    TREND_LONG_LOOKBACK_DAYS,
    ForecastUnavailable,
    build_volume_profile,
    estimate_target_interval,
    fit_state_gap_regression,
    lookup_volume_profile,
    normalize_forecast_points,
    predict_state_gap_hours,
)

MODEL_NAME = "Phase Nowcast Hybrid"
MODEL_SLUG = "phase_nowcast"
MODEL_METHODOLOGY = """\
Breastfeed-aware recursive state-space model built on a
Phase-Locked Oscillator (PLO) backbone. Inputs are bottle-centered
events whose effective volume includes breastfeeding logged within
the merge window. The model first estimates a nominal target
interval from the most recent up to 24 events: it computes
recency-weighted observed gaps (half-life = 36h), blends that
70/30 with a feeds-per-day prior derived from day-level weights
(clamped to 6.0-10.5 feeds/day), then clips to 1.5-6.0h.

The PLO initializes its period at that target interval and walks
forward through roughly the last 28 events. For each observed
transition, it predicts the next gap as
`period + 0.5 * (volume - running_avg)`, measures the error
versus the actual gap, and updates the period with filter gain
beta = 0.05. The running average volume updates as 70% old + 30%
new. During forecast rollout, the period mean-reverts 20% toward
the target interval on each step. Projected volume is
`clip(0.65 * tod_bin_mean + 0.35 * running_avg, 0.5, 8.0)`,
where the time-of-day profile is a 12-bin weighted volume profile
over the last 7 days with global-mean fallback for empty bins.

The "nowcast" layer fits a separate weighted linear regression on
the last 5 days of events to predict only the immediate next gap.
Features: volume, previous gap, rolling 3-gap mean, sin(hour),
cos(hour), with exponential sample weights (half-life = 36h). If
the local first-gap estimate is within 30 minutes of the phase
estimate and the latest event is a full feed (>=1.5 oz), the first
gap is blended as 40% phase + 60% regression. All later forecast
points shift by the same delta, preserving the PLO's internal
spacing. Otherwise the raw phase forecast is used unchanged."""


def forecast_phase_nowcast_hybrid(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Blend phase and local state timing when both agree on the next feed."""
    # The design goal is conservative complexity: use the richer local
    # nowcast only when it broadly agrees with the phase model.
    phase_forecast = _forecast_phase_locked_oscillator(history, cutoff, horizon_hours)
    if not phase_forecast.points:
        raise ForecastUnavailable(
            "Phase Nowcast Hybrid needs the phase model to emit at least one point."
        )

    phase_first_gap = (
        phase_forecast.points[0].time - history[-1].time
    ).total_seconds() / 3600
    blend_reason = "phase_only"
    state_training_examples = 0
    state_first_gap: float | None = None

    try:
        coefficients, _, state_training_examples = fit_state_gap_regression(
            history,
            cutoff,
            lookback_days=GAP_CONDITIONAL_LOOKBACK_DAYS,
        )
        state_first_gap = predict_state_gap_hours(history, coefficients)
    except ForecastUnavailable:
        coefficients = None

    last_event_is_snack = history[-1].volume_oz < SNACK_THRESHOLD_OZ
    gap_difference = (
        None if state_first_gap is None else abs(phase_first_gap - state_first_gap)
    )
    should_blend = (
        state_first_gap is not None
        and not last_event_is_snack
        and gap_difference is not None
        and gap_difference <= PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS
    )

    if should_blend and state_first_gap is not None:
        selected_first_gap = (PHASE_NOWCAST_BLEND_PHASE_WEIGHT * phase_first_gap) + (
            (1 - PHASE_NOWCAST_BLEND_PHASE_WEIGHT) * state_first_gap
        )
        blend_reason = "agreement_blend"
    elif state_first_gap is None:
        selected_first_gap = phase_first_gap
        blend_reason = "state_unavailable"
    elif last_event_is_snack:
        selected_first_gap = phase_first_gap
        blend_reason = "latest_event_snack"
    else:
        selected_first_gap = phase_first_gap
        blend_reason = "model_disagreement"

    gap_shift_hours = selected_first_gap - phase_first_gap
    shifted_points: list[ForecastPoint] = []
    for index, point in enumerate(phase_forecast.points):
        shifted_points.append(
            ForecastPoint(
                time=point.time + timedelta(hours=gap_shift_hours),
                volume_oz=point.volume_oz,
                gap_hours=selected_first_gap if index == 0 else point.gap_hours,
            )
        )

    diagnostics = dict(phase_forecast.diagnostics)
    diagnostics.update(
        {
            "phase_first_gap_hours": round(phase_first_gap, 3),
            "state_first_gap_hours": _round_or_none(state_first_gap),
            "state_training_examples": state_training_examples,
            "last_event_is_snack": last_event_is_snack,
            "first_gap_difference_minutes": _round_or_none(
                None if gap_difference is None else gap_difference * 60
            ),
            "first_gap_blend_applied": should_blend,
            "first_gap_blend_reason": blend_reason,
            "state_coefficients_available": coefficients is not None,
        }
    )
    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalize_forecast_points(shifted_points, cutoff, horizon_hours),
        methodology=MODEL_METHODOLOGY,
        diagnostics=diagnostics,
    )


def _forecast_phase_locked_oscillator(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Project feeds with a recursive phase filter and volume correction."""
    if len(history) < 6:
        raise ForecastUnavailable("Phase-Locked Oscillator needs at least six events.")

    recent_events = history[-min(len(history), 28) :]
    target_interval = estimate_target_interval(recent_events, cutoff)
    average_volume = float(np.mean([event.volume_oz for event in recent_events[:3]]))
    period_hours = target_interval

    for previous, current in zip(recent_events, recent_events[1:]):
        predicted_gap = np.clip(
            period_hours
            + (PHASE_LOCKED_VOLUME_GAIN * (previous.volume_oz - average_volume)),
            MIN_INTERVAL_HOURS,
            MAX_INTERVAL_HOURS,
        )
        actual_gap = (current.time - previous.time).total_seconds() / 3600
        period_error = actual_gap - predicted_gap
        period_hours = float(
            np.clip(
                period_hours + (PHASE_LOCKED_FILTER_BETA * period_error),
                MIN_INTERVAL_HOURS,
                MAX_INTERVAL_HOURS,
            )
        )
        average_volume = (0.7 * average_volume) + (0.3 * previous.volume_oz)

    volume_profile = build_volume_profile(
        recent_events,
        cutoff=cutoff,
        lookback_days=TREND_LONG_LOOKBACK_DAYS,
        half_life_hours=RECENT_HALF_LIFE_HOURS,
    )

    current_period_hours = period_hours
    current_average_volume = average_volume
    last_time = history[-1].time
    last_volume = history[-1].volume_oz
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []

    while True:
        predicted_gap = float(
            np.clip(
                period_hours
                + (PHASE_LOCKED_VOLUME_GAIN * (last_volume - average_volume)),
                MIN_INTERVAL_HOURS,
                MAX_INTERVAL_HOURS,
            )
        )
        next_time = last_time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, _ = lookup_volume_profile(volume_profile, next_time)
        predicted_volume = float(
            np.clip((0.65 * base_volume) + (0.35 * average_volume), 0.5, 8.0)
        )
        points.append(
            ForecastPoint(
                time=next_time,
                volume_oz=predicted_volume,
                gap_hours=predicted_gap,
            )
        )

        last_time = next_time
        last_volume = predicted_volume
        average_volume = (0.7 * average_volume) + (0.3 * predicted_volume)
        period_hours = float(
            np.clip(
                ((1 - PHASE_LOCKED_MEAN_REVERSION) * period_hours)
                + (PHASE_LOCKED_MEAN_REVERSION * target_interval),
                MIN_INTERVAL_HOURS,
                MAX_INTERVAL_HOURS,
            )
        )

    return Forecast(
        name="Phase-Locked Oscillator",
        slug="phase_locked_oscillator",
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=(
            "Internal recursive phase model used as the backbone for Phase Nowcast."
        ),
        diagnostics={
            "target_interval_hours": round(target_interval, 3),
            "current_period_hours": round(current_period_hours, 3),
            "running_average_volume_oz": round(current_average_volume, 3),
            "last_feed_volume_oz": round(history[-1].volume_oz, 3),
            "last_volume_delta_oz": round(
                history[-1].volume_oz - current_average_volume, 3
            ),
            "current_volume_adjustment_hours": round(
                PHASE_LOCKED_VOLUME_GAIN
                * (history[-1].volume_oz - current_average_volume),
                3,
            ),
        },
    )


def _round_or_none(value: float | None) -> float | None:
    """Round a float if present."""
    if value is None:
        return None
    return round(value, 3)
