"""Tests for pipeline pre-flight checks, retro score formatting, and orchestration."""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from feedcast.agent_runner import (
    AGENT_TARGET_RUNTIME_SECONDS,
    AGENT_TIMEOUT_SECONDS,
)
from feedcast.data import ExportSnapshot, Forecast, ForecastPoint
from feedcast.pipeline import (
    _assert_clean_git_worktree,
    _best_retro_scores,
    _run_agent_inference,
    _run_execution,
    _run_trend_insights,
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


class TrendInsightsTests(unittest.TestCase):
    """Trend insights output validation."""

    def test_empty_trend_insights_output_raises(self) -> None:
        """A zero-length agent write should fail fast."""
        snapshot = ExportSnapshot(
            export_path=Path("exports/fake.csv"),
            activities=[],
            latest_activity_time=datetime(2026, 4, 1, 12, 0),
            dataset_id="sha256:fake",
            source_hash="sha256:fake",
        )

        with patch("feedcast.pipeline.invoke_agent"):
            with self.assertRaisesRegex(RuntimeError, "without writing any content"):
                _run_trend_insights(
                    agent="claude",
                    snapshot=snapshot,
                    cutoff=snapshot.latest_activity_time,
                )


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


class AgentInferenceRuntimeBudgetTests(unittest.TestCase):
    """Runtime budget context passed into the inference prompt."""

    def test_run_agent_inference_passes_runtime_budget_context(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="feedcast-agent-inference-test-"
        ) as temp_dir:
            agents_dir = Path(temp_dir)
            (agents_dir / "methodology.md").write_text(
                "Methodology\n",
                encoding="utf-8",
            )

            with patch("feedcast.pipeline.AGENTS_DIR", agents_dir), patch(
                "feedcast.pipeline.invoke_agent"
            ) as invoke_mock, patch(
                "feedcast.pipeline.validate_agent_forecast",
                return_value=[_FAKE_POINT],
            ):
                _run_agent_inference(
                    agent="claude",
                    snapshot=_FAKE_SNAPSHOT,
                    cutoff=_FAKE_SNAPSHOT.latest_activity_time,
                )

        context = invoke_mock.call_args.kwargs["context"]
        self.assertEqual(
            context["target_runtime_seconds"],
            str(AGENT_TARGET_RUNTIME_SECONDS),
        )
        self.assertEqual(
            context["target_runtime_minutes"],
            str(AGENT_TARGET_RUNTIME_SECONDS // 60),
        )
        self.assertEqual(
            context["hard_timeout_seconds"],
            str(AGENT_TIMEOUT_SECONDS),
        )
        self.assertEqual(
            context["hard_timeout_minutes"],
            str(AGENT_TIMEOUT_SECONDS // 60),
        )
        self.assertIn("T", context["runtime_start_time"])
        self.assertIn("T", context["runtime_deadline"])


class ExecutionLoggingTests(unittest.TestCase):
    """Execution logging should make remaining work explicit."""

    def test_run_execution_logs_when_only_agent_remains(self) -> None:
        def slow_agent(*args: object, **kwargs: object) -> Forecast:
            time.sleep(0.1)
            return _FAKE_AGENT

        with patch(
            "feedcast.pipeline.run_all_models",
            return_value=[_FAKE_BASE],
        ), patch(
            "feedcast.pipeline._run_agent_inference",
            side_effect=slow_agent,
        ):
            with self.assertLogs("feedcast.pipeline", level="INFO") as logs:
                _run_execution(
                    agent="claude",
                    snapshot=_FAKE_SNAPSHOT,
                    cutoff=_FAKE_SNAPSHOT.latest_activity_time,
                    skip_agent_inference=False,
                )

        output = "\n".join(logs.output)
        self.assertIn("Scripted models: done", output)
        self.assertIn("waiting on agent inference only", output)
if __name__ == "__main__":
    unittest.main()
