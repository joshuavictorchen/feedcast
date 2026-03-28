"""Behavior tests for the consensus blend selector."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.data import FeedEvent, Forecast, ForecastPoint
from feedcast.models.consensus_blend.model import (
    _collapse_to_episode_points,
    _majority_floor,
    generate_candidate_clusters,
    run_consensus_blend,
    select_candidate_sequence,
)


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
            abs(
                (
                    forecast.points[0].time - (cutoff + timedelta(hours=3))
                ).total_seconds()
            ),
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
            abs(
                (
                    forecast.points[0].time - (cutoff + timedelta(hours=3))
                ).total_seconds()
            ),
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

    def test_single_use_prevents_point_reuse_across_feeds(self) -> None:
        """A model prediction should not count as evidence for two consensus feeds.

        Each model has 2 predictions spaced 3h apart.  Without single-use
        enforcement, the wide anchor radius would let the middle predictions
        vote for both the early and late consensus feeds, producing 3 points.
        With single-use, each prediction is claimed once, limiting the output
        to 2 feeds.
        """
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [2.0, 5.0]),
                _forecast("model_b", [2.1, 5.1]),
                _forecast("model_c", [2.2, 5.2]),
                _forecast("model_d", [2.3, 5.3]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        # Each model has exactly 2 predictions, so at most 2 consensus feeds.
        self.assertLessEqual(len(forecast.points), 2)

    def test_no_model_point_appears_in_two_selected_candidates(self) -> None:
        """Verify the single-use invariant directly on selected candidates.

        After selection, no model:index key should appear in more than
        one selected candidate's point_key.
        """
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        forecasts = {
            "a": _forecast("a", [2.0, 5.0]),
            "b": _forecast("b", [2.1, 5.1]),
            "c": _forecast("c", [2.2, 5.2]),
            "d": _forecast("d", [2.3, 5.3]),
        }
        majority_floor = _majority_floor(len(forecasts))
        candidates = generate_candidate_clusters(forecasts)
        selected = select_candidate_sequence(candidates, majority_floor)

        all_keys: list[str] = []
        for candidate in selected:
            all_keys.extend(candidate.point_key)

        self.assertEqual(
            len(all_keys),
            len(set(all_keys)),
            f"Point reuse detected: {all_keys}",
        )

    def test_candidate_generation_is_independent_of_model_order(self) -> None:
        """Candidate identity should not depend on input dict ordering."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        forecasts_one = {
            "zebra": Forecast(
                name="zebra",
                slug="zebra",
                points=[
                    ForecastPoint(
                        time=cutoff + timedelta(hours=3.0),
                        volume_oz=1.0,
                        gap_hours=3.0,
                    )
                ],
                methodology="",
                diagnostics={},
            ),
            "alpha": Forecast(
                name="alpha",
                slug="alpha",
                points=[
                    ForecastPoint(
                        time=cutoff + timedelta(hours=3.1),
                        volume_oz=8.0,
                        gap_hours=3.1,
                    )
                ],
                methodology="",
                diagnostics={},
            ),
            "middle": Forecast(
                name="middle",
                slug="middle",
                points=[
                    ForecastPoint(
                        time=cutoff + timedelta(hours=3.2),
                        volume_oz=5.0,
                        gap_hours=3.2,
                    )
                ],
                methodology="",
                diagnostics={},
            ),
        }
        forecasts_two = {
            "alpha": forecasts_one["alpha"],
            "middle": forecasts_one["middle"],
            "zebra": forecasts_one["zebra"],
        }

        candidates_one = generate_candidate_clusters(forecasts_one)
        candidates_two = generate_candidate_clusters(forecasts_two)

        summary_one = {
            (candidate.point_key, candidate.time, candidate.support)
            for candidate in candidates_one
        }
        summary_two = {
            (candidate.point_key, candidate.time, candidate.support)
            for candidate in candidates_two
        }
        self.assertEqual(summary_one, summary_two)

    def test_candidate_generator_emits_majority_subset_alternatives(self) -> None:
        """The exact selector should have tight-majority alternatives to choose."""
        forecasts = {
            "model_a": _forecast("model_a", [2.92]),
            "model_b": _forecast("model_b", [3.0]),
            "model_c": _forecast("model_c", [3.08]),
            "model_d": _forecast("model_d", [4.0]),
        }

        candidates = generate_candidate_clusters(forecasts)
        candidate_models = {candidate.models for candidate in candidates}

        self.assertIn(
            ("model_a", "model_b", "model_c"),
            candidate_models,
        )
        self.assertIn(
            ("model_a", "model_b", "model_c", "model_d"),
            candidate_models,
        )


    def test_collapse_merges_cluster_predictions_into_one_episode(self) -> None:
        """Close-together forecast points should collapse into one episode point."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        points = [
            ForecastPoint(
                time=cutoff + timedelta(hours=3),
                volume_oz=3.0,
                gap_hours=3.0,
            ),
            # 50-min gap: within the 73-min base cluster rule.
            ForecastPoint(
                time=cutoff + timedelta(hours=3, minutes=50),
                volume_oz=1.0,
                gap_hours=50 / 60,
            ),
            # Standalone feed well outside cluster range.
            ForecastPoint(
                time=cutoff + timedelta(hours=6),
                volume_oz=4.0,
                gap_hours=2.17,
            ),
        ]

        collapsed = _collapse_to_episode_points(points)

        self.assertEqual(len(collapsed), 2)
        # First episode: canonical time at 3h, volume = 3.0 + 1.0.
        self.assertEqual(collapsed[0].time, cutoff + timedelta(hours=3))
        self.assertAlmostEqual(collapsed[0].volume_oz, 4.0)
        # Second episode: standalone feed at 6h.
        self.assertEqual(collapsed[1].time, cutoff + timedelta(hours=6))
        self.assertAlmostEqual(collapsed[1].volume_oz, 4.0)

    def test_cluster_predictions_produce_same_consensus_as_clean(self) -> None:
        """A model's cluster predictions should produce the same consensus
        as a single episode-level prediction after collapsing."""
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]

        # Model A predicts a cluster: 3h feed + 3h50m top-up (50-min gap).
        cluster_result = run_consensus_blend(
            base_forecasts=[
                Forecast(
                    name="model_a",
                    slug="model_a",
                    points=[
                        ForecastPoint(
                            time=cutoff + timedelta(hours=3),
                            volume_oz=3.0,
                            gap_hours=3.0,
                        ),
                        ForecastPoint(
                            time=cutoff + timedelta(hours=3, minutes=50),
                            volume_oz=1.0,
                            gap_hours=50 / 60,
                        ),
                    ],
                    methodology="",
                    diagnostics={},
                ),
                _forecast("model_b", [3.08]),
                _forecast("model_c", [3.17]),
                _forecast("model_d", [3.25]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        # Model A predicts one clean feed at 3h (same canonical time as cluster).
        clean_result = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [3.0]),
                _forecast("model_b", [3.08]),
                _forecast("model_c", [3.17]),
                _forecast("model_d", [3.25]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        # Both should produce the same consensus schedule.
        self.assertEqual(len(cluster_result.points), len(clean_result.points))
        for cluster_point, clean_point in zip(
            cluster_result.points, clean_result.points
        ):
            self.assertEqual(cluster_point.time, clean_point.time)

    def test_conflict_window_admits_106_minute_episode_pair(self) -> None:
        """Two majority-supported feeds 106 minutes apart should both survive.

        The 105-minute conflict window admits pairs at 105+ minutes. Pairs
        closer than 105 minutes are treated as competing candidates for the
        same feed, and only the better-supported one survives.
        """
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        # All four models agree on two feeds 106 minutes apart.
        early_offset = 3.0
        late_offset = early_offset + 106 / 60  # 106 minutes later
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [early_offset, late_offset]),
                _forecast("model_b", [early_offset + 0.05, late_offset + 0.05]),
                _forecast("model_c", [early_offset + 0.1, late_offset + 0.1]),
                _forecast("model_d", [early_offset + 0.15, late_offset + 0.15]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        self.assertEqual(len(forecast.points), 2)


if __name__ == "__main__":
    unittest.main()
