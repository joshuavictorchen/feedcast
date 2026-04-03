"""Behavior tests for multi-window replay scoring and tuning."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from feedcast.replay import score_model, tune_model

from tests.simulation.export import write_nara_export
from tests.simulation.factories import bottle_activities_from_schedule


def _write_export(path: Path) -> None:
    """Create a synthetic bottle-only export with several complete days.

    48 feeds at 3-hour intervals starting 2026-03-15 00:00, giving 6 full
    days of data.  The latest feed is at 2026-03-20 21:00, so the replay
    cutoff lands at 2026-03-19 21:00 with 8 actuals in the last 24 hours.
    """
    base_time = datetime(2026, 3, 15, 0, 0, 0)
    activities = bottle_activities_from_schedule(
        [
            (base_time + timedelta(hours=3 * index), 4.0)
            for index in range(48)
        ]
    )
    write_nara_export(path, activities)


class ReplayScoreTests(unittest.TestCase):
    """Replay score behavior."""

    def test_score_consensus_writes_artifact(self) -> None:
        """Scoring consensus_blend should replay across windows and persist a JSON artifact."""
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
                payload["validation"], "multi_window_directional_replay"
            )

            # Multi-window result structure
            rw = payload["replay_windows"]
            self.assertIn("aggregate", rw)
            self.assertIn("per_window", rw)
            self.assertGreater(rw["window_count"], 0)
            self.assertGreater(rw["scored_window_count"], 0)
            self.assertEqual(rw["cutoff_mode"], "episode")

            self.assertTrue(output_dir.exists())
            result_path = Path(payload["results_path"])
            self.assertTrue(result_path.exists())

            saved = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["model"]["slug"], "consensus_blend")

    def test_score_with_overrides_includes_overrides(self) -> None:
        """Scoring with overrides should record them at the top level."""
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

            self.assertGreater(
                payload["replay_windows"]["scored_window_count"], 0
            )
            self.assertEqual(payload["overrides"], {"LOOKBACK_DAYS": 5})

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

    def test_multi_window_result_structure(self) -> None:
        """Score result should contain the complete multi-window schema."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            payload = score_model(
                "slot_drift",
                export_path=export_path,
                output_dir=output_dir,
            )

            rw = payload["replay_windows"]

            # Top-level config
            self.assertIn("lookback_hours", rw)
            self.assertIn("half_life_hours", rw)
            self.assertIn("cutoff_mode", rw)
            self.assertIn("window_count", rw)
            self.assertIn("scored_window_count", rw)
            self.assertIn("availability_ratio", rw)

            # Aggregate sub-keys
            aggregate = rw["aggregate"]
            self.assertIn("headline", aggregate)
            self.assertIn("count", aggregate)
            self.assertIn("timing", aggregate)

            # Per-window entries
            self.assertGreater(len(rw["per_window"]), 1)
            window = rw["per_window"][0]
            self.assertIn("cutoff", window)
            self.assertIn("observed_until", window)
            self.assertIn("weight", window)
            self.assertIn("status", window)
            self.assertIn("score", window)

    def test_lookback_and_half_life_passthrough(self) -> None:
        """Custom lookback and half-life should appear in the result."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            payload = score_model(
                "slot_drift",
                export_path=export_path,
                output_dir=output_dir,
                lookback_hours=48.0,
                half_life_hours=24.0,
            )

            rw = payload["replay_windows"]
            self.assertEqual(rw["lookback_hours"], 48.0)
            self.assertEqual(rw["half_life_hours"], 24.0)


class ReplayTuneTests(unittest.TestCase):
    """Replay tune behavior."""

    def _without_results_path(self, payload: dict) -> dict:
        """Drop the artifact path so payloads from separate runs compare cleanly."""
        return {k: v for k, v in payload.items() if k != "results_path"}

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
            self.assertEqual(
                payload["validation"], "multi_window_directional_replay"
            )
            self.assertEqual(payload["search"]["total_candidates"], 3)
            self.assertEqual(payload["search"]["evaluated"], 3)
            self.assertIn("LOOKBACK_DAYS", payload["baseline"]["params"])
            self.assertIn("LOOKBACK_DAYS", payload["best"]["params"])
            self.assertEqual(len(payload["candidates"]), 3)
            self.assertTrue(Path(payload["results_path"]).exists())

            # Top-level replay_windows has shared config
            self.assertIn("replay_windows", payload)
            top_rw = payload["replay_windows"]
            self.assertIn("lookback_hours", top_rw)
            self.assertIn("half_life_hours", top_rw)
            self.assertIn("cutoff_mode", top_rw)
            self.assertIn("window_count", top_rw)

            # Each candidate has the consistent replay_windows schema
            for candidate in payload["candidates"]:
                self.assertIn("params", candidate)
                self.assertIn("replay_windows", candidate)
                self.assertIn("aggregate", candidate["replay_windows"])

            # Candidates ranked by (-scored_window_count, -headline_score)
            candidates = payload["candidates"]
            for i in range(len(candidates) - 1):
                a_rw = candidates[i]["replay_windows"]
                b_rw = candidates[i + 1]["replay_windows"]
                a_key = (a_rw["scored_window_count"], a_rw["aggregate"]["headline"])
                b_key = (b_rw["scored_window_count"], b_rw["aggregate"]["headline"])
                self.assertGreaterEqual(a_key, b_key)

            # Best includes deltas vs baseline
            self.assertIn("headline_delta", payload["best"])
            self.assertIn("availability_delta", payload["best"])

    def test_tune_best_never_worse_than_baseline(self) -> None:
        """Best should never regress vs baseline — baseline competes in ranking."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            # LOOKBACK_DAYS=1 is likely worse than the default; best should
            # still be at least as good as baseline.
            payload = tune_model(
                "slot_drift",
                candidates_by_name={"LOOKBACK_DAYS": [1]},
                export_path=export_path,
                output_dir=output_dir,
            )

            baseline_rw = payload["baseline"]["replay_windows"]
            best_rw = payload["best"]["replay_windows"]
            best_key = (
                best_rw["scored_window_count"],
                best_rw["aggregate"]["headline"],
            )
            baseline_key = (
                baseline_rw["scored_window_count"],
                baseline_rw["aggregate"]["headline"],
            )
            self.assertGreaterEqual(best_key, baseline_key)
            self.assertGreaterEqual(payload["best"]["headline_delta"], 0.0)

    def test_tune_parallel_candidates_matches_serial(self) -> None:
        """Candidate-parallel tuning should exactly match the serial result."""
        with tempfile.TemporaryDirectory(prefix="feedcast-replay-test-") as temp_dir:
            temp_path = Path(temp_dir)
            export_path = temp_path / "export_narababy_silas_20260320.csv"
            output_dir = temp_path / ".replay-results"
            _write_export(export_path)

            serial_payload = tune_model(
                "slot_drift",
                candidates_by_name={"LOOKBACK_DAYS": [3, 5]},
                export_path=export_path,
                output_dir=output_dir,
            )
            parallel_payload = tune_model(
                "slot_drift",
                candidates_by_name={"LOOKBACK_DAYS": [3, 5]},
                export_path=export_path,
                output_dir=output_dir,
                parallel_candidates=True,
                candidate_workers=2,
            )

            self.assertEqual(
                self._without_results_path(parallel_payload),
                self._without_results_path(serial_payload),
            )

    def test_tune_consensus_raises(self) -> None:
        """Tuning should be rejected for non-scripted models."""
        with self.assertRaises(ValueError, msg="scripted models"):
            tune_model("consensus_blend", candidates_by_name={"X": [1]})

    def test_tune_empty_candidates_raises(self) -> None:
        """Tuning with no candidates should fail."""
        with self.assertRaises(ValueError, msg="at least one"):
            tune_model("slot_drift", candidates_by_name={})

    def test_tune_invalid_candidate_workers_raises(self) -> None:
        """candidate_workers must be positive when provided."""
        with self.assertRaises(ValueError, msg="at least 1"):
            tune_model(
                "slot_drift",
                candidates_by_name={"LOOKBACK_DAYS": [5]},
                candidate_workers=0,
            )


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
