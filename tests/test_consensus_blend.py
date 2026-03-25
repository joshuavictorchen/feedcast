"""Behavior tests for the consensus blend selector."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.data import FeedEvent, Forecast, ForecastPoint
from feedcast.models.consensus_blend.model import run_consensus_blend


def _history_event(base_time: datetime) -> FeedEvent:
    """Build one historical feed event."""
    return FeedEvent(
        time=base_time,
        volume_oz=4.0,
        bottle_volume_oz=4.0,
        breastfeeding_volume_oz=0.0,
    )


def _forecast(slug: str, offsets_hours: list[float]) -> Forecast:
    """Build one model forecast with points at fixed offsets."""
    cutoff = datetime(2026, 3, 24, 12, 0, 0)
    return Forecast(
        name=slug,
        slug=slug,
        points=[
            ForecastPoint(
                time=cutoff + timedelta(hours=offset),
                volume_oz=4.0,
                gap_hours=max(offset, 0.1),
            )
            for offset in offsets_hours
        ],
        methodology="",
        diagnostics={},
    )


class ConsensusBlendTests(unittest.TestCase):
    """End-to-end consensus blend behavior checks."""

    def test_majority_anchor_overrides_minority_outlier(self) -> None:
        """Three near-agreeing models should beat one late outlier."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [2.92]),
                _forecast("model_b", [3.0]),
                _forecast("model_c", [3.08]),
                _forecast("model_d", [4.0]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        self.assertEqual(len(forecast.points), 1)
        self.assertLess(
            abs((forecast.points[0].time - (cutoff + timedelta(hours=3))).total_seconds()),
            10 * 60,
        )

    def test_requires_simple_majority_of_available_models(self) -> None:
        """A 2-of-4 split should not emit a consensus point."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [3.0]),
                _forecast("model_b", [3.08]),
                _forecast("model_c", [6.0]),
                _forecast("model_d", [6.08]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertFalse(forecast.available)
        self.assertEqual(forecast.points, [])

    def test_two_of_three_available_models_is_a_majority(self) -> None:
        """When only three models are available, 2-of-3 should survive."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [3.0]),
                _forecast("model_b", [3.08]),
                _forecast("model_c", [6.5]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        self.assertEqual(len(forecast.points), 1)
        self.assertLess(
            abs((forecast.points[0].time - (cutoff + timedelta(hours=3))).total_seconds()),
            10 * 60,
        )

    def test_sequence_selector_collapses_overlapping_majority_slots(self) -> None:
        """Two majority explanations inside one conflict window should collapse."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [3.0, 4.17]),
                _forecast("model_b", [3.08, 4.25]),
                _forecast("model_c", [3.17, 4.33]),
                _forecast("model_d", [3.25, 4.42]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        self.assertEqual(len(forecast.points), 1)

    def test_sequence_selector_keeps_separate_majority_feeds(self) -> None:
        """Majority-supported feeds outside the conflict window should both survive."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [3.0, 5.5]),
                _forecast("model_b", [3.08, 5.58]),
                _forecast("model_c", [3.17, 5.67]),
                _forecast("model_d", [3.25, 5.75]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        self.assertEqual(len(forecast.points), 2)


if __name__ == "__main__":
    unittest.main()
