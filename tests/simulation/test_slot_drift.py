"""Simulation tests for the Slot Drift model."""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta

from feedcast.models.slot_drift import model as slot_drift_model
from feedcast.replay import tune_model
from tests.simulation.assertions import (
    assert_forecast_times_close,
    assert_value_within_tolerance,
)
from tests.simulation.factories import ScheduleEntry, bottle_activities_from_schedule

TRUE_SLOT_HOURS = [1.0, 4.0, 7.0, 10.0, 13.0, 16.0, 19.0, 22.0]
TRUE_SLOT_VOLUMES_OZ = [2.5, 2.75, 3.0, 3.1, 3.2, 3.35, 3.5, 3.65]
TRUE_DRIFT_RATES_HOURS_PER_DAY = [-0.24, -0.18, -0.12, -0.06, 0.06, 0.12, 0.18, 0.24]
STRUCTURAL_HISTORY_DAYS = 8
CANONICAL_HISTORY_DAYS = 16
CANONICAL_JITTER_STD_HOURS = 0.25
CANONICAL_SIMULATION_SEED = 19

# The targeted replay grid contrasts three regimes:
# - LOOKBACK_DAYS=2 is under-supported because it cannot satisfy MIN_COMPLETE_DAYS=3.
# - MATCH_COST_THRESHOLD_HOURS=0.25 is narrower than the intended noisy-but-valid matches.
# - DRIFT_WEIGHT_HALF_LIFE_DAYS=0.25 overreacts to jitter in a stationary linear-drift DGP.
CANONICAL_GRID = {
    "DRIFT_WEIGHT_HALF_LIFE_DAYS": [0.25, 1.0, 7.0],
    "LOOKBACK_DAYS": [2, 5, 7],
    "MATCH_COST_THRESHOLD_HOURS": [0.25, 1.5],
}


def _generate_slot_drift_schedule(
    *,
    anchor_day: datetime,
    day_offsets: Sequence[int],
    jitter_std_hours: float = 0.0,
    seed: int = 0,
) -> list[ScheduleEntry]:
    """Generate a bottle-only schedule from the Slot Drift DGP.

    The anchor day is the day whose slot positions equal ``TRUE_SLOT_HOURS``.
    Earlier and later days shift linearly by the known per-slot drift rate.
    """
    if jitter_std_hours < 0:
        raise ValueError("jitter_std_hours must be non-negative.")

    rng = random.Random(seed)
    schedule: list[ScheduleEntry] = []
    for day_offset in day_offsets:
        day = anchor_day + timedelta(days=day_offset)
        for base_hour, drift_rate, volume_oz in zip(
            TRUE_SLOT_HOURS,
            TRUE_DRIFT_RATES_HOURS_PER_DAY,
            TRUE_SLOT_VOLUMES_OZ,
        ):
            jitter_hours = (
                rng.gauss(0.0, jitter_std_hours) if jitter_std_hours > 0 else 0.0
            )
            timestamp = day + timedelta(
                hours=base_hour + (drift_rate * day_offset) + jitter_hours
            )
            schedule.append((timestamp, volume_oz))

    return schedule


def _expected_template_hours(complete_days_used: int) -> list[float]:
    """Return the template positions implied by the actual history used."""
    recent_offsets = list(range(-complete_days_used, 0))
    median_offset = float(statistics.median(recent_offsets))
    return [
        base_hour + (drift_rate * median_offset)
        for base_hour, drift_rate in zip(
            TRUE_SLOT_HOURS,
            TRUE_DRIFT_RATES_HOURS_PER_DAY,
        )
    ]


def _expected_forecast_times(anchor_day: datetime) -> list[datetime]:
    """Return the known next-day slot times for the anchor day."""
    return [anchor_day + timedelta(hours=hour) for hour in TRUE_SLOT_HOURS]


def _candidate_for_params(
    payload: dict[str, object],
    *,
    drift_weight_half_life_days: float,
    lookback_days: int,
    match_cost_threshold_hours: float,
) -> dict[str, object]:
    """Return one replay candidate by its parameter tuple."""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise AssertionError("Replay payload missing candidates list.")

    expected_params = {
        "DRIFT_WEIGHT_HALF_LIFE_DAYS": drift_weight_half_life_days,
        "LOOKBACK_DAYS": lookback_days,
        "MATCH_COST_THRESHOLD_HOURS": match_cost_threshold_hours,
    }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("params") == expected_params:
            return candidate

    raise AssertionError(f"Replay payload missing candidate {expected_params!r}.")


def test_diagnostics_recover_slot_template_and_drift_on_clean_history() -> None:
    """Diagnostics should recover the deterministic slot structure and drift."""
    forecast_day = datetime(2026, 3, 24, 0, 0, 0)
    history_schedule = _generate_slot_drift_schedule(
        anchor_day=forecast_day,
        day_offsets=range(-STRUCTURAL_HISTORY_DAYS, 0),
    )
    forecast = slot_drift_model.forecast_slot_drift(
        bottle_activities_from_schedule(history_schedule),
        forecast_day,
        horizon_hours=24,
    )
    diagnostics = forecast.diagnostics
    expected_complete_days = min(
        STRUCTURAL_HISTORY_DAYS,
        slot_drift_model.LOOKBACK_DAYS,
    )

    assert diagnostics["slot_count"] == len(TRUE_SLOT_HOURS)
    assert diagnostics["complete_days_used"] == expected_complete_days
    assert diagnostics["filled_slots_today"] == []
    assert set(diagnostics["daily_episode_counts"].values()) == {len(TRUE_SLOT_HOURS)}

    for index, (actual, expected) in enumerate(
        zip(diagnostics["template_hours"], _expected_template_hours(expected_complete_days)),
        start=1,
    ):
        assert_value_within_tolerance(
            actual=float(actual),
            expected=expected,
            tolerance=0.02,
            name=f"template_hour[{index}]",
        )

    for index, (slot_diag, expected_rate) in enumerate(
        zip(diagnostics["per_slot"], TRUE_DRIFT_RATES_HOURS_PER_DAY),
        start=1,
    ):
        assert slot_diag["observations"] == expected_complete_days
        assert_value_within_tolerance(
            actual=float(slot_diag["drift_hours_per_day"]),
            expected=expected_rate,
            tolerance=1e-3,
            name=f"drift_hours_per_day[{index}]",
        )


def test_forecast_matches_next_day_times_and_volumes_on_clean_history() -> None:
    """The public forecaster should extrapolate the next deterministic day exactly."""
    forecast_day = datetime(2026, 3, 24, 0, 0, 0)
    history_schedule = _generate_slot_drift_schedule(
        anchor_day=forecast_day,
        day_offsets=range(-STRUCTURAL_HISTORY_DAYS, 0),
    )
    forecast = slot_drift_model.forecast_slot_drift(
        bottle_activities_from_schedule(history_schedule),
        forecast_day,
        horizon_hours=24,
    )

    assert len(forecast.points) == len(TRUE_SLOT_HOURS)
    assert_forecast_times_close(
        points=forecast.points,
        expected_times=_expected_forecast_times(forecast_day),
        tolerance_minutes=0.01,
    )
    for index, (point, expected_volume) in enumerate(
        zip(forecast.points, TRUE_SLOT_VOLUMES_OZ),
        start=1,
    ):
        assert_value_within_tolerance(
            actual=point.volume_oz,
            expected=expected_volume,
            tolerance=1e-9,
            name=f"forecast_volume[{index}]",
        )


def test_canonical_replay_prefers_well_supported_drift_tracking_regime(
    replay_output_dir,
    write_simulation_export,
) -> None:
    """Replay tuning should reward enough history, enough smoothing, and valid matches."""
    anchor_day = datetime(2026, 3, 30, 0, 0, 0)
    schedule = _generate_slot_drift_schedule(
        anchor_day=anchor_day,
        day_offsets=range(-(CANONICAL_HISTORY_DAYS - 1), 1),
        jitter_std_hours=CANONICAL_JITTER_STD_HOURS,
        seed=CANONICAL_SIMULATION_SEED,
    )
    export_path = write_simulation_export(
        bottle_activities_from_schedule(schedule),
    )
    payload = tune_model(
        "slot_drift",
        candidates_by_name=CANONICAL_GRID,
        export_path=export_path,
        output_dir=replay_output_dir,
        lookback_hours=96,
    )

    best = payload["best"]
    assert isinstance(best, dict)

    # The scoring function's preference for well-supported regimes is
    # verified below via pairwise comparisons against deliberately broken
    # candidates (under-supported lookback, too-tight match threshold,
    # twitchy drift half-life). We do not assert specific "best" params,
    # since the current production baseline competes with the grid and its
    # values drift as the model is retuned.
    under_supported = _candidate_for_params(
        payload,
        drift_weight_half_life_days=7.0,
        lookback_days=2,
        match_cost_threshold_hours=1.5,
    )
    tight_threshold = _candidate_for_params(
        payload,
        drift_weight_half_life_days=7.0,
        lookback_days=7,
        match_cost_threshold_hours=0.25,
    )
    twitchy_half_life = _candidate_for_params(
        payload,
        drift_weight_half_life_days=0.25,
        lookback_days=7,
        match_cost_threshold_hours=1.5,
    )

    under_supported_rw = under_supported["replay_windows"]
    tight_threshold_rw = tight_threshold["replay_windows"]
    twitchy_half_life_rw = twitchy_half_life["replay_windows"]
    best_rw = best["replay_windows"]
    assert isinstance(under_supported_rw, dict)
    assert isinstance(tight_threshold_rw, dict)
    assert isinstance(twitchy_half_life_rw, dict)
    assert isinstance(best_rw, dict)

    assert under_supported_rw["scored_window_count"] == 0
    assert (
        float(best_rw["aggregate"]["headline"])
        > float(tight_threshold_rw["aggregate"]["headline"]) + 10.0
    )
    assert (
        float(best_rw["aggregate"]["headline"])
        > float(twitchy_half_life_rw["aggregate"]["headline"]) + 15.0
    )
