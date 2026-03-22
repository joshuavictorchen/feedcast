"""Forecasting and backtesting pipeline for Silas feeding data.

The project intentionally stays small:

- load the newest Nara export
- clamp history to a fixed floor date
- run several forecast models
- backtest them at every bottle-feed cutoff
- select a headliner model for the current forecast

The code is organized around plain dataclasses and functions so future
claodex sessions can add or replace models without rewriting the harness.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import numpy as np

ML_TO_FLOZ = 0.033814
DATA_FLOOR = datetime(2026, 3, 15)
HORIZON_HOURS = 24
DISPLAY_DAYS = 7

SNACK_THRESHOLD_OZ = 1.5
MIN_INTERVAL_HOURS = 1.5
MAX_INTERVAL_HOURS = 6.0
MIN_POINT_GAP_MINUTES = 45
CONSENSUS_MATCH_WINDOW_MINUTES = 90

DEFAULT_BREASTFEED_OZ_PER_30_MIN = 0.5
DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES = 45

RECENT_LOOKBACK_DAYS = 3
TREND_SHORT_LOOKBACK_DAYS = 5
TREND_LONG_LOOKBACK_DAYS = 7
DAILY_SHIFT_LOOKBACK_DAYS = 5

RECENT_HALF_LIFE_HOURS = 36
TREND_HALF_LIFE_HOURS = 48
DAILY_SHIFT_HALF_LIFE_DAYS = 2
RECENT_PERFORMANCE_HOURS = 48

UNMATCHED_PENALTY_MINUTES = 180.0


class ForecastUnavailable(RuntimeError):
    """Raised when a model cannot produce a forecast for a cutoff."""


@dataclass(frozen=True)
class Activity:
    """A parsed activity from the export."""

    kind: str
    start: datetime
    end: datetime
    volume_oz: float


@dataclass(frozen=True)
class ExportSnapshot:
    """The newest export and the activities extracted from it."""

    export_path: Path
    activities: list[Activity]
    latest_activity_time: datetime


@dataclass(frozen=True)
class FeedEvent:
    """A bottle-centered event used by forecasting models."""

    time: datetime
    volume_oz: float
    bottle_volume_oz: float
    breastfeeding_volume_oz: float


@dataclass(frozen=True)
class ForecastPoint:
    """A single forecasted feed."""

    time: datetime
    volume_oz: float
    low_volume_oz: float
    high_volume_oz: float
    gap_hours: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "time": self.time.isoformat(),
            "volume_oz": round(self.volume_oz, 3),
            "low_volume_oz": round(self.low_volume_oz, 3),
            "high_volume_oz": round(self.high_volume_oz, 3),
            "gap_hours": round(self.gap_hours, 3),
        }


@dataclass
class ForecastResult:
    """Forecast output for a single model."""

    points: list[ForecastPoint]
    notes: list[str]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class ModelDefinition:
    """A forecast model plus its data interpretation choices."""

    slug: str
    title: str
    description: str
    merge_window_minutes: int | None
    forecast_fn: Callable[[list[FeedEvent], datetime, int], ForecastResult]
    notes: list[str]


@dataclass(frozen=True)
class BacktestCase:
    """Evaluation for a single cutoff."""

    cutoff: datetime
    observed_horizon_hours: float
    predicted_count: int
    actual_count: int
    first_predicted_time: datetime | None
    first_actual_time: datetime | None
    first_feed_error_minutes: float | None
    timing_mae_minutes: float | None
    volume_mae_oz: float | None
    unmatched_predicted: int
    unmatched_actual: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "cutoff": self.cutoff.isoformat(),
            "observed_horizon_hours": round(self.observed_horizon_hours, 3),
            "predicted_count": self.predicted_count,
            "actual_count": self.actual_count,
            "first_predicted_time": (
                None if self.first_predicted_time is None else self.first_predicted_time.isoformat()
            ),
            "first_actual_time": None if self.first_actual_time is None else self.first_actual_time.isoformat(),
            "first_feed_error_minutes": _round_or_none(self.first_feed_error_minutes),
            "timing_mae_minutes": _round_or_none(self.timing_mae_minutes),
            "volume_mae_oz": _round_or_none(self.volume_mae_oz),
            "unmatched_predicted": self.unmatched_predicted,
            "unmatched_actual": self.unmatched_actual,
        }


@dataclass(frozen=True)
class BacktestSummary:
    """Aggregate model performance."""

    total_cutoffs: int
    first_feed_cases: int
    full_horizon_cases: int
    mean_first_feed_error_minutes: float | None
    median_first_feed_error_minutes: float | None
    recent_first_feed_error_minutes: float | None
    mean_timing_mae_minutes: float | None
    mean_volume_mae_oz: float | None
    mean_unmatched_predicted: float | None
    mean_unmatched_actual: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "total_cutoffs": self.total_cutoffs,
            "first_feed_cases": self.first_feed_cases,
            "full_horizon_cases": self.full_horizon_cases,
            "mean_first_feed_error_minutes": _round_or_none(self.mean_first_feed_error_minutes),
            "median_first_feed_error_minutes": _round_or_none(self.median_first_feed_error_minutes),
            "recent_first_feed_error_minutes": _round_or_none(self.recent_first_feed_error_minutes),
            "mean_timing_mae_minutes": _round_or_none(self.mean_timing_mae_minutes),
            "mean_volume_mae_oz": _round_or_none(self.mean_volume_mae_oz),
            "mean_unmatched_predicted": _round_or_none(self.mean_unmatched_predicted),
            "mean_unmatched_actual": _round_or_none(self.mean_unmatched_actual),
        }


@dataclass(frozen=True)
class ModelRun:
    """Current forecast and historical evaluation for one model."""

    definition: ModelDefinition
    events: list[FeedEvent]
    forecast: ForecastResult
    backtest_cases: list[BacktestCase]
    backtest_summary: BacktestSummary


@dataclass(frozen=True)
class PipelineResult:
    """End-to-end output for a single run."""

    run_id: str
    generated_at: datetime
    snapshot: ExportSnapshot
    analysis_time: datetime
    model_runs: list[ModelRun]
    headliner_slug: str

    @property
    def headliner(self) -> ModelRun:
        """Return the selected headliner model run."""
        for model_run in self.model_runs:
            if model_run.definition.slug == self.headliner_slug:
                return model_run
        raise KeyError(f"Unknown headliner: {self.headliner_slug}")


def run_forecasting_pipeline(
    exports_dir: Path = Path("exports"),
    analysis_time: datetime | None = None,
    export_path: Path | None = None,
) -> PipelineResult:
    """Run the full forecast and backtest pipeline.

    Args:
        exports_dir: Directory containing Nara exports.
        analysis_time: Optional explicit forecast cutoff for the current run.
        export_path: Optional explicit export path. Defaults to newest export.

    Returns:
        A fully populated pipeline result.
    """
    snapshot = load_export_snapshot(exports_dir=exports_dir, export_path=export_path)
    effective_analysis_time = analysis_time or snapshot.latest_activity_time

    if effective_analysis_time < DATA_FLOOR:
        raise ForecastUnavailable("Analysis time is earlier than the data floor.")

    model_runs: list[ModelRun] = []
    for definition in build_model_definitions():
        events = build_feed_events(
            snapshot.activities,
            merge_window_minutes=definition.merge_window_minutes,
        )
        history = [event for event in events if event.time <= effective_analysis_time]
        forecast = definition.forecast_fn(history, effective_analysis_time, HORIZON_HOURS)
        backtest_cases = backtest_model(events, definition, effective_analysis_time)
        summary = summarize_backtests(backtest_cases, effective_analysis_time)
        model_runs.append(
            ModelRun(
                definition=definition,
                events=events,
                forecast=forecast,
                backtest_cases=backtest_cases,
                backtest_summary=summary,
            )
        )

    generated_at = datetime.now()
    run_id = generated_at.strftime("%Y%m%d-%H%M%S")
    headliner_slug = select_headliner_slug(model_runs)
    return PipelineResult(
        run_id=run_id,
        generated_at=generated_at,
        snapshot=snapshot,
        analysis_time=effective_analysis_time,
        model_runs=model_runs,
        headliner_slug=headliner_slug,
    )


def load_export_snapshot(
    exports_dir: Path = Path("exports"),
    export_path: Path | None = None,
) -> ExportSnapshot:
    """Load the newest export and parse its relevant activities.

    Args:
        exports_dir: Directory containing export CSV files.
        export_path: Optional explicit export path.

    Returns:
        The parsed export snapshot.
    """
    path = export_path or find_latest_export(exports_dir)
    activities: list[Activity] = []
    latest_activity_time: datetime | None = None

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_start = row["Start Date/time"].strip()
            if not raw_start:
                continue

            start = datetime.strptime(raw_start, "%Y-%m-%d %H:%M:%S")
            if start < DATA_FLOOR:
                continue

            if row["Type"] == "Bottle Feed":
                volume_oz = parse_bottle_volume_oz(row)
                if volume_oz <= 0:
                    continue
                activities.append(
                    Activity(
                        kind="bottle",
                        start=start,
                        end=start,
                        volume_oz=volume_oz,
                    )
                )
                latest_activity_time = start if latest_activity_time is None else max(latest_activity_time, start)
                continue

            if row["Type"] == "Breastfeed":
                left_seconds = int(row["[Breastfeed] Left Duration (Seconds)"] or 0)
                right_seconds = int(row["[Breastfeed] Right Duration (Seconds)"] or 0)
                duration_seconds = left_seconds + right_seconds
                if duration_seconds <= 0:
                    continue
                end = start + timedelta(seconds=duration_seconds)
                activities.append(
                    Activity(
                        kind="breastfeed",
                        start=start,
                        end=end,
                        volume_oz=DEFAULT_BREASTFEED_OZ_PER_30_MIN * (duration_seconds / 1800),
                    )
                )
                latest_activity_time = end if latest_activity_time is None else max(latest_activity_time, end)

    if latest_activity_time is None:
        raise ForecastUnavailable(f"No timestamped activity found in {path}.")

    activities.sort(key=lambda activity: activity.start)
    return ExportSnapshot(
        export_path=path,
        activities=activities,
        latest_activity_time=latest_activity_time,
    )


def build_model_definitions() -> list[ModelDefinition]:
    """Return the current model lineup."""
    return [
        ModelDefinition(
            slug="recent_cadence",
            title="Recent Cadence",
            description="Recency-weighted recent intervals with time-of-day volume bins.",
            merge_window_minutes=None,
            forecast_fn=forecast_recent_cadence,
            notes=[
                "Bottle-only baseline.",
                "Uses the last few days of full feeds to estimate the next gap.",
            ],
        ),
        ModelDefinition(
            slug="trend_hybrid",
            title="Trend Hybrid",
            description="Weighted linear trend on intervals plus time-of-day volume profile.",
            merge_window_minutes=None,
            forecast_fn=forecast_trend_hybrid,
            notes=[
                "Bottle-only baseline.",
                "Closest descendant of the original one-off script.",
            ],
        ),
        ModelDefinition(
            slug="daily_shift",
            title="Daily Shift",
            description="Per-day feed-slot template that projects the schedule forward day by day.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_daily_shift,
            notes=[
                "Adds estimated breastfeeding volume to the next bottle when that bottle starts within 45 minutes.",
                "Treats ~feeds-per-day and daily schedule drift as first-class signals.",
            ],
        ),
        ModelDefinition(
            slug="consensus_blend",
            title="Consensus Blend",
            description="Median-timestamp blend of the three base forecasting styles.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_consensus_blend,
            notes=[
                "Uses the same breastfeeding starting heuristic as Daily Shift.",
                "Consensus model built from the three base forecasting styles.",
            ],
        ),
    ]


def backtest_model(
    events: list[FeedEvent],
    definition: ModelDefinition,
    analysis_time: datetime,
) -> list[BacktestCase]:
    """Backtest a model at every bottle-feed cutoff.

    Args:
        events: Model-specific event history.
        definition: The model to evaluate.
        analysis_time: The current run's forecast cutoff.

    Returns:
        A list of backtest cases ordered by cutoff time.
    """
    if not events:
        return []

    last_event_time = events[-1].time
    cases: list[BacktestCase] = []
    cutoffs = [event.time for event in events if event.time < analysis_time]

    for cutoff in cutoffs:
        history = [event for event in events if event.time <= cutoff]
        future = [event for event in events if event.time > cutoff]
        if not future:
            continue

        observed_horizon_hours = min(
            HORIZON_HOURS,
            (last_event_time - cutoff).total_seconds() / 3600,
        )
        if observed_horizon_hours <= 0:
            continue

        actual_future = [
            event for event in future if event.time <= cutoff + timedelta(hours=HORIZON_HOURS)
        ]

        try:
            forecast = definition.forecast_fn(history, cutoff, HORIZON_HOURS)
        except ForecastUnavailable:
            continue

        first_predicted_time = forecast.points[0].time if forecast.points else None
        first_actual_time = actual_future[0].time if actual_future else None
        first_feed_error_minutes = None
        if first_predicted_time is not None and first_actual_time is not None:
            first_feed_error_minutes = abs(
                (first_predicted_time - first_actual_time).total_seconds()
            ) / 60

        timing_mae_minutes, volume_mae_oz, unmatched_predicted, unmatched_actual = align_forecast_to_actual(
            forecast.points,
            actual_future,
        )
        cases.append(
            BacktestCase(
                cutoff=cutoff,
                observed_horizon_hours=observed_horizon_hours,
                predicted_count=len(forecast.points),
                actual_count=len(actual_future),
                first_predicted_time=first_predicted_time,
                first_actual_time=first_actual_time,
                first_feed_error_minutes=first_feed_error_minutes,
                timing_mae_minutes=timing_mae_minutes,
                volume_mae_oz=volume_mae_oz,
                unmatched_predicted=unmatched_predicted,
                unmatched_actual=unmatched_actual,
            )
        )

    return cases


def summarize_backtests(
    cases: list[BacktestCase],
    analysis_time: datetime,
) -> BacktestSummary:
    """Aggregate backtest cases into report-friendly metrics."""
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
        case.timing_mae_minutes for case in full_horizon_cases if case.timing_mae_minutes is not None
    ]
    volume_errors = [
        case.volume_mae_oz for case in full_horizon_cases if case.volume_mae_oz is not None
    ]
    unmatched_predicted = [
        case.unmatched_predicted for case in full_horizon_cases
    ]
    unmatched_actual = [
        case.unmatched_actual for case in full_horizon_cases
    ]

    return BacktestSummary(
        total_cutoffs=len(cases),
        first_feed_cases=len(first_feed_errors),
        full_horizon_cases=len(full_horizon_cases),
        mean_first_feed_error_minutes=_mean_or_none(first_feed_errors),
        median_first_feed_error_minutes=_median_or_none(first_feed_errors),
        recent_first_feed_error_minutes=_mean_or_none(recent_first_feed_errors),
        mean_timing_mae_minutes=_mean_or_none(timing_errors),
        mean_volume_mae_oz=_mean_or_none(volume_errors),
        mean_unmatched_predicted=_mean_or_none(unmatched_predicted),
        mean_unmatched_actual=_mean_or_none(unmatched_actual),
    )


def select_headliner_slug(model_runs: list[ModelRun]) -> str:
    """Choose the model to feature in the summary report.

    The current forecast matters more than model competition, so the
    selector prefers recent first-feed accuracy before broader averages.
    """
    if not model_runs:
        raise ForecastUnavailable("No model runs available.")

    def sort_key(model_run: ModelRun) -> tuple[float, float, float, str]:
        summary = model_run.backtest_summary
        recent_first = _sortable_metric(summary.recent_first_feed_error_minutes)
        overall_first = _sortable_metric(summary.mean_first_feed_error_minutes)
        volume = _sortable_metric(summary.mean_volume_mae_oz)
        return (recent_first, overall_first, volume, model_run.definition.slug)

    return min(model_runs, key=sort_key).definition.slug


def forecast_recent_cadence(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Project feeds using recent intervals and time-of-day volume bins."""
    if len(history) < 4:
        raise ForecastUnavailable("Recent Cadence needs at least four events.")

    recent_start = cutoff - timedelta(days=RECENT_LOOKBACK_DAYS)
    recent_events = [event for event in history if recent_start <= event.time <= cutoff]
    full_events = [event for event in recent_events if event.volume_oz >= SNACK_THRESHOLD_OZ]
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
    daily_count_weights = day_weights(sorted(daily_counts.keys()), cutoff.date(), DAILY_SHIFT_HALF_LIFE_DAYS)
    average_feeds_per_day = float(
        np.average(
            [daily_counts[date] for date in sorted(daily_counts)],
            weights=daily_count_weights,
        )
    )
    target_interval = 24 / np.clip(average_feeds_per_day, 6.5, 10.5)
    blended_interval = np.clip((0.7 * weighted_interval) + (0.3 * target_interval), MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS)

    volume_profile = build_volume_profile(
        recent_events,
        cutoff=cutoff,
        lookback_days=TREND_LONG_LOOKBACK_DAYS,
        half_life_hours=RECENT_HALF_LIFE_HOURS,
    )
    points = roll_forward_constant_interval(
        history=history,
        cutoff=cutoff,
        horizon_hours=horizon_hours,
        interval_hours=blended_interval,
        volume_profile=volume_profile,
        label_interval_hours=blended_interval,
    )
    return ForecastResult(
        points=points,
        notes=[
            f"Uses {len(full_events)} full recent feeds over the last {RECENT_LOOKBACK_DAYS} days.",
            f"Blends recent interval ({weighted_interval:.2f}h) with recent feeds/day prior ({target_interval:.2f}h).",
        ],
        diagnostics={
            "recent_full_feeds": len(full_events),
            "weighted_interval_hours": round(weighted_interval, 3),
            "average_feeds_per_day": round(average_feeds_per_day, 3),
            "blended_interval_hours": round(blended_interval, 3),
        },
    )


def forecast_trend_hybrid(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Project feeds with interval and volume trends."""
    if len(history) < 5:
        raise ForecastUnavailable("Trend Hybrid needs at least five events.")

    short_start = cutoff - timedelta(days=TREND_SHORT_LOOKBACK_DAYS)
    long_start = cutoff - timedelta(days=TREND_LONG_LOOKBACK_DAYS)
    short_events = [event for event in history if short_start <= event.time <= cutoff]
    long_events = [event for event in history if long_start <= event.time <= cutoff]
    full_events = [event for event in short_events if event.volume_oz >= SNACK_THRESHOLD_OZ]

    if len(full_events) < 3 or len(long_events) < 4:
        raise ForecastUnavailable("Trend Hybrid needs more recent history.")

    interval_values: list[float] = []
    interval_times: list[datetime] = []
    for previous, current in zip(full_events, full_events[1:]):
        interval_values.append((current.time - previous.time).total_seconds() / 3600)
        interval_times.append(previous.time + (current.time - previous.time) / 2)

    interval_weights = exp_weights(interval_times, cutoff, TREND_HALF_LIFE_HOURS)
    x_interval = np.array([(timestamp - cutoff).total_seconds() / 3600 for timestamp in interval_times])
    slope_interval, intercept_interval = weighted_linregress(
        x_interval,
        np.array(interval_values),
        interval_weights,
    )
    current_interval = float(np.clip(intercept_interval, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS))
    interval_variability = weighted_std(np.array(interval_values), interval_weights)

    volume_times = [event.time for event in long_events]
    volume_values = np.array([event.volume_oz for event in long_events])
    volume_weights = exp_weights(volume_times, cutoff, TREND_HALF_LIFE_HOURS)
    x_volume = np.array([(timestamp - cutoff).total_seconds() / 3600 for timestamp in volume_times])
    slope_volume, intercept_volume = weighted_linregress(x_volume, volume_values, volume_weights)

    volume_profile = build_volume_profile(
        long_events,
        cutoff=cutoff,
        lookback_days=TREND_LONG_LOOKBACK_DAYS,
        half_life_hours=TREND_HALF_LIFE_HOURS,
    )

    last_time = history[-1].time
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []
    current_time = last_time
    while True:
        hours_from_cutoff = max((current_time - cutoff).total_seconds() / 3600, 0.0)
        projected_interval = np.clip(
            current_interval + (slope_interval * hours_from_cutoff),
            MIN_INTERVAL_HOURS,
            MAX_INTERVAL_HOURS,
        )
        current_time = current_time + timedelta(hours=projected_interval)
        if current_time >= end:
            break

        base_volume, volume_std = lookup_volume_profile(volume_profile, current_time)
        trended_volume = np.clip(
            base_volume + (slope_volume * (current_time - cutoff).total_seconds() / 3600),
            0.5,
            8.0,
        )
        points.append(
            ForecastPoint(
                time=current_time,
                volume_oz=trended_volume,
                low_volume_oz=max(0.1, trended_volume - max(volume_std, 0.35)),
                high_volume_oz=trended_volume + max(volume_std, 0.35),
                gap_hours=projected_interval,
            )
        )

    points = normalize_forecast_points(points, cutoff, horizon_hours)
    return ForecastResult(
        points=points,
        notes=[
            f"Interval trend fit over {len(full_events)} full feeds from the last {TREND_SHORT_LOOKBACK_DAYS} days.",
            f"Volume trend fit over {len(long_events)} feeds from the last {TREND_LONG_LOOKBACK_DAYS} days.",
        ],
        diagnostics={
            "current_interval_hours": round(current_interval, 3),
            "interval_slope_hours_per_hour": round(float(slope_interval), 5),
            "interval_variability_hours": round(interval_variability, 3),
            "current_volume_oz": round(float(intercept_volume), 3),
            "volume_slope_oz_per_hour": round(float(slope_volume), 5),
        },
    )


def forecast_daily_shift(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Project feeds as a drifting daily schedule with roughly stable slot order."""
    if len(history) < 8:
        raise ForecastUnavailable("Daily Shift needs at least eight events.")

    day_groups = group_events_by_date(history)
    candidate_days = [
        date
        for date in sorted(day_groups)
        if date < cutoff.date() and date >= (cutoff.date() - timedelta(days=DAILY_SHIFT_LOOKBACK_DAYS))
    ]
    if len(candidate_days) < 3:
        raise ForecastUnavailable("Daily Shift needs at least three completed recent days.")

    full_day_events = {
        date: [event for event in day_groups[date] if event.volume_oz >= SNACK_THRESHOLD_OZ]
        for date in candidate_days
    }
    usable_days = [date for date in candidate_days if len(full_day_events[date]) >= 6]
    if len(usable_days) < 3:
        raise ForecastUnavailable("Daily Shift needs at least three usable recent full-feed days.")

    counts = {date: len(full_day_events[date]) for date in usable_days}
    count_weights = day_weights(usable_days, cutoff.date(), DAILY_SHIFT_HALF_LIFE_DAYS)
    target_feed_count = int(
        round(
            np.clip(
                np.average([counts[date] for date in usable_days], weights=count_weights),
                6,
                10,
            )
        )
    )

    slot_hours_by_day: list[np.ndarray] = []
    slot_volumes_by_day: list[np.ndarray] = []
    for date in usable_days:
        events = full_day_events[date]
        slot_hours_by_day.append(
            resample_sequence([hour_of_day(event.time) for event in events], target_feed_count)
        )
        slot_volumes_by_day.append(
            resample_sequence([event.volume_oz for event in events], target_feed_count)
        )

    x_days = np.array([(date - cutoff.date()).days for date in usable_days], dtype=float)
    slot_predictions_today: list[float] = []
    slot_predictions_tomorrow: list[float] = []
    slot_volumes: list[tuple[float, float]] = []
    slot_slopes: list[float] = []

    for slot_index in range(target_feed_count):
        y_hours = np.array([slot_hours[slot_index] for slot_hours in slot_hours_by_day])
        y_volumes = np.array([slot_volumes[slot_index] for slot_volumes in slot_volumes_by_day])
        slope_hours, intercept_hours = weighted_linregress(x_days, y_hours, count_weights)
        slope_volume, intercept_volume = weighted_linregress(x_days, y_volumes, count_weights)
        slot_predictions_today.append(float(intercept_hours))
        slot_predictions_tomorrow.append(float(intercept_hours + slope_hours))
        slot_slopes.append(float(slope_hours))
        slot_volume_std = weighted_std(y_volumes, count_weights)
        slot_volumes.append((float(intercept_volume), slot_volume_std))

    slot_predictions_today = normalize_day_hours(slot_predictions_today)
    slot_predictions_tomorrow = normalize_day_hours(slot_predictions_tomorrow)

    points: list[ForecastPoint] = []
    for day_offset, slot_hours in enumerate([slot_predictions_today, slot_predictions_tomorrow]):
        target_date = cutoff.date() + timedelta(days=day_offset)
        for slot_index, predicted_hour in enumerate(slot_hours):
            predicted_time = datetime.combine(target_date, datetime.min.time()) + timedelta(hours=predicted_hour)
            if predicted_time <= cutoff:
                continue
            if predicted_time >= cutoff + timedelta(hours=horizon_hours):
                continue

            volume_oz, volume_std = slot_volumes[slot_index]
            previous_time = points[-1].time if points else history[-1].time
            gap_hours = max((predicted_time - previous_time).total_seconds() / 3600, MIN_INTERVAL_HOURS)
            points.append(
                ForecastPoint(
                    time=predicted_time,
                    volume_oz=np.clip(volume_oz, 0.5, 8.0),
                    low_volume_oz=max(0.1, volume_oz - max(volume_std, 0.35)),
                    high_volume_oz=volume_oz + max(volume_std, 0.35),
                    gap_hours=gap_hours,
                )
            )

    points = normalize_forecast_points(points, cutoff, horizon_hours)
    average_shift_minutes_per_day = float(np.mean(slot_slopes) * 60) if slot_slopes else 0.0
    return ForecastResult(
        points=points,
        notes=[
            f"Uses {len(usable_days)} completed days and projects about {target_feed_count} feeds per day.",
            f"Average day-to-day slot drift: {average_shift_minutes_per_day:+.0f} minutes.",
        ],
        diagnostics={
            "usable_days": len(usable_days),
            "target_feeds_per_day": target_feed_count,
            "average_shift_minutes_per_day": round(average_shift_minutes_per_day, 2),
        },
    )


def forecast_consensus_blend(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Blend the base forecasting styles into a consensus forecast."""
    component_functions = [
        ("recent_cadence", forecast_recent_cadence),
        ("trend_hybrid", forecast_trend_hybrid),
        ("daily_shift", forecast_daily_shift),
    ]
    component_results: dict[str, ForecastResult] = {}
    unavailable_components: dict[str, str] = {}
    for slug, component in component_functions:
        try:
            component_results[slug] = component(history, cutoff, horizon_hours)
        except ForecastUnavailable as error:
            unavailable_components[slug] = str(error)

    if len(component_results) < 2:
        raise ForecastUnavailable(
            "Consensus Blend needs at least two component forecasts at this cutoff."
        )

    points, skipped_outliers = blend_consensus_points_by_time(component_results, history, cutoff, horizon_hours)

    points = normalize_forecast_points(points, cutoff, horizon_hours)
    return ForecastResult(
        points=points,
        notes=[
            "Blends whichever base models are available at the cutoff instead of failing when one drops out.",
            "Groups component predictions by time proximity, not raw forecast index.",
        ],
        diagnostics={
            "component_models": list(component_results.keys()),
            "component_forecast_counts": {
                slug: len(result.points) for slug, result in component_results.items()
            },
            "unavailable_components": unavailable_components,
            "skipped_outlier_points": skipped_outliers,
        },
    )


def blend_consensus_points_by_time(
    component_results: dict[str, ForecastResult],
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> tuple[list[ForecastPoint], int]:
    """Blend component forecasts using time-based grouping.

    The blend should not assume every component emits the same number of
    feeds or that feed index N refers to the same real-world event across
    models. Instead, it clusters the next unconsumed points from each
    component by time proximity and blends only points that plausibly
    refer to the same upcoming feed.
    """
    component_indices = {slug: 0 for slug in component_results}
    points: list[ForecastPoint] = []
    skipped_outliers = 0
    match_window = timedelta(minutes=CONSENSUS_MATCH_WINDOW_MINUTES)

    while True:
        next_candidates = [
            (slug, result.points[component_indices[slug]])
            for slug, result in component_results.items()
            if component_indices[slug] < len(result.points)
        ]
        if len(next_candidates) < 2:
            break

        candidate_timestamps = np.array([point.time.timestamp() for _, point in next_candidates], dtype=float)
        anchor_time = datetime.fromtimestamp(float(np.median(candidate_timestamps)))
        cluster_start = anchor_time - match_window
        cluster_end = anchor_time + match_window

        leading_outliers = [
            slug
            for slug, point in next_candidates
            if point.time < cluster_start
        ]
        for slug in leading_outliers:
            component_indices[slug] += 1
            skipped_outliers += 1

        if leading_outliers:
            continue

        cluster = [
            (slug, point)
            for slug, point in next_candidates
            if cluster_start <= point.time <= cluster_end
        ]
        if len(cluster) < 2:
            earliest_slug = min(next_candidates, key=lambda item: item[1].time)[0]
            component_indices[earliest_slug] += 1
            skipped_outliers += 1
            continue

        timestamp_values = np.array([point.time.timestamp() for _, point in cluster], dtype=float)
        consensus_time = datetime.fromtimestamp(float(np.median(timestamp_values)))
        volume_values = np.array([point.volume_oz for _, point in cluster], dtype=float)
        low_values = np.array([point.low_volume_oz for _, point in cluster], dtype=float)
        high_values = np.array([point.high_volume_oz for _, point in cluster], dtype=float)
        previous_time = points[-1].time if points else history[-1].time
        gap_hours = max((consensus_time - previous_time).total_seconds() / 3600, MIN_INTERVAL_HOURS)
        points.append(
            ForecastPoint(
                time=consensus_time,
                volume_oz=float(np.mean(volume_values)),
                low_volume_oz=float(np.mean(low_values)),
                high_volume_oz=float(np.mean(high_values)),
                gap_hours=gap_hours,
            )
        )
        for slug, _ in cluster:
            component_indices[slug] += 1

    return normalize_forecast_points(points, cutoff, horizon_hours), skipped_outliers


def find_latest_export(exports_dir: Path) -> Path:
    """Return the newest export based on the filename date."""
    pattern = re.compile(r"export_narababy_silas_(\d{8})\.csv$")
    candidates: list[tuple[str, Path]] = []
    for path in exports_dir.glob("export_narababy_silas_*.csv"):
        match = pattern.match(path.name)
        if not match:
            continue
        candidates.append((match.group(1), path))

    if not candidates:
        raise FileNotFoundError(f"No matching exports found in {exports_dir}.")

    _, latest_path = max(candidates, key=lambda item: item[0])
    return latest_path


def parse_bottle_volume_oz(row: dict[str, str]) -> float:
    """Parse bottle volume from a Nara export row."""
    total_floz = 0.0
    for volume_key, unit_key in [
        ("[Bottle Feed] Breast Milk Volume", "[Bottle Feed] Breast Milk Volume Unit"),
        ("[Bottle Feed] Formula Volume", "[Bottle Feed] Formula Volume Unit"),
    ]:
        raw = row[volume_key].strip()
        if not raw:
            continue
        volume = float(raw)
        unit = row[unit_key].strip()
        total_floz += volume * ML_TO_FLOZ if unit == "ML" else volume

    if total_floz > 0:
        return total_floz

    raw_total = row["[Bottle Feed] Volume"].strip()
    if not raw_total:
        return 0.0
    volume = float(raw_total)
    unit = row["[Bottle Feed] Volume Unit"].strip()
    return volume * ML_TO_FLOZ if unit == "ML" else volume


def build_feed_events(
    activities: list[Activity],
    merge_window_minutes: int | None,
) -> list[FeedEvent]:
    """Construct bottle-centered events from raw activities.

    Breastfeeding is an optional volume adjustment only. All event times
    remain anchored on the logged bottle start time so model timing targets
    stay directly comparable.
    """
    bottles = [activity for activity in activities if activity.kind == "bottle"]
    breastfeeds = [activity for activity in activities if activity.kind == "breastfeed"]
    events: list[FeedEvent] = []
    breastfeed_index = 0

    for bottle in bottles:
        breastfeeding_volume_oz = 0.0
        if merge_window_minutes is not None:
            while breastfeed_index < len(breastfeeds) and breastfeeds[breastfeed_index].end <= bottle.start:
                breastfeed = breastfeeds[breastfeed_index]
                gap_minutes = (bottle.start - breastfeed.end).total_seconds() / 60
                if gap_minutes <= merge_window_minutes:
                    breastfeeding_volume_oz += breastfeed.volume_oz
                breastfeed_index += 1

        events.append(
            FeedEvent(
                time=bottle.start,
                volume_oz=bottle.volume_oz + breastfeeding_volume_oz,
                bottle_volume_oz=bottle.volume_oz,
                breastfeeding_volume_oz=breastfeeding_volume_oz,
            )
        )

    return events


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

    values = np.array([event.volume_oz for event in recent_events])
    weights = exp_weights([event.time for event in recent_events], cutoff, half_life_hours)
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


def roll_forward_constant_interval(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
    interval_hours: float,
    volume_profile: dict[str, Any],
    label_interval_hours: float,
) -> list[ForecastPoint]:
    """Roll forward a constant-gap forecast."""
    last_time = history[-1].time
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []
    current_time = last_time
    while True:
        current_time = current_time + timedelta(hours=interval_hours)
        if current_time >= end:
            break

        volume_oz, volume_std = lookup_volume_profile(volume_profile, current_time)
        points.append(
            ForecastPoint(
                time=current_time,
                volume_oz=volume_oz,
                low_volume_oz=max(0.1, volume_oz - max(volume_std, 0.35)),
                high_volume_oz=volume_oz + max(volume_std, 0.35),
                gap_hours=label_interval_hours,
            )
        )

    return normalize_forecast_points(points, cutoff, horizon_hours)


def lookup_volume_profile(volume_profile: dict[str, Any], target_time: datetime) -> tuple[float, float]:
    """Look up average and variability for a forecast time."""
    bins = volume_profile["bins"]
    index = min(int((hour_of_day(target_time) / 24) * bins), bins - 1)
    return (
        float(volume_profile["average_by_bin"][index]),
        float(volume_profile["std_by_bin"][index]),
    )


def align_forecast_to_actual(
    predicted: list[ForecastPoint],
    actual: list[FeedEvent],
) -> tuple[float | None, float | None, int, int]:
    """Align two ordered feed sequences with an order-preserving DP."""
    if not predicted and not actual:
        return None, None, 0, 0

    predicted_count = len(predicted)
    actual_count = len(actual)
    dp = np.full((predicted_count + 1, actual_count + 1), np.inf)
    step = np.empty((predicted_count + 1, actual_count + 1), dtype=object)
    dp[0, 0] = 0.0

    for i in range(predicted_count + 1):
        for j in range(actual_count + 1):
            base_cost = dp[i, j]
            if np.isinf(base_cost):
                continue

            if i < predicted_count and j < actual_count:
                match_cost = abs((predicted[i].time - actual[j].time).total_seconds()) / 60
                if base_cost + match_cost < dp[i + 1, j + 1]:
                    dp[i + 1, j + 1] = base_cost + match_cost
                    step[i + 1, j + 1] = "match"

            if i < predicted_count:
                if base_cost + UNMATCHED_PENALTY_MINUTES < dp[i + 1, j]:
                    dp[i + 1, j] = base_cost + UNMATCHED_PENALTY_MINUTES
                    step[i + 1, j] = "skip_predicted"

            if j < actual_count:
                if base_cost + UNMATCHED_PENALTY_MINUTES < dp[i, j + 1]:
                    dp[i, j + 1] = base_cost + UNMATCHED_PENALTY_MINUTES
                    step[i, j + 1] = "skip_actual"

    i = predicted_count
    j = actual_count
    matched_time_errors: list[float] = []
    matched_volume_errors: list[float] = []
    unmatched_predicted = 0
    unmatched_actual = 0

    while i > 0 or j > 0:
        action = step[i, j]
        if action == "match":
            matched_time_errors.append(abs((predicted[i - 1].time - actual[j - 1].time).total_seconds()) / 60)
            matched_volume_errors.append(abs(predicted[i - 1].volume_oz - actual[j - 1].volume_oz))
            i -= 1
            j -= 1
            continue
        if action == "skip_predicted":
            unmatched_predicted += 1
            i -= 1
            continue
        if action == "skip_actual":
            unmatched_actual += 1
            j -= 1
            continue
        break

    matched_time_errors.reverse()
    matched_volume_errors.reverse()
    timing_mae = _mean_or_none(matched_time_errors)
    volume_mae = _mean_or_none(matched_volume_errors)
    return timing_mae, volume_mae, unmatched_predicted, unmatched_actual


def normalize_forecast_points(
    points: list[ForecastPoint],
    cutoff: datetime,
    horizon_hours: int,
) -> list[ForecastPoint]:
    """Clamp forecast points to a clean, ordered next-24h schedule."""
    normalized: list[ForecastPoint] = []
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    for point in sorted(points, key=lambda item: item.time):
        if point.time <= cutoff or point.time >= horizon_end:
            continue

        adjusted_time = point.time
        if normalized:
            minimum_time = normalized[-1].time + timedelta(minutes=MIN_POINT_GAP_MINUTES)
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
                low_volume_oz=float(max(0.1, min(point.low_volume_oz, point.volume_oz))),
                high_volume_oz=float(max(point.volume_oz, point.high_volume_oz)),
                gap_hours=float(max(gap_hours, 0.1)),
            )
        )

    return normalized


def group_events_by_date(events: list[FeedEvent]) -> dict[datetime.date, list[FeedEvent]]:
    """Group events by local calendar day."""
    grouped: dict[datetime.date, list[FeedEvent]] = {}
    for event in events:
        grouped.setdefault(event.time.date(), []).append(event)
    return grouped


def daily_feed_counts(events: list[FeedEvent]) -> dict[datetime.date, int]:
    """Count feeds per day."""
    counts: dict[datetime.date, int] = {}
    for event in events:
        counts[event.time.date()] = counts.get(event.time.date(), 0) + 1
    return counts


def resample_sequence(values: list[float], target_count: int) -> np.ndarray:
    """Resample a day sequence to a fixed number of slots."""
    if len(values) == target_count:
        return np.array(values, dtype=float)
    if len(values) == 1:
        return np.repeat(values[0], target_count)

    source_positions = np.linspace(0.0, 1.0, num=len(values))
    target_positions = np.linspace(0.0, 1.0, num=target_count)
    return np.interp(target_positions, source_positions, values)


def normalize_day_hours(hours: list[float]) -> list[float]:
    """Ensure daily slot predictions stay ordered within the day."""
    clipped = list(np.clip(hours, 0.25, 23.75))
    ordered: list[float] = []
    for hour in clipped:
        if not ordered:
            ordered.append(hour)
            continue
        ordered.append(max(hour, ordered[-1] + 1.0))
    return list(np.clip(ordered, 0.25, 23.75))


def exp_weights(
    timestamps: list[datetime],
    now: datetime,
    half_life_hours: float,
) -> np.ndarray:
    """Return exponential recency weights."""
    decay = np.log(2) / half_life_hours
    ages_hours = np.array([(now - timestamp).total_seconds() / 3600 for timestamp in timestamps], dtype=float)
    return np.exp(-decay * ages_hours)


def day_weights(
    dates: list[datetime.date],
    reference_date: datetime.date,
    half_life_days: float,
) -> np.ndarray:
    """Return exponential day-level recency weights."""
    decay = np.log(2) / half_life_days
    ages_days = np.array([(reference_date - date).days for date in dates], dtype=float)
    return np.exp(-decay * ages_days)


def weighted_linregress(x: np.ndarray, y: np.ndarray, weights: np.ndarray) -> tuple[float, float]:
    """Run a weighted least-squares regression.

    Args:
        x: Independent variable values.
        y: Dependent variable values.
        weights: Non-negative sample weights.

    Returns:
        slope, intercept
    """
    normalized_weights = weights / np.sum(weights)
    x_mean = np.average(x, weights=normalized_weights)
    y_mean = np.average(y, weights=normalized_weights)
    variance = np.average((x - x_mean) ** 2, weights=normalized_weights)
    if variance < 1e-10:
        return 0.0, float(y_mean)
    covariance = np.average((x - x_mean) * (y - y_mean), weights=normalized_weights)
    slope = covariance / variance
    intercept = y_mean - (slope * x_mean)
    return float(slope), float(intercept)


def weighted_std(values: np.ndarray, weights: np.ndarray) -> float:
    """Return a weighted standard deviation."""
    if len(values) == 1:
        return 0.0
    normalized_weights = weights / np.sum(weights)
    mean_value = np.average(values, weights=normalized_weights)
    variance = np.average((values - mean_value) ** 2, weights=normalized_weights)
    return float(np.sqrt(max(variance, 0.0)))


def hour_of_day(timestamp: datetime) -> float:
    """Return decimal hour-of-day."""
    return timestamp.hour + (timestamp.minute / 60) + (timestamp.second / 3600)


def _mean_or_none(values: list[float | int]) -> float | None:
    """Return the arithmetic mean or None for empty input."""
    if not values:
        return None
    return float(np.mean(values))


def _median_or_none(values: list[float | int]) -> float | None:
    """Return the median or None for empty input."""
    if not values:
        return None
    return float(np.median(values))


def _sortable_metric(value: float | None) -> float:
    """Convert None to infinity for ordering."""
    return float("inf") if value is None else value


def _round_or_none(value: float | None) -> float | None:
    """Round a float if present."""
    if value is None:
        return None
    return round(value, 3)
