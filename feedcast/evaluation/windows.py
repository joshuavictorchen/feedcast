"""Shared multi-window retrospective evaluation helpers.

This module keeps multi-window scoring logic in one place so replay and
research scripts can share the same cutoff generation, recency weighting,
and aggregate semantics.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Sequence

from feedcast.clustering import FeedEpisode
from feedcast.data import FeedEvent, Forecast, HORIZON_HOURS

from .scoring import ForecastScore, score_forecast


@dataclass(frozen=True)
class WindowResult:
    """Outcome for one retrospective cutoff window."""

    cutoff: datetime
    observed_until: datetime
    weight: float
    score: ForecastScore | None
    status: str
    error_message: str | None


@dataclass(frozen=True)
class MultiWindowResult:
    """Weighted aggregate plus per-window scoring detail."""

    headline_score: float
    count_score: float
    timing_score: float
    window_count: int
    scored_window_count: int
    availability_ratio: float
    half_life_hours: float
    per_window: list[WindowResult]


def recency_weight(age_hours: float, half_life_hours: float) -> float:
    """Return the exponential recency weight for one cutoff.

    Args:
        age_hours: Non-negative age relative to the latest cutoff.
        half_life_hours: Positive half-life for the decay curve.

    Returns:
        The decay weight. ``age_hours == 0`` returns ``1.0``.

    Raises:
        ValueError: If ``age_hours`` is negative or ``half_life_hours`` is not
            positive.
    """
    if age_hours < 0:
        raise ValueError("age_hours must be non-negative.")
    if half_life_hours <= 0:
        raise ValueError("half_life_hours must be positive.")

    return 2.0 ** (-age_hours / half_life_hours)


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    """Return the weighted arithmetic mean.

    Args:
        values: Numeric samples.
        weights: Non-negative weights aligned with ``values``.

    Returns:
        The weighted mean.

    Raises:
        ValueError: If the sequences are empty, have different lengths, or sum
            to a non-positive total weight.
    """
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length.")
    if not values:
        raise ValueError("weighted_mean requires at least one value.")

    total_weight = math.fsum(weights)
    if total_weight <= 0:
        raise ValueError("weighted_mean requires a positive total weight.")

    weighted_total = math.fsum(value * weight for value, weight in zip(values, weights))
    return weighted_total / total_weight


def generate_episode_boundary_cutoffs(
    episodes: Sequence[FeedEpisode],
    latest_activity_time: datetime,
    lookback_hours: float = 96.0,
) -> list[datetime]:
    """Generate cutoffs from episode boundaries within the replay lookback.

    Args:
        episodes: Precomputed feeding episodes.
        latest_activity_time: Upper bound of the observed dataset.
        lookback_hours: Maximum lookback from ``latest_activity_time``.

    Returns:
        Unique cutoff timestamps sorted chronologically.

    Raises:
        ValueError: If replay cannot form a full 24-hour window or the
            parameters are invalid.
    """
    _validate_lookback_hours(lookback_hours)
    if not episodes:
        raise ValueError("Episode-boundary replay needs at least one feed episode.")

    replay_cutoff = latest_activity_time - timedelta(hours=HORIZON_HOURS)
    earliest_episode_time = min(episode.time for episode in episodes)
    _validate_full_horizon(replay_cutoff, earliest_episode_time)

    oldest_cutoff = latest_activity_time - timedelta(hours=lookback_hours)
    cutoffs = {
        episode.time
        for episode in episodes
        if oldest_cutoff <= episode.time <= replay_cutoff
    }
    cutoffs.add(replay_cutoff)
    return sorted(cutoffs)


def generate_fixed_step_cutoffs(
    latest_activity_time: datetime,
    earliest_activity_time: datetime,
    lookback_hours: float = 96.0,
    step_hours: float = 12.0,
) -> list[datetime]:
    """Generate fixed-interval retrospective cutoffs.

    Args:
        latest_activity_time: Upper bound of the observed dataset.
        earliest_activity_time: Earliest timestamp available for replay.
        lookback_hours: Maximum lookback from ``latest_activity_time``.
        step_hours: Step size between generated cutoffs.

    Returns:
        Unique cutoff timestamps sorted chronologically.

    Raises:
        ValueError: If replay cannot form a full 24-hour window or the
            parameters are invalid.
    """
    _validate_lookback_hours(lookback_hours)
    if step_hours <= 0:
        raise ValueError("step_hours must be positive.")

    replay_cutoff = latest_activity_time - timedelta(hours=HORIZON_HOURS)
    _validate_full_horizon(replay_cutoff, earliest_activity_time)

    oldest_cutoff = max(
        earliest_activity_time,
        latest_activity_time - timedelta(hours=lookback_hours),
    )
    step = timedelta(hours=step_hours)
    cutoffs = {replay_cutoff}

    cutoff = oldest_cutoff
    while cutoff <= replay_cutoff:
        cutoffs.add(cutoff)
        cutoff += step

    return sorted(cutoffs)


def evaluate_multi_window(
    forecast_fn: Callable[[datetime], Forecast],
    scoring_events: Sequence[FeedEvent],
    cutoffs: Sequence[datetime],
    latest_activity_time: datetime,
    half_life_hours: float = 36.0,
    parallel: bool = False,
) -> MultiWindowResult:
    """Evaluate one forecast function across multiple retrospective windows.

    Args:
        forecast_fn: Callable that produces a forecast from one cutoff.
        scoring_events: Bottle-only feed events for the full dataset.
        cutoffs: Unique retrospective cutoff timestamps.
        latest_activity_time: Upper bound of the observed dataset.
        half_life_hours: Recency decay half-life for window weighting.
        parallel: Whether to score windows concurrently.

    Returns:
        Aggregate weighted scores plus per-window detail. Unavailable and
        error windows remain in ``per_window`` but are excluded from the
        aggregate score.

    Raises:
        ValueError: If no cutoffs are provided, cutoffs are duplicated, or the
            weighting parameters are invalid.
    """
    if half_life_hours <= 0:
        raise ValueError("half_life_hours must be positive.")

    ordered_cutoffs = list(cutoffs)
    if not ordered_cutoffs:
        raise ValueError("Multi-window evaluation needs at least one cutoff.")
    if len(set(ordered_cutoffs)) != len(ordered_cutoffs):
        raise ValueError("Cutoffs must be unique.")

    ordered_cutoffs.sort()
    latest_cutoff = ordered_cutoffs[-1]
    if latest_cutoff > latest_activity_time:
        raise ValueError("Cutoffs cannot be after latest_activity_time.")

    if parallel:
        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(
                    _evaluate_window,
                    forecast_fn=forecast_fn,
                    scoring_events=scoring_events,
                    cutoff=cutoff,
                    latest_cutoff=latest_cutoff,
                    latest_activity_time=latest_activity_time,
                    half_life_hours=half_life_hours,
                )
                for cutoff in ordered_cutoffs
            ]
            per_window = [future.result() for future in futures]
    else:
        per_window = [
            _evaluate_window(
                forecast_fn=forecast_fn,
                scoring_events=scoring_events,
                cutoff=cutoff,
                latest_cutoff=latest_cutoff,
                latest_activity_time=latest_activity_time,
                half_life_hours=half_life_hours,
            )
            for cutoff in ordered_cutoffs
        ]

    scored_windows = [window for window in per_window if window.score is not None]
    if scored_windows:
        weights = [window.weight for window in scored_windows]
        headline_score = weighted_mean(
            [window.score.score for window in scored_windows],
            weights,
        )
        count_score = weighted_mean(
            [window.score.count_score for window in scored_windows],
            weights,
        )
        timing_score = weighted_mean(
            [window.score.timing_score for window in scored_windows],
            weights,
        )
    else:
        headline_score = 0.0
        count_score = 0.0
        timing_score = 0.0

    window_count = len(per_window)
    scored_window_count = len(scored_windows)
    return MultiWindowResult(
        headline_score=round(headline_score, 3),
        count_score=round(count_score, 3),
        timing_score=round(timing_score, 3),
        window_count=window_count,
        scored_window_count=scored_window_count,
        availability_ratio=round(scored_window_count / window_count, 6),
        half_life_hours=half_life_hours,
        per_window=per_window,
    )


def _validate_lookback_hours(lookback_hours: float) -> None:
    """Validate that lookback can include the replay-equivalent cutoff."""
    if lookback_hours < HORIZON_HOURS:
        raise ValueError(
            "lookback_hours must be at least one forecast horizon (24 hours)."
        )


def _validate_full_horizon(
    replay_cutoff: datetime,
    earliest_activity_time: datetime,
) -> None:
    """Ensure the dataset contains at least one full replay horizon."""
    if replay_cutoff < earliest_activity_time:
        raise ValueError(
            "Replay needs at least 24 observed hours in the export snapshot."
        )


def _evaluate_window(
    *,
    forecast_fn: Callable[[datetime], Forecast],
    scoring_events: Sequence[FeedEvent],
    cutoff: datetime,
    latest_cutoff: datetime,
    latest_activity_time: datetime,
    half_life_hours: float,
) -> WindowResult:
    """Evaluate one cutoff and normalize it into a WindowResult."""
    observed_until = min(
        cutoff + timedelta(hours=HORIZON_HOURS),
        latest_activity_time,
    )
    age_hours = (latest_cutoff - cutoff).total_seconds() / 3600.0
    weight = recency_weight(age_hours=age_hours, half_life_hours=half_life_hours)

    try:
        forecast = forecast_fn(cutoff)
    except Exception as error:
        return WindowResult(
            cutoff=cutoff,
            observed_until=observed_until,
            weight=weight,
            score=None,
            status="error",
            error_message=str(error),
        )

    if not forecast.available:
        return WindowResult(
            cutoff=cutoff,
            observed_until=observed_until,
            weight=weight,
            score=None,
            status="unavailable",
            error_message=forecast.error_message,
        )

    try:
        score = score_forecast(
            predicted_points=forecast.points,
            actual_events=scoring_events,
            prediction_time=cutoff,
            observed_until=observed_until,
        )
    except Exception as error:
        return WindowResult(
            cutoff=cutoff,
            observed_until=observed_until,
            weight=weight,
            score=None,
            status="error",
            error_message=str(error),
        )

    return WindowResult(
        cutoff=cutoff,
        observed_until=observed_until,
        weight=weight,
        score=score,
        status="scored",
        error_message=None,
    )
