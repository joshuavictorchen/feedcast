"""Slot Drift research: analyze daily feeding patterns for slot count and template.

Run from the repo root:
    .venv/bin/python -m feedcast.models.slot_drift.research

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
from datetime import datetime
from io import StringIO
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from feedcast.data import (
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
    SNACK_THRESHOLD_OZ,
)
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
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
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

    # Save results alongside the script.
    results_path = OUTPUT_DIR / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
