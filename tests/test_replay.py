"""Behavior tests for latest-24h replay scoring and tuning."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from feedcast.replay import score_model, tune_model


CSV_HEADERS = [
    "Type",
    "Start Date/time",
    "Start Date/time (Epoch)",
    "[Bottle Feed] Breast Milk Volume",
    "[Bottle Feed] Breast Milk Volume Unit",
    "[Bottle Feed] Formula Volume",
    "[Bottle Feed] Formula Volume Unit",
    "[Bottle Feed] Volume",
    "[Bottle Feed] Volume Unit",
    "[Breastfeed] Left Duration (Seconds)",
    "[Breastfeed] Right Duration (Seconds)",
]


def _write_export(path: Path) -> None:
    """Create a synthetic bottle-only export with several complete days.

    48 feeds at 3-hour intervals starting 2026-03-15 00:00, giving 6 full
    days of data.  The latest feed is at 2026-03-20 21:00, so the replay
    cutoff lands at 2026-03-19 21:00 with 8 actuals in the last 24 hours.
    """
    base_time = datetime(2026, 3, 15, 0, 0, 0)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for index in range(48):
            timestamp = base_time + timedelta(hours=3 * index)
            writer.writerow(
                {
                    "Type": "Bottle Feed",
                    "Start Date/time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "Start Date/time (Epoch)": str(int(timestamp.timestamp())),
                    "[Bottle Feed] Breast Milk Volume": "4.0",
                    "[Bottle Feed] Breast Milk Volume Unit": "oz",
                    "[Bottle Feed] Formula Volume": "",
                    "[Bottle Feed] Formula Volume Unit": "",
                    "[Bottle Feed] Volume": "",
                    "[Bottle Feed] Volume Unit": "",
                    "[Breastfeed] Left Duration (Seconds)": "",
                    "[Breastfeed] Right Duration (Seconds)": "",
                }
            )


class ReplayScoreTests(unittest.TestCase):
    """Replay score behavior."""

    def test_score_consensus_writes_artifact(self) -> None:
        """Scoring consensus_blend should replay and persist a JSON artifact."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            payload = score_model(
                "consensus_blend",
                export_path=export_path,
                output_dir=output_dir,
            )

            self.assertEqual(payload["mode"], "score")
            self.assertEqual(
                payload["validation"], "latest_24h_directional_replay_only"
            )
            self.assertEqual(payload["result"]["status"], "scored")
            self.assertTrue(output_dir.exists())
            result_path = Path(payload["results_path"])
            self.assertTrue(result_path.exists())

            saved = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["model"]["slug"], "consensus_blend")
            self.assertEqual(saved["result"]["score"]["actual_episode_count"], 8)

    def test_score_with_overrides_includes_overrides(self) -> None:
        """Scoring with overrides should record them in the result."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            payload = score_model(
                "slot_drift",
                overrides={"LOOKBACK_DAYS": 5},
                export_path=export_path,
                output_dir=output_dir,
            )

            self.assertEqual(payload["result"]["status"], "scored")
            self.assertEqual(payload["result"]["overrides"], {"LOOKBACK_DAYS": 5})

    def test_score_overrides_on_consensus_raises(self) -> None:
        """Overrides should be rejected for non-scripted models."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            _write_export(export_path)

            with self.assertRaises(ValueError, msg="scripted models"):
                score_model(
                    "consensus_blend",
                    overrides={"SOME_PARAM": 1},
                    export_path=export_path,
                    output_dir=temp_path / ".replay-results",
                )


class ReplayTuneTests(unittest.TestCase):
    """Replay tune behavior."""

    def test_tune_evaluates_cross_product(self) -> None:
        """Tuning should evaluate the full cross-product and rank results."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            payload = tune_model(
                "slot_drift",
                candidates_by_name={"LOOKBACK_DAYS": [3, 5, 7]},
                export_path=export_path,
                output_dir=output_dir,
            )

            self.assertEqual(payload["mode"], "tune")
            self.assertEqual(payload["search"]["total_candidates"], 3)
            self.assertEqual(payload["search"]["evaluated"], 3)
            self.assertIn("LOOKBACK_DAYS", payload["baseline"]["params"])
            self.assertIn("LOOKBACK_DAYS", payload["best"]["params"])
            self.assertEqual(len(payload["candidates"]), 3)
            self.assertTrue(Path(payload["results_path"]).exists())

            # Results should be sorted by descending score
            scores = [c["effective_score"] for c in payload["candidates"]]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_tune_consensus_raises(self) -> None:
        """Tuning should be rejected for non-scripted models."""
        with self.assertRaises(ValueError, msg="scripted models"):
            tune_model("consensus_blend", candidates_by_name={"X": [1]})

    def test_tune_empty_candidates_raises(self) -> None:
        """Tuning with no candidates should fail."""
        with self.assertRaises(ValueError, msg="at least one"):
            tune_model("slot_drift", candidates_by_name={})


class CLIParsingTests(unittest.TestCase):
    """CLI param-parsing behavior."""

    def setUp(self) -> None:
        # Import the parsing functions from the CLI module.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from run_replay import _parse_params

        self._parse_params = _parse_params

    def test_inline_single_value(self) -> None:
        """KEY=VALUE with one value should produce a single-element list."""
        result = self._parse_params(["LOOKBACK_DAYS=5"])
        self.assertEqual(result, {"LOOKBACK_DAYS": [5]})

    def test_inline_comma_separated(self) -> None:
        """Comma-separated values should produce multiple candidates."""
        result = self._parse_params(["LOOKBACK_DAYS=5,7,9"])
        self.assertEqual(result, {"LOOKBACK_DAYS": [5, 7, 9]})

    def test_inline_json_array_is_single_candidate(self) -> None:
        """A JSON array should be one candidate, not split on commas."""
        result = self._parse_params(["WEIGHTS=[1,1,2,2]"])
        self.assertEqual(result, {"WEIGHTS": [[1, 1, 2, 2]]})

    def test_inline_float_values(self) -> None:
        """Float values should be parsed correctly."""
        result = self._parse_params(["HALF_LIFE=2.0,3.0"])
        self.assertEqual(result, {"HALF_LIFE": [2.0, 3.0]})

    def test_yaml_file_list_values(self) -> None:
        """YAML list values should produce multiple candidates."""
        with tempfile.NamedTemporaryFile(
            suffix=".yaml", mode="w", delete=False
        ) as handle:
            handle.write("LOOKBACK_DAYS: [5, 7, 9]\nHALF_LIFE: 3.0\n")
            yaml_path = handle.name

        try:
            result = self._parse_params([yaml_path])
            self.assertEqual(result["LOOKBACK_DAYS"], [5, 7, 9])
            # Scalar YAML values become single-element lists.
            self.assertEqual(result["HALF_LIFE"], [3.0])
        finally:
            Path(yaml_path).unlink()

    def test_yaml_missing_file_raises(self) -> None:
        """A missing YAML file should raise ValueError."""
        with self.assertRaises(ValueError, msg="not found"):
            self._parse_params(["/nonexistent/sweep.yaml"])

    def test_invalid_format_raises(self) -> None:
        """An arg without = and not a .yaml path should raise ValueError."""
        with self.assertRaises(ValueError):
            self._parse_params(["NOT_A_VALID_PARAM"])


if __name__ == "__main__":
    unittest.main()
