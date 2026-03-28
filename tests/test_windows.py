"""Behavior tests for shared multi-window evaluation helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.clustering import FeedEpisode
from feedcast.data import FeedEvent, Forecast, ForecastPoint
from feedcast.evaluation.windows import (
    evaluate_multi_window,
    generate_episode_boundary_cutoffs,
    generate_fixed_step_cutoffs,
    recency_weight,
)


def _event_at(time: datetime) -> FeedEvent:
    """Build one bottle-feed event at an absolute timestamp."""
    return FeedEvent(
        time=time,
        volume_oz=4.0,
        bottle_volume_oz=4.0,
        breastfeeding_volume_oz=0.0,
    )


def _point_at(time: datetime) -> ForecastPoint:
    """Build one forecast point at an absolute timestamp."""
    return ForecastPoint(
        time=time,
        volume_oz=4.0,
        gap_hours=0.0,
    )


def _episode_at(time: datetime) -> FeedEpisode:
    """Build a single-feed episode for cutoff generation tests."""
    event = _event_at(time)
    return FeedEpisode(
        time=time,
        volume_oz=event.volume_oz,
        feed_count=1,
        constituents=(event,),
    )


class RecencyWeightTests(unittest.TestCase):
    """Recency decay behavior."""

    def test_known_half_life_values(self) -> None:
        """Age zero should be full weight and one half-life should halve it."""
        self.assertEqual(recency_weight(age_hours=0.0, half_life_hours=36.0), 1.0)
        self.assertEqual(recency_weight(age_hours=36.0, half_life_hours=36.0), 0.5)


class CutoffGenerationTests(unittest.TestCase):
    """Cutoff generation semantics."""

    def test_episode_boundary_cutoffs_are_sorted_and_include_replay_cutoff(
        self,
    ) -> None:
        """Episode cutoffs should respect lookback and inject the replay cutoff."""
        latest_activity_time = datetime(2026, 3, 20, 0, 0, 0)
        cutoffs = generate_episode_boundary_cutoffs(
            episodes=[
                _episode_at(datetime(2026, 3, 17, 18, 0, 0)),
                _episode_at(datetime(2026, 3, 18, 6, 0, 0)),
                _episode_at(datetime(2026, 3, 18, 18, 0, 0)),
                _episode_at(datetime(2026, 3, 19, 6, 0, 0)),
            ],
            latest_activity_time=latest_activity_time,
            lookback_hours=48.0,
        )

        self.assertEqual(
            cutoffs,
            [
                datetime(2026, 3, 18, 6, 0, 0),
                datetime(2026, 3, 18, 18, 0, 0),
                datetime(2026, 3, 19, 0, 0, 0),
            ],
        )

    def test_fixed_step_cutoffs_include_replay_cutoff_when_step_misses_it(
        self,
    ) -> None:
        """Fixed-step cutoffs should inject the replay-equivalent window."""
        latest_activity_time = datetime(2026, 3, 20, 12, 0, 0)
        cutoffs = generate_fixed_step_cutoffs(
            latest_activity_time=latest_activity_time,
            earliest_activity_time=datetime(2026, 3, 18, 0, 0, 0),
            lookback_hours=60.0,
            step_hours=20.0,
        )

        self.assertEqual(
            cutoffs,
            [
                datetime(2026, 3, 18, 0, 0, 0),
                datetime(2026, 3, 18, 20, 0, 0),
                datetime(2026, 3, 19, 12, 0, 0),
            ],
        )

    def test_less_than_one_horizon_of_episode_data_raises(self) -> None:
        """Replay needs at least one full 24-hour observed horizon."""
        latest_activity_time = datetime(2026, 3, 20, 0, 0, 0)
        with self.assertRaisesRegex(ValueError, "24 observed hours"):
            generate_episode_boundary_cutoffs(
                episodes=[_episode_at(datetime(2026, 3, 19, 12, 0, 0))],
                latest_activity_time=latest_activity_time,
            )


class MultiWindowEvaluationTests(unittest.TestCase):
    """Multi-window aggregation behavior."""

    def test_unavailable_windows_do_not_drag_down_aggregate(self) -> None:
        """Unavailable windows stay visible but are excluded from the mean."""
        cutoffs = [
            datetime(2026, 3, 18, 0, 0, 0),
            datetime(2026, 3, 19, 0, 0, 0),
        ]
        latest_activity_time = datetime(2026, 3, 20, 0, 0, 0)
        scoring_events = [_event_at(cutoff + timedelta(hours=2)) for cutoff in cutoffs]

        def forecast_fn(cutoff: datetime) -> Forecast:
            if cutoff == cutoffs[1]:
                return Forecast(
                    name="Test",
                    slug="test",
                    points=[],
                    methodology="",
                    diagnostics={},
                    available=False,
                    error_message="not enough history",
                )

            return Forecast(
                name="Test",
                slug="test",
                points=[_point_at(cutoff + timedelta(hours=2))],
                methodology="",
                diagnostics={},
            )

        result = evaluate_multi_window(
            forecast_fn=forecast_fn,
            scoring_events=scoring_events,
            cutoffs=cutoffs,
            latest_activity_time=latest_activity_time,
            half_life_hours=24.0,
        )

        self.assertEqual(result.headline_score, 100.0)
        self.assertEqual(result.window_count, 2)
        self.assertEqual(result.scored_window_count, 1)
        self.assertEqual(result.availability_ratio, 0.5)
        self.assertEqual(result.per_window[1].status, "unavailable")

    def test_aggregate_matches_hand_calculated_weighted_mean(self) -> None:
        """Aggregate scores should weight newer windows more heavily."""
        cutoffs = [
            datetime(2026, 3, 18, 0, 0, 0),
            datetime(2026, 3, 19, 0, 0, 0),
        ]
        latest_activity_time = datetime(2026, 3, 20, 0, 0, 0)
        scoring_events = [_event_at(cutoff + timedelta(hours=2)) for cutoff in cutoffs]

        def forecast_fn(cutoff: datetime) -> Forecast:
            if cutoff == cutoffs[0]:
                points: list[ForecastPoint] = []
            else:
                points = [_point_at(cutoff + timedelta(hours=2))]

            return Forecast(
                name="Test",
                slug="test",
                points=points,
                methodology="",
                diagnostics={},
            )

        result = evaluate_multi_window(
            forecast_fn=forecast_fn,
            scoring_events=scoring_events,
            cutoffs=cutoffs,
            latest_activity_time=latest_activity_time,
            half_life_hours=24.0,
        )

        expected = round((0.5 * 0.0 + 1.0 * 100.0) / 1.5, 3)
        self.assertEqual(result.headline_score, expected)
        self.assertEqual(result.count_score, expected)
        self.assertEqual(result.timing_score, expected)
        self.assertEqual(
            [window.status for window in result.per_window], ["scored", "scored"]
        )


if __name__ == "__main__":
    unittest.main()
