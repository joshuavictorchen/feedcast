"""Run the end-to-end forecast pipeline for one export snapshot.

This module is the orchestration layer: load data, invoke agents for trend
analysis and model tuning, run scripted models and agent inference, compare
the prior run to new actuals, render the report, and update the tracker.
"""

from __future__ import annotations

import logging
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from feedcast.agent_runner import (
    AGENT_TARGET_RUNTIME_SECONDS,
    AGENT_TIMEOUT_SECONDS,
    invoke_agent,
    validate_agent_forecast,
)
from feedcast.data import (
    BIRTH_DATE,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    ExportSnapshot,
    Forecast,
    HORIZON_HOURS,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.models import (
    MODELS,
    run_all_models,
    run_consensus_blend,
    select_featured_forecast,
)
from feedcast.report import generate_report
from feedcast.tracker import (
    Retrospective,
    build_run_entry,
    compute_retrospective,
    load_tracker,
    save_run,
    summarize_retrospective_history,
)

logger = logging.getLogger("feedcast.pipeline")

TRACKER_PATH = Path("tracker.json")
REPORT_DIR = Path("report")
AGENTS_DIR = Path("feedcast/agents")
SKILLS_DIR = Path("skills")

AGENT_INFERENCE_NAME = "Agent Inference"
AGENT_INFERENCE_SLUG = "agent_inference"


def main(
    export_path: Path | None = None,
    agent: str = "claude",
    skip_tuning: bool = False,
    skip_insights: bool = False,
    skip_agent_inference: bool = False,
) -> None:
    """Run the forecasting pipeline for one export.

    Args:
        export_path: Explicit export CSV path. Defaults to the latest export.
        agent: Agent CLI to use ("claude" or "codex").
        skip_tuning: Skip agent model tuning step.
        skip_insights: Skip agent trend insights step.
        skip_agent_inference: Skip agent inference forecast.
    """
    # Pre-flight
    _assert_clean_git_worktree()
    snapshot = load_export_snapshot(export_path=export_path)
    cutoff = snapshot.latest_activity_time
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger.info(
        "Pre-flight: %s, cutoff %s",
        snapshot.export_path.name, cutoff.isoformat(sep=" "),
    )

    # All mutations happen on a dedicated run branch
    _create_run_branch(run_id)
    logger.info("Branch: feedcast/%s", run_id)

    # Compute retrospective early — tuning agents need prior scores, and
    # the same result is reused for the report and tracker later.
    retrospective = compute_retrospective(TRACKER_PATH, snapshot)
    if retrospective.available:
        n_scored = sum(
            1 for r in retrospective.results if r.score is not None
        )
        logger.info(
            "Retrospective: %d models scored (%.0fh observed)",
            n_scored, retrospective.observed_horizon_hours,
        )
    else:
        logger.info("Retrospective: not available (same dataset or no prior run)")

    # Trend insights (agent analyzes recent feeding patterns)
    agent_insights: str | None = None
    if not skip_insights:
        logger.info("Trend insights: starting...")
        t0 = time.monotonic()
        agent_insights = _run_trend_insights(agent, snapshot, cutoff)
        logger.info("Trend insights: done (%s)", _elapsed(t0))
    else:
        logger.info("Trend insights: skipped")

    # Model tuning (agent assesses and optionally tunes each scripted model)
    if not skip_tuning:
        slugs = [spec.slug for spec in MODELS]
        logger.info("Model tuning: %d models in parallel (%s)...", len(slugs), ", ".join(slugs))
        t0 = time.monotonic()
        _run_model_tuning(agent, snapshot, retrospective)
        logger.info("Model tuning: all done (%s)", _elapsed(t0))
    else:
        logger.info("Model tuning: skipped")

    # Tuning commit — capture the SHA as provenance for tracker and report.
    # The worktree will be dirty again after execution produces outputs.
    _git_commit_all("Agent tuning", allow_empty=True)
    tuning_sha = _git_short_sha()
    logger.info("Tuning commit: %s", tuning_sha)

    # Execute scripted models and agent inference in parallel
    logger.info(
        "Execution: scripted models%s...",
        " + agent inference" if not skip_agent_inference else "",
    )
    if skip_agent_inference:
        logger.info("Agent inference: skipped")
    base_forecasts, agent_forecast = _run_execution(
        agent=agent,
        snapshot=snapshot,
        cutoff=cutoff,
        skip_agent_inference=skip_agent_inference,
    )

    # Consensus blend and featured selection (scripted models only)
    pipeline_events = build_feed_events(
        snapshot.activities,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    )
    consensus_forecast = run_consensus_blend(
        base_forecasts, pipeline_events, cutoff, HORIZON_HOURS,
    )
    featured_slug = select_featured_forecast([*base_forecasts, consensus_forecast])
    blend_count = len(consensus_forecast.points) if consensus_forecast.available else 0
    logger.info(
        "Consensus blend: %d feeds, featured=%s", blend_count, featured_slug,
    )

    # Agent forecast is in the report and tracker but excluded from the
    # consensus blend — append after blend computation.
    all_forecasts = [*base_forecasts, consensus_forecast]
    if agent_forecast is not None:
        all_forecasts.append(agent_forecast)

    # Retrospective history (reuses early-computed retrospective)
    historical_accuracy = summarize_retrospective_history(
        TRACKER_PATH, additional_retrospective=retrospective,
    )

    # Build tracker entry with the captured tuning commit as provenance
    run_entry = build_run_entry(
        run_id=run_id,
        snapshot=snapshot,
        cutoff=cutoff,
        forecasts=all_forecasts,
        featured_slug=featured_slug,
        retrospective=retrospective,
        git_commit=tuning_sha,
        git_dirty=False,
    )

    # Render report and persist tracker
    logger.info("Generating report...")
    report_dir = generate_report(
        snapshot=snapshot,
        all_forecasts=all_forecasts,
        featured_slug=featured_slug,
        events=pipeline_events,
        cutoff=cutoff,
        run_id=run_id,
        retrospective=retrospective,
        historical_accuracy=historical_accuracy,
        tracker_meta=run_entry,
        agent_insights=agent_insights,
    )
    save_run(TRACKER_PATH, run_entry)
    logger.info("Report: %s", report_dir / "report.md")

    # Results commit — report, tracker, forecast.json, methodology changes
    _git_commit_all("Pipeline results")
    logger.info("Results committed")

    _print_summary(
        snapshot=snapshot,
        cutoff=cutoff,
        featured_slug=featured_slug,
        all_forecasts=all_forecasts,
        report_dir=report_dir,
        tracker_path=TRACKER_PATH,
    )


# ---------------------------------------------------------------------------
# Agent orchestration
# ---------------------------------------------------------------------------


def _run_trend_insights(
    agent: str,
    snapshot: ExportSnapshot,
    cutoff: datetime,
) -> str:
    """Run the trend insights skill and return the agent's analysis.

    Publishes `report/agent-insights.md` as soon as the agent finishes, so
    readers of the run branch see fresh insights without waiting for the
    rest of the pipeline. The finalize-time atomic report swap later
    rewrites the same file with identical content from the staging dir, so
    the committed state is unchanged.
    """
    output_path = REPORT_DIR / "agent-insights.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Clear any prior-run content first so a failed agent write cannot be
    # mistaken for a valid output on the read-back below.
    output_path.unlink(missing_ok=True)

    invoke_agent(
        agent=agent,
        prompt_path=SKILLS_DIR / "trend_insights" / "prompt.md",
        context={
            "export_path": str(snapshot.export_path),
            "baby_age_days": str((cutoff.date() - BIRTH_DATE.date()).days),
            "cutoff_time": cutoff.isoformat(),
            "output_path": str(output_path),
        },
    )

    insights = (
        output_path.read_text(encoding="utf-8").strip()
        if output_path.exists()
        else ""
    )
    if not insights:
        raise RuntimeError(
            "Trend insights agent completed without writing any content."
        )
    return insights


def _run_model_tuning(
    agent: str,
    snapshot: ExportSnapshot,
    retrospective: Retrospective,
) -> None:
    """Run model tuning agents in parallel for all scripted models."""
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                _tune_one_model, agent, snapshot, retrospective, spec.slug,
            ): spec.slug
            for spec in MODELS
        }
        for future in futures:
            future.result()


def _tune_one_model(
    agent: str,
    snapshot: ExportSnapshot,
    retrospective: Retrospective,
    model_slug: str,
) -> None:
    """Invoke the model tuning skill for one scripted model."""
    logger.info("Model tuning [%s]: starting", model_slug)
    t0 = time.monotonic()
    start_time = datetime.now().astimezone()
    deadline = start_time + timedelta(seconds=AGENT_TIMEOUT_SECONDS)
    invoke_agent(
        agent=agent,
        prompt_path=SKILLS_DIR / "model_tuning" / "prompt.md",
        context={
            "model_slug": model_slug,
            "model_dir": str(Path("feedcast/models") / model_slug),
            "export_path": str(snapshot.export_path),
            "last_retro_scores": _best_retro_scores(retrospective, model_slug),
            "research_hub_path": "feedcast/research",
            "target_runtime_seconds": str(AGENT_TARGET_RUNTIME_SECONDS),
            "target_runtime_minutes": str(AGENT_TARGET_RUNTIME_SECONDS // 60),
            "hard_timeout_seconds": str(AGENT_TIMEOUT_SECONDS),
            "hard_timeout_minutes": str(AGENT_TIMEOUT_SECONDS // 60),
            "runtime_start_time": start_time.isoformat(timespec="seconds"),
            "runtime_deadline": deadline.isoformat(timespec="seconds"),
        },
    )
    logger.info("Model tuning [%s]: done (%s)", model_slug, _elapsed(t0))


def _run_execution(
    agent: str,
    snapshot: ExportSnapshot,
    cutoff: datetime,
    skip_agent_inference: bool,
) -> tuple[list[Forecast], Forecast | None]:
    """Run scripted models and agent inference in parallel."""
    t0 = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=2)
    models_future = executor.submit(
        run_all_models, snapshot.activities, cutoff, HORIZON_HOURS,
    )
    agent_future = None
    if not skip_agent_inference:
        agent_future = executor.submit(
            _run_agent_inference, agent, snapshot, cutoff,
        )

    # Wait for models first — they're fast. If they fail, surface the
    # error immediately instead of waiting for the agent to finish.
    try:
        base_forecasts = models_future.result()
    except Exception:
        if agent_future is not None:
            agent_future.cancel()
        executor.shutdown(wait=False)
        raise

    available = sum(1 for f in base_forecasts if f.available)
    unavailable = sum(1 for f in base_forecasts if not f.available)
    logger.info(
        "Scripted models: done (%s, %d available, %d unavailable)",
        _elapsed(t0), available, unavailable,
    )
    if agent_future is not None and not agent_future.done():
        logger.info(
            "Execution: scripted models finished; waiting on agent inference "
            "only (target=%dm, hard timeout=%dm)",
            AGENT_TARGET_RUNTIME_SECONDS // 60,
            AGENT_TIMEOUT_SECONDS // 60,
        )

    agent_forecast = agent_future.result() if agent_future else None
    executor.shutdown(wait=False)
    return base_forecasts, agent_forecast


def _run_agent_inference(
    agent: str,
    snapshot: ExportSnapshot,
    cutoff: datetime,
) -> Forecast:
    """Run the agent inference model and return its Forecast."""
    forecast_path = AGENTS_DIR / "forecast.json"
    methodology_path = AGENTS_DIR / "methodology.md"

    # Read methodology before invoking the agent so the report text matches
    # the provenance SHA (tuning commit). Workspace mutations the agent makes
    # during this run take effect in the next run's report.
    methodology = methodology_path.read_text(encoding="utf-8").strip()

    # Delete stale output from a prior run
    if forecast_path.exists():
        forecast_path.unlink()

    start_time = datetime.now().astimezone()
    deadline = start_time + timedelta(seconds=AGENT_TIMEOUT_SECONDS)
    logger.info(
        "Agent inference: starting (%s, target=%dm, hard timeout=%dm)...",
        agent,
        AGENT_TARGET_RUNTIME_SECONDS // 60,
        AGENT_TIMEOUT_SECONDS // 60,
    )
    t0 = time.monotonic()
    invoke_agent(
        agent=agent,
        prompt_path=AGENTS_DIR / "prompt.md",
        context={
            "export_path": str(snapshot.export_path),
            "workspace_path": str(AGENTS_DIR),
            "cutoff_time": cutoff.isoformat(),
            "horizon_hours": str(HORIZON_HOURS),
            "target_runtime_seconds": str(AGENT_TARGET_RUNTIME_SECONDS),
            "target_runtime_minutes": str(AGENT_TARGET_RUNTIME_SECONDS // 60),
            "hard_timeout_seconds": str(AGENT_TIMEOUT_SECONDS),
            "hard_timeout_minutes": str(AGENT_TIMEOUT_SECONDS // 60),
            "runtime_start_time": start_time.isoformat(timespec="seconds"),
            "runtime_deadline": deadline.isoformat(timespec="seconds"),
        },
    )

    points = validate_agent_forecast(
        forecast_path, snapshot.latest_activity_time, HORIZON_HOURS,
    )
    logger.info("Agent inference: done (%s, %d feeds)", _elapsed(t0), len(points))

    return Forecast(
        name=AGENT_INFERENCE_NAME,
        slug=AGENT_INFERENCE_SLUG,
        points=points,
        methodology=methodology,
        diagnostics={},
    )


def _best_retro_scores(
    current_retro: Retrospective,
    model_slug: str,
) -> str:
    """Format the best available retrospective scores for a tuning prompt.

    Uses the current retrospective when it has scores for this model.
    Otherwise falls back to the latest completed retrospective in tracker
    history. This handles same-dataset reruns where the current retro is
    unavailable even though prior runs have evidence.
    """
    if current_retro.available:
        for result in current_retro.results:
            if result.slug == model_slug and result.score is not None:
                return (
                    f"Score: {result.score:.1f}, "
                    f"Count: {result.count_score:.1f}, "
                    f"Timing: {result.timing_score:.1f}\n"
                    f"Episodes: {result.predicted_episode_count} predicted, "
                    f"{result.actual_episode_count} actual, "
                    f"{result.matched_episode_count} matched\n"
                    f"Status: {result.status}"
                )

    # Fall back to the latest completed retrospective from tracker history
    tracker = load_tracker(TRACKER_PATH)
    for run in reversed(tracker["runs"]):
        retro_data = run.get("retrospective", {})
        if not retro_data.get("available"):
            continue
        for result in retro_data.get("results", []):
            if result.get("slug") == model_slug and result.get("score") is not None:
                return (
                    f"Score: {result['score']:.1f}, "
                    f"Count: {result['count_score']:.1f}, "
                    f"Timing: {result['timing_score']:.1f}\n"
                    f"(from prior run {run.get('run_id', 'unknown')})"
                )

    return "No retrospective scores available yet."
# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _create_run_branch(run_id: str) -> None:
    """Create and check out a dedicated branch for this pipeline run."""
    subprocess.run(
        ["git", "checkout", "-b", f"feedcast/{run_id}"],
        cwd=_repo_root(),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_commit_all(message: str, allow_empty: bool = False) -> None:
    """Stage all changes and commit."""
    subprocess.run(
        ["git", "add", "-A"],
        cwd=_repo_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    cmd = ["git", "commit", "-m", message]
    if allow_empty:
        cmd.append("--allow-empty")
    subprocess.run(
        cmd, cwd=_repo_root(), check=True, capture_output=True, text=True,
    )


def _git_short_sha() -> str:
    """Return the current short commit SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=_repo_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _print_summary(
    snapshot: ExportSnapshot,
    cutoff: datetime,
    featured_slug: str,
    all_forecasts: list[Forecast],
    report_dir: Path,
    tracker_path: Path,
) -> None:
    """Print a compact run summary to stdout."""
    featured = next(
        forecast for forecast in all_forecasts if forecast.slug == featured_slug
    )
    print(f"Export:      {snapshot.export_path}")
    print(f"Dataset ID:  {snapshot.dataset_id}")
    print(f"Cutoff:      {cutoff.isoformat(sep=' ')}")
    print(f"Featured:    {featured.name}")
    if featured.points:
        first_point = featured.points[0]
        print(
            "First feed:  "
            f"{first_point.time.strftime('%Y-%m-%d %I:%M %p')} "
            f"({first_point.gap_hours:.1f}h, {first_point.volume_oz:.1f} oz)"
        )
    print(f"Report:      {report_dir / 'report.md'}")
    print(f"Tracker:     {tracker_path}")


def _assert_clean_git_worktree() -> None:
    """Refuse to run when the repository has local changes."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("git is required to run the forecast pipeline.") from error
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip()
        raise RuntimeError(
            f"Failed to inspect git worktree: {stderr or 'no stderr'}"
        ) from error

    if result.stdout.strip():
        raise RuntimeError(
            "Refusing to run with a dirty git worktree. Commit or stash changes "
            "first."
        )


def _elapsed(start: float) -> str:
    """Format elapsed time since *start* as a compact human-readable string."""
    total = time.monotonic() - start
    if total < 1:
        return f"{max(1, int(total * 1000))}ms"
    if total < 60:
        return f"{int(total)}s"
    minutes, secs = divmod(int(total), 60)
    return f"{minutes}m {secs:02d}s"


def _repo_root() -> Path:
    """Return the repository root used for git commands."""
    return Path(__file__).resolve().parent.parent
