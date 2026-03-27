"""Behavior tests for the Slot Drift model's episode-level history."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.data import Activity, FeedEvent
from feedcast.clustering import episodes_as_events
from feedcast.models.slot_drift.model import (
    _determine_slot_count,
    _group_by_day,
    _recent_complete_days,
    forecast_slot_drift,
    LOOKBACK_DAYS,
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


def _build_day_feeds(
    day: datetime,
    hour_volumes: list[tuple[float, float]],
) -> list[FeedEvent]:
    """Build feed events for one day at given (hour, volume) pairs."""
    return [
        _feed(
            datetime(day.year, day.month, day.day)
            + timedelta(hours=hour),
            volume_oz=volume,
        )
        for hour, volume in hour_volumes
    ]


class EpisodesAsEventsTests(unittest.TestCase):
    """Test the episode collapse helper."""

    def test_cluster_feeds_collapse_into_one_event(self) -> None:
        """A main feed + top-up within 73 minutes becomes one episode event."""
        main_feed = _feed(datetime(2026, 3, 24, 14, 45), 3.0)
        topup = _feed(datetime(2026, 3, 24, 15, 35), 1.0)
        result = episodes_as_events([main_feed, topup])
        self.assertEqual(len(result), 1)
        # Episode uses the first feed's timestamp.
        self.assertEqual(result[0].time, main_feed.time)
        # Volume is summed.
        self.assertAlmostEqual(result[0].volume_oz, 4.0)

    def test_independent_feeds_stay_separate(self) -> None:
        """Feeds separated by more than 80 minutes remain independent."""
        feed_a = _feed(datetime(2026, 3, 24, 10, 0), 3.0)
        feed_b = _feed(datetime(2026, 3, 24, 12, 0), 3.5)
        result = episodes_as_events([feed_a, feed_b])
        self.assertEqual(len(result), 2)


class SlotCountFromEpisodesTests(unittest.TestCase):
    """Slot count should reflect episode counts, not raw feed counts."""

    def test_cluster_day_does_not_inflate_slot_count(self) -> None:
        """Days with cluster feeds should not inflate the median slot count.

        Constructs 5 days: 3 have 8 clean feeds, 2 have 8 feeds plus
        cluster top-ups (10 raw feeds each). With raw feeds the median
        would be 8-10 depending on distribution. With episode collapse,
        all days have 8 episodes and the median is 8.
        """
        cutoff = datetime(2026, 3, 25, 12, 0)
        # Regular daily template: 8 feeds spaced ~3 hours apart.
        regular_hours = [
            (1.0, 3.0), (4.0, 3.0), (7.0, 3.0), (10.0, 3.0),
            (13.0, 3.0), (16.0, 3.0), (19.0, 3.0), (22.0, 3.0),
        ]
        # Cluster day: 8 regular feeds + 2 top-ups within 73 min.
        cluster_hours = sorted(regular_hours + [
            (10.5, 1.0),  # 30 min after the 10:00 feed
            (16.8, 1.0),  # 48 min after the 16:00 feed
        ])

        history: list[FeedEvent] = []
        for day_offset in range(5):
            day = datetime(2026, 3, 20 + day_offset)
            hours = cluster_hours if day_offset >= 3 else regular_hours
            history.extend(_build_day_feeds(day, hours))

        # With episode collapse, all days should have 8 episodes.
        episode_history = episodes_as_events(history)
        daily = _group_by_day(episode_history, cutoff)
        complete_days = _recent_complete_days(daily, cutoff)
        slot_count = _determine_slot_count(complete_days)
        self.assertEqual(slot_count, 8)


class SlotDriftDiagnosticsTests(unittest.TestCase):
    """Diagnostics should use episode-level naming."""

    def test_diagnostics_use_episode_keys(self) -> None:
        """Diagnostics should report episode counts, not raw feed counts."""
        cutoff = datetime(2026, 3, 25, 12, 0)
        regular_hours = [
            (1.0, 3.0), (4.0, 3.0), (7.0, 3.0), (10.0, 3.0),
            (13.0, 3.0), (16.0, 3.0), (19.0, 3.0), (22.0, 3.0),
        ]
        # Build 5 days of clean 8-feed data as activities.
        activities: list[Activity] = []
        for day_offset in range(5):
            day = datetime(2026, 3, 20 + day_offset)
            for hour, volume in regular_hours:
                activities.append(_bottle_activity(
                    datetime(day.year, day.month, day.day) + timedelta(hours=hour),
                    volume_oz=volume,
                ))

        result = forecast_slot_drift(activities, cutoff, horizon_hours=24)
        diag = result.diagnostics
        # Episode-level keys should exist, raw-level keys should not.
        self.assertIn("daily_episode_counts", diag)
        self.assertNotIn("daily_feed_counts", diag)
        for day_quality in diag["per_day_match_quality"].values():
            self.assertIn("total_episodes", day_quality)
            self.assertNotIn("total_feeds", day_quality)


if __name__ == "__main__":
    unittest.main()
