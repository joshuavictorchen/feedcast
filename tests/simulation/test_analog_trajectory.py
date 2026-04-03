"""Simulation tests for the Analog Trajectory model."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from feedcast.models.analog_trajectory import model as analog_model
from feedcast.replay import override_constants, tune_model
from tests.simulation.assertions import (
    assert_forecast_times_close,
    assert_value_within_tolerance,
)
from tests.simulation.factories import ScheduleEntry, bottle_activities_from_schedule

MODEL_MODULE = "feedcast.models.analog_trajectory.model"
START_DAY = datetime(2026, 3, 15, 0, 0, 0)
ANCHOR_HOUR = 8
QUERY_DAY_INDEX = 12
QUERY_DAY_COUNT = 14

RECENT_ONLY = [2.0, 0.5, 2.0, 0.5, 1.0, 1.0]
MEANS_ONLY = [0.5, 2.0, 0.5, 2.0, 1.0, 1.0]
CANONICAL_JITTER_CYCLE = [-1.0, -0.5, 0.0, 0.5, 1.0]

FOCUSED_OVERRIDES = {
    "HISTORY_MODE": "raw",
    "LOOKBACK_HOURS": 12,
    "FEATURE_WEIGHTS": RECENT_ONLY,
    "K_NEIGHBORS": 5,
    "RECENCY_HALF_LIFE_HOURS": 72,
    "TRAJECTORY_LENGTH_METHOD": "median",
    "ALIGNMENT": "gap",
}

CANONICAL_GRID = {
    "LOOKBACK_HOURS": [12, 72],
    "FEATURE_WEIGHTS": [RECENT_ONLY, MEANS_ONLY],
    "K_NEIGHBORS": [5, 9],
}

ARCHETYPES = {
    "A": {
        "pre_hour": 4,
        "pre_volume_oz": 4.0,
        "anchor_volume_oz": 6.0,
        "future_hours": [11, 15],
        "future_volumes_oz": [4.5, 4.0],
    },
    "B": {
        "pre_hour": 6,
        "pre_volume_oz": 2.5,
        "anchor_volume_oz": 2.0,
        "future_hours": [10, 13],
        "future_volumes_oz": [2.5, 3.0],
    },
}


def _anchor_type(day_index: int) -> str:
    """Return the planted archetype for one synthetic anchor day."""
    return "A" if day_index % 2 == 0 else "B"


def _generate_archetype_schedule(
    *,
    day_count: int,
    jittered: bool = False,
) -> tuple[list[ScheduleEntry], dict[datetime, str]]:
    """Generate an alternating same-hour archetype schedule.

    Both archetypes anchor at 08:00 so hour-of-day alone cannot solve the
    retrieval problem. The distinction comes from the recent gap/volume state
    and the different future trajectories.

    The next day's pre-anchor event acts as the previous day's late third
    future event. That keeps each anchor state complete without adding extra
    synthetic structure that the analog model could exploit.
    """
    schedule: list[ScheduleEntry] = []
    anchor_labels: dict[datetime, str] = {}

    for day_index in range(day_count):
        day = START_DAY + timedelta(days=day_index)
        archetype = _anchor_type(day_index)
        spec = ARCHETYPES[archetype]

        pre_hour = spec["pre_hour"]
        anchor_volume_oz = spec["anchor_volume_oz"]
        future_hours = list(spec["future_hours"])
        future_volumes_oz = list(spec["future_volumes_oz"])

        if jittered:
            jitter = CANONICAL_JITTER_CYCLE[
                (day_index // 2) % len(CANONICAL_JITTER_CYCLE)
            ]
            pre_hour += 0.2 * jitter
            anchor_volume_oz += (0.35 if archetype == "A" else 0.2) * jitter
            future_hours = [
                future_hours[0] + (0.35 if archetype == "A" else 0.25) * jitter,
                future_hours[1] + (0.55 if archetype == "A" else 0.35) * jitter,
            ]
            future_volumes_oz = [
                future_volumes_oz[0] + (0.2 if archetype == "A" else 0.1) * jitter,
                future_volumes_oz[1] + (0.15 if archetype == "A" else 0.1) * jitter,
            ]

        pre_time = day + timedelta(hours=pre_hour)
        anchor_time = day + timedelta(hours=ANCHOR_HOUR)

        schedule.append((pre_time, spec["pre_volume_oz"]))
        schedule.append((anchor_time, anchor_volume_oz))
        anchor_labels[anchor_time] = archetype

        for future_hour, volume_oz in zip(
            future_hours,
            future_volumes_oz,
            strict=True,
        ):
            schedule.append((day + timedelta(hours=future_hour), volume_oz))

    schedule.sort(key=lambda entry: entry[0])
    return schedule, anchor_labels


def _minimum_gap_minutes(schedule: Sequence[ScheduleEntry]) -> float:
    """Return the shortest gap between consecutive schedule entries."""
    return min(
        (later[0] - earlier[0]).total_seconds() / 60
        for earlier, later in zip(schedule, schedule[1:])
    )


def _query_fixture() -> tuple[
    list[ScheduleEntry],
    datetime,
    list[datetime],
    list[float],
    dict[datetime, str],
]:
    """Return a clean A-archetype query state with known future."""
    schedule, anchor_labels = _generate_archetype_schedule(day_count=QUERY_DAY_COUNT)
    cutoff = START_DAY + timedelta(days=QUERY_DAY_INDEX, hours=ANCHOR_HOUR)
    horizon_end = cutoff + timedelta(hours=24)

    history_schedule = [entry for entry in schedule if entry[0] <= cutoff]
    future_schedule = [entry for entry in schedule if cutoff < entry[0] < horizon_end]

    return (
        history_schedule,
        cutoff,
        [timestamp for timestamp, _ in future_schedule],
        [volume_oz for _, volume_oz in future_schedule],
        anchor_labels,
    )


def _candidate_for_params(
    payload: dict[str, object],
    *,
    lookback_hours: int,
    feature_weights: Sequence[float],
    k_neighbors: int,
) -> dict[str, object]:
    """Return one replay candidate by its parameter tuple."""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise AssertionError("Replay payload missing candidates list.")

    expected_params = {
        "FEATURE_WEIGHTS": list(feature_weights),
        "K_NEIGHBORS": k_neighbors,
        "LOOKBACK_HOURS": lookback_hours,
    }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("params") == expected_params:
            return candidate

    raise AssertionError(f"Replay payload missing candidate {expected_params!r}.")


def test_retrieval_recovers_same_archetype_neighbors_on_clean_history() -> None:
    """Nearest-neighbor retrieval should land on the planted matching archetype."""
    history_schedule, cutoff, _, _, anchor_labels = _query_fixture()
    activities = bottle_activities_from_schedule(history_schedule)

    with override_constants(MODEL_MODULE, FOCUSED_OVERRIDES):
        forecast = analog_model.forecast_analog_trajectory(
            activities,
            cutoff,
            horizon_hours=24,
        )

    same_archetype_anchor_times = {
        time.isoformat()
        for time, archetype in anchor_labels.items()
        if time < cutoff and archetype == anchor_labels[cutoff]
    }
    neighbors = forecast.diagnostics["neighbors"]

    assert forecast.diagnostics["complete_states"] >= analog_model.MIN_COMPLETE_STATES
    assert len(neighbors) == FOCUSED_OVERRIDES["K_NEIGHBORS"]
    assert {
        neighbor["state_time"] for neighbor in neighbors
    } <= same_archetype_anchor_times

    for index, neighbor in enumerate(neighbors, start=1):
        assert_value_within_tolerance(
            actual=float(neighbor["distance"]),
            expected=0.0,
            tolerance=1e-9,
            name=f"neighbor_distance[{index}]",
        )


def test_forecast_matches_planted_archetype_future_on_new_occurrence() -> None:
    """The public forecaster should reproduce the known future of the query archetype."""
    history_schedule, cutoff, expected_times, expected_volumes, _ = _query_fixture()
    activities = bottle_activities_from_schedule(history_schedule)

    with override_constants(MODEL_MODULE, FOCUSED_OVERRIDES):
        forecast = analog_model.forecast_analog_trajectory(
            activities,
            cutoff,
            horizon_hours=24,
        )

    assert forecast.diagnostics["complete_states"] >= analog_model.MIN_COMPLETE_STATES
    assert len(forecast.diagnostics["neighbors"]) == FOCUSED_OVERRIDES["K_NEIGHBORS"]
    assert len(forecast.points) == len(expected_times)

    assert_forecast_times_close(
        points=forecast.points,
        expected_times=expected_times,
        tolerance_minutes=0.01,
    )
    for index, (point, expected_volume) in enumerate(
        zip(forecast.points, expected_volumes, strict=True),
        start=1,
    ):
        assert_value_within_tolerance(
            actual=point.volume_oz,
            expected=expected_volume,
            tolerance=1e-9,
            name=f"forecast_volume[{index}]",
        )


def test_canonical_replay_prefers_focused_archetype_matching_regime(
    replay_output_dir,
    write_simulation_export,
) -> None:
    """Replay should reward the regime that preserves clean archetype retrieval."""
    schedule, _ = _generate_archetype_schedule(
        day_count=QUERY_DAY_COUNT,
        jittered=True,
    )

    # Replay evaluates the model with its shipping HISTORY_MODE="episode".
    # This DGP keeps every gap above the clustering-extension boundary, so
    # episode collapse is intentionally a no-op and replay exercises the same
    # event sequence as the raw-history specification tests.
    assert _minimum_gap_minutes(schedule) > 80.0

    export_path = write_simulation_export(
        bottle_activities_from_schedule(schedule),
    )
    payload = tune_model(
        "analog_trajectory",
        candidates_by_name=CANONICAL_GRID,
        export_path=export_path,
        output_dir=replay_output_dir,
        lookback_hours=96,
    )

    focused = _candidate_for_params(
        payload,
        lookback_hours=12,
        feature_weights=RECENT_ONLY,
        k_neighbors=5,
    )
    mixed_neighbors = _candidate_for_params(
        payload,
        lookback_hours=12,
        feature_weights=RECENT_ONLY,
        k_neighbors=9,
    )
    blurred_means = _candidate_for_params(
        payload,
        lookback_hours=72,
        feature_weights=MEANS_ONLY,
        k_neighbors=5,
    )

    focused_rw = focused["replay_windows"]
    mixed_neighbors_rw = mixed_neighbors["replay_windows"]
    blurred_means_rw = blurred_means["replay_windows"]
    assert isinstance(focused_rw, dict)
    assert isinstance(mixed_neighbors_rw, dict)
    assert isinstance(blurred_means_rw, dict)

    assert focused_rw["scored_window_count"] == focused_rw["window_count"]
    assert (
        mixed_neighbors_rw["scored_window_count"] == mixed_neighbors_rw["window_count"]
    )
    assert blurred_means_rw["scored_window_count"] == blurred_means_rw["window_count"]

    assert (
        float(focused_rw["aggregate"]["headline"])
        > float(mixed_neighbors_rw["aggregate"]["headline"]) + 0.25
    )
    assert (
        float(focused_rw["aggregate"]["headline"])
        > float(blurred_means_rw["aggregate"]["headline"]) + 0.25
    )
