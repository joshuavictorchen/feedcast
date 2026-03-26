"""Behavior tests for feed episode grouping."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.clustering import (
    BASE_GAP_MINUTES,
    EXTENSION_GAP_MINUTES,
    SECOND_FEED_MAX_OZ,
    FeedEpisode,
    group_into_episodes,
)
from feedcast.data import FeedEvent, ForecastPoint


def _event(
    base: datetime,
    offset_minutes: float,
    volume_oz: float = 3.0,
) -> FeedEvent:
    """Build a FeedEvent at a fixed offset from base."""
    return FeedEvent(
        time=base + timedelta(minutes=offset_minutes),
        volume_oz=volume_oz,
        bottle_volume_oz=volume_oz,
        breastfeeding_volume_oz=0.0,
    )


def _point(
    base: datetime,
    offset_minutes: float,
    volume_oz: float = 3.0,
) -> ForecastPoint:
    """Build a ForecastPoint at a fixed offset from base."""
    return ForecastPoint(
        time=base + timedelta(minutes=offset_minutes),
        volume_oz=volume_oz,
        gap_hours=offset_minutes / 60,
    )


class GroupIntoEpisodesTests(unittest.TestCase):
    """Episode grouping behavior tests."""

    BASE = datetime(2026, 3, 24, 8, 0, 0)

    def test_empty_input(self) -> None:
        """Empty list returns no episodes."""
        self.assertEqual(group_into_episodes([]), [])

    def test_single_feed(self) -> None:
        """A lone feed becomes a single-feed episode."""
        events = [_event(self.BASE, 0)]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].feed_count, 1)
        self.assertEqual(episodes[0].time, events[0].time)
        self.assertEqual(episodes[0].volume_oz, 3.0)

    def test_all_singletons(self) -> None:
        """Feeds separated by wide gaps produce one episode each."""
        events = [
            _event(self.BASE, 0),
            _event(self.BASE, 180),  # 3 hours later
            _event(self.BASE, 360),  # 6 hours later
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 3)
        for episode in episodes:
            self.assertEqual(episode.feed_count, 1)

    def test_simple_cluster(self) -> None:
        """A large feed followed by a small top-up within the base gap."""
        events = [
            _event(self.BASE, 0, volume_oz=3.0),
            _event(self.BASE, 50, volume_oz=1.0),  # 50 min, clearly within 73
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].feed_count, 2)
        self.assertEqual(episodes[0].time, events[0].time)
        self.assertAlmostEqual(episodes[0].volume_oz, 4.0)

    def test_multi_attachment_cluster(self) -> None:
        """Three feeds chaining transitively into one episode.

        Mirrors the motivating 3/24 example: 20:16 -> 21:25 -> 22:15.
        """
        events = [
            _event(self.BASE, 0, volume_oz=3.6),
            _event(self.BASE, 69, volume_oz=1.0),   # 69 min from anchor
            _event(self.BASE, 119, volume_oz=1.5),   # 50 min from previous
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].feed_count, 3)
        self.assertAlmostEqual(episodes[0].volume_oz, 6.1)

    def test_extension_arm_captures_small_second_feed(self) -> None:
        """Gap in (73, 80] with small second feed → same episode."""
        events = [
            _event(self.BASE, 0, volume_oz=4.0),
            _event(self.BASE, 77.5, volume_oz=1.25),  # within extension
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].feed_count, 2)

    def test_extension_arm_rejects_large_second_feed(self) -> None:
        """Gap in (73, 80] with large second feed → new episode."""
        events = [
            _event(self.BASE, 0, volume_oz=3.0),
            _event(self.BASE, 77, volume_oz=2.0),  # too large for extension
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 2)

    def test_base_gap_boundary_inclusive(self) -> None:
        """Gap exactly at BASE_GAP_MINUTES → same episode."""
        events = [
            _event(self.BASE, 0),
            _event(self.BASE, BASE_GAP_MINUTES),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)

    def test_just_beyond_base_gap_large_volume(self) -> None:
        """Gap just above base threshold with large second feed → new episode."""
        events = [
            _event(self.BASE, 0, volume_oz=3.0),
            _event(self.BASE, BASE_GAP_MINUTES + 1, volume_oz=3.0),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 2)

    def test_extension_boundary_inclusive(self) -> None:
        """Gap exactly at EXTENSION_GAP_MINUTES with small feed → same episode."""
        events = [
            _event(self.BASE, 0),
            _event(self.BASE, EXTENSION_GAP_MINUTES, volume_oz=SECOND_FEED_MAX_OZ),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)

    def test_beyond_extension_gap(self) -> None:
        """Gap beyond extension threshold → new episode regardless of volume."""
        events = [
            _event(self.BASE, 0),
            _event(self.BASE, EXTENSION_GAP_MINUTES + 1, volume_oz=0.5),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 2)

    def test_small_anchor(self) -> None:
        """A small feed can anchor a cluster (no minimum anchor volume)."""
        events = [
            _event(self.BASE, 0, volume_oz=1.0),   # small anchor
            _event(self.BASE, 40, volume_oz=3.0),   # larger second feed
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].feed_count, 2)
        self.assertAlmostEqual(episodes[0].volume_oz, 4.0)

    def test_back_to_back_clusters(self) -> None:
        """Two separate clusters with a wide gap between them."""
        events = [
            # Cluster 1
            _event(self.BASE, 0, volume_oz=3.0),
            _event(self.BASE, 50, volume_oz=1.0),
            # Wide gap
            # Cluster 2
            _event(self.BASE, 250, volume_oz=3.5),
            _event(self.BASE, 300, volume_oz=1.0),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0].feed_count, 2)
        self.assertEqual(episodes[1].feed_count, 2)

    def test_unsorted_input_raises(self) -> None:
        """Non-chronological input raises ValueError."""
        events = [
            _event(self.BASE, 60),
            _event(self.BASE, 0),  # before the first
        ]
        with self.assertRaises(ValueError):
            group_into_episodes(events)

    def test_duplicate_timestamps_raises(self) -> None:
        """Feeds at the same timestamp are rejected as non-increasing."""
        events = [
            _event(self.BASE, 0, volume_oz=2.0),
            _event(self.BASE, 0, volume_oz=1.0),
        ]
        with self.assertRaises(ValueError):
            group_into_episodes(events)

    def test_constituents_preserved(self) -> None:
        """Episode stores references to the original feed objects."""
        events = [
            _event(self.BASE, 0, volume_oz=3.0),
            _event(self.BASE, 50, volume_oz=1.0),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes[0].constituents), 2)
        self.assertIs(episodes[0].constituents[0], events[0])
        self.assertIs(episodes[0].constituents[1], events[1])

    def test_forecast_points(self) -> None:
        """Works with ForecastPoint inputs, not just FeedEvent."""
        points = [
            _point(self.BASE, 0, volume_oz=3.0),
            _point(self.BASE, 50, volume_oz=1.0),   # cluster
            _point(self.BASE, 200, volume_oz=4.0),   # standalone
        ]
        episodes = group_into_episodes(points)

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0].feed_count, 2)
        self.assertEqual(episodes[1].feed_count, 1)
        # Constituents are ForecastPoint objects
        self.assertIsInstance(episodes[0].constituents[0], ForecastPoint)

    def test_transitive_chaining_exceeds_extension(self) -> None:
        """Transitive chaining produces one episode even when the total span
        exceeds the extension window.

        A->B is 60 min, B->C is 60 min. A->C is 120 min, well beyond 80 min,
        but both individual boundaries satisfy the base rule.
        """
        events = [
            _event(self.BASE, 0, volume_oz=3.0),
            _event(self.BASE, 60, volume_oz=2.0),
            _event(self.BASE, 120, volume_oz=1.5),
        ]
        episodes = group_into_episodes(events)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].feed_count, 3)


if __name__ == "__main__":
    unittest.main()
