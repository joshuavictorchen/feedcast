"""Tests for pipeline pre-flight checks, retro score formatting, and orchestration."""

from __future__ import annotations

import subprocess
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from feedcast.data import ExportSnapshot, Forecast, ForecastPoint
from feedcast.pipeline import (
    _assert_clean_git_worktree,
    _best_retro_scores,
    main,
)
from feedcast.tracker import Retrospective, RetrospectiveResult


class CleanWorktreeTests(unittest.TestCase):
    """Pre-flight git cleanliness checks."""

    def test_clean_worktree_passes(self) -> None:
        """An empty porcelain status should pass."""
        result = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
        )

        with patch("feedcast.pipeline.subprocess.run", return_value=result):
            _assert_clean_git_worktree()

    def test_dirty_worktree_raises(self) -> None:
        """Any porcelain output should fail fast."""
        result = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=" M feedcast/pipeline.py\n",
            stderr="",
        )

        with patch("feedcast.pipeline.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "dirty git worktree"):
                _assert_clean_git_worktree()


class BestRetroScoresTests(unittest.TestCase):
    """Retro score formatting for model tuning prompts."""

    def _make_result(
        self,
        slug: str = "slot_drift",
        score: float | None = 72.5,
        count_score: float | None = 80.0,
        timing_score: float | None = 65.0,
    ) -> RetrospectiveResult:
        return RetrospectiveResult(
            name="Slot Drift",
            slug=slug,
            score=score,
            count_score=count_score,
            timing_score=timing_score,
            predicted_episode_count=7,
            actual_episode_count=6,
            matched_episode_count=5,
            status="Full 24h observed",
        )

    def test_current_retro_with_scores(self) -> None:
        """Uses current retrospective when it has scores for the model."""
        retro = Retrospective(
            available=True,
            results=[self._make_result()],
        )
        result = _best_retro_scores(retro, "slot_drift")
        self.assertIn("72.5", result)
        self.assertIn("80.0", result)
        self.assertIn("65.0", result)
        self.assertIn("7 predicted", result)
        self.assertIn("Full 24h observed", result)

    def test_current_retro_unavailable_falls_back(self) -> None:
        """Falls back to tracker history when current retro is unavailable."""
        retro = Retrospective(available=False, same_dataset=True)
        tracker_data = {
            "runs": [
                {
                    "run_id": "20260401-120000",
                    "retrospective": {
                        "available": True,
                        "results": [
                            {
                                "slug": "slot_drift",
                                "score": 68.0,
                                "count_score": 75.0,
                                "timing_score": 61.0,
                            },
                        ],
                    },
                },
            ],
        }
        with patch("feedcast.pipeline.load_tracker", return_value=tracker_data):
            result = _best_retro_scores(retro, "slot_drift")
        self.assertIn("68.0", result)
        self.assertIn("prior run 20260401-120000", result)

    def test_no_retro_available(self) -> None:
        """Returns default message when no retro exists anywhere."""
        retro = Retrospective(available=False)
        with patch("feedcast.pipeline.load_tracker", return_value={"runs": []}):
            result = _best_retro_scores(retro, "slot_drift")
        self.assertEqual(result, "No retrospective scores available yet.")

    def test_current_retro_missing_model_falls_back(self) -> None:
        """Falls back when current retro has scores but not for this model."""
        retro = Retrospective(
            available=True,
            results=[self._make_result(slug="other_model")],
        )
        with patch("feedcast.pipeline.load_tracker", return_value={"runs": []}):
            result = _best_retro_scores(retro, "slot_drift")
        self.assertEqual(result, "No retrospective scores available yet.")

    def test_current_retro_null_score_falls_back(self) -> None:
        """Falls back when the model's score is None (no observed horizon)."""
        retro = Retrospective(
            available=True,
            results=[self._make_result(score=None, count_score=None, timing_score=None)],
        )
        with patch("feedcast.pipeline.load_tracker", return_value={"runs": []}):
            result = _best_retro_scores(retro, "slot_drift")
        self.assertEqual(result, "No retrospective scores available yet.")

    def test_fallback_uses_latest_run(self) -> None:
        """Falls back to the most recent completed retrospective, not earliest."""
        retro = Retrospective(available=False)
        tracker_data = {
            "runs": [
                {
                    "run_id": "old-run",
                    "retrospective": {
                        "available": True,
                        "results": [
                            {"slug": "slot_drift", "score": 50.0, "count_score": 50.0, "timing_score": 50.0},
                        ],
                    },
                },
                {
                    "run_id": "new-run",
                    "retrospective": {
                        "available": True,
                        "results": [
                            {"slug": "slot_drift", "score": 90.0, "count_score": 90.0, "timing_score": 90.0},
                        ],
                    },
                },
            ],
        }
        with patch("feedcast.pipeline.load_tracker", return_value=tracker_data):
            result = _best_retro_scores(retro, "slot_drift")
        self.assertIn("90.0", result)
        self.assertIn("new-run", result)


# ---------------------------------------------------------------------------
# Pipeline orchestration tests
# ---------------------------------------------------------------------------

# Shared fixtures for orchestration tests
_FAKE_SNAPSHOT = ExportSnapshot(
    export_path=Path("exports/fake.csv"),
    activities=[],
    latest_activity_time=datetime(2026, 4, 1, 12, 0),
    dataset_id="sha256:fake",
    source_hash="sha256:fake",
)
_FAKE_POINT = ForecastPoint(time=datetime(2026, 4, 1, 15, 0), volume_oz=3.5, gap_hours=3.0)
_FAKE_BASE = Forecast(name="Test", slug="test_model", points=[_FAKE_POINT], methodology="t", diagnostics={})
_FAKE_CONSENSUS = Forecast(name="Consensus", slug="consensus_blend", points=[_FAKE_POINT], methodology="b", diagnostics={})
_FAKE_AGENT = Forecast(name="Agent Inference", slug="agent_inference", points=[_FAKE_POINT], methodology="a", diagnostics={})


def _run_mocked_main(**main_kwargs: object) -> dict[str, object]:
    """Run pipeline main() with all externals mocked. Returns mock dict."""
    mocks: dict[str, object] = {}
    with ExitStack() as stack:

        def p(target: str, **kw: object) -> object:
            mock = stack.enter_context(patch(f"feedcast.pipeline.{target}", **kw))
            return mock

        p("_assert_clean_git_worktree")
        p("load_export_snapshot", return_value=_FAKE_SNAPSHOT)
        mocks["branch"] = p("_create_run_branch")
        p("compute_retrospective", return_value=Retrospective(available=False))
        mocks["insights"] = p("_run_trend_insights", return_value="Test insights")
        mocks["tuning"] = p("_run_model_tuning")
        mocks["commit"] = p("_git_commit_all")
        p("_git_short_sha", return_value="abc1234")
        p("run_all_models", return_value=[_FAKE_BASE])
        mocks["agent_inf"] = p("_run_agent_inference", return_value=_FAKE_AGENT)
        p("build_feed_events", return_value=[])
        p("run_consensus_blend", return_value=_FAKE_CONSENSUS)
        mocks["featured"] = p("select_featured_forecast", return_value="consensus_blend")
        p("summarize_retrospective_history", return_value=[])
        mocks["entry"] = p(
            "build_run_entry",
            return_value={"run_id": "t", "git_commit": "abc1234", "git_dirty": False},
        )
        mocks["report"] = p("generate_report", return_value=Path("report"))
        p("save_run")

        main(**main_kwargs)

    return mocks


class SkipFlagsTests(unittest.TestCase):
    """CLI skip flags suppress the corresponding agent steps."""

    def test_all_skips_prevent_agent_calls(self) -> None:
        mocks = _run_mocked_main(
            skip_tuning=True, skip_insights=True, skip_agent_inference=True,
        )
        mocks["insights"].assert_not_called()
        mocks["tuning"].assert_not_called()
        mocks["agent_inf"].assert_not_called()

    def test_no_skips_run_all_agents(self) -> None:
        mocks = _run_mocked_main()
        mocks["insights"].assert_called_once()
        mocks["tuning"].assert_called_once()
        mocks["agent_inf"].assert_called_once()


class ProvenanceTests(unittest.TestCase):
    """Tuning SHA flows into tracker and report as provenance."""

    def test_tuning_sha_in_build_run_entry(self) -> None:
        mocks = _run_mocked_main()
        kwargs = mocks["entry"].call_args.kwargs
        self.assertEqual(kwargs["git_commit"], "abc1234")
        self.assertFalse(kwargs["git_dirty"])


class AgentForecastOrderingTests(unittest.TestCase):
    """Agent forecast excluded from consensus but included in report."""

    def test_consensus_excludes_agent(self) -> None:
        """select_featured_forecast sees only scripted + consensus."""
        mocks = _run_mocked_main()
        feat_forecasts = mocks["featured"].call_args.args[0]
        slugs = [f.slug for f in feat_forecasts]
        self.assertNotIn("agent_inference", slugs)
        self.assertIn("consensus_blend", slugs)
        self.assertIn("test_model", slugs)

    def test_report_includes_agent(self) -> None:
        """generate_report receives all_forecasts including agent."""
        mocks = _run_mocked_main()
        report_forecasts = mocks["report"].call_args.kwargs["all_forecasts"]
        slugs = [f.slug for f in report_forecasts]
        self.assertIn("agent_inference", slugs)
        self.assertIn("consensus_blend", slugs)

    def test_agent_insights_passed_to_report(self) -> None:
        """generate_report receives agent insights content."""
        mocks = _run_mocked_main()
        self.assertEqual(
            mocks["report"].call_args.kwargs["agent_insights"],
            "Test insights",
        )
if __name__ == "__main__":
    unittest.main()
