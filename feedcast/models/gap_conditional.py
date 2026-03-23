"""Event-state regression model for next-gap prediction.

This model fits one weighted regression over recent event state and then rolls
that regression forward autoregressively across the forecast window.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from feedcast.data import FeedEvent, Forecast, ForecastPoint
from .shared import (
    GAP_CONDITIONAL_HALF_LIFE_HOURS,
    GAP_CONDITIONAL_LOOKBACK_DAYS,
    build_volume_profile,
    fit_state_gap_regression,
    lookup_volume_profile,
    normalize_forecast_points,
    predict_state_gap_hours,
)

MODEL_NAME = "Gap-Conditional"
MODEL_SLUG = "gap_conditional"
MODEL_METHODOLOGY = """\
Breastfeed-aware event-level regression. It uses bottle-centered events whose \
effective volume includes breastfeeding merged into the next bottle feed. \
Training data is the last 5 days of events (including snacks). For each \
eligible event, the target is the observed gap until the following feed. The \
feature vector is [current volume, previous observed gap, rolling mean of the \
last 3 gaps, sin(2*pi*hour/24), cos(2*pi*hour/24)]. Samples receive \
exponential recency weights with half-life = 36h, and coefficients are fitted \
with weighted normal equations `(X^T W X)^-1 X^T W y`. The predicted gap is \
clipped to 1.5-6.0h.

For projection, the model does not emit one gap and stop. It rolls forward \
autoregressively: each predicted feed is appended as a synthetic event, using \
volume from a 12-bin two-hour time-of-day profile built over the same 5-day \
window with exponential recency weighting and global-mean fallback for empty \
bins. The next gap is then predicted from this updated synthetic state using \
the same fitted coefficients. That preserves volume-to-gap feedback across the \
entire 24-hour forecast horizon instead of treating each step independently."""


def forecast_gap_conditional(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Predict each gap from the latest event state."""
    # Unlike the cadence baseline, this model keeps updating the synthetic
    # state after each projected feed so the entire horizon is coupled.
    coefficients, recent, training_examples = fit_state_gap_regression(
        history,
        cutoff,
        lookback_days=GAP_CONDITIONAL_LOOKBACK_DAYS,
    )
    volume_profile = build_volume_profile(
        recent,
        cutoff=cutoff,
        lookback_days=GAP_CONDITIONAL_LOOKBACK_DAYS,
        half_life_hours=GAP_CONDITIONAL_HALF_LIFE_HOURS,
    )

    simulated_events = list(history)
    end = cutoff + timedelta(hours=horizon_hours)
    points = []

    while True:
        predicted_gap = predict_state_gap_hours(simulated_events, coefficients)
        next_time = simulated_events[-1].time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, _ = lookup_volume_profile(volume_profile, next_time)
        projected_volume = float(np.clip(base_volume, 0.5, 8.0))
        points.append(
            ForecastPoint(
                time=next_time,
                volume_oz=projected_volume,
                gap_hours=predicted_gap,
            )
        )
        simulated_events.append(
            FeedEvent(
                time=next_time,
                volume_oz=projected_volume,
                bottle_volume_oz=projected_volume,
                breastfeeding_volume_oz=0.0,
            )
        )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=MODEL_METHODOLOGY,
        diagnostics={
            "coefficients": {
                "intercept": round(coefficients[0], 3),
                "volume": round(coefficients[1], 3),
                "previous_gap": round(coefficients[2], 3),
                "rolling_gap": round(coefficients[3], 3),
                "hour_sin": round(coefficients[4], 3),
                "hour_cos": round(coefficients[5], 3),
            },
            "training_examples": training_examples,
        },
    )
