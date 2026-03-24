"""Shared scripted-model utilities.

These helpers cover the common mechanics reused by more than one model:
weighting, volume profiling, forecast normalization, and local next-gap
regression support.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from feedcast.data import (
    FeedEvent,
    ForecastPoint,
    MAX_INTERVAL_HOURS,
    MIN_INTERVAL_HOURS,
    MIN_POINT_GAP_MINUTES,
    daily_feed_counts,
    hour_of_day,
)

RECENT_LOOKBACK_DAYS = 3
TREND_LONG_LOOKBACK_DAYS = 7
DAILY_SHIFT_HALF_LIFE_DAYS = 2
RECENT_HALF_LIFE_HOURS = 36

PHASE_LOCKED_FILTER_BETA = 0.05
PHASE_LOCKED_VOLUME_GAIN = 0.5
PHASE_LOCKED_MEAN_REVERSION = 0.2
PHASE_NOWCAST_BLEND_PHASE_WEIGHT = 0.4
PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS = 0.5

GAP_CONDITIONAL_LOOKBACK_DAYS = 5
GAP_CONDITIONAL_HALF_LIFE_HOURS = 36
STATE_GAP_MIN_EVENTS = 8
STATE_GAP_MIN_TRAINING_EXAMPLES = 6

CONSENSUS_MATCH_WINDOW_MINUTES = 90


class ForecastUnavailable(RuntimeError):
    """Raised when a model cannot produce a forecast for the given cutoff."""


def load_methodology(model_file: str) -> str:
    """Load the report methodology from a model's methodology.md file.

    Reads everything before the first ## heading. The # title line is
    stripped. This lets methodology.md contain both the report-ready
    text and supplementary sections (design decisions, research) that
    don't appear in the report.

    Args:
        model_file: The __file__ of the calling model module.

    Returns:
        The methodology text for the report.
    """
    path = Path(model_file).parent / "methodology.md"
    lines = path.read_text().splitlines()
    methodology_lines: list[str] = []
    for line in lines:
        # Skip the title line
        if line.startswith("# ") and not methodology_lines:
            continue
        # Stop at the first section heading
        if line.startswith("## "):
            break
        methodology_lines.append(line)
    return "\n".join(methodology_lines).strip()


def exp_weights(
    timestamps: list[datetime],
    now: datetime,
    half_life_hours: float,
) -> np.ndarray:
    """Return exponential recency weights."""
    decay = np.log(2) / half_life_hours
    ages_hours = np.array(
        [(now - timestamp).total_seconds() / 3600 for timestamp in timestamps],
        dtype=float,
    )
    return np.exp(-decay * ages_hours)


def day_weights(
    dates: list[date],
    reference_date: date,
    half_life_days: float,
) -> np.ndarray:
    """Return exponential day-level recency weights."""
    decay = np.log(2) / half_life_days
    ages_days = np.array(
        [(reference_date - current_date).days for current_date in dates],
        dtype=float,
    )
    return np.exp(-decay * ages_days)


def _weighted_multi_linregress(
    features: np.ndarray,
    targets: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Run weighted multivariate linear regression via normal equations."""
    observation_count, feature_count = features.shape
    normalized_weights = weights / weights.sum()
    design_matrix = np.column_stack([np.ones(observation_count), features])
    weight_matrix = np.diag(normalized_weights)
    weighted_design = design_matrix.T @ weight_matrix
    try:
        coefficients = np.linalg.solve(
            weighted_design @ design_matrix,
            weighted_design @ targets,
        )
    except np.linalg.LinAlgError:
        coefficients = np.zeros(feature_count + 1)
        coefficients[0] = float(np.average(targets, weights=normalized_weights))
    return coefficients


def weighted_std(values: np.ndarray, weights: np.ndarray) -> float:
    """Return a weighted standard deviation."""
    if len(values) == 1:
        return 0.0
    normalized_weights = weights / np.sum(weights)
    mean_value = np.average(values, weights=normalized_weights)
    variance = np.average((values - mean_value) ** 2, weights=normalized_weights)
    return float(np.sqrt(max(variance, 0.0)))


def build_volume_profile(
    events: list[FeedEvent],
    cutoff: datetime,
    lookback_days: int,
    half_life_hours: float,
    bins: int = 12,
) -> dict[str, Any]:
    """Build a weighted time-of-day volume profile."""
    window_start = cutoff - timedelta(days=lookback_days)
    recent_events = [event for event in events if window_start <= event.time <= cutoff]
    if not recent_events:
        raise ForecastUnavailable("Volume profile needs at least one recent event.")

    values = np.array([event.volume_oz for event in recent_events], dtype=float)
    weights = exp_weights(
        [event.time for event in recent_events], cutoff, half_life_hours
    )
    global_average = float(np.average(values, weights=weights))

    average_by_bin = np.zeros(bins)
    std_by_bin = np.zeros(bins)
    counts_by_bin = np.zeros(bins, dtype=int)
    grouped_values: list[list[float]] = [[] for _ in range(bins)]
    grouped_weights: list[list[float]] = [[] for _ in range(bins)]
    for event, weight in zip(recent_events, weights):
        index = min(int((hour_of_day(event.time) / 24) * bins), bins - 1)
        grouped_values[index].append(event.volume_oz)
        grouped_weights[index].append(float(weight))

    for index in range(bins):
        if not grouped_values[index]:
            average_by_bin[index] = global_average
            std_by_bin[index] = max(weighted_std(values, weights), 0.4)
            continue

        bin_values = np.array(grouped_values[index], dtype=float)
        bin_weights = np.array(grouped_weights[index], dtype=float)
        average_by_bin[index] = float(np.average(bin_values, weights=bin_weights))
        std_by_bin[index] = max(weighted_std(bin_values, bin_weights), 0.35)
        counts_by_bin[index] = len(bin_values)

    return {
        "bins": bins,
        "average_by_bin": average_by_bin,
        "std_by_bin": std_by_bin,
        "counts_by_bin": counts_by_bin,
        "global_average": global_average,
    }


def lookup_volume_profile(
    volume_profile: dict[str, Any],
    target_time: datetime,
) -> tuple[float, float]:
    """Look up average and variability for a forecast time."""
    bins = volume_profile["bins"]
    index = min(int((hour_of_day(target_time) / 24) * bins), bins - 1)
    return (
        float(volume_profile["average_by_bin"][index]),
        float(volume_profile["std_by_bin"][index]),
    )


def normalize_forecast_points(
    points: list[ForecastPoint],
    cutoff: datetime,
    horizon_hours: int,
) -> list[ForecastPoint]:
    """Clamp forecast points to a clean, ordered next-window schedule."""
    normalized: list[ForecastPoint] = []
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    for point in sorted(points, key=lambda item: item.time):
        if point.time <= cutoff or point.time >= horizon_end:
            continue

        adjusted_time = point.time
        if normalized:
            minimum_time = normalized[-1].time + timedelta(
                minutes=MIN_POINT_GAP_MINUTES
            )
            if adjusted_time < minimum_time:
                adjusted_time = minimum_time
        if adjusted_time >= horizon_end:
            break

        gap_hours = point.gap_hours
        if normalized:
            gap_hours = (adjusted_time - normalized[-1].time).total_seconds() / 3600

        normalized.append(
            ForecastPoint(
                time=adjusted_time,
                volume_oz=float(np.clip(point.volume_oz, 0.1, 8.0)),
                gap_hours=float(max(gap_hours, 0.1)),
            )
        )

    return normalized


def roll_forward_constant_interval(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
    interval_hours: float,
    volume_profile: dict[str, Any],
    label_interval_hours: float,
) -> list[ForecastPoint]:
    """Roll forward a constant-gap forecast."""
    current_time = history[-1].time
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []
    while True:
        current_time = current_time + timedelta(hours=interval_hours)
        if current_time >= end:
            break

        volume_oz, _ = lookup_volume_profile(volume_profile, current_time)
        points.append(
            ForecastPoint(
                time=current_time,
                volume_oz=volume_oz,
                gap_hours=label_interval_hours,
            )
        )

    return normalize_forecast_points(points, cutoff, horizon_hours)


def state_gap_recent_events(
    history: list[FeedEvent],
    cutoff: datetime,
    lookback_days: int,
) -> list[FeedEvent]:
    """Return the recent events used by local next-gap models."""
    lookback_start = cutoff - timedelta(days=lookback_days)
    recent = [event for event in history if lookback_start <= event.time <= cutoff]
    if len(recent) < STATE_GAP_MIN_EVENTS:
        raise ForecastUnavailable(
            f"State gap models need at least {STATE_GAP_MIN_EVENTS} recent events."
        )
    return recent


def state_gap_feature_vector(events: list[FeedEvent], index: int) -> np.ndarray:
    """Return features for predicting the next gap after one event."""
    if index < 1:
        raise ValueError("State gap features need at least one prior event.")

    event = events[index]
    previous_gap = (events[index].time - events[index - 1].time).total_seconds() / 3600
    rolling_gap = rolling_gap_hours(events, index)
    hour = hour_of_day(event.time)
    return np.array(
        [
            event.volume_oz,
            previous_gap,
            rolling_gap,
            float(np.sin(2 * np.pi * hour / 24)),
            float(np.cos(2 * np.pi * hour / 24)),
        ],
        dtype=float,
    )


def fit_state_gap_regression(
    history: list[FeedEvent],
    cutoff: datetime,
    lookback_days: int,
) -> tuple[np.ndarray, list[FeedEvent], int]:
    """Fit a weighted linear model for next-gap prediction from event state."""
    recent = state_gap_recent_events(history, cutoff, lookback_days)
    feature_rows: list[np.ndarray] = []
    targets: list[float] = []
    timestamps: list[datetime] = []

    for index in range(1, len(recent) - 1):
        gap_hours = (recent[index + 1].time - recent[index].time).total_seconds() / 3600
        feature_rows.append(state_gap_feature_vector(recent, index))
        targets.append(gap_hours)
        timestamps.append(recent[index].time)

    if len(feature_rows) < STATE_GAP_MIN_TRAINING_EXAMPLES:
        raise ForecastUnavailable(
            "State gap regression needs at least "
            f"{STATE_GAP_MIN_TRAINING_EXAMPLES} training examples."
        )

    feature_matrix = np.vstack(feature_rows)
    target_array = np.array(targets, dtype=float)
    weights = exp_weights(timestamps, cutoff, GAP_CONDITIONAL_HALF_LIFE_HOURS)
    coefficients = _weighted_multi_linregress(feature_matrix, target_array, weights)
    return coefficients, recent, len(targets)


def predict_state_gap_hours(events: list[FeedEvent], coefficients: np.ndarray) -> float:
    """Predict the next gap from the latest event in a sequence."""
    features = state_gap_feature_vector(events, len(events) - 1)
    raw_gap = float(coefficients[0] + (features @ coefficients[1:]))
    return float(np.clip(raw_gap, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS))


def rolling_gap_hours(events: list[FeedEvent], index: int, window: int = 3) -> float:
    """Return the mean of the last few observed gaps ending at one event."""
    gap_values = [
        (events[position].time - events[position - 1].time).total_seconds() / 3600
        for position in range(max(1, index - window + 1), index + 1)
    ]
    return float(np.mean(gap_values)) if gap_values else 3.0


def estimate_target_interval(
    events: list[FeedEvent],
    cutoff: datetime,
) -> float:
    """Estimate a recency-weighted nominal interval from recent history."""
    if len(events) < 2:
        raise ForecastUnavailable("Need at least two events to estimate an interval.")

    recent_events = events[-min(len(events), 24) :]
    intervals = np.array(
        [
            (current.time - previous.time).total_seconds() / 3600
            for previous, current in zip(recent_events, recent_events[1:])
        ],
        dtype=float,
    )
    interval_times = [
        previous.time + ((current.time - previous.time) / 2)
        for previous, current in zip(recent_events, recent_events[1:])
    ]
    interval_weights = exp_weights(interval_times, cutoff, RECENT_HALF_LIFE_HOURS)
    weighted_interval = float(np.average(intervals, weights=interval_weights))

    daily_counts = daily_feed_counts(recent_events)
    dates = sorted(daily_counts)
    count_weights = day_weights(dates, cutoff.date(), DAILY_SHIFT_HALF_LIFE_DAYS)
    feeds_per_day = float(
        np.average(
            [daily_counts[current_date] for current_date in dates],
            weights=count_weights,
        )
    )
    target_interval = 24 / np.clip(feeds_per_day, 6.0, 10.5)
    return float(
        np.clip(
            (0.7 * weighted_interval) + (0.3 * target_interval),
            MIN_INTERVAL_HOURS,
            MAX_INTERVAL_HOURS,
        )
    )
