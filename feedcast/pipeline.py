"""Run the end-to-end forecast pipeline for one export snapshot.

This module is the orchestration layer: load data, run models and agents,
compare the prior run to new actuals, render the report, and update the
tracker.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from feedcast.agents import prompt_hash, run_all_agents
from feedcast.data import (
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    HORIZON_HOURS,
    ExportSnapshot,
    Forecast,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.models import (
    run_all_models,
    run_consensus_blend,
    select_featured_forecast,
)
from feedcast.report import generate_report
from feedcast.tracker import (
    build_run_entry,
    compute_retrospective,
    summarize_retrospective_history,
    save_run,
)

TRACKER_PATH = Path("tracker.json")


def main() -> None:
    """Run the forecasting pipeline for one export."""
    parser = argparse.ArgumentParser(
        description="Forecast Silas's next 24 hours of bottle feeds."
    )
    parser.add_argument(
        "--export-path",
        type=Path,
        default=None,
        help="Optional explicit export CSV. Defaults to the latest matching file.",
    )
    parser.add_argument(
        "--skip-agents",
        action="store_true",
        help="Skip Claude/Codex agent forecasts and run scripted models only.",
    )
    args = parser.parse_args()

    snapshot = load_export_snapshot(export_path=args.export_path)
    cutoff = snapshot.latest_activity_time
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    base_forecasts = run_all_models(snapshot.activities, cutoff, HORIZON_HOURS)

    # Pipeline-level events for consensus blend and reporting. This is a
    # pipeline concern, not a model concern — models build their own events.
    pipeline_events = build_feed_events(
        snapshot.activities,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    )
    consensus_forecast = run_consensus_blend(
        base_forecasts,
        pipeline_events,
        cutoff,
        HORIZON_HOURS,
    )
    featured_slug = select_featured_forecast([*base_forecasts, consensus_forecast])

    agent_forecasts: list[Forecast] = []
    prompt_hashes: dict[str, str] = {}
    if not args.skip_agents:
        agent_forecasts = run_all_agents(snapshot)
        shared_prompt_hash = prompt_hash()
        prompt_hashes = {
            forecast.slug: shared_prompt_hash for forecast in agent_forecasts
        }

    all_forecasts = [*base_forecasts, consensus_forecast, *agent_forecasts]
    retrospective = compute_retrospective(TRACKER_PATH, snapshot)
    historical_accuracy = summarize_retrospective_history(
        TRACKER_PATH,
        additional_retrospective=retrospective,
    )
    run_entry = build_run_entry(
        run_id=run_id,
        snapshot=snapshot,
        cutoff=cutoff,
        forecasts=all_forecasts,
        featured_slug=featured_slug,
        retrospective=retrospective,
        prompt_hashes=prompt_hashes,
    )

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
    )
    save_run(TRACKER_PATH, run_entry)

    _print_summary(
        snapshot=snapshot,
        cutoff=cutoff,
        featured_slug=featured_slug,
        all_forecasts=all_forecasts,
        report_dir=report_dir,
        tracker_path=TRACKER_PATH,
    )


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
