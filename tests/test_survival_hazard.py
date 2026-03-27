"""Behavior tests for the Survival Hazard model's episode-level history."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.clustering import episodes_as_events
from feedcast.data import Activity, FeedEvent
from feedcast.models.survival_hazard.model import (
    DAYTIME_SHAPE,
    OVERNIGHT_SHAPE,
    _estimate_daypart_scales,
    forecast_survival_hazard,
)


def _feed(time: datetime, volume_oz: float = 3.0) -> FeedEvent:
    """Build a bottle-only feed event."""
    return FeedEvent(
        time=time,
        volume_oz=volume_oz,
        bottle_volume_oz=volume_oz,
        breastfeeding_volume_oz=0.0,
    )


def _bottle_activity(time: datetime, volume_oz: float = 3.0) -> Activity:
    """Build a bottle activity for passing to forecast functions."""
    return Activity(
        kind="bottle", start=time, end=time, volume_oz=volume_oz, raw_fields={},
    )


class EpisodeLevelScaleEstimationTests(unittest.TestCase):
    """Scale estimation should use episode-level gaps, not raw gaps."""

    def test_cluster_gaps_excluded_from_scale_estimation(self) -> None:
        """A cluster top-up should not produce a short gap in scale estimation.

        Without episode collapse, a 3.0 oz feed followed 50 min later by a
        1.0 oz top-up contributes a 0.83h gap to the Weibull scale estimate —
        far shorter than any real inter-episode gap. With collapse, the pair
        becomes one 4.0 oz episode and only inter-episode gaps are used.
        """
        base = datetime(2026, 3, 24, 12, 0)
        # Build 8 regular daytime feeds (~2.5h apart) followed by a cluster.
        events = []
        for i in range(8):
            events.append(_feed(base + timedelta(hours=2.5 * i), 3.0))
        # Cluster: main feed + top-up 50 min later.
        cluster_time = base + timedelta(hours=20)
        events.append(_feed(cluster_time, 3.0))
        events.append(_feed(cluster_time + timedelta(minutes=50), 1.0))
        # One more feed to close the last gap.
        events.append(_feed(cluster_time + timedelta(hours=2.5), 3.0))

        cutoff = events[-1].time

        # Episode collapse should remove the 50-min gap.
        episode_events = episodes_as_events(events)
        _, _, details = _estimate_daypart_scales(episode_events, cutoff)

        # No fit detail should have a gap under 1 hour.
        gap_hours = [d["gap_hours"] for d in details]
        self.assertTrue(
            all(g >= 1.0 for g in gap_hours),
            f"Found sub-1h gap in fit details (cluster not collapsed): {gap_hours}",
        )

    def test_episode_volume_used_for_sim_volume(self) -> None:
        """Simulation volume should reflect total episode volume, not just anchor.

        A 3.0+1.0 oz cluster = 4.0 oz episode. The median volume for
        simulation should be computed from episode volumes.
        """
        base = datetime(2026, 3, 18, 0, 0)
        # Build regular feeds with one cluster pair.
        activities = [
            _bottle_activity(base + timedelta(hours=3 * i), 3.5)
            for i in range(25)
        ]
        # Add a cluster: two feeds 50 min apart.
        cluster_time = base + timedelta(hours=75)
        activities.append(_bottle_activity(cluster_time, 3.0))
        activities.append(
            _bottle_activity(cluster_time + timedelta(minutes=50), 1.5)
        )
        # More regular feeds after the cluster.
        for i in range(4):
            activities.append(
                _bottle_activity(
                    cluster_time + timedelta(hours=3 * (i + 1)), 3.5,
                )
            )
        cutoff = activities[-1].start
        forecast = forecast_survival_hazard(activities, cutoff, horizon_hours=24)

        # The cluster episode (3.0+1.5=4.5 oz) should appear in the sim
        # volume computation. If raw events were used instead, the 1.5 oz
        # top-up would pull the median down.
        self.assertGreater(
            forecast.diagnostics["sim_volume_oz"], 3.0,
            "Sim volume should reflect episode volumes, not raw feed volumes",
        )


class DiagnosticsTests(unittest.TestCase):
    """Diagnostics keys should reflect episode-level semantics."""

    def test_diagnostics_use_episode_keys(self) -> None:
        """Forecast diagnostics should use 'episode' naming, not 'event'."""
        base = datetime(2026, 3, 18, 0, 0)
        activities = [
            _bottle_activity(base + timedelta(hours=3 * i), 3.5)
            for i in range(30)
        ]
        cutoff = activities[-1].start

        forecast = forecast_survival_hazard(activities, cutoff, horizon_hours=24)
        diag = forecast.diagnostics

        self.assertIn("total_fit_episode_gaps", diag)
        self.assertNotIn("total_fit_gaps", diag)


class ShapeConstantsTests(unittest.TestCase):
    """Verify the re-tuned shape constants are in place."""

    def test_shapes_are_episode_tuned(self) -> None:
        """Shape constants should be re-fitted from episode-level data."""
        # Episode-level overnight shape is higher (more regular) than the
        # raw-gap fit because cluster-internal gaps no longer depress it.
        self.assertAlmostEqual(OVERNIGHT_SHAPE, 6.54, places=2)
        self.assertAlmostEqual(DAYTIME_SHAPE, 3.04, places=2)


if __name__ == "__main__":
    unittest.main()
