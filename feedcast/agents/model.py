#!/usr/bin/env python3
"""Four-bucket cadence projection for agent inference.

Projects the next N hours of bottle-feed episodes by stepping forward
from the cutoff using a recency-weighted median inter-episode gap drawn
from one of four clock-hour sub-periods:

    evening       19:00 - 22:00
    deep night    22:00 - 03:00
    early morning 03:00 - 07:00
    daytime       07:00 - 19:00

Each inter-episode gap is tagged by the clock hour of the feed that
*starts* the gap. Bucket medians use an exponential recency weight
(48-hour half-life). Volume is the recency-weighted median of recent
episode volumes.

Run as a script (from repo root):
    .venv/bin/python feedcast/agents/model.py \\
        --export exports/export_narababy_silas_YYYYMMDD.csv \\
        --cutoff 2026-04-16T21:15:00 --horizon 24
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from feedcast.clustering import episodes_as_events
from feedcast.data import DATA_FLOOR, build_feed_events, load_activities

# ── Tuning Constants ──────────────────────────────────────────────

LOOKBACK_DAYS = 7
HALF_LIFE_HOURS = 48.0
MIN_BUCKET_GAPS = 3
MIN_GAP_HOURS = 1.0
MIN_EPISODES = 5
DEFAULT_VOLUME_OZ = 4.0

BUCKETS = ("evening", "deep_night", "early_morning", "daytime")


# ── Helpers ───────────────────────────────────────────────────────


def classify(hour: int) -> str:
    """Return the sub-period bucket for a gap-starting clock hour."""
    if 19 <= hour < 22:
        return "evening"
    if hour >= 22 or hour < 3:
        return "deep_night"
    if 3 <= hour < 7:
        return "early_morning"
    return "daytime"


def weight(event_time: datetime, reference: datetime) -> float:
    """Exponential recency weight: 1.0 at reference, halving every half-life."""
    age_hours = max((reference - event_time).total_seconds() / 3600, 0)
    return 2.0 ** (-age_hours / HALF_LIFE_HOURS)


def weighted_median(values: list[float], weights: list[float]) -> float | None:
    """Weighted median. Returns None if inputs are empty or weights sum to zero."""
    if not values:
        return None
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    if total == 0:
        return None
    midpoint = total / 2
    cumulative = 0.0
    for value, w in pairs:
        cumulative += w
        if cumulative >= midpoint:
            return value
    return pairs[-1][0]


# ── Core Algorithm ────────────────────────────────────────────────


def load_episodes(export_path: Path, cutoff: datetime):
    """Load export, build bottle-only episodes, filter to lookback window."""
    activities = load_activities(export_path)
    events = build_feed_events(activities, merge_window_minutes=None)
    episodes = episodes_as_events(events)
    lookback_start = max(cutoff - timedelta(days=LOOKBACK_DAYS), DATA_FLOOR)
    recent = [e for e in episodes if lookback_start <= e.time <= cutoff]
    recent.sort(key=lambda e: e.time)
    return recent


def bucket_medians(episodes, cutoff):
    """Compute recency-weighted median gap per sub-period.

    Gaps with fewer than ``MIN_BUCKET_GAPS`` observations fall back to
    the overall recency-weighted median.
    """
    buckets: dict[str, list[tuple[float, float]]] = {b: [] for b in BUCKETS}
    all_gaps: list[tuple[float, float]] = []
    for i in range(1, len(episodes)):
        gap_hours = (episodes[i].time - episodes[i - 1].time).total_seconds() / 3600
        if gap_hours < 0.1:
            continue
        bucket = classify(episodes[i - 1].time.hour)
        w = weight(episodes[i].time, cutoff)
        buckets[bucket].append((gap_hours, w))
        all_gaps.append((gap_hours, w))

    overall = weighted_median([g for g, _ in all_gaps], [w for _, w in all_gaps])

    medians: dict[str, float] = {}
    for b in BUCKETS:
        entries = buckets[b]
        if len(entries) >= MIN_BUCKET_GAPS:
            medians[b] = weighted_median([g for g, _ in entries], [w for _, w in entries])
        else:
            medians[b] = overall
    return medians, buckets, overall


def compute_volume(episodes, cutoff):
    """Recency-weighted median episode volume."""
    vols = [(e.volume_oz, weight(e.time, cutoff)) for e in episodes if e.volume_oz > 0]
    if not vols:
        return DEFAULT_VOLUME_OZ
    v = weighted_median([x for x, _ in vols], [w for _, w in vols])
    return round(v, 1) if v else DEFAULT_VOLUME_OZ


def project(cutoff: datetime, horizon_hours: int, medians: dict[str, float], volume_oz: float):
    """Step forward from the cutoff placing feeds at sub-period gaps."""
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    current = cutoff
    feeds = []
    while True:
        gap = max(medians[classify(current.hour)], MIN_GAP_HOURS)
        next_time = current + timedelta(hours=gap)
        if next_time >= horizon_end:
            break
        feeds.append({
            "time": next_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "volume_oz": volume_oz,
        })
        current = next_time
    return feeds


# ── CLI Entry Point ───────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Four-bucket cadence projection — agentic inference forecast"
    )
    parser.add_argument("--export", required=True, help="Path to Nara Baby export CSV")
    parser.add_argument("--cutoff", required=True, help="Forecast cutoff (ISO 8601)")
    parser.add_argument(
        "--horizon", type=int, default=24, help="Horizon in hours (default 24)"
    )
    args = parser.parse_args()

    cutoff = datetime.fromisoformat(args.cutoff)
    episodes = load_episodes(Path(args.export), cutoff)
    if len(episodes) < MIN_EPISODES:
        raise SystemExit(
            f"Insufficient episodes: {len(episodes)} "
            f"(need {MIN_EPISODES} in {LOOKBACK_DAYS}-day window)"
        )

    medians, buckets, overall = bucket_medians(episodes, cutoff)
    volume_oz = compute_volume(episodes, cutoff)
    feeds = project(cutoff, args.horizon, medians, volume_oz)

    print("── Bucket medians (h) ──")
    for b in BUCKETS:
        print(f"  {b:14s} n={len(buckets[b]):2d}  median={medians[b]:.2f}")
    print(f"  overall         median={overall:.2f}")
    print(f"  episodes in window: {len(episodes)}")
    print(f"  volume_oz: {volume_oz}")
    print(f"\n── Forecast ({len(feeds)} feeds) ──")
    for f in feeds:
        print(f"  {f['time']}  {f['volume_oz']} oz")

    output_path = Path(__file__).parent / "forecast.json"
    with open(output_path, "w") as fh:
        json.dump({"feeds": feeds}, fh, indent=2)
    print(f"\nForecast written to {output_path}")


if __name__ == "__main__":
    main()
