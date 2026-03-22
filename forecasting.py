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
from scipy.stats import weibull_min

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

PHASE_LOCKED_FILTER_BETA = 0.05
PHASE_LOCKED_VOLUME_GAIN = 0.5
PHASE_LOCKED_MEAN_REVERSION = 0.2
PHASE_NOWCAST_BLEND_PHASE_WEIGHT = 0.4
PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS = 0.5

TEMPLATE_WINDOW_EVENTS = 4
TEMPLATE_NEIGHBORS = 3
TEMPLATE_GAP_SCALE = 1.0
TEMPLATE_VOLUME_SCALE = 0.5
TEMPLATE_HOUR_SCALE = 3.0
TEMPLATE_RECENCY_PENALTY = 0.05

DAILY_SHIFT_MIN_COMPLETE_DAYS = 3
DAILY_SHIFT_MIN_FEEDS_PER_DAY = 6
DAILY_SHIFT_SCALE_MIN = 0.65
DAILY_SHIFT_SCALE_MAX = 1.35

GAP_CONDITIONAL_LOOKBACK_DAYS = 5
GAP_CONDITIONAL_HALF_LIFE_HOURS = 36
STATE_GAP_MIN_EVENTS = 8
STATE_GAP_MIN_TRAINING_EXAMPLES = 6

SURVIVAL_LOOKBACK_DAYS = 5
SURVIVAL_NIGHT_START = 21  # 9 PM
SURVIVAL_NIGHT_END = 6     # 6 AM

GBM_LOOKBACK_DAYS = 7
GBM_N_ESTIMATORS = 30
GBM_MAX_DEPTH = 2
GBM_LEARNING_RATE = 0.1

# Satiety Decay: models hunger as linearly increasing over time,
# reset proportionally by feed volume. A feed happens when hunger
# exceeds a threshold.
SATIETY_LOOKBACK_DAYS = 5
SATIETY_HALF_LIFE_HOURS = 36

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

    potential_cutoffs: int
    total_cutoffs: int
    first_feed_cases: int
    full_horizon_cases: int
    cutoff_coverage_ratio: float
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
            "potential_cutoffs": self.potential_cutoffs,
            "total_cutoffs": self.total_cutoffs,
            "first_feed_cases": self.first_feed_cases,
            "full_horizon_cases": self.full_horizon_cases,
            "cutoff_coverage_ratio": _round_or_none(self.cutoff_coverage_ratio),
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
        potential_cutoffs = len([event for event in events if event.time < effective_analysis_time])
        summary = summarize_backtests(
            backtest_cases,
            effective_analysis_time,
            potential_cutoffs=potential_cutoffs,
        )
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
            slug="phase_locked_oscillator",
            title="Phase-Locked Oscillator",
            description="Recursive state-space timing model with volume-driven phase shifts.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_phase_locked_oscillator,
            notes=[
                "Uses a lightweight recursive phase filter instead of raw recent-gap averaging.",
                "Adjusts the next gap based on how large the latest feed was relative to the running volume baseline.",
            ],
        ),
        ModelDefinition(
            slug="phase_nowcast_hybrid",
            title="Phase Nowcast Hybrid",
            description="Agreement-gated first-feed blend of the phase model and a local event-state nowcast.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_phase_nowcast_hybrid,
            notes=[
                "Uses the phase model as the backbone for the full 24-hour schedule.",
                "Only blends in the local nowcast when both models already agree within 30 minutes and the latest event is not a snack.",
            ],
        ),
        ModelDefinition(
            slug="template_match",
            title="Template Match",
            description="Nearest-neighbor analog forecast using recent gap, volume, and time-of-day patterns.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_template_match,
            notes=[
                "Searches historical analog windows instead of fitting a single global trend.",
                "Weights the next sequence by similarity to the recent multi-feed pattern.",
            ],
        ),
        ModelDefinition(
            slug="daily_shift",
            title="Daily Shift",
            description="Gap-slot daily template with explicit today-to-tomorrow transition handling.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_daily_shift,
            notes=[
                "Adds estimated breastfeeding volume to the next bottle when that bottle starts within 45 minutes.",
                "Matches today's observed cadence to recent day templates before rolling the schedule forward.",
            ],
        ),
        ModelDefinition(
            slug="gap_conditional",
            title="Gap-Conditional",
            description="Predicts each gap from the latest event state: last volume, recent gaps, and hour-of-day cycle.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_gap_conditional,
            notes=[
                "Directly exploits the volume→gap signal (bigger feed → longer gap).",
                "Fits on recent bottle events directly instead of training on full feeds and patching snacks at inference time.",
            ],
        ),
        ModelDefinition(
            slug="survival_weibull",
            title="Survival (Weibull)",
            description="Models time-to-next-feed as a Weibull distribution conditioned on time of day.",
            merge_window_minutes=None,
            forecast_fn=forecast_survival_weibull,
            notes=[
                "Bottle-only. Fits a Weibull distribution to recent inter-feed intervals.",
                "Conditions the scale parameter on day/night regime and last feed volume.",
            ],
        ),
        ModelDefinition(
            slug="gradient_boosted",
            title="Gradient Boosted",
            description="Gradient boosted regression on per-feed features: volume, hour, rolling stats.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_gradient_boosted,
            notes=[
                "Uses sklearn GradientBoostingRegressor with conservative hyperparameters.",
                "Features: last_volume, hour_of_day, rolling_3_avg_gap, rolling_3_avg_vol, feeds_today.",
            ],
        ),
        ModelDefinition(
            slug="satiety_decay",
            title="Satiety Decay",
            description="Physiological model: hunger accumulates over time, feeds reset it proportional to volume.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_satiety_decay,
            notes=[
                "Models hunger as a linear ramp that resets on each feed.",
                "Bigger feeds provide more satiety → longer gap before hunger threshold is reached again.",
                "Naturally handles snacks: a small feed only partially resets the hunger clock.",
            ],
        ),
        ModelDefinition(
            slug="consensus_blend",
            title="Consensus Blend",
            description="Median-timestamp blend of all available forecasting styles.",
            merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
            forecast_fn=forecast_consensus_blend,
            notes=[
                "Uses the same breastfeeding starting heuristic as the breastfeed-aware timing models.",
                "Consensus model built from all available base models at each cutoff.",
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
    potential_cutoffs: int,
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
    coverage_ratio = (len(cases) / potential_cutoffs) if potential_cutoffs else 0.0

    return BacktestSummary(
        potential_cutoffs=potential_cutoffs,
        total_cutoffs=len(cases),
        first_feed_cases=len(first_feed_errors),
        full_horizon_cases=len(full_horizon_cases),
        cutoff_coverage_ratio=coverage_ratio,
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
    selector prefers recent first-feed accuracy before broader averages,
    but it also penalizes models that fail on too many cutoffs.
    """
    if not model_runs:
        raise ForecastUnavailable("No model runs available.")

    def sort_key(model_run: ModelRun) -> tuple[float, float, float, str]:
        summary = model_run.backtest_summary
        recent_first = availability_adjusted_first_feed_error(summary)
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


def forecast_phase_locked_oscillator(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Project feeds with a recursive phase filter and volume correction."""
    if len(history) < 6:
        raise ForecastUnavailable("Phase-Locked Oscillator needs at least six events.")

    recent_events = history[-min(len(history), 28):]
    target_interval = estimate_target_interval(recent_events, cutoff)
    average_volume = float(np.mean([event.volume_oz for event in recent_events[:3]]))
    period_hours = target_interval

    for previous, current in zip(recent_events, recent_events[1:]):
        predicted_gap = np.clip(
            period_hours + (PHASE_LOCKED_VOLUME_GAIN * (previous.volume_oz - average_volume)),
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
                period_hours + (PHASE_LOCKED_VOLUME_GAIN * (last_volume - average_volume)),
                MIN_INTERVAL_HOURS,
                MAX_INTERVAL_HOURS,
            )
        )
        next_time = last_time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, volume_std = lookup_volume_profile(volume_profile, next_time)
        predicted_volume = np.clip((0.65 * base_volume) + (0.35 * average_volume), 0.5, 8.0)
        points.append(
            ForecastPoint(
                time=next_time,
                volume_oz=float(predicted_volume),
                low_volume_oz=max(0.1, float(predicted_volume) - max(volume_std, 0.35)),
                high_volume_oz=float(predicted_volume) + max(volume_std, 0.35),
                gap_hours=predicted_gap,
            )
        )

        last_time = next_time
        last_volume = float(predicted_volume)
        average_volume = (0.7 * average_volume) + (0.3 * predicted_volume)
        period_hours = float(
            np.clip(
                ((1 - PHASE_LOCKED_MEAN_REVERSION) * period_hours)
                + (PHASE_LOCKED_MEAN_REVERSION * target_interval),
                MIN_INTERVAL_HOURS,
                MAX_INTERVAL_HOURS,
            )
        )

    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"Recursive period estimate starts from a target interval of {target_interval:.2f}h.",
            "After a larger-than-usual feed, the next projected gap lengthens instead of snapping back to the recent mean.",
        ],
        diagnostics={
            "target_interval_hours": round(target_interval, 3),
            "current_period_hours": round(current_period_hours, 3),
            "running_average_volume_oz": round(float(current_average_volume), 3),
            "last_feed_volume_oz": round(float(history[-1].volume_oz), 3),
            "last_volume_delta_oz": round(float(history[-1].volume_oz - current_average_volume), 3),
            "current_volume_adjustment_hours": round(
                float(PHASE_LOCKED_VOLUME_GAIN * (history[-1].volume_oz - current_average_volume)),
                3,
            ),
        },
    )


def forecast_phase_nowcast_hybrid(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Blend phase and local state timing when both agree on the next feed."""
    phase_result = forecast_phase_locked_oscillator(history, cutoff, horizon_hours)
    if not phase_result.points:
        raise ForecastUnavailable("Phase Nowcast Hybrid needs the phase model to emit at least one point.")

    phase_first_gap = (phase_result.points[0].time - history[-1].time).total_seconds() / 3600
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
    gap_difference = None if state_first_gap is None else abs(phase_first_gap - state_first_gap)
    should_blend = (
        state_first_gap is not None
        and not last_event_is_snack
        and gap_difference is not None
        and gap_difference <= PHASE_NOWCAST_AGREEMENT_WINDOW_HOURS
    )

    if should_blend and state_first_gap is not None:
        selected_first_gap = (
            (PHASE_NOWCAST_BLEND_PHASE_WEIGHT * phase_first_gap)
            + ((1 - PHASE_NOWCAST_BLEND_PHASE_WEIGHT) * state_first_gap)
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
    for index, point in enumerate(phase_result.points):
        shifted_time = point.time + timedelta(hours=gap_shift_hours)
        shifted_gap = selected_first_gap if index == 0 else point.gap_hours
        shifted_points.append(
            ForecastPoint(
                time=shifted_time,
                volume_oz=point.volume_oz,
                low_volume_oz=point.low_volume_oz,
                high_volume_oz=point.high_volume_oz,
                gap_hours=shifted_gap,
            )
        )

    notes = list(phase_result.notes)
    notes.append(
        "Blends the first feed only when the phase model and the local event-state nowcast already agree; otherwise it trusts the phase schedule."
    )

    diagnostics = dict(phase_result.diagnostics)
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
        }
    )
    return ForecastResult(
        points=normalize_forecast_points(shifted_points, cutoff, horizon_hours),
        notes=notes,
        diagnostics=diagnostics,
    )


def forecast_template_match(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Project feeds by matching the recent pattern to prior analog windows."""
    if len(history) < TEMPLATE_WINDOW_EVENTS + 3:
        raise ForecastUnavailable("Template Match needs more history.")

    target_window = history[-TEMPLATE_WINDOW_EVENTS:]
    target_features = template_feature_vector(target_window)
    target_scale = np.array(
        ([TEMPLATE_GAP_SCALE] * (TEMPLATE_WINDOW_EVENTS - 1))
        + ([TEMPLATE_VOLUME_SCALE] * TEMPLATE_WINDOW_EVENTS)
        + ([TEMPLATE_HOUR_SCALE] * TEMPLATE_WINDOW_EVENTS),
        dtype=float,
    )

    analogs: list[tuple[float, list[tuple[float, float]]]] = []
    for end_index in range(TEMPLATE_WINDOW_EVENTS, len(history) - 1):
        analog_window = history[end_index - TEMPLATE_WINDOW_EVENTS:end_index]
        analog_features = template_feature_vector(analog_window)
        distance = float(np.linalg.norm((analog_features - target_features) / target_scale))
        recency_age_hours = (history[-1].time - analog_window[-1].time).total_seconds() / 3600
        distance += TEMPLATE_RECENCY_PENALTY * (recency_age_hours / 24)

        analog_points: list[tuple[float, float]] = []
        base_time = analog_window[-1].time
        for future_event in history[end_index:]:
            offset_hours = (future_event.time - base_time).total_seconds() / 3600
            if offset_hours > horizon_hours:
                break
            analog_points.append((offset_hours, future_event.volume_oz))
        if analog_points:
            analogs.append((distance, analog_points))

    if len(analogs) < 2:
        raise ForecastUnavailable("Template Match needs at least two analog windows.")

    analogs.sort(key=lambda item: item[0])
    selected_analogs = analogs[:TEMPLATE_NEIGHBORS]
    distances = np.array([distance for distance, _ in selected_analogs], dtype=float)
    weights = np.exp(-distances)

    max_steps = max(len(points) for _, points in selected_analogs)
    points: list[ForecastPoint] = []
    for step_index in range(max_steps):
        step_offsets: list[float] = []
        step_volumes: list[float] = []
        step_weights: list[float] = []
        for analog_weight, (_, analog_points) in zip(weights, selected_analogs):
            if step_index >= len(analog_points):
                continue
            offset_hours, volume_oz = analog_points[step_index]
            step_offsets.append(offset_hours)
            step_volumes.append(volume_oz)
            step_weights.append(float(analog_weight))
        if len(step_offsets) < 2:
            continue

        offset_hours = float(np.average(step_offsets, weights=step_weights))
        predicted_time = history[-1].time + timedelta(hours=offset_hours)
        if predicted_time >= cutoff + timedelta(hours=horizon_hours):
            break

        predicted_volume = float(np.average(step_volumes, weights=step_weights))
        volume_std = weighted_std(np.array(step_volumes, dtype=float), np.array(step_weights, dtype=float))
        previous_time = points[-1].time if points else history[-1].time
        gap_hours = max((predicted_time - previous_time).total_seconds() / 3600, MIN_INTERVAL_HOURS)
        points.append(
            ForecastPoint(
                time=predicted_time,
                volume_oz=predicted_volume,
                low_volume_oz=max(0.1, predicted_volume - max(volume_std, 0.35)),
                high_volume_oz=predicted_volume + max(volume_std, 0.35),
                gap_hours=gap_hours,
            )
        )

    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"Matches the last {TEMPLATE_WINDOW_EVENTS} feeds against prior analog windows.",
            f"Averages the next sequence from the {TEMPLATE_NEIGHBORS} closest analogs.",
        ],
        diagnostics={
            "window_events": TEMPLATE_WINDOW_EVENTS,
            "neighbor_count": TEMPLATE_NEIGHBORS,
            "best_analog_distances": [round(distance, 3) for distance, _ in selected_analogs],
        },
    )


def forecast_daily_shift(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Project feeds with a daily gap template and explicit overnight gap."""
    if len(history) < 8:
        raise ForecastUnavailable("Daily Shift needs at least eight events.")

    template = build_daily_shift_template(history, cutoff)
    today_events = [event for event in history if event.time.date() == cutoff.date()]
    if not today_events:
        raise ForecastUnavailable("Daily Shift needs at least one event on the cutoff day.")

    current_slot, scale, fit_error = fit_daily_shift_state(
        today_events=today_events,
        slot_hour_template=template["slot_hour_template"],
        intra_gap_template=template["intra_gap_template"],
    )

    points: list[ForecastPoint] = []
    current_time = history[-1].time
    cycle_scale = scale
    current_slot_index = current_slot
    end = cutoff + timedelta(hours=horizon_hours)

    while True:
        if current_slot_index < template["target_feed_count"] - 1:
            next_gap = float(
                np.clip(
                    template["intra_gap_template"][current_slot_index] * cycle_scale,
                    MIN_INTERVAL_HOURS,
                    MAX_INTERVAL_HOURS,
                )
            )
            next_slot_index = current_slot_index + 1
        else:
            next_gap = float(
                np.clip(
                    template["overnight_gap_hours"] * ((0.6 * cycle_scale) + 0.4),
                    MIN_INTERVAL_HOURS,
                    MAX_INTERVAL_HOURS,
                )
            )
            next_slot_index = 0
            cycle_scale = (0.6 * cycle_scale) + 0.4

        next_time = current_time + timedelta(hours=next_gap)
        if next_time >= end:
            break

        volume_oz = float(template["slot_volume_template"][next_slot_index])
        volume_std = float(template["slot_volume_std"][next_slot_index])
        points.append(
            ForecastPoint(
                time=next_time,
                volume_oz=np.clip(volume_oz, 0.5, 8.0),
                low_volume_oz=max(0.1, volume_oz - max(volume_std, 0.35)),
                high_volume_oz=volume_oz + max(volume_std, 0.35),
                gap_hours=next_gap,
            )
        )

        current_time = next_time
        current_slot_index = next_slot_index

    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"Uses {template['usable_days']} recent completed days and about {template['target_feed_count']} feeds per day.",
            "Fits today's observed feed cadence to recent slot gaps, then carries that cadence through the overnight transition.",
        ],
        diagnostics={
            "usable_days": template["usable_days"],
            "target_feeds_per_day": template["target_feed_count"],
            "current_slot_index": current_slot,
            "gap_scale": round(scale, 3),
            "fit_error_hours": round(fit_error, 3),
            "overnight_gap_hours": round(float(template["overnight_gap_hours"]), 3),
        },
    )


def forecast_gap_conditional(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Predict each gap from the latest event state."""
    coefficients, recent, training_examples = fit_state_gap_regression(
        history,
        cutoff,
        lookback_days=GAP_CONDITIONAL_LOOKBACK_DAYS,
    )
    volume_profile = build_volume_profile(
        recent, cutoff=cutoff,
        lookback_days=GAP_CONDITIONAL_LOOKBACK_DAYS,
        half_life_hours=GAP_CONDITIONAL_HALF_LIFE_HOURS,
    )

    simulated_events = list(history)
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []

    while True:
        predicted_gap = predict_state_gap_hours(simulated_events, coefficients)
        next_time = simulated_events[-1].time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, volume_std = lookup_volume_profile(volume_profile, next_time)
        projected_volume = float(np.clip(base_volume, 0.5, 8.0))
        points.append(
            ForecastPoint(
                time=next_time,
                volume_oz=projected_volume,
                low_volume_oz=max(0.1, base_volume - max(volume_std, 0.35)),
                high_volume_oz=base_volume + max(volume_std, 0.35),
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

    vol_coeff = coefficients[1]
    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"Volume coefficient: {vol_coeff:+.2f}h/oz — each extra ounce adds ~{vol_coeff * 60:+.0f} min to the next gap.",
            f"Trained on {training_examples} recent event pairs from the last {GAP_CONDITIONAL_LOOKBACK_DAYS} days.",
        ],
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


def forecast_survival_weibull(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Model time-to-next-feed as a Weibull distribution.

    The Weibull naturally captures that feeding probability increases over
    time since the last feed (increasing hazard rate). The scale parameter
    is conditioned on day/night regime and last feed volume.
    """
    lookback_start = cutoff - timedelta(days=SURVIVAL_LOOKBACK_DAYS)
    recent = [event for event in history if lookback_start <= event.time <= cutoff]
    full_recent = [event for event in recent if event.volume_oz >= SNACK_THRESHOLD_OZ]
    if len(full_recent) < 6:
        raise ForecastUnavailable("Survival model needs at least six recent full feeds.")

    # Compute inter-feed intervals and classify day/night
    intervals: list[float] = []
    is_night: list[bool] = []
    volumes: list[float] = []
    for i in range(len(full_recent) - 1):
        gap = (full_recent[i + 1].time - full_recent[i].time).total_seconds() / 3600
        if gap < 0.5:
            continue
        intervals.append(gap)
        h = hour_of_day(full_recent[i].time)
        is_night.append(h >= SURVIVAL_NIGHT_START or h < SURVIVAL_NIGHT_END)
        volumes.append(full_recent[i].volume_oz)

    if len(intervals) < 4:
        raise ForecastUnavailable("Survival model needs more interval data.")

    interval_array = np.array(intervals)

    # Fit overall Weibull parameters
    # weibull_min.fit returns (shape, loc, scale)
    shape, loc, scale = weibull_min.fit(interval_array, floc=0)

    # Condition scale on day/night and volume:
    # night feeds tend to have longer gaps, bigger feeds → longer gaps
    night_intervals = [g for g, n in zip(intervals, is_night) if n]
    day_intervals = [g for g, n in zip(intervals, is_night) if not n]
    night_scale = float(np.mean(night_intervals)) if len(night_intervals) >= 2 else scale
    day_scale = float(np.mean(day_intervals)) if len(day_intervals) >= 2 else scale

    # Volume adjustment: linear relationship between volume and gap
    if len(volumes) >= 4:
        vol_array = np.array(volumes)
        vol_mean = float(np.mean(vol_array))
        vol_corr = np.corrcoef(vol_array, interval_array[:len(vol_array)])[0, 1]
        # Regression slope of gap on volume
        vol_slope = vol_corr * np.std(interval_array[:len(vol_array)]) / max(np.std(vol_array), 0.1)
    else:
        vol_mean = 3.0
        vol_slope = 0.0

    volume_profile = build_volume_profile(
        recent, cutoff=cutoff,
        lookback_days=SURVIVAL_LOOKBACK_DAYS,
        half_life_hours=RECENT_HALF_LIFE_HOURS,
    )

    # Roll forward: use Weibull mode as point forecast, adjusted for regime
    last_event = history[-1]
    current_time = last_event.time
    current_volume = effective_timing_volume(history)
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []

    while True:
        h = hour_of_day(current_time)
        is_night_now = h >= SURVIVAL_NIGHT_START or h < SURVIVAL_NIGHT_END
        base_scale = night_scale if is_night_now else day_scale

        # Volume adjustment to scale
        volume_adj = float(vol_slope) * (current_volume - vol_mean)
        adjusted_scale = float(np.clip(base_scale + volume_adj, 1.0, 6.0))

        # Weibull mode = scale * ((shape - 1) / shape)^(1/shape) for shape > 1
        if shape > 1:
            mode_gap = adjusted_scale * ((shape - 1) / shape) ** (1 / shape)
        else:
            mode_gap = adjusted_scale * 0.7  # fallback for shape <= 1

        predicted_gap = float(np.clip(mode_gap, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS))
        next_time = current_time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, volume_std = lookup_volume_profile(volume_profile, next_time)
        points.append(ForecastPoint(
            time=next_time,
            volume_oz=float(np.clip(base_volume, 0.5, 8.0)),
            low_volume_oz=max(0.1, base_volume - max(volume_std, 0.35)),
            high_volume_oz=base_volume + max(volume_std, 0.35),
            gap_hours=predicted_gap,
        ))

        current_time = next_time
        current_volume = base_volume

    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"Weibull shape={shape:.2f}, overall scale={scale:.2f}h.",
            f"Day scale={day_scale:.2f}h, night scale={night_scale:.2f}h.",
            f"Volume effect: {vol_slope:+.2f}h per oz above average.",
        ],
        diagnostics={
            "weibull_shape": round(shape, 3),
            "weibull_scale": round(scale, 3),
            "day_scale_hours": round(day_scale, 3),
            "night_scale_hours": round(night_scale, 3),
            "volume_slope_hours_per_oz": round(float(vol_slope), 3),
        },
    )


def forecast_gradient_boosted(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Gradient boosted regression on per-feed features.

    Uses conservative hyperparameters (shallow trees, few estimators) to
    avoid overfitting on the small dataset. Features are designed to capture
    volume→gap, time-of-day, and recent cadence signals simultaneously.
    """
    try:
        from sklearn.ensemble import GradientBoostingRegressor
    except ModuleNotFoundError as error:
        raise ForecastUnavailable("GBM needs scikit-learn installed in the project venv.") from error

    lookback_start = cutoff - timedelta(days=GBM_LOOKBACK_DAYS)
    recent = [event for event in history if lookback_start <= event.time <= cutoff]
    full_recent = [event for event in recent if event.volume_oz >= SNACK_THRESHOLD_OZ]
    if len(full_recent) < 10:
        raise ForecastUnavailable("GBM needs at least 10 recent full feeds.")

    # Build training data: predict gap to next feed from per-feed features
    feature_rows: list[list[float]] = []
    gap_targets: list[float] = []
    for i in range(3, len(full_recent) - 1):
        current = full_recent[i]
        next_event = full_recent[i + 1]
        gap = (next_event.time - current.time).total_seconds() / 3600

        # Features
        prior_gaps = [
            (full_recent[j + 1].time - full_recent[j].time).total_seconds() / 3600
            for j in range(max(0, i - 3), i)
        ]
        prior_vols = [full_recent[j].volume_oz for j in range(max(0, i - 3), i)]

        feature_rows.append([
            current.volume_oz,                              # last feed volume
            hour_of_day(current.time),                      # hour of day
            float(np.mean(prior_gaps)),                     # rolling avg gap
            float(np.mean(prior_vols)),                     # rolling avg volume
            float(np.std(prior_gaps)) if len(prior_gaps) > 1 else 0.0,  # gap variability
            # Is it nighttime?
            1.0 if (hour_of_day(current.time) >= 21 or hour_of_day(current.time) < 6) else 0.0,
        ])
        gap_targets.append(gap)

    if len(gap_targets) < 6:
        raise ForecastUnavailable("GBM needs more training examples.")

    feature_matrix = np.array(feature_rows)
    target_array = np.array(gap_targets)

    # Exponential sample weights so recent data dominates
    timestamps = [full_recent[i].time for i in range(3, len(full_recent) - 1)]
    sample_weights = exp_weights(timestamps, cutoff, GAP_CONDITIONAL_HALF_LIFE_HOURS)

    model = GradientBoostingRegressor(
        n_estimators=GBM_N_ESTIMATORS,
        max_depth=GBM_MAX_DEPTH,
        learning_rate=GBM_LEARNING_RATE,
        subsample=0.8,
        random_state=42,
    )
    model.fit(feature_matrix, target_array, sample_weight=sample_weights)

    volume_profile = build_volume_profile(
        recent, cutoff=cutoff,
        lookback_days=GBM_LOOKBACK_DAYS,
        half_life_hours=RECENT_HALF_LIFE_HOURS,
    )

    # Roll forward — use effective volume to handle snack-at-inference
    last_event = history[-1]
    current_time = last_event.time
    current_volume = effective_timing_volume(history)
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []

    # Initialize rolling stats from the last few full feeds
    recent_gaps = [
        (full_recent[i + 1].time - full_recent[i].time).total_seconds() / 3600
        for i in range(max(0, len(full_recent) - 4), len(full_recent) - 1)
    ]
    recent_vols = [e.volume_oz for e in full_recent[-3:]]

    while True:
        features = np.array([[
            current_volume,
            hour_of_day(current_time),
            float(np.mean(recent_gaps[-3:])) if recent_gaps else 3.0,
            float(np.mean(recent_vols[-3:])) if recent_vols else 3.0,
            float(np.std(recent_gaps[-3:])) if len(recent_gaps) >= 2 else 0.0,
            1.0 if (hour_of_day(current_time) >= 21 or hour_of_day(current_time) < 6) else 0.0,
        ]])
        predicted_gap = float(np.clip(
            model.predict(features)[0],
            MIN_INTERVAL_HOURS,
            MAX_INTERVAL_HOURS,
        ))

        next_time = current_time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, volume_std = lookup_volume_profile(volume_profile, next_time)
        points.append(ForecastPoint(
            time=next_time,
            volume_oz=float(np.clip(base_volume, 0.5, 8.0)),
            low_volume_oz=max(0.1, base_volume - max(volume_std, 0.35)),
            high_volume_oz=base_volume + max(volume_std, 0.35),
            gap_hours=predicted_gap,
        ))

        recent_gaps.append(predicted_gap)
        recent_vols.append(base_volume)
        current_time = next_time
        current_volume = base_volume

    # Feature importances for diagnostics
    feature_names = ["volume", "hour_of_day", "rolling_avg_gap",
                     "rolling_avg_vol", "gap_variability", "is_night"]
    importances = dict(zip(feature_names, [round(float(v), 3) for v in model.feature_importances_]))

    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"GBM trained on {len(gap_targets)} examples with {GBM_N_ESTIMATORS} trees (depth {GBM_MAX_DEPTH}).",
            f"Top feature: {max(importances, key=importances.get)} ({max(importances.values()):.0%} importance).",
        ],
        diagnostics={
            "training_examples": len(gap_targets),
            "feature_importances": importances,
            "train_score": round(float(model.score(feature_matrix, target_array, sample_weight=sample_weights)), 3),
        },
    )


def forecast_satiety_decay(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> ForecastResult:
    """Physiological model: hunger accumulates, feeds provide satiety.

    Core idea: the baby has a "hunger level" that increases at a constant
    rate over time. Each feed resets it downward in proportion to feed
    volume. A feed is predicted when hunger crosses a threshold.

    This naturally handles:
    - Bigger feeds → longer gaps (more satiety to burn through)
    - Snacks → shorter gaps (partial reset)
    - Cluster feeding → hunger stays high, feeds keep coming

    Parameters are estimated from recent data:
    - hunger_rate: how fast hunger accumulates (units: "hunger" per hour)
    - satiety_per_oz: how much hunger each ounce of milk resets
    - threshold: hunger level that triggers a feed
    We normalize by setting hunger_rate = 1.0 and solving for the other two.
    """
    lookback_start = cutoff - timedelta(days=SATIETY_LOOKBACK_DAYS)
    recent = [event for event in history if lookback_start <= event.time <= cutoff]
    if len(recent) < 6:
        raise ForecastUnavailable("Satiety Decay needs at least six recent events.")

    # Estimate satiety_per_oz from observed (volume, gap) pairs.
    # With hunger_rate = 1.0, satiety = volume * satiety_per_oz,
    # and the next feed happens when accumulated_hunger = satiety,
    # so gap_hours = satiety = volume * satiety_per_oz.
    # Therefore satiety_per_oz ≈ mean(gap / volume) for full feeds.
    full_recent = [e for e in recent if e.volume_oz >= SNACK_THRESHOLD_OZ]
    if len(full_recent) < 4:
        raise ForecastUnavailable("Satiety Decay needs more full feeds.")

    ratios: list[float] = []
    ratio_times: list[datetime] = []
    for i in range(len(full_recent) - 1):
        gap = (full_recent[i + 1].time - full_recent[i].time).total_seconds() / 3600
        if gap < 0.5:
            continue
        ratios.append(gap / max(full_recent[i].volume_oz, 0.5))
        ratio_times.append(full_recent[i].time)

    if len(ratios) < 3:
        raise ForecastUnavailable("Satiety Decay needs more gap/volume pairs.")

    weights = exp_weights(ratio_times, cutoff, SATIETY_HALF_LIFE_HOURS)
    satiety_per_oz = float(np.average(ratios, weights=weights))

    # Time-of-day modulation: night feeds have higher satiety per oz
    # (baby sleeps more deeply, burns less). Estimate from data.
    night_ratios = [r for r, t in zip(ratios, ratio_times)
                    if hour_of_day(t) >= SURVIVAL_NIGHT_START or hour_of_day(t) < SURVIVAL_NIGHT_END]
    day_ratios = [r for r, t in zip(ratios, ratio_times)
                  if not (hour_of_day(t) >= SURVIVAL_NIGHT_START or hour_of_day(t) < SURVIVAL_NIGHT_END)]
    night_spo = float(np.mean(night_ratios)) if len(night_ratios) >= 2 else satiety_per_oz
    day_spo = float(np.mean(day_ratios)) if len(day_ratios) >= 2 else satiety_per_oz

    volume_profile = build_volume_profile(
        recent, cutoff=cutoff,
        lookback_days=SATIETY_LOOKBACK_DAYS,
        half_life_hours=SATIETY_HALF_LIFE_HOURS,
    )

    # Simulate forward from the last feed
    # Hunger accumulates at rate 1.0/hr. Each feed resets hunger by volume * spo.
    # After a feed, current_hunger = 0 (fully reset). Next feed when hunger ≈ volume * spo.
    last_event = history[-1]
    current_time = last_event.time
    eff_volume = effective_timing_volume(history)
    end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []

    while True:
        h = hour_of_day(current_time)
        is_night = h >= SURVIVAL_NIGHT_START or h < SURVIVAL_NIGHT_END
        spo = night_spo if is_night else day_spo

        # Predicted gap = how long until hunger exceeds the satiety provided
        predicted_gap = float(np.clip(eff_volume * spo, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS))

        next_time = current_time + timedelta(hours=predicted_gap)
        if next_time >= end:
            break

        base_volume, volume_std = lookup_volume_profile(volume_profile, next_time)
        points.append(ForecastPoint(
            time=next_time,
            volume_oz=float(np.clip(base_volume, 0.5, 8.0)),
            low_volume_oz=max(0.1, base_volume - max(volume_std, 0.35)),
            high_volume_oz=base_volume + max(volume_std, 0.35),
            gap_hours=predicted_gap,
        ))

        current_time = next_time
        eff_volume = base_volume  # projected feeds are full feeds

    return ForecastResult(
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        notes=[
            f"Satiety per oz: {satiety_per_oz:.2f}h/oz overall, "
            f"day={day_spo:.2f}h/oz, night={night_spo:.2f}h/oz.",
            "Bigger feeds → more satiety → longer gap. Snacks only partially reset the hunger clock.",
        ],
        diagnostics={
            "satiety_per_oz_overall": round(satiety_per_oz, 3),
            "satiety_per_oz_day": round(day_spo, 3),
            "satiety_per_oz_night": round(night_spo, 3),
            "training_ratios": len(ratios),
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
        ("phase_locked_oscillator", forecast_phase_locked_oscillator),
        ("template_match", forecast_template_match),
        ("daily_shift", forecast_daily_shift),
        ("gap_conditional", forecast_gap_conditional),
        ("survival_weibull", forecast_survival_weibull),
        ("satiety_decay", forecast_satiety_decay),
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
            "Blends whichever robust component models are available at the cutoff instead of failing when one drops out.",
            "Groups component predictions by time proximity, not raw forecast index.",
            "Leaves the gradient-boosted model out of the blend because it is still a higher-variance canary on limited data.",
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


def effective_timing_volume(history: list[FeedEvent], snack_threshold: float = SNACK_THRESHOLD_OZ) -> float:
    """Return a volume appropriate for gap prediction from the end of history.

    Problem: models trained on full-feed intervals use last_volume to predict
    the next gap. When the last event is a snack (<1.5 oz), using that raw
    volume extrapolates outside the training regime. Instead, aggregate the
    most recent cluster of closely-spaced events into a single "effective"
    volume. If the last feed was a full feed, this just returns its volume.

    Example: full feed at 11:54 (3.5 oz) then snack at 13:06 (1.0 oz)
    → effective volume = 3.5 + 1.0 = 4.5 oz (the baby ate 4.5 oz total
    in the recent cluster, so the next real gap should be long).
    """
    if not history:
        return 3.0  # fallback

    last = history[-1]
    if last.volume_oz >= snack_threshold:
        return last.volume_oz

    # Walk backward, collecting events in the recent cluster
    # A cluster ends when the gap exceeds 2 hours
    cluster_volume = last.volume_oz
    cluster_start = last.time
    for i in range(len(history) - 2, -1, -1):
        gap = (cluster_start - history[i].time).total_seconds() / 3600
        if gap > 2.0:
            break
        cluster_volume += history[i].volume_oz
        cluster_start = history[i].time
        if history[i].volume_oz >= snack_threshold:
            break  # reached a full feed, stop

    return cluster_volume


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
            f"State gap regression needs at least {STATE_GAP_MIN_TRAINING_EXAMPLES} training examples."
        )

    feature_matrix = np.vstack(feature_rows)
    target_array = np.array(targets, dtype=float)
    weights = exp_weights(timestamps, cutoff, GAP_CONDITIONAL_HALF_LIFE_HOURS)
    coefficients = _weighted_multi_linregress(feature_matrix, target_array, weights)
    return coefficients, recent, len(targets)


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

    recent_events = events[-min(len(events), 24):]
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
        np.average([daily_counts[date] for date in dates], weights=count_weights)
    )
    target_interval = 24 / np.clip(feeds_per_day, 6.0, 10.5)
    return float(np.clip((0.7 * weighted_interval) + (0.3 * target_interval), MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS))


def template_feature_vector(events: list[FeedEvent]) -> np.ndarray:
    """Return the analog-matching feature vector for a feed window."""
    gaps = [
        (current.time - previous.time).total_seconds() / 3600
        for previous, current in zip(events, events[1:])
    ]
    volumes = [event.volume_oz for event in events]
    hours = [hour_of_day(event.time) for event in events]
    return np.array(gaps + volumes + hours, dtype=float)


def build_daily_shift_template(
    history: list[FeedEvent],
    cutoff: datetime,
) -> dict[str, Any]:
    """Build the daily gap and slot templates used by Daily Shift."""
    day_groups = group_events_by_date(history)
    candidate_days = [
        date
        for date in sorted(day_groups)
        if date < cutoff.date() and date >= (cutoff.date() - timedelta(days=DAILY_SHIFT_LOOKBACK_DAYS))
    ]

    usable_days: list[datetime.date] = []
    day_sequences: list[list[FeedEvent]] = []
    next_day_first_feeds: list[FeedEvent] = []
    for date in candidate_days:
        day_events = day_groups[date]
        next_day_events = day_groups.get(date + timedelta(days=1))
        if len(day_events) < DAILY_SHIFT_MIN_FEEDS_PER_DAY or not next_day_events:
            continue
        usable_days.append(date)
        day_sequences.append(day_events)
        next_day_first_feeds.append(next_day_events[0])

    if len(usable_days) < DAILY_SHIFT_MIN_COMPLETE_DAYS:
        raise ForecastUnavailable("Daily Shift needs at least three completed recent days.")

    count_weights = day_weights(usable_days, cutoff.date(), DAILY_SHIFT_HALF_LIFE_DAYS)
    target_feed_count = int(
        round(
            np.clip(
                np.average([len(sequence) for sequence in day_sequences], weights=count_weights),
                6,
                10,
            )
        )
    )

    slot_hour_sequences: list[np.ndarray] = []
    intra_gap_sequences: list[np.ndarray] = []
    overnight_gaps: list[float] = []
    slot_volume_sequences: list[np.ndarray] = []
    for day_events, next_first_feed in zip(day_sequences, next_day_first_feeds):
        slot_hour_sequences.append(
            resample_sequence([hour_of_day(event.time) for event in day_events], target_feed_count)
        )
        slot_volume_sequences.append(
            resample_sequence([event.volume_oz for event in day_events], target_feed_count)
        )
        intra_gap_sequences.append(
            resample_sequence(
                [
                    (current.time - previous.time).total_seconds() / 3600
                    for previous, current in zip(day_events, day_events[1:])
                ],
                target_feed_count - 1,
            )
        )
        overnight_gaps.append((next_first_feed.time - day_events[-1].time).total_seconds() / 3600)

    slot_hour_matrix = np.vstack(slot_hour_sequences)
    intra_gap_matrix = np.vstack(intra_gap_sequences)
    slot_volume_matrix = np.vstack(slot_volume_sequences)
    normalized_slot_hours = normalize_day_hours(
        list(np.average(slot_hour_matrix, axis=0, weights=count_weights))
    )
    return {
        "usable_days": len(usable_days),
        "target_feed_count": target_feed_count,
        "slot_hour_template": np.array(normalized_slot_hours, dtype=float),
        "intra_gap_template": np.average(intra_gap_matrix, axis=0, weights=count_weights),
        "slot_volume_template": np.average(slot_volume_matrix, axis=0, weights=count_weights),
        "slot_volume_std": np.std(slot_volume_matrix, axis=0),
        "overnight_gap_hours": float(np.average(overnight_gaps, weights=count_weights)),
    }


def fit_daily_shift_state(
    today_events: list[FeedEvent],
    slot_hour_template: np.ndarray,
    intra_gap_template: np.ndarray,
) -> tuple[int, float, float]:
    """Align today's observed events to the daily slot template."""
    current_hour = hour_of_day(today_events[-1].time)
    if len(today_events) == 1:
        current_slot = int(np.argmin(np.abs(slot_hour_template - current_hour)))
        return current_slot, 1.0, float(abs(slot_hour_template[current_slot] - current_hour))

    observed_gaps = np.array(
        [
            (current.time - previous.time).total_seconds() / 3600
            for previous, current in zip(today_events, today_events[1:])
        ],
        dtype=float,
    )

    best_alignment: tuple[float, int, float] | None = None
    max_start_slot = max(0, len(slot_hour_template) - len(today_events))
    for start_slot in range(max_start_slot + 1):
        template_slice = intra_gap_template[start_slot:start_slot + len(observed_gaps)]
        if len(template_slice) != len(observed_gaps):
            continue

        scale = float(
            np.clip(
                np.median(observed_gaps / np.maximum(template_slice, 1e-6)),
                DAILY_SHIFT_SCALE_MIN,
                DAILY_SHIFT_SCALE_MAX,
            )
        )
        gap_error = float(np.mean(np.abs(observed_gaps - (template_slice * scale))))
        current_slot = start_slot + len(today_events) - 1
        phase_error = abs(slot_hour_template[current_slot] - current_hour) / 3
        total_error = gap_error + phase_error

        if best_alignment is None or total_error < best_alignment[0]:
            best_alignment = (total_error, current_slot, scale)

    if best_alignment is None:
        raise ForecastUnavailable("Daily Shift could not align today's events to the daily template.")

    fit_error, current_slot, scale = best_alignment
    return current_slot, scale, fit_error


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


def _weighted_multi_linregress(
    features: np.ndarray,
    targets: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Weighted multivariate linear regression via normal equations.

    Returns array of [intercept, coeff1, coeff2, ...].
    """
    n, p = features.shape
    w = weights / weights.sum()
    # Add intercept column
    design = np.column_stack([np.ones(n), features])
    # Weighted normal equations: (X^T W X)^-1 X^T W y
    w_diag = np.diag(w)
    xtw = design.T @ w_diag
    try:
        coefficients = np.linalg.solve(xtw @ design, xtw @ targets)
    except np.linalg.LinAlgError:
        # Fallback: use mean target as intercept, zero slopes
        coefficients = np.zeros(p + 1)
        coefficients[0] = float(np.average(targets, weights=w))
    return coefficients


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


def availability_adjusted_first_feed_error(summary: BacktestSummary) -> float:
    """Return recent first-feed error with a penalty for low cutoff coverage."""
    recent_first = _sortable_metric(summary.recent_first_feed_error_minutes)
    coverage_shortfall = max(0.0, 0.75 - summary.cutoff_coverage_ratio)
    coverage_penalty = 40.0 * (coverage_shortfall / 0.75)
    return recent_first + coverage_penalty


def _round_or_none(value: float | None) -> float | None:
    """Round a float if present."""
    if value is None:
        return None
    return round(value, 3)
