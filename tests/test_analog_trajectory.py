"""Behavior tests for the Analog Trajectory model."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import numpy as np

from feedcast.data import Activity
from feedcast.models.analog_trajectory.model import (
    ALIGNMENT,
    FEATURE_WEIGHTS,
    HISTORY_MODE,
    K_NEIGHBORS,
    LOOKBACK_HOURS,
    RECENCY_HALF_LIFE_HOURS,
    TRAJECTORY_LENGTH_METHOD,
    forecast_analog_trajectory,
)
from feedcast.replay import override_constants

MODEL_MODULE = "feedcast.models.analog_trajectory.model"


def _bottle_activity(time: datetime, volume_oz: float = 3.5) -> Activity:
    """Build a bottle activity for forecast tests."""
    return Activity(
        kind="bottle",
        start=time,
        end=time,
        volume_oz=volume_oz,
        raw_fields={},
    )


class LookbackOverrideTests(unittest.TestCase):
    """Replay overrides should change analog lookback behavior."""

    def test_lookback_override_changes_query_mean_gap(self) -> None:
        """12h and 72h overrides should produce different query features."""
        base = datetime(2026, 3, 18, 0, 0)
        activities: list[Activity] = []
        current = base
        for index in range(24):
            activities.append(_bottle_activity(current, 3.5))
            if index < 11:
                current += timedelta(hours=4)
            else:
                current += timedelta(hours=2)

        cutoff = activities[-1].start

        with override_constants(
            MODEL_MODULE,
            {"HISTORY_MODE": "raw", "LOOKBACK_HOURS": 12},
        ):
            short_forecast = forecast_analog_trajectory(
                activities,
                cutoff,
                horizon_hours=24,
            )

        with override_constants(
            MODEL_MODULE,
            {"HISTORY_MODE": "raw", "LOOKBACK_HOURS": 72},
        ):
            long_forecast = forecast_analog_trajectory(
                activities,
                cutoff,
                horizon_hours=24,
            )

        short_mean_gap = short_forecast.diagnostics["query_features"]["mean_gap"]
        long_mean_gap = long_forecast.diagnostics["query_features"]["mean_gap"]

        self.assertLess(short_mean_gap, 2.5)
        self.assertGreater(long_mean_gap, 2.8)
        self.assertNotEqual(short_mean_gap, long_mean_gap)


class HistoryModeTests(unittest.TestCase):
    """History mode should change the state library the model sees."""

    def test_episode_history_collapses_cluster_states(self) -> None:
        """Episode mode should reduce the number of complete states."""
        base = datetime(2026, 3, 18, 0, 0)
        activities = [
            _bottle_activity(base + timedelta(hours=3 * index), 3.5)
            for index in range(30)
        ]
        for offset_hours in [18, 42, 66]:
            activities.append(
                _bottle_activity(
                    base + timedelta(hours=offset_hours, minutes=50),
                    1.0,
                )
            )
        activities.sort(key=lambda activity: activity.start)
        cutoff = activities[-1].start

        with override_constants(MODEL_MODULE, {"HISTORY_MODE": "raw"}):
            raw_forecast = forecast_analog_trajectory(
                activities,
                cutoff,
                horizon_hours=24,
            )

        with override_constants(MODEL_MODULE, {"HISTORY_MODE": "episode"}):
            episode_forecast = forecast_analog_trajectory(
                activities,
                cutoff,
                horizon_hours=24,
            )

        self.assertEqual(raw_forecast.diagnostics["history_mode"], "raw")
        self.assertEqual(episode_forecast.diagnostics["history_mode"], "episode")
        self.assertGreater(
            raw_forecast.diagnostics["complete_states"],
            episode_forecast.diagnostics["complete_states"],
        )


class CanonicalConstantsTests(unittest.TestCase):
    """The shipped analog constants should reflect the canonical winner."""

    def test_constants_match_canonical_winner(self) -> None:
        """4.4 should leave the production constants at the best replay config."""
        self.assertEqual(HISTORY_MODE, "episode")
        self.assertEqual(LOOKBACK_HOURS, 9)
        self.assertEqual(K_NEIGHBORS, 7)
        self.assertEqual(RECENCY_HALF_LIFE_HOURS, 120)
        self.assertEqual(TRAJECTORY_LENGTH_METHOD, "median")
        self.assertEqual(ALIGNMENT, "gap")
        np.testing.assert_allclose(
            FEATURE_WEIGHTS,
            np.array([1.0, 1.0, 1.0, 1.0, 2.0, 2.0]),
        )


if __name__ == "__main__":
    unittest.main()
