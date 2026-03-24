"""Slot Drift research: analyze daily feeding patterns for slot count and template.

Run from the repo root:
    .venv/bin/python -m feedcast.models.slot_drift.research

This script reproduces the data analysis that informed the Slot Drift
design. It reads the latest export, groups feeds by day, and shows:
  - Daily feed counts (total and full-feed-only)
  - Candidate template from days matching the median count
  - Trial Hungarian alignment with cost threshold

Update this script and re-run when new exports are available or when
revisiting model assumptions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from feedcast.data import (
    build_feed_events,
    hour_of_day,
    load_activities,
)

SNACK_THRESHOLD_OZ = 1.5
COST_THRESHOLD_HOURS = 2.0


def circular_distance(a: float, b: float, period: float = 24.0) -> float:
    """Circular distance between two hour-of-day values."""
    diff = abs(a - b) % period
    return min(diff, period - diff)


def main() -> None:
    """Run the analysis."""
    export_dir = Path("exports")
    latest_export = max(export_dir.glob("export_narababy_silas_*.csv"))
    print(f"Export: {latest_export}\n")

    activities = load_activities(latest_export)
    events = build_feed_events(activities, merge_window_minutes=None)

    # Group by calendar day.
    daily: dict[datetime, list] = defaultdict(list)
    for event in events:
        daily[event.time.date()].append(event)

    # Exclude the last day (likely incomplete: cutoff mid-day).
    all_dates = sorted(daily.keys())
    complete_dates = all_dates[:-1]

    # --- Daily feed counts ---
    print("=== DAILY FEED COUNTS ===\n")
    print(f"{'Date':<12} {'Total':>5} {'Full':>5} {'Snack':>5}  Feed times")
    counts = []
    full_counts = []
    for date in complete_dates:
        feeds = daily[date]
        full = [f for f in feeds if f.volume_oz >= SNACK_THRESHOLD_OZ]
        counts.append(len(feeds))
        full_counts.append(len(full))
        times_str = "  ".join(f.time.strftime("%H:%M") for f in feeds)
        print(
            f"{date}  {len(feeds):>5} {len(full):>5} "
            f"{len(feeds) - len(full):>5}  {times_str}"
        )

    print(f"\nTotal counts:     {counts}")
    print(f"  mean={np.mean(counts):.1f}  median={np.median(counts):.0f}")
    print(f"Full-feed counts: {full_counts}")
    print(f"  mean={np.mean(full_counts):.1f}  median={np.median(full_counts):.0f}")

    # --- Candidate template ---
    slot_count = int(np.median(counts))
    print(f"\n=== TEMPLATE (slot_count={slot_count}) ===\n")

    exact_days = [d for d in complete_dates if len(daily[d]) == slot_count]
    if not exact_days:
        print("No days with exactly the median count. Using closest.")
        exact_days = sorted(complete_dates, key=lambda d: abs(len(daily[d]) - slot_count))[:2]

    slot_matrix = []
    for date in exact_days:
        hours = sorted(hour_of_day(f.time) for f in daily[date])
        if len(hours) >= slot_count:
            slot_matrix.append(hours[:slot_count])
    template = np.median(np.array(slot_matrix), axis=0) if slot_matrix else np.linspace(0.5, 22, slot_count)

    print(f"Days used for template: {exact_days}")
    for i, hour in enumerate(template):
        h, m = int(hour), int((hour % 1) * 60)
        print(f"  Slot {i + 1}: {h:02d}:{m:02d} ({hour:.2f}h)")

    # --- Trial alignment ---
    print(f"\n=== TRIAL ALIGNMENT (threshold={COST_THRESHOLD_HOURS}h) ===\n")
    for date in complete_dates:
        feeds = daily[date]
        hours = np.array([hour_of_day(f.time) for f in feeds])
        feed_count = len(hours)

        cost = np.zeros((feed_count, slot_count))
        for i in range(feed_count):
            for j in range(slot_count):
                cost[i, j] = circular_distance(hours[i], template[j])

        row_ind, col_ind = linear_sum_assignment(cost)
        matched_count = sum(
            1 for r, c in zip(row_ind, col_ind)
            if cost[r, c] <= COST_THRESHOLD_HOURS
        )
        unmatched_count = feed_count - matched_count
        max_cost = max(
            (cost[r, c] for r, c in zip(row_ind, col_ind)
             if cost[r, c] <= COST_THRESHOLD_HOURS),
            default=0.0,
        )
        print(
            f"{date} ({feed_count} feeds): "
            f"{matched_count} matched, {unmatched_count} unmatched, "
            f"max_cost={max_cost:.2f}h"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
