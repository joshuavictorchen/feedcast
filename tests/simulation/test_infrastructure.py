"""Behavior tests for shared simulation infrastructure."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from feedcast.data import Activity, ForecastPoint, load_activities
from feedcast.replay import score_model

from tests.simulation.assertions import (
    assert_forecast_times_close,
    assert_replay_best_param_within_tolerance,
)
from tests.simulation.export import validate_export_activities
from tests.simulation.factories import (
    bottle_activities_from_schedule,
    bottle_activity,
    breastfeed_activity,
)


def _activity_signature(activity: Activity) -> tuple[str, datetime, datetime, float]:
    """Return the semantic fields that should round-trip through export parsing."""
    return (
        activity.kind,
        activity.start,
        activity.end,
        round(activity.volume_oz, 6),
    )


def test_bottle_schedule_factory_sorts_and_preserves_volume() -> None:
    """Bottle schedule conversion should return production-shaped Activities."""
    base = datetime(2026, 3, 18, 6, 0, 0)
    activities = bottle_activities_from_schedule(
        [
            (base + timedelta(hours=6), 4.0),
            (base, 2.5),
            (base + timedelta(hours=3), 3.0),
        ]
    )

    assert [activity.kind for activity in activities] == ["bottle", "bottle", "bottle"]
    assert [activity.start for activity in activities] == [
        base,
        base + timedelta(hours=3),
        base + timedelta(hours=6),
    ]
    assert [activity.volume_oz for activity in activities] == [2.5, 3.0, 4.0]


def test_export_writer_round_trips_bottle_and_breastfeed_activities(
    write_simulation_export,
) -> None:
    """Synthetic exports should parse back into the same semantic activities."""
    base = datetime(2026, 3, 18, 8, 0, 0)
    original = [
        breastfeed_activity(base, duration_minutes=30),
        bottle_activity(base + timedelta(hours=1), 3.5),
        breastfeed_activity(base + timedelta(hours=4), duration_minutes=20),
        bottle_activity(base + timedelta(hours=5), 4.0),
    ]

    export_path = write_simulation_export(original)
    parsed = load_activities(export_path)

    assert [_activity_signature(activity) for activity in parsed] == [
        _activity_signature(activity) for activity in original
    ]


def test_validate_export_activities_fails_fast_before_data_floor() -> None:
    """Synthetic export validation should reject timestamps production would drop."""
    with pytest.raises(ValueError, match="DATA_FLOOR"):
        validate_export_activities(
            [bottle_activity(datetime(2026, 3, 14, 23, 59, 0), 3.0)]
        )


def test_replay_compatible_export_supports_score_model(
    write_simulation_export,
    replay_output_dir,
) -> None:
    """The shared export path should work end-to-end with replay scoring."""
    base = datetime(2026, 3, 15, 0, 0, 0)
    activities = bottle_activities_from_schedule(
        [
            (base + timedelta(hours=3 * index), 4.0)
            for index in range(48)
        ]
    )

    export_path = write_simulation_export(activities, filename_date="20260320")
    payload = score_model(
        "slot_drift",
        export_path=export_path,
        output_dir=replay_output_dir,
    )

    assert payload["mode"] == "score"
    assert payload["replay_windows"]["scored_window_count"] > 0


def test_shared_assertions_cover_timing_and_best_param_shapes() -> None:
    """Shared assertions should be usable from simulation tests without wrappers."""
    cutoff = datetime(2026, 3, 24, 8, 0, 0)
    points = [
        ForecastPoint(
            time=cutoff + timedelta(hours=2),
            volume_oz=4.0,
            gap_hours=2.0,
        ),
        ForecastPoint(
            time=cutoff + timedelta(hours=5),
            volume_oz=4.0,
            gap_hours=3.0,
        ),
    ]
    expected_times = [cutoff + timedelta(hours=2), cutoff + timedelta(hours=5)]

    assert_forecast_times_close(
        points=points,
        expected_times=expected_times,
        tolerance_minutes=1.0,
    )

    payload = {"best": {"params": {"SATIETY_RATE": 0.2}}}
    assert_replay_best_param_within_tolerance(
        payload,
        param_name="SATIETY_RATE",
        expected=0.2,
        tolerance=0.01,
    )
