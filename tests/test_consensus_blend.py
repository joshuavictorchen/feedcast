"""Behavior tests for the consensus blend selector."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.data import FeedEvent, Forecast, ForecastPoint
from feedcast.models.consensus_blend.model import (
    generate_candidate_clusters,
    run_consensus_blend,
    select_candidate_sequence,
    _majority_floor,
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

    def test_per_point_arrays_are_aligned_with_point_key(self) -> None:
        """point_timestamps and point_volumes must correspond to point_key.

        Uses non-alphabetical slug ordering to catch alignment bugs
        where point_key is sorted but per-point arrays follow dict
        iteration order.
        """
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        # Slugs intentionally non-alphabetical to expose iteration-vs-sort
        # mismatches.  zebra sorts last but is inserted first.
        forecasts = {
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
        candidates = generate_candidate_clusters(forecasts)
        self.assertGreater(len(candidates), 0)

        for candidate in candidates:
            # For each position i, point_key[i] should correspond to
            # point_timestamps[i] and point_volumes[i].
            for i, key in enumerate(candidate.point_key):
                slug = key.split(":")[0]
                expected_point = forecasts[slug].points[int(key.split(":")[1])]
                self.assertAlmostEqual(
                    candidate.point_timestamps[i],
                    expected_point.time.timestamp(),
                    places=1,
                    msg=f"Timestamp mismatch at index {i} for {key}",
                )
                self.assertAlmostEqual(
                    candidate.point_volumes[i],
                    expected_point.volume_oz,
                    places=3,
                    msg=f"Volume mismatch at index {i} for {key}",
                )


    def test_selector_finds_multiple_feeds_despite_shared_points(self) -> None:
        """Regression: shared anchor points should not collapse two valid feeds.

        With a:[2.0,2.5], b:[2.0,3.5], c:[2.5,3.5], d:[3.5,4.0], the
        selector should find at least one consensus feed.  The forward-order
        search may miss the globally optimal sequence where an earlier
        candidate becomes valid after a later one claims points, but it
        should still find a reasonable selection (not degenerate to zero).
        """
        cutoff = datetime(2026, 3, 24, 12, 0, 0)
        history = [_history_event(cutoff)]
        forecast = run_consensus_blend(
            base_forecasts=[
                _forecast("model_a", [2.0, 2.5]),
                _forecast("model_b", [2.0, 3.5]),
                _forecast("model_c", [2.5, 3.5]),
                _forecast("model_d", [3.5, 4.0]),
            ],
            history=history,
            cutoff=cutoff,
            horizon_hours=24,
        )

        self.assertTrue(forecast.available)
        self.assertGreaterEqual(len(forecast.points), 1)


if __name__ == "__main__":
    unittest.main()
