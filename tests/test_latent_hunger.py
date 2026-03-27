"""Behavior tests for the Latent Hunger model's episode-level history."""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta

from feedcast.clustering import episodes_as_events
from feedcast.data import Activity, FeedEvent
from feedcast.models.latent_hunger.model import (
    SATIETY_RATE,
    _estimate_growth_rate,
    _hunger_after_feed,
    forecast_latent_hunger,
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


class EpisodeLevelGrowthRateTests(unittest.TestCase):
    """Growth rate estimation should use episode-level signals."""

    def test_cluster_pairs_excluded_from_growth_rate(self) -> None:
        """A cluster top-up should not produce a separate implied growth rate.

        Without episode collapse, a 3.0 oz feed followed 50 min later by a
        1.0 oz top-up generates an implied growth rate from a 0.83h gap —
        far shorter than any real inter-episode gap. With collapse, the pair
        becomes one 4.0 oz episode and the growth rate is estimated from
        the inter-episode gap only.
        """
        base = datetime(2026, 3, 24, 0, 0)
        # Build 8 regular feeds (~3h apart) followed by one cluster pair.
        events = []
        for i in range(8):
            events.append(_feed(base + timedelta(hours=3 * i), 3.5))
        # Cluster: main feed + top-up 50 min later.
        cluster_time = base + timedelta(hours=24)
        events.append(_feed(cluster_time, 3.0))
        events.append(_feed(cluster_time + timedelta(minutes=50), 1.0))
        # One more feed after the cluster to close the last gap.
        events.append(_feed(cluster_time + timedelta(hours=3), 3.5))

        cutoff = events[-1].time
        # The model converts to episodes before estimating growth rate.
        # Replicate that data flow here.
        episode_events = episodes_as_events(events)
        growth_rate, details = _estimate_growth_rate(episode_events, cutoff)

        # With episode collapse, no fit detail should have a gap under 1 hour.
        # The 50-min cluster-internal gap should not appear.
        gap_hours = [d["gap_hours"] for d in details]
        self.assertTrue(
            all(g >= 1.0 for g in gap_hours),
            f"Found sub-1h gap in fit details (cluster not collapsed): {gap_hours}",
        )

    def test_episode_volume_used_for_hunger_reset(self) -> None:
        """The hunger reset should use total episode volume, not just the anchor.

        A 3.0+1.0 oz cluster = 4.0 oz episode. The hunger after a 4.0 oz
        episode should be lower than after a 3.0 oz feed alone.
        """
        hunger_3oz = _hunger_after_feed(3.0)
        hunger_4oz = _hunger_after_feed(4.0)
        self.assertLess(
            hunger_4oz, hunger_3oz,
            "4.0 oz episode should produce a deeper hunger reset than 3.0 oz",
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

        forecast = forecast_latent_hunger(activities, cutoff, horizon_hours=24)
        diag = forecast.diagnostics

        self.assertIn("recent_episodes_in_window", diag)
        self.assertIn("fit_episodes_used", diag)
        self.assertNotIn("recent_events_in_window", diag)
        self.assertNotIn("fit_events_used", diag)


class SatietyRateTests(unittest.TestCase):
    """Verify the re-tuned satiety rate is in place."""

    def test_satiety_rate_is_episode_tuned(self) -> None:
        """SATIETY_RATE should be 0.257 (re-tuned on episode-level data)."""
        self.assertAlmostEqual(SATIETY_RATE, 0.257, places=3)


if __name__ == "__main__":
    unittest.main()
