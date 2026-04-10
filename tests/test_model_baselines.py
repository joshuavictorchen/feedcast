"""Consistency tests for committed model baselines."""

from __future__ import annotations

import unittest
from pathlib import Path

from feedcast.research.consistency import (
    TUNABLE_MODEL_CONSTANTS,
    _parse_baseline_params,
    _read_model_constants,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = REPO_ROOT / "feedcast" / "models"


class ModelBaselineConsistencyTests(unittest.TestCase):
    """Committed research baselines should match shipped model constants."""

    def test_research_baselines_match_current_model_constants(self) -> None:
        """Current constants should stay in sync with committed research artifacts."""
        for slug, constant_names in TUNABLE_MODEL_CONSTANTS.items():
            with self.subTest(model=slug):
                model_dir = MODELS_ROOT / slug
                results_text = (model_dir / "artifacts" / "research_results.txt").read_text(
                    encoding="utf-8",
                )
                baseline_params = _parse_baseline_params(results_text)
                self.assertIsNotNone(
                    baseline_params,
                    f"{slug} is missing baseline params in research_results.txt",
                )
                current_params = _read_model_constants(
                    model_dir / "model.py",
                    constant_names,
                )
                self.assertEqual(current_params, baseline_params)


if __name__ == "__main__":
    unittest.main()
