"""Slot Drift research: analyze daily feeding patterns for slot count and template.

Run from the repo root:
    .venv/bin/python -m feedcast.models.slot_drift.analysis

This script reproduces the data analysis that informed the Slot Drift
design. It uses the same export selection, data parsing, lookback window,
and tuning constants as the model itself, so its output matches what the
model would see at the same cutoff.

Update this script and re-run when new exports are available or when
revisiting model assumptions.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from io import StringIO
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from feedcast.clustering import FeedEpisode, group_into_episodes
from feedcast.data import (
    FeedEvent,
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
)
from feedcast.replay import score_model, tune_model

# Volume threshold for filtering snack-sized feeds in research analysis.
SNACK_THRESHOLD_OZ = 1.5
from feedcast.models.slot_drift.model import (
    LOOKBACK_DAYS,
    MATCH_COST_THRESHOLD_HOURS,
    _circular_distance,
    _recent_complete_days,
    _group_by_day,
)

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent


def main() -> None:
    """Run the analysis using the same data window as the model."""
    # Tee output to both stdout and a results file.
    output_capture = StringIO()

    def log(text: str = "") -> None:
        print(text)
        output_capture.write(text + "\n")

    snapshot = load_export_snapshot()
    cutoff = snapshot.latest_activity_time
    events = build_feed_events(snapshot.activities, merge_window_minutes=None)
    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Lookback: {LOOKBACK_DAYS} days")
    log()

    # Use the model's own grouping and lookback logic.
    daily_feeds = _group_by_day(events, cutoff)
    complete_days = _recent_complete_days(daily_feeds, cutoff)

    # --- Daily feed counts ---
    log("=== DAILY FEED COUNTS ===")
    log()
    log(f"{'Date':<12} {'Total':>5} {'Full':>5} {'Snack':>5}  Feed times")
    counts = []
    full_counts = []
    for date, feeds in complete_days:
        full = [f for f in feeds if f.volume_oz >= SNACK_THRESHOLD_OZ]
        counts.append(len(feeds))
        full_counts.append(len(full))
        times_str = "  ".join(f.time.strftime("%H:%M") for f in feeds)
        log(
            f"{date}  {len(feeds):>5} {len(full):>5} "
            f"{len(feeds) - len(full):>5}  {times_str}"
        )

    log(f"\nTotal counts:     {counts}")
    log(f"  mean={np.mean(counts):.1f}  median={np.median(counts):.0f}")
    log(f"Full-feed counts: {full_counts}")
    log(f"  mean={np.mean(full_counts):.1f}  median={np.median(full_counts):.0f}")

    # --- Candidate template ---
    slot_count = int(np.median(counts))
    log(f"\n=== TEMPLATE (slot_count={slot_count}) ===")
    log()

    exact_days = [(d, f) for d, f in complete_days if len(f) == slot_count]
    if not exact_days:
        log("No days with exactly the median count. Using closest.")
        exact_days = sorted(complete_days, key=lambda pair: abs(len(pair[1]) - slot_count))[:2]

    slot_matrix = []
    for date, feeds in exact_days:
        hours = sorted(hour_of_day(f.time) for f in feeds)
        if len(hours) >= slot_count:
            slot_matrix.append(hours[:slot_count])
    template = (
        np.median(np.array(slot_matrix), axis=0)
        if slot_matrix
        else np.linspace(0.5, 22, slot_count)
    )

    log(f"Days used for template: {[d for d, _ in exact_days]}")
    for i, hour in enumerate(template):
        h, m = int(hour), int((hour % 1) * 60)
        log(f"  Slot {i + 1}: {h:02d}:{m:02d} ({hour:.2f}h)")

    # --- Trial alignment ---
    log(f"\n=== TRIAL ALIGNMENT (threshold={MATCH_COST_THRESHOLD_HOURS}h) ===")
    log()
    for date, feeds in complete_days:
        hours = np.array([hour_of_day(f.time) for f in feeds])
        feed_count = len(hours)

        cost = np.zeros((feed_count, slot_count))
        for i in range(feed_count):
            for j in range(slot_count):
                cost[i, j] = _circular_distance(hours[i], template[j])

        row_ind, col_ind = linear_sum_assignment(cost)
        matched_count = sum(
            1 for r, c in zip(row_ind, col_ind)
            if cost[r, c] <= MATCH_COST_THRESHOLD_HOURS
        )
        unmatched_count = feed_count - matched_count
        max_cost = max(
            (cost[r, c] for r, c in zip(row_ind, col_ind)
             if cost[r, c] <= MATCH_COST_THRESHOLD_HOURS),
            default=0.0,
        )
        log(
            f"{date} ({feed_count} feeds): "
            f"{matched_count} matched, {unmatched_count} unmatched, "
            f"max_cost={max_cost:.2f}h"
        )

    # --- Episode-level analysis ---
    # Group raw feeds into episodes and repeat the daily count and template
    # analysis to see how clustering changes slot count and template positions.
    log(f"\n\n{'=' * 60}")
    log("EPISODE-LEVEL ANALYSIS")
    log(f"{'=' * 60}")
    log()

    # Group each day's feeds into episodes.
    log("=== DAILY EPISODE COUNTS ===")
    log()
    log(f"{'Date':<12} {'Raw':>5} {'Episodes':>8}  Episode times (feed counts)")
    episode_counts = []
    for day, feeds in complete_days:
        episodes = group_into_episodes(feeds)
        episode_counts.append(len(episodes))
        ep_strs = []
        for episode in episodes:
            time_str = episode.time.strftime("%H:%M")
            if episode.feed_count > 1:
                ep_strs.append(f"{time_str}({episode.feed_count})")
            else:
                ep_strs.append(time_str)
        log(
            f"{day}  {len(feeds):>5} {len(episodes):>8}  "
            f"{'  '.join(ep_strs)}"
        )

    log(f"\nRaw counts:     {counts}")
    log(f"  mean={np.mean(counts):.1f}  median={np.median(counts):.0f}")
    log(f"Episode counts: {episode_counts}")
    log(f"  mean={np.mean(episode_counts):.1f}  median={np.median(episode_counts):.0f}")

    # Build episode-level template for comparison.
    episode_slot_count = int(np.median(episode_counts))
    log(f"\n=== EPISODE TEMPLATE (slot_count={episode_slot_count}) ===")
    log()

    # Collect episode-level days and build template from those with
    # the canonical episode count.
    episode_days: list[tuple] = []
    for day, feeds in complete_days:
        episodes = group_into_episodes(feeds)
        episode_days.append((day, episodes))

    exact_episode_days = [
        (d, eps) for d, eps in episode_days if len(eps) == episode_slot_count
    ]
    if not exact_episode_days:
        log("No days with exactly the median episode count. Using closest.")
        exact_episode_days = sorted(
            episode_days, key=lambda pair: abs(len(pair[1]) - episode_slot_count),
        )[:2]

    ep_slot_matrix = []
    for day, episodes in exact_episode_days:
        hours = sorted(hour_of_day(ep.time) for ep in episodes)
        if len(hours) >= episode_slot_count:
            ep_slot_matrix.append(hours[:episode_slot_count])

    episode_template = (
        np.median(np.array(ep_slot_matrix), axis=0)
        if ep_slot_matrix
        else np.linspace(0.5, 22, episode_slot_count)
    )

    log(f"Days used for episode template: {[d for d, _ in exact_episode_days]}")
    for i, hour in enumerate(episode_template):
        h, m = int(hour), int((hour % 1) * 60)
        log(f"  Slot {i + 1}: {h:02d}:{m:02d} ({hour:.2f}h)")

    # Trial alignment with episode-level data.
    log(f"\n=== EPISODE TRIAL ALIGNMENT (threshold={MATCH_COST_THRESHOLD_HOURS}h) ===")
    log()
    for day, episodes in episode_days:
        hours = np.array([hour_of_day(ep.time) for ep in episodes])
        ep_count = len(hours)

        cost = np.zeros((ep_count, episode_slot_count))
        for i in range(ep_count):
            for j in range(episode_slot_count):
                cost[i, j] = _circular_distance(hours[i], episode_template[j])

        row_ind, col_ind = linear_sum_assignment(cost)
        matched_count = sum(
            1 for r, c in zip(row_ind, col_ind)
            if cost[r, c] <= MATCH_COST_THRESHOLD_HOURS
        )
        unmatched_count = ep_count - matched_count
        max_cost = max(
            (cost[r, c] for r, c in zip(row_ind, col_ind)
             if cost[r, c] <= MATCH_COST_THRESHOLD_HOURS),
            default=0.0,
        )
        log(
            f"{day} ({ep_count} episodes): "
            f"{matched_count} matched, {unmatched_count} unmatched, "
            f"max_cost={max_cost:.2f}h"
        )

    # Compare raw vs. episode templates.
    if slot_count == episode_slot_count:
        log(f"\n=== TEMPLATE COMPARISON (both {slot_count} slots) ===")
        log()
        log(f"{'Slot':<6} {'Raw':>10} {'Episode':>10} {'Delta (min)':>12}")
        for i in range(slot_count):
            raw_h = template[i]
            ep_h = episode_template[i]
            delta_min = (ep_h - raw_h) * 60
            raw_str = f"{int(raw_h):02d}:{int((raw_h % 1) * 60):02d}"
            ep_str = f"{int(ep_h):02d}:{int((ep_h % 1) * 60):02d}"
            log(f"  {i + 1:<4} {raw_str:>10} {ep_str:>10} {delta_min:>+10.1f}")
    else:
        log(
            f"\nSlot counts differ: raw={slot_count}, episode={episode_slot_count}. "
            f"Direct template comparison not possible."
        )

    # ================================================================
    # CANONICAL MULTI-WINDOW EVALUATION
    # ================================================================
    log(f"\n\n{'=' * 60}")
    log("CANONICAL MULTI-WINDOW EVALUATION")
    log(f"{'=' * 60}")
    log()
    log("Production-constant evaluation via score_model (same")
    log("infrastructure as the replay CLI).")
    log()

    canonical = score_model("slot_drift", export_path=snapshot.export_path)
    rw = canonical["replay_windows"]
    agg = rw["aggregate"]
    log(f"Aggregate:  headline={agg['headline']:.1f}  count={agg['count']:.1f}  "
        f"timing={agg['timing']:.1f}")
    log(f"Windows:    {rw['scored_window_count']} scored / {rw['window_count']} total "
        f"({rw['availability_ratio'] * 100:.1f}% availability)")
    log(f"Half-life:  {rw['half_life_hours']}h  Lookback: {rw['lookback_hours']}h")
    log()
    log("Per-window breakdown:")
    log(f"  {'Cutoff':<22} {'Weight':>7} {'Head':>7} {'Count':>7} {'Time':>7}  Status")
    for w in rw["per_window"]:
        if w["score"] is not None:
            s = w["score"]
            log(f"  {w['cutoff']:<22} {w['weight']:>7.4f} {s['headline']:>7.1f} "
                f"{s['count']:>7.1f} {s['timing']:>7.1f}  {w['status']}")
        else:
            log(f"  {w['cutoff']:<22} {w['weight']:>7.4f} {'--':>7} {'--':>7} "
                f"{'--':>7}  {w['status']}")
    log()

    # ================================================================
    # CANONICAL MULTI-WINDOW TUNING
    # ================================================================
    log(f"\n\n{'=' * 60}")
    log("CANONICAL MULTI-WINDOW TUNING")
    log(f"{'=' * 60}")
    log()
    log("Parameter sweep via tune_model (same infrastructure as the")
    log("replay CLI). Sweeps DRIFT_WEIGHT_HALF_LIFE_DAYS,")
    log("MATCH_COST_THRESHOLD_HOURS, and LOOKBACK_DAYS jointly.")
    log()

    tune_result = tune_model(
        "slot_drift",
        candidates_by_name={
            "DRIFT_WEIGHT_HALF_LIFE_DAYS": [
                0.25,
                0.5,
                0.75,
                1.0,
                1.25,
                1.5,
                2.0,
                2.5,
                3.0,
                4.0,
                5.0,
                7.0,
            ],
            "MATCH_COST_THRESHOLD_HOURS": [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0],
            "LOOKBACK_DAYS": [3, 4, 5, 6, 7, 10, 14],
        },
        export_path=snapshot.export_path,
    )

    bl = tune_result["baseline"]
    bl_rw = bl["replay_windows"]
    bl_agg = bl_rw["aggregate"]
    log(f"Baseline: {bl['params']}")
    log(f"  headline={bl_agg['headline']:.1f}  count={bl_agg['count']:.1f}  "
        f"timing={bl_agg['timing']:.1f}  "
        f"scored={bl_rw['scored_window_count']}/{bl_rw['window_count']}")

    best = tune_result["best"]
    best_rw = best["replay_windows"]
    best_agg = best_rw["aggregate"]
    log(f"\nBest: {best['params']}")
    log(f"  headline={best_agg['headline']:.1f}  count={best_agg['count']:.1f}  "
        f"timing={best_agg['timing']:.1f}  "
        f"scored={best_rw['scored_window_count']}/{best_rw['window_count']}")
    log(f"  headline_delta={best['headline_delta']:+.1f}  "
        f"availability_delta={best['availability_delta']:+d}")

    log(f"\nTop 10 of {tune_result['search']['total_candidates']} candidates:")
    log(f"  {'Rank':<5} {'Headline':>9} {'Count':>7} {'Timing':>7} "
        f"{'Scored':>7}  Params")
    for i, c in enumerate(tune_result["candidates"][:10]):
        cw = c["replay_windows"]
        ca = cw["aggregate"]
        log(f"  {i + 1:<5} {ca['headline']:>9.1f} {ca['count']:>7.1f} "
            f"{ca['timing']:>7.1f} "
            f"{cw['scored_window_count']:>3}/{cw['window_count']:<3}  {c['params']}")
    log()

    # Save results alongside the script.
    artifacts_dir = OUTPUT_DIR / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    results_path = artifacts_dir / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
