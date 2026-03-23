"""Current-export temporal backtesting for scripted forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from data import (
    Activity,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    FeedEvent,
    Forecast,
    ForecastPoint,
    HORIZON_HOURS,
)
from models import (
    CONSENSUS_BLEND_NAME,
    CONSENSUS_BLEND_SLUG,
    MODELS,
    ModelFn,
    build_event_cache,
    run_all_models_from_cache,
    run_consensus_blend,
)
from models.shared import ForecastUnavailable

RECENT_PERFORMANCE_HOURS = 48
UNMATCHED_PENALTY_MINUTES = 180.0


@dataclass(frozen=True)
class BacktestCase:
    """One historical cutoff evaluation."""

    cutoff: datetime
    observed_horizon_hours: float
    predicted_count: int
    actual_count: int
    first_predicted_time: datetime | None
    first_actual_time: datetime | None
    first_feed_error_minutes: float | None
    timing_mae_minutes: float | None


@dataclass(frozen=True)
class BacktestSummary:
    """Aggregate backtest metrics for one forecast source."""

    potential_cutoffs: int
    total_cutoffs: int
    cutoff_coverage_ratio: float
    mean_first_feed_error_minutes: float | None
    recent_first_feed_error_minutes: float | None
    mean_timing_mae_minutes: float | None


@dataclass(frozen=True)
class ModelBacktest:
    """Backtest output for one scripted model or consensus blend."""

    name: str
    slug: str
    cases: list[BacktestCase]
    summary: BacktestSummary


def run_backtests(
    activities: list[Activity],
    analysis_time: datetime,
    horizon_hours: int = HORIZON_HOURS,
) -> list[ModelBacktest]:
    """Run current-export backtests for scripted models and consensus.

    Args:
        activities: Parsed activities from the selected export.
        analysis_time: Current forecast cutoff.
        horizon_hours: Backtest horizon in hours.

    Returns:
        Backtest results for the three scripted models plus consensus.
    """
    event_cache = build_event_cache(activities)
    results: list[ModelBacktest] = []

    for spec in MODELS:
        events = event_cache[spec.merge_window_minutes]
        cases = backtest_model(events, spec.forecast_fn, analysis_time, horizon_hours)
        results.append(
            ModelBacktest(
                name=spec.name,
                slug=spec.slug,
                cases=cases,
                summary=summarize_backtests(
                    cases,
                    analysis_time,
                    potential_cutoffs=len(
                        [event for event in events if event.time < analysis_time]
                    ),
                ),
            )
        )

    consensus_events = event_cache[DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES]
    consensus_cases = backtest_consensus(
        event_cache,
        consensus_events,
        analysis_time,
        horizon_hours,
    )
    results.append(
        ModelBacktest(
            name=CONSENSUS_BLEND_NAME,
            slug=CONSENSUS_BLEND_SLUG,
            cases=consensus_cases,
            summary=summarize_backtests(
                consensus_cases,
                analysis_time,
                potential_cutoffs=len(
                    [event for event in consensus_events if event.time < analysis_time]
                ),
            ),
        )
    )
    return results


def backtest_model(
    events: list[FeedEvent],
    forecast_fn: ModelFn,
    analysis_time: datetime,
    horizon_hours: int = HORIZON_HOURS,
) -> list[BacktestCase]:
    """Backtest one model at every historical cutoff.

    Args:
        events: Model-specific event history.
        forecast_fn: Forecast function to evaluate.
        analysis_time: Current forecast cutoff.
        horizon_hours: Backtest horizon in hours.

    Returns:
        Historical backtest cases ordered by cutoff.
    """
    if not events:
        return []

    last_event_time = events[-1].time
    cutoffs = [event.time for event in events if event.time < analysis_time]
    cases: list[BacktestCase] = []

    for cutoff in cutoffs:
        history = [event for event in events if event.time <= cutoff]
        future = [event for event in events if event.time > cutoff]
        if not future:
            continue

        observed_horizon_hours = min(
            horizon_hours,
            (last_event_time - cutoff).total_seconds() / 3600,
        )
        if observed_horizon_hours <= 0:
            continue

        actual_future = [
            event
            for event in future
            if event.time <= cutoff + timedelta(hours=horizon_hours)
        ]

        try:
            forecast = forecast_fn(history, cutoff, horizon_hours)
        except ForecastUnavailable:
            continue
        if not forecast.available:
            continue

        cases.append(
            _build_backtest_case(
                forecast=forecast,
                actual_future=actual_future,
                cutoff=cutoff,
                observed_horizon_hours=observed_horizon_hours,
            )
        )

    return cases


def backtest_consensus(
    event_cache: dict[int | None, list[FeedEvent]],
    events: list[FeedEvent],
    analysis_time: datetime,
    horizon_hours: int = HORIZON_HOURS,
) -> list[BacktestCase]:
    """Backtest the scripted consensus blend at every historical cutoff."""
    if not events:
        return []

    last_event_time = events[-1].time
    cutoffs = [event.time for event in events if event.time < analysis_time]
    cases: list[BacktestCase] = []

    for cutoff in cutoffs:
        future = [event for event in events if event.time > cutoff]
        if not future:
            continue

        observed_horizon_hours = min(
            horizon_hours,
            (last_event_time - cutoff).total_seconds() / 3600,
        )
        if observed_horizon_hours <= 0:
            continue

        base_forecasts = run_all_models_from_cache(event_cache, cutoff, horizon_hours)
        history = [event for event in events if event.time <= cutoff]
        forecast = run_consensus_blend(base_forecasts, history, cutoff, horizon_hours)
        if not forecast.available:
            continue

        actual_future = [
            event
            for event in future
            if event.time <= cutoff + timedelta(hours=horizon_hours)
        ]
        cases.append(
            _build_backtest_case(
                forecast=forecast,
                actual_future=actual_future,
                cutoff=cutoff,
                observed_horizon_hours=observed_horizon_hours,
            )
        )

    return cases


def summarize_backtests(
    cases: list[BacktestCase],
    analysis_time: datetime,
    potential_cutoffs: int,
) -> BacktestSummary:
    """Aggregate backtest cases into report-friendly metrics.

    Args:
        cases: Historical backtest cases for one forecast source.
        analysis_time: Current run cutoff used to define the recent window.
        potential_cutoffs: Number of historical cutoffs that were eligible.

    Returns:
        Summary metrics for ranking and reporting.
    """
    first_feed_errors = [
        case.first_feed_error_minutes
        for case in cases
        if case.first_feed_error_minutes is not None
    ]
    recent_cutoff = analysis_time - timedelta(hours=RECENT_PERFORMANCE_HOURS)
    recent_first_feed_errors = [
        case.first_feed_error_minutes
        for case in cases
        if case.cutoff >= recent_cutoff and case.first_feed_error_minutes is not None
    ]
    full_horizon_cases = [
        case for case in cases if case.observed_horizon_hours >= HORIZON_HOURS
    ]
    timing_errors = [
        case.timing_mae_minutes
        for case in full_horizon_cases
        if case.timing_mae_minutes is not None
    ]
    coverage_ratio = (len(cases) / potential_cutoffs) if potential_cutoffs else 0.0

    return BacktestSummary(
        potential_cutoffs=potential_cutoffs,
        total_cutoffs=len(cases),
        cutoff_coverage_ratio=coverage_ratio,
        mean_first_feed_error_minutes=_mean_or_none(first_feed_errors),
        recent_first_feed_error_minutes=_mean_or_none(recent_first_feed_errors),
        mean_timing_mae_minutes=_mean_or_none(timing_errors),
    )


def align_forecast_to_actual(
    predicted: list[ForecastPoint],
    actual: list[FeedEvent],
) -> tuple[float | None, int, int]:
    """Align two ordered feed sequences with an order-preserving DP.

    Args:
        predicted: Forecasted feed points.
        actual: Actual bottle-centered feed events.

    Returns:
        Tuple of timing MAE, unmatched predicted count, unmatched actual count.
    """
    if not predicted and not actual:
        return None, 0, 0

    predicted_count = len(predicted)
    actual_count = len(actual)
    dp = np.full((predicted_count + 1, actual_count + 1), np.inf)
    step = np.empty((predicted_count + 1, actual_count + 1), dtype=object)
    dp[0, 0] = 0.0

    for predicted_index in range(predicted_count + 1):
        for actual_index in range(actual_count + 1):
            base_cost = dp[predicted_index, actual_index]
            if np.isinf(base_cost):
                continue

            if predicted_index < predicted_count and actual_index < actual_count:
                match_cost = (
                    abs(
                        (
                            predicted[predicted_index].time - actual[actual_index].time
                        ).total_seconds()
                    )
                    / 60
                )
                if base_cost + match_cost < dp[predicted_index + 1, actual_index + 1]:
                    dp[predicted_index + 1, actual_index + 1] = base_cost + match_cost
                    step[predicted_index + 1, actual_index + 1] = "match"

            if predicted_index < predicted_count:
                skip_predicted_cost = base_cost + UNMATCHED_PENALTY_MINUTES
                if skip_predicted_cost < dp[predicted_index + 1, actual_index]:
                    dp[predicted_index + 1, actual_index] = skip_predicted_cost
                    step[predicted_index + 1, actual_index] = "skip_predicted"

            if actual_index < actual_count:
                skip_actual_cost = base_cost + UNMATCHED_PENALTY_MINUTES
                if skip_actual_cost < dp[predicted_index, actual_index + 1]:
                    dp[predicted_index, actual_index + 1] = skip_actual_cost
                    step[predicted_index, actual_index + 1] = "skip_actual"

    predicted_index = predicted_count
    actual_index = actual_count
    matched_time_errors: list[float] = []
    unmatched_predicted = 0
    unmatched_actual = 0

    while predicted_index > 0 or actual_index > 0:
        action = step[predicted_index, actual_index]
        if action == "match":
            matched_time_errors.append(
                abs(
                    (
                        predicted[predicted_index - 1].time
                        - actual[actual_index - 1].time
                    ).total_seconds()
                )
                / 60
            )
            predicted_index -= 1
            actual_index -= 1
            continue
        if action == "skip_predicted":
            unmatched_predicted += 1
            predicted_index -= 1
            continue
        if action == "skip_actual":
            unmatched_actual += 1
            actual_index -= 1
            continue
        break

    matched_time_errors.reverse()
    return _mean_or_none(matched_time_errors), unmatched_predicted, unmatched_actual


def availability_adjusted_first_feed_error(summary: BacktestSummary) -> float:
    """Return recent first-feed error with a penalty for low cutoff coverage.

    Args:
        summary: Summary metrics for one forecast source.

    Returns:
        Ranking metric that penalizes models which fail on too many cutoffs.
    """
    recent_first = _sortable_metric(summary.recent_first_feed_error_minutes)
    coverage_shortfall = max(0.0, 0.75 - summary.cutoff_coverage_ratio)
    coverage_penalty = 40.0 * (coverage_shortfall / 0.75)
    return recent_first + coverage_penalty


def rank_backtests(backtests: list[ModelBacktest]) -> list[str]:
    """Return backtest slugs ordered from best to worst.

    Args:
        backtests: Current-export backtest results.

    Returns:
        Best-to-worst scripted ranking for featured-forecast fallback.
    """

    def sort_key(result: ModelBacktest) -> tuple[float, float, float, str]:
        summary = result.summary
        recent_first = availability_adjusted_first_feed_error(summary)
        timing_24h = _sortable_metric(summary.mean_timing_mae_minutes)
        overall_first = _sortable_metric(summary.mean_first_feed_error_minutes)
        return (recent_first, timing_24h, overall_first, result.slug)

    return [result.slug for result in sorted(backtests, key=sort_key)]


def _build_backtest_case(
    forecast: Forecast,
    actual_future: list[FeedEvent],
    cutoff: datetime,
    observed_horizon_hours: float,
) -> BacktestCase:
    """Build one backtest case from a forecast and future actuals.

    Args:
        forecast: Forecast emitted from a historical cutoff.
        actual_future: Actual events observed after that cutoff.
        cutoff: Historical cutoff being evaluated.
        observed_horizon_hours: How much future truth is actually available.

    Returns:
        Backtest case with first-feed and sequence-alignment metrics.
    """
    first_predicted_time = forecast.points[0].time if forecast.points else None
    first_actual_time = actual_future[0].time if actual_future else None
    first_feed_error_minutes = None
    if first_predicted_time is not None and first_actual_time is not None:
        first_feed_error_minutes = (
            abs((first_predicted_time - first_actual_time).total_seconds()) / 60
        )

    timing_mae_minutes, _, _ = align_forecast_to_actual(forecast.points, actual_future)
    return BacktestCase(
        cutoff=cutoff,
        observed_horizon_hours=observed_horizon_hours,
        predicted_count=len(forecast.points),
        actual_count=len(actual_future),
        first_predicted_time=first_predicted_time,
        first_actual_time=first_actual_time,
        first_feed_error_minutes=first_feed_error_minutes,
        timing_mae_minutes=timing_mae_minutes,
    )


def _mean_or_none(values: list[float | int]) -> float | None:
    """Return the arithmetic mean or None for empty input."""
    if not values:
        return None
    return float(np.mean(values))


def _sortable_metric(value: float | None) -> float:
    """Convert None to infinity for ordering."""
    return float("inf") if value is None else value
