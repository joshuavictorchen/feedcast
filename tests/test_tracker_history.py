"""Tests for retrospective history aggregation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from feedcast.tracker import summarize_retrospective_history


class HistoricalSummaryTests(unittest.TestCase):
    """History aggregation behavior checks."""

    def test_partial_retrospectives_are_downweighted_by_evidence(self) -> None:
        """Thin partial windows should not inflate the historical mean."""
        tracker_payload = {
            "runs": [
                {
                    "retrospective": {
                        "available": True,
                        "same_dataset": False,
                        "observed_horizon_hours": 2.0,
                        "results": [
                            {
                                "name": "Model",
                                "slug": "model",
                                "score": 100.0,
                                "count_score": 100.0,
                                "timing_score": 100.0,
                                "status": "Partial horizon (2.0h observed)",
                            }
                        ],
                    }
                },
                {
                    "retrospective": {
                        "available": True,
                        "same_dataset": False,
                        "observed_horizon_hours": 24.0,
                        "results": [
                            {
                                "name": "Model",
                                "slug": "model",
                                "score": 50.0,
                                "count_score": 50.0,
                                "timing_score": 50.0,
                                "status": "Full 24h observed",
                            }
                        ],
                    }
                },
            ]
        }
        partial_weight = (1 - 2 ** (-2 / 24)) / (1 - 2 ** (-24 / 24))
        expected_weighted_mean = (100 * partial_weight + 50) / (partial_weight + 1)

        with tempfile.TemporaryDirectory(prefix="feedcast-history-test-") as temp_dir:
            tracker_path = Path(temp_dir) / "tracker.json"
            tracker_path.write_text(json.dumps(tracker_payload), encoding="utf-8")
            summaries = summarize_retrospective_history(tracker_path)

        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary.comparison_count, 2)
        self.assertEqual(summary.full_horizon_count, 1)
        self.assertAlmostEqual(summary.mean_score, expected_weighted_mean, places=3)
        self.assertAlmostEqual(
            summary.mean_count_score,
            expected_weighted_mean,
            places=3,
        )
        self.assertAlmostEqual(
            summary.mean_timing_score,
            expected_weighted_mean,
            places=3,
        )
        self.assertAlmostEqual(
            summary.mean_coverage_ratio, (2 / 24 + 1.0) / 2, places=3
        )


if __name__ == "__main__":
    unittest.main()
