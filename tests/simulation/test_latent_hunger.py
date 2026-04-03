"""Simulation tests for the Latent Hunger model."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import datetime, timedelta

from feedcast.clustering import episodes_as_events
from feedcast.data import FeedEvent, build_feed_events
from feedcast.models.latent_hunger import model as latent_hunger_model
from feedcast.models.latent_hunger.analysis import _evaluate_multiplicative
from feedcast.replay import tune_model

from tests.simulation.assertions import (
    assert_forecast_times_close,
    assert_replay_best_param_within_tolerance,
    assert_value_within_tolerance,
)
from tests.simulation.factories import ScheduleEntry, bottle_activities_from_schedule

TRUE_SATIETY_RATE = 0.35
TRUE_GROWTH_RATE = 0.2
RECOVERY_NOISE_STD_HOURS = 0.15
MIN_SYNTHETIC_GAP_HOURS = 1.75
CANONICAL_LOOKBACK_HOURS = 24.0
RECOVERY_GRID = [0.15, 0.25, 0.35, 0.45, 0.55]


def _latent_hunger_gap_hours(
    volume_oz: float,
    *,
    satiety_rate: float,
    growth_rate: float,
) -> float:
    """Return the DGP gap implied by the shipped Latent Hunger formula."""
    satiety_effect = 1.0 - math.exp(-satiety_rate * volume_oz)
    return latent_hunger_model.HUNGER_THRESHOLD * satiety_effect / growth_rate


def _generate_schedule(
    *,
    start: datetime,
    volumes_oz: Sequence[float],
    satiety_rate: float,
    growth_rate: float,
    noise_std_hours: float = 0.0,
    seed: int = 0,
) -> list[ScheduleEntry]:
    """Generate a bottle-only schedule from the Latent Hunger DGP.

    The generator keeps gaps well above the episode-clustering boundary so
    these tests stay focused on Latent Hunger rather than cluster collapse.
    """
    if not volumes_oz:
        raise ValueError("Need at least one synthetic volume.")
    if noise_std_hours < 0:
        raise ValueError("noise_std_hours must be non-negative.")

    rng = random.Random(seed)
    schedule: list[ScheduleEntry] = []
    current_time = start

    for volume_oz in volumes_oz:
        schedule.append((current_time, volume_oz))

        gap_hours = _latent_hunger_gap_hours(
            volume_oz,
            satiety_rate=satiety_rate,
            growth_rate=growth_rate,
        )
        if noise_std_hours > 0:
            gap_hours += rng.gauss(0.0, noise_std_hours)
        gap_hours = max(gap_hours, MIN_SYNTHETIC_GAP_HOURS)

        current_time += timedelta(hours=gap_hours)

    return schedule


def _build_episode_events(schedule: Sequence[ScheduleEntry]) -> list[FeedEvent]:
    """Convert a synthetic schedule into the episode-level history the model uses."""
    activities = bottle_activities_from_schedule(schedule)
    raw_events = build_feed_events(activities, merge_window_minutes=None)
    return episodes_as_events(raw_events)


def _best_internal_satiety_rate(
    events: Sequence[FeedEvent],
    *,
    candidate_rates: Sequence[float],
    growth_rate: float,
) -> tuple[float, dict[float, float]]:
    """Return the best satiety rate under the model's gap-MAE diagnostic."""
    gap_mae_by_rate = {
        rate: _evaluate_multiplicative(
            list(events),
            growth_rate=growth_rate,
            satiety_rate=rate,
        )["gap1_mae"]
        for rate in candidate_rates
    }
    best_rate = min(
        gap_mae_by_rate,
        key=lambda rate: (gap_mae_by_rate[rate], rate),
    )
    return best_rate, gap_mae_by_rate


def test_internal_gap_mae_recovers_true_satiety_rate_from_noisy_history() -> None:
    """Gap-MAE recovery should identify the DGP satiety rate on noisy synthetic data."""
    schedule = _generate_schedule(
        start=datetime(2026, 3, 15, 0, 0, 0),
        volumes_oz=[2.0, 3.0, 4.0] * 24,
        satiety_rate=TRUE_SATIETY_RATE,
        growth_rate=TRUE_GROWTH_RATE,
        noise_std_hours=RECOVERY_NOISE_STD_HOURS,
        seed=7,
    )
    events = _build_episode_events(schedule)

    best_rate, gap_mae_by_rate = _best_internal_satiety_rate(
        events,
        candidate_rates=RECOVERY_GRID,
        growth_rate=TRUE_GROWTH_RATE,
    )

    assert_value_within_tolerance(
        actual=best_rate,
        expected=TRUE_SATIETY_RATE,
        tolerance=1e-9,
        name="internal_best_satiety_rate",
    )
    assert gap_mae_by_rate[TRUE_SATIETY_RATE] < gap_mae_by_rate[0.25]
    assert gap_mae_by_rate[TRUE_SATIETY_RATE] < gap_mae_by_rate[0.45]


def test_forecast_matches_deterministic_dgp_when_rate_is_true(
    monkeypatch,
) -> None:
    """The public forecaster should reproduce a deterministic conforming future.

    The production forecaster simulates all future feeds at the recent median
    volume. The synthetic history therefore ends with a constant-volume tail so
    the known future matches that forecasting assumption instead of testing an
    artifact of future-volume misspecification.
    """
    history_volumes = [2.0, 3.0, 4.0] * 16 + [3.0] * 12
    future_volumes = [3.0] * 8
    schedule = _generate_schedule(
        start=datetime(2026, 3, 15, 0, 0, 0),
        volumes_oz=history_volumes + future_volumes,
        satiety_rate=TRUE_SATIETY_RATE,
        growth_rate=TRUE_GROWTH_RATE,
    )

    history_schedule = schedule[: len(history_volumes)]
    cutoff = history_schedule[-1][0]
    activities = bottle_activities_from_schedule(history_schedule)
    expected_times = [
        timestamp
        for timestamp, _ in schedule[len(history_volumes) :]
        if timestamp <= cutoff + timedelta(hours=24)
    ]

    monkeypatch.setattr(
        latent_hunger_model,
        "SATIETY_RATE",
        TRUE_SATIETY_RATE,
    )
    forecast = latent_hunger_model.forecast_latent_hunger(
        activities,
        cutoff,
        horizon_hours=24,
    )

    assert len(forecast.points) == len(expected_times)
    assert_forecast_times_close(
        points=forecast.points,
        expected_times=expected_times,
        tolerance_minutes=0.01,
    )
    assert_value_within_tolerance(
        actual=float(forecast.diagnostics["growth_rate"]),
        expected=TRUE_GROWTH_RATE,
        tolerance=1e-4,
        name="diagnostic growth_rate",
    )
    assert_value_within_tolerance(
        actual=float(forecast.diagnostics["sim_volume_oz"]),
        expected=3.0,
        tolerance=1e-9,
        name="diagnostic sim_volume_oz",
    )


def test_canonical_replay_matches_internal_recovery_on_constant_volume_tail(
    replay_output_dir,
    write_simulation_export,
) -> None:
    """Canonical replay should recover the true rate when replay windows are clean.

    This export uses the same constant-volume tail as the deterministic
    forecast test above. Restricting replay to that tail isolates replay
    mechanics from a known Latent Hunger simplification: future gaps are
    simulated at the median recent volume rather than a varying future
    volume sequence.
    """
    schedule = _generate_schedule(
        start=datetime(2026, 3, 15, 0, 0, 0),
        volumes_oz=[2.0, 3.0, 4.0] * 16 + [3.0] * 12,
        satiety_rate=TRUE_SATIETY_RATE,
        growth_rate=TRUE_GROWTH_RATE,
    )
    events = _build_episode_events(schedule)
    internal_best_rate, _ = _best_internal_satiety_rate(
        events,
        candidate_rates=RECOVERY_GRID,
        growth_rate=TRUE_GROWTH_RATE,
    )

    export_path = write_simulation_export(
        bottle_activities_from_schedule(schedule),
    )
    payload = tune_model(
        "latent_hunger",
        candidates_by_name={"SATIETY_RATE": RECOVERY_GRID},
        export_path=export_path,
        output_dir=replay_output_dir,
        lookback_hours=CANONICAL_LOOKBACK_HOURS,
    )

    assert_value_within_tolerance(
        actual=internal_best_rate,
        expected=TRUE_SATIETY_RATE,
        tolerance=1e-9,
        name="internal_best_satiety_rate",
    )
    assert_replay_best_param_within_tolerance(
        payload,
        param_name="SATIETY_RATE",
        expected=TRUE_SATIETY_RATE,
        tolerance=1e-9,
    )
