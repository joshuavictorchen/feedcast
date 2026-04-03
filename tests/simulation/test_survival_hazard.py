"""Simulation tests for the Survival Hazard model."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import datetime, timedelta

import numpy as np

from feedcast.data import hour_of_day
from feedcast.models.survival_hazard import model as survival_hazard_model
from feedcast.models.survival_hazard.analysis import _fit_weibull
from feedcast.replay import tune_model

from tests.simulation.assertions import (
    assert_forecast_times_close,
    assert_replay_best_param_within_tolerance,
    assert_value_within_tolerance,
)
from tests.simulation.factories import ScheduleEntry, bottle_activities_from_schedule

TRUE_OVERNIGHT_SHAPE = 4.5
TRUE_DAYTIME_SHAPE = 2.0
TRUE_OVERNIGHT_SCALE = 4.5
TRUE_DAYTIME_SCALE = 3.2
SIMULATION_SEED = 0
CANONICAL_GRID = {
    "OVERNIGHT_SHAPE": [4.0, 4.25, 4.5, 4.75, 5.0],
    "DAYTIME_SHAPE": [1.75, 2.0, 2.25],
}


def _sample_weibull_gap_hours(
    *,
    shape: float,
    scale: float,
    rng: random.Random,
) -> float:
    """Draw one Weibull gap via inverse-CDF sampling."""
    u = rng.random()
    return scale * (-math.log(1.0 - u)) ** (1.0 / shape)


def _daypart_params(hour: float) -> tuple[float, float]:
    """Return the true `(shape, scale)` pair for a gap starting at `hour`."""
    if survival_hazard_model._is_overnight(hour):
        return TRUE_OVERNIGHT_SHAPE, TRUE_OVERNIGHT_SCALE
    return TRUE_DAYTIME_SHAPE, TRUE_DAYTIME_SCALE


def _generate_weibull_schedule(
    *,
    start: datetime,
    event_count: int,
    deterministic: bool = False,
    seed: int = SIMULATION_SEED,
) -> list[ScheduleEntry]:
    """Generate a chronological bottle schedule from the Survival Hazard DGP."""
    rng = random.Random(seed)
    current_time = start
    schedule: list[ScheduleEntry] = []

    for _ in range(event_count):
        schedule.append((current_time, 3.5))
        shape, scale = _daypart_params(hour_of_day(current_time))
        if deterministic:
            gap_hours = survival_hazard_model._weibull_median(shape, scale)
        else:
            gap_hours = _sample_weibull_gap_hours(
                shape=shape,
                scale=scale,
                rng=rng,
            )
        current_time += timedelta(hours=gap_hours)

    return schedule


def _expected_median_path(
    *,
    cutoff: datetime,
    horizon_hours: int,
    elapsed_since_last_hours: float = 0.0,
    gap_start_hour: float | None = None,
) -> list[datetime]:
    """Return the deterministic median-path future implied by the true DGP."""
    if gap_start_hour is None:
        gap_start_hour = hour_of_day(cutoff)

    first_shape, first_scale = _daypart_params(gap_start_hour)
    feed_time = cutoff + timedelta(
        hours=survival_hazard_model._weibull_conditional_remaining(
            first_shape,
            first_scale,
            elapsed_since_last_hours,
        )
    )

    expected_times: list[datetime] = []
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    while feed_time < horizon_end:
        expected_times.append(feed_time)

        shape, scale = _daypart_params(hour_of_day(feed_time))
        feed_time += timedelta(
            hours=survival_hazard_model._weibull_median(shape, scale)
        )

    return expected_times


def test_analysis_mle_recovers_daypart_shapes_from_direct_gap_samples() -> None:
    """The analysis-code MLE fitter should recover known Weibull shapes.

    This validates the day-part Weibull fitter itself. Chronological
    history extraction is exercised separately by the public forecast and
    replay tests below.
    """
    rng = random.Random(SIMULATION_SEED)
    overnight_gaps = np.array(
        [
            _sample_weibull_gap_hours(
                shape=TRUE_OVERNIGHT_SHAPE,
                scale=TRUE_OVERNIGHT_SCALE,
                rng=rng,
            )
            for _ in range(400)
        ]
    )
    daytime_gaps = np.array(
        [
            _sample_weibull_gap_hours(
                shape=TRUE_DAYTIME_SHAPE,
                scale=TRUE_DAYTIME_SCALE,
                rng=rng,
            )
            for _ in range(500)
        ]
    )

    overnight_fit = _fit_weibull(overnight_gaps, np.ones_like(overnight_gaps))
    daytime_fit = _fit_weibull(daytime_gaps, np.ones_like(daytime_gaps))

    assert_value_within_tolerance(
        actual=float(overnight_fit["shape"]),
        expected=TRUE_OVERNIGHT_SHAPE,
        tolerance=0.25,
        name="overnight_shape_mle",
    )
    assert_value_within_tolerance(
        actual=float(daytime_fit["shape"]),
        expected=TRUE_DAYTIME_SHAPE,
        tolerance=0.2,
        name="daytime_shape_mle",
    )


def test_forecast_tracks_true_median_path_on_stochastic_history(monkeypatch) -> None:
    """The shipped forecaster should stay close to the true hazard median path."""
    history_schedule = _generate_weibull_schedule(
        start=datetime(2026, 3, 15, 0, 0, 0),
        event_count=180,
    )
    cutoff = history_schedule[-1][0]
    activities = bottle_activities_from_schedule(history_schedule)
    expected_times = _expected_median_path(cutoff=cutoff, horizon_hours=24)

    monkeypatch.setattr(
        survival_hazard_model,
        "OVERNIGHT_SHAPE",
        TRUE_OVERNIGHT_SHAPE,
    )
    monkeypatch.setattr(
        survival_hazard_model,
        "DAYTIME_SHAPE",
        TRUE_DAYTIME_SHAPE,
    )
    forecast = survival_hazard_model.forecast_survival_hazard(
        activities,
        cutoff,
        horizon_hours=24,
    )

    assert len(forecast.points) == len(expected_times)
    assert_forecast_times_close(
        points=forecast.points,
        expected_times=expected_times,
        tolerance_minutes=40.0,
    )
    assert_value_within_tolerance(
        actual=float(forecast.diagnostics["overnight_scale"]),
        expected=TRUE_OVERNIGHT_SCALE,
        tolerance=0.35,
        name="overnight_scale_diagnostic",
    )
    assert_value_within_tolerance(
        actual=float(forecast.diagnostics["daytime_scale"]),
        expected=TRUE_DAYTIME_SCALE,
        tolerance=0.2,
        name="daytime_scale_diagnostic",
    )


def test_first_gap_uses_gap_start_daypart_across_boundary(
    monkeypatch,
) -> None:
    """The first conditional gap should be anchored to the last feed's day-part."""
    base = datetime(2026, 3, 17, 16, 30, 0)
    history_schedule = [(base + timedelta(hours=3 * index), 3.5) for index in range(6)]
    activities = bottle_activities_from_schedule(history_schedule)
    cutoff = history_schedule[-1][0] + timedelta(hours=1)
    fit_details = [
        {"daypart": "overnight"},
        {"daypart": "overnight"},
        {"daypart": "overnight"},
        {"daypart": "daytime"},
        {"daypart": "daytime"},
    ]

    monkeypatch.setattr(
        survival_hazard_model,
        "OVERNIGHT_SHAPE",
        TRUE_OVERNIGHT_SHAPE,
    )
    monkeypatch.setattr(
        survival_hazard_model,
        "DAYTIME_SHAPE",
        TRUE_DAYTIME_SHAPE,
    )
    monkeypatch.setattr(
        survival_hazard_model,
        "_estimate_daypart_scales",
        lambda events, cutoff_time: (
            TRUE_OVERNIGHT_SCALE,
            TRUE_DAYTIME_SCALE,
            fit_details,
        ),
    )

    forecast = survival_hazard_model.forecast_survival_hazard(
        activities,
        cutoff,
        horizon_hours=24,
    )
    expected_times = _expected_median_path(
        cutoff=cutoff,
        horizon_hours=24,
        elapsed_since_last_hours=1.0,
        gap_start_hour=hour_of_day(history_schedule[-1][0]),
    )

    assert forecast.diagnostics["first_gap_daypart"] == "overnight"
    assert_forecast_times_close(
        points=forecast.points,
        expected_times=expected_times,
        tolerance_minutes=0.01,
    )


def test_canonical_replay_best_shapes_stay_close_to_true_dgp(
    replay_output_dir,
    write_simulation_export,
) -> None:
    """Canonical replay should keep the best shapes near the true DGP values."""
    schedule = _generate_weibull_schedule(
        start=datetime(2026, 3, 15, 0, 0, 0),
        event_count=320,
    )
    export_path = write_simulation_export(
        bottle_activities_from_schedule(schedule),
    )
    payload = tune_model(
        "survival_hazard",
        candidates_by_name=CANONICAL_GRID,
        export_path=export_path,
        output_dir=replay_output_dir,
        lookback_hours=96,
    )

    assert_replay_best_param_within_tolerance(
        payload,
        param_name="OVERNIGHT_SHAPE",
        expected=TRUE_OVERNIGHT_SHAPE,
        tolerance=0.25,
    )
    assert_replay_best_param_within_tolerance(
        payload,
        param_name="DAYTIME_SHAPE",
        expected=TRUE_DAYTIME_SHAPE,
        tolerance=0.25,
    )
