"""Behavior tests for retrospective forecast scoring."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.data import FeedEvent, ForecastPoint
from feedcast.scoring import score_forecast


def _point(cutoff: datetime, offset_hours: float) -> ForecastPoint:
    """Build one forecast point at a fixed offset from cutoff."""
    return ForecastPoint(
        time=cutoff + timedelta(hours=offset_hours),
        volume_oz=4.0,
        gap_hours=offset_hours,
    )


def _event(cutoff: datetime, offset_hours: float) -> FeedEvent:
    """Build one bottle-feed event at a fixed offset from cutoff."""
    return FeedEvent(
        time=cutoff + timedelta(hours=offset_hours),
        volume_oz=4.0,
        bottle_volume_oz=4.0,
        breastfeeding_volume_oz=0.0,
    )


class ScoreForecastTests(unittest.TestCase):
    """End-to-end scoring behavior checks."""

    def test_perfect_match_scores_full_credit(self) -> None:
        """Exact matches should receive full count and timing credit."""
        cutoff = datetime(2026, 3, 24, 8, 0, 0)
        result = score_forecast(
            predicted_points=[_point(cutoff, 2), _point(cutoff, 6)],
            actual_events=[_event(cutoff, 2), _event(cutoff, 6)],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=24),
        )

        self.assertEqual(result.score, 100.0)
        self.assertEqual(result.count_score, 100.0)
        self.assertEqual(result.timing_score, 100.0)
        self.assertEqual(result.matched_count, 2)

    def test_thirty_minute_error_halves_timing_credit(self) -> None:
        """The timing half-life should be soft rather than a hard threshold."""
        cutoff = datetime(2026, 3, 24, 8, 0, 0)
        result = score_forecast(
            predicted_points=[_point(cutoff, 2.5)],
            actual_events=[_event(cutoff, 2)],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=24),
        )

        self.assertEqual(result.count_score, 100.0)
        self.assertAlmostEqual(result.timing_score, 50.0, places=3)
        self.assertAlmostEqual(result.score, 70.711, places=3)

    def test_partial_window_penalizes_false_positive_before_any_bottle_feed(
        self,
    ) -> None:
        """Elapsed time with no bottle feed is still evidence for scoring."""
        cutoff = datetime(2026, 3, 24, 8, 0, 0)
        result = score_forecast(
            predicted_points=[_point(cutoff, 1)],
            actual_events=[],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=2),
        )

        self.assertEqual(result.predicted_count, 1)
        self.assertEqual(result.actual_count, 0)
        self.assertEqual(result.matched_count, 0)
        self.assertEqual(result.count_score, 0.0)
        self.assertEqual(result.timing_score, 0.0)
        self.assertEqual(result.score, 0.0)
        self.assertAlmostEqual(result.coverage_ratio, 2 / 24, places=6)

    def test_over_and_under_count_are_penalized_symmetrically(self) -> None:
        """Missing one feed and predicting one extra feed should hurt equally."""
        cutoff = datetime(2026, 3, 24, 8, 0, 0)
        extra_prediction = score_forecast(
            predicted_points=[_point(cutoff, 1), _point(cutoff, 2)],
            actual_events=[_event(cutoff, 1)],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=24),
        )
        missed_prediction = score_forecast(
            predicted_points=[_point(cutoff, 1)],
            actual_events=[_event(cutoff, 1), _event(cutoff, 2)],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=24),
        )

        self.assertAlmostEqual(
            extra_prediction.count_score,
            missed_prediction.count_score,
            places=3,
        )

    def test_guardrail_blocks_absurd_pairings(self) -> None:
        """Events more than four hours apart should remain unmatched."""
        cutoff = datetime(2026, 3, 24, 8, 0, 0)
        result = score_forecast(
            predicted_points=[_point(cutoff, 1)],
            actual_events=[_event(cutoff, 6)],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=24),
        )

        self.assertEqual(result.matched_count, 0)
        self.assertEqual(result.count_score, 0.0)
        self.assertEqual(result.timing_score, 0.0)


if __name__ == "__main__":
    unittest.main()
