"""Reusable forecast scoring for retrospective bottle-feed accuracy.

The scoring model intentionally separates two questions:

1. Did the forecast predict the right number of episodes in the observed window?
2. For the episodes that plausibly correspond, how close were the timestamps?

Both actuals and predictions are collapsed into episodes before matching,
so cluster feeds are scored as single feeding events. See
feedcast/research/feed_clustering/research.md for the boundary rule.

This keeps the metric diagnosable while still producing one headline score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from feedcast.clustering import FeedEpisode, group_into_episodes
from feedcast.data import FeedEvent, ForecastPoint, HORIZON_HOURS

DEFAULT_HORIZON_WEIGHT_HALF_LIFE_HOURS = 24.0
DEFAULT_TIMING_CREDIT_HALF_LIFE_MINUTES = 30.0
DEFAULT_MAX_MATCH_GAP_HOURS = 4.0
_INVALID_MATCH_COST = 1.0


@dataclass(frozen=True)
class ScoringConfig:
    """Configuration for retrospective forecast scoring."""

    horizon_hours: float = float(HORIZON_HOURS)
    horizon_weight_half_life_hours: float = DEFAULT_HORIZON_WEIGHT_HALF_LIFE_HOURS
    timing_credit_half_life_minutes: float = DEFAULT_TIMING_CREDIT_HALF_LIFE_MINUTES
    max_match_gap_hours: float = DEFAULT_MAX_MATCH_GAP_HOURS


@dataclass(frozen=True)
class ForecastScore:
    """Normalized score for one forecast over one observed window.

    Counts reflect episodes (after cluster collapsing), not raw feeds.

    Attributes:
        score: Headline 0-100 score from the geometric mean of count and timing.
        count_score: 0-100 weighted F1 over episode count in the observed window.
        timing_score: 0-100 weighted timing credit on matched episodes only.
        observed_horizon_hours: How much of the 24-hour horizon was observable.
        coverage_ratio: Observed-horizon fraction in [0, 1].
        predicted_episode_count: Predicted episodes inside the observed window.
        actual_episode_count: Actual episodes observed inside the same window.
        matched_episode_count: One-to-one matches accepted by the assignment step.
    """

    score: float
    count_score: float
    timing_score: float
    observed_horizon_hours: float
    coverage_ratio: float
    predicted_episode_count: int
    actual_episode_count: int
    matched_episode_count: int


@dataclass(frozen=True)
class _MatchedPair:
    """Internal representation of one accepted predicted/actual pairing."""

    predicted_index: int
    actual_index: int
    predicted_weight: float
    actual_weight: float
    error_minutes: float
    timing_credit: float


def score_forecast(
    predicted_points: Sequence[ForecastPoint],
    actual_events: Sequence[FeedEvent],
    prediction_time: datetime,
    observed_until: datetime,
    config: ScoringConfig = ScoringConfig(),
) -> ForecastScore:
    """Score a forecast against newly observed bottle feeds.

    Both actuals and predictions are collapsed into episodes before
    matching. Actuals are grouped with pre-cutoff context so that
    post-cutoff attachment feeds attach to their pre-cutoff anchors;
    episodes whose canonical timestamp precedes the cutoff are then
    excluded. Predictions are windowed first (they should not exist
    before cutoff) and then grouped.

    Args:
        predicted_points: Forecast points emitted at ``prediction_time``.
        actual_events: Actual bottle-feed events from the next dataset.
            May include events before ``prediction_time`` for grouping
            context.
        prediction_time: Cutoff timestamp used when the forecast was generated.
        observed_until: Latest timestamp covered by the new dataset.
        config: Scoring constants.

    Returns:
        A normalized score bundle over the observed portion of the horizon.

    Raises:
        ValueError: If the observed window is empty or the config is invalid.
    """
    _validate_config(config)

    observed_horizon_hours = _observed_horizon_hours(
        prediction_time=prediction_time,
        observed_until=observed_until,
        horizon_hours=config.horizon_hours,
    )
    if observed_horizon_hours <= 0:
        raise ValueError("Scoring requires an observed window after prediction_time.")

    evaluation_end = prediction_time + timedelta(hours=observed_horizon_hours)

    # Actuals: group with pre-cutoff context so cross-cutoff attachments
    # attach to their anchors, then keep only episodes in the scoring window.
    actual_episodes = [
        episode
        for episode in group_into_episodes(list(actual_events))
        if prediction_time < episode.time <= evaluation_end
    ]

    # Predictions: window first (predictions should not exist before cutoff),
    # then group so that clustered predictions collapse before matching.
    predicted_window = [
        point
        for point in predicted_points
        if prediction_time < point.time <= evaluation_end
    ]
    predicted_episodes = group_into_episodes(predicted_window)

    predicted_weights = [
        _horizon_weight(
            hours_from_prediction=(episode.time - prediction_time).total_seconds()
            / 3600.0,
            half_life_hours=config.horizon_weight_half_life_hours,
        )
        for episode in predicted_episodes
    ]
    actual_weights = [
        _horizon_weight(
            hours_from_prediction=(episode.time - prediction_time).total_seconds()
            / 3600.0,
            half_life_hours=config.horizon_weight_half_life_hours,
        )
        for episode in actual_episodes
    ]

    matched_pairs = _match_episodes(
        predicted_episodes=predicted_episodes,
        actual_episodes=actual_episodes,
        predicted_weights=predicted_weights,
        actual_weights=actual_weights,
        config=config,
    )

    count_score = _count_score(
        matched_pairs=matched_pairs,
        predicted_weights=predicted_weights,
        actual_weights=actual_weights,
    )
    timing_score = _timing_score(
        matched_pairs=matched_pairs,
        predicted_weights=predicted_weights,
        actual_weights=actual_weights,
    )
    headline_score = math.sqrt(count_score * timing_score) * 100.0

    return ForecastScore(
        score=round(headline_score, 3),
        count_score=round(count_score * 100.0, 3),
        timing_score=round(timing_score * 100.0, 3),
        observed_horizon_hours=round(observed_horizon_hours, 3),
        coverage_ratio=round(observed_horizon_hours / config.horizon_hours, 6),
        predicted_episode_count=len(predicted_episodes),
        actual_episode_count=len(actual_episodes),
        matched_episode_count=len(matched_pairs),
    )


def _match_episodes(
    predicted_episodes: Sequence[FeedEpisode],
    actual_episodes: Sequence[FeedEpisode],
    predicted_weights: Sequence[float],
    actual_weights: Sequence[float],
    config: ScoringConfig,
) -> list[_MatchedPair]:
    """Return the best one-to-one episode matches with optional unmatched events.

    The padded assignment matrix lets either side choose a zero-value dummy
    partner instead of being forced into a bad real-world match.
    """
    predicted_count = len(predicted_episodes)
    actual_count = len(actual_episodes)
    total_size = predicted_count + actual_count

    if total_size == 0:
        return []

    cost = np.zeros((total_size, total_size), dtype=float)
    if predicted_count and actual_count:
        cost[:predicted_count, :actual_count] = _INVALID_MATCH_COST

    pair_details: dict[tuple[int, int], _MatchedPair] = {}
    for predicted_index, predicted in enumerate(predicted_episodes):
        for actual_index, actual in enumerate(actual_episodes):
            error_minutes = abs(
                (predicted.time - actual.time).total_seconds()
            ) / 60.0
            error_hours = error_minutes / 60.0
            if error_hours > config.max_match_gap_hours:
                continue

            timing_credit = _timing_credit(
                error_minutes=error_minutes,
                half_life_minutes=config.timing_credit_half_life_minutes,
            )
            # The assignment should protect early-horizon matches when pairings
            # conflict, because the final metric values those episodes more highly.
            pair_credit = (
                (predicted_weights[predicted_index] + actual_weights[actual_index])
                / 2.0
            ) * timing_credit
            cost[predicted_index, actual_index] = -pair_credit
            pair_details[(predicted_index, actual_index)] = _MatchedPair(
                predicted_index=predicted_index,
                actual_index=actual_index,
                predicted_weight=predicted_weights[predicted_index],
                actual_weight=actual_weights[actual_index],
                error_minutes=error_minutes,
                timing_credit=timing_credit,
            )

    row_indices, column_indices = linear_sum_assignment(cost)
    matches: list[_MatchedPair] = []
    for row_index, column_index in zip(row_indices, column_indices):
        if row_index >= predicted_count or column_index >= actual_count:
            continue
        match = pair_details.get((row_index, column_index))
        if match is not None:
            matches.append(match)

    return matches


def _count_score(
    matched_pairs: Sequence[_MatchedPair],
    predicted_weights: Sequence[float],
    actual_weights: Sequence[float],
) -> float:
    """Return weighted F1 over predicted-vs-actual feed presence."""
    matched_predicted_weight = sum(match.predicted_weight for match in matched_pairs)
    matched_actual_weight = sum(match.actual_weight for match in matched_pairs)
    total_predicted_weight = sum(predicted_weights)
    total_actual_weight = sum(actual_weights)

    precision = (
        matched_predicted_weight / total_predicted_weight
        if total_predicted_weight > 0
        else 1.0
    )
    recall = (
        matched_actual_weight / total_actual_weight if total_actual_weight > 0 else 1.0
    )
    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _timing_score(
    matched_pairs: Sequence[_MatchedPair],
    predicted_weights: Sequence[float],
    actual_weights: Sequence[float],
) -> float:
    """Return weighted timing credit across matched feeds only."""
    if matched_pairs:
        matched_actual_weight = sum(match.actual_weight for match in matched_pairs)
        timing_credit = sum(
            match.actual_weight * match.timing_credit for match in matched_pairs
        )
        return timing_credit / matched_actual_weight

    if not predicted_weights and not actual_weights:
        return 1.0
    return 0.0


def _observed_horizon_hours(
    prediction_time: datetime,
    observed_until: datetime,
    horizon_hours: float,
) -> float:
    """Clamp the observed window to the forecast horizon."""
    return max(
        0.0,
        min(
            horizon_hours,
            (observed_until - prediction_time).total_seconds() / 3600.0,
        ),
    )


def _horizon_weight(hours_from_prediction: float, half_life_hours: float) -> float:
    """Return exponential horizon weight for one event."""
    return 2.0 ** (-hours_from_prediction / half_life_hours)


def _timing_credit(error_minutes: float, half_life_minutes: float) -> float:
    """Return soft timing credit for one matched pair."""
    return 2.0 ** (-error_minutes / half_life_minutes)


def _validate_config(config: ScoringConfig) -> None:
    """Fail fast on invalid scoring configuration."""
    if config.horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive.")
    if config.horizon_weight_half_life_hours <= 0:
        raise ValueError("horizon_weight_half_life_hours must be positive.")
    if config.timing_credit_half_life_minutes <= 0:
        raise ValueError("timing_credit_half_life_minutes must be positive.")
    if config.max_match_gap_hours <= 0:
        raise ValueError("max_match_gap_hours must be positive.")
