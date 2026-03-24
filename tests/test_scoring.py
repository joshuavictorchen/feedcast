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

    def test_multi_feed_assignment_handles_extra_actual_feed(self) -> None:
        """Hungarian matching should recover the best set of real feed pairings."""
        cutoff = datetime(2026, 3, 24, 8, 0, 0)
        predicted_offsets = [2.0, 6.0, 10.0, 14.0, 18.0]
        actual_offsets = [2.25, 6.5, 9.75, 14.25, 18.5, 22.5]
        result = score_forecast(
            predicted_points=[_point(cutoff, offset) for offset in predicted_offsets],
            actual_events=[_event(cutoff, offset) for offset in actual_offsets],
            prediction_time=cutoff,
            observed_until=cutoff + timedelta(hours=24),
        )

        matched_actual_weights = [2 ** (-offset / 24) for offset in actual_offsets[:-1]]
        recall = sum(matched_actual_weights) / sum(
            2 ** (-offset / 24) for offset in actual_offsets
        )
        expected_count_score = 100 * (2 * recall / (1 + recall))
        timing_credits = [2 ** (-error / 30) for error in [15, 30, 15, 15, 30]]
        expected_timing_score = 100 * (
            sum(
                weight * credit
                for weight, credit in zip(matched_actual_weights, timing_credits)
            )
            / sum(matched_actual_weights)
        )

        self.assertEqual(result.predicted_count, 5)
        self.assertEqual(result.actual_count, 6)
        self.assertEqual(result.matched_count, 5)
        self.assertAlmostEqual(result.count_score, expected_count_score, places=3)
        self.assertAlmostEqual(result.timing_score, expected_timing_score, places=3)


if __name__ == "__main__":
    unittest.main()
