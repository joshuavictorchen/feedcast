#!/usr/bin/env python3
"""Empirical Cadence Projection — Agentic Inference Model.

Forecasts bottle-feed episodes by projecting forward from recent
inter-episode gap patterns, split by day-part (overnight vs. daytime),
with count calibration against recent daily episode counts.

The approach is deliberately non-parametric: it uses recency-weighted
empirical gap medians rather than fitting a distribution (Weibull,
exponential, etc.). This avoids shape assumptions that may not hold
for a fast-changing newborn.

Run as a script (from repo root):
    .venv/bin/python feedcast/agents/model.py \\
        --export exports/export_narababy_silas_20260327.csv \\
        --cutoff 2026-03-27T21:00:33 --horizon 24

Or import and call:
    from feedcast.agents.model import forecast
    result = forecast("exports/export.csv", "2026-03-27T21:00:33", 24)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Ensure repo root is importable
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from feedcast.clustering import episodes_as_events
from feedcast.data import DATA_FLOOR, build_feed_events, load_activities

# ── Tuning Constants ──────────────────────────────────────────────
# These are the model's primary knobs. Future agents should feel free
# to adjust based on retrospective evidence. See strategy.md for
# rationale and known trade-offs.

LOOKBACK_DAYS = 7  # History window for gap and count estimation
RECENCY_HALF_LIFE_HOURS = 48  # Exponential decay; 2-day half-life

OVERNIGHT_START_HOUR = 19  # Feeds at/after 7 PM use overnight gap
OVERNIGHT_END_HOUR = 7  # Feeds before 7 AM use overnight gap

MIN_GAP_HOURS = 1.0  # Floor on projected gaps (prevents degenerate cascades)
MIN_EPISODES = 5  # Minimum episodes needed to produce a forecast
MIN_DAYPART_GAPS = 3  # Fall back to overall median if a bucket has fewer

COUNT_CALIBRATION_THRESHOLD = 0.30  # Scale gaps if count differs >30% from expected

VOLUME_CLIP_MIN = 0.5
VOLUME_CLIP_MAX = 8.0
DEFAULT_VOLUME_OZ = 3.5


# ── Helpers ───────────────────────────────────────────────────────


def is_overnight(hour: float) -> bool:
    """True if the decimal hour falls in the overnight window (7 PM – 7 AM)."""
    return hour >= OVERNIGHT_START_HOUR or hour < OVERNIGHT_END_HOUR


def decimal_hour(dt: datetime) -> float:
    """Convert a datetime to a decimal hour (0.0–24.0)."""
    return dt.hour + dt.minute / 60 + dt.second / 3600


def recency_weight(event_time: datetime, reference: datetime) -> float:
    """Exponential recency weight: 1.0 at reference, halving every half-life."""
    age_hours = max((reference - event_time).total_seconds() / 3600, 0)
    return 2.0 ** (-age_hours / RECENCY_HALF_LIFE_HOURS)


def weighted_median(values: list[float], weights: list[float]) -> float | None:
    """Weighted median. Returns None if inputs are empty."""
    if not values:
        return None
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    if total == 0:
        return None
    cumulative = 0.0
    midpoint = total / 2
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= midpoint:
            return value
    return pairs[-1][0]


def weighted_mean(values: list[float], weights: list[float]) -> float | None:
    """Weighted mean. Returns None if inputs are empty or weights sum to zero."""
    if not values:
        return None
    total_weight = sum(weights)
    if total_weight == 0:
        return None
    return sum(v * w for v, w in zip(values, weights)) / total_weight


# ── Core Algorithm ────────────────────────────────────────────────


def load_recent_episodes(export_path: str, cutoff: datetime) -> list:
    """Load export, build bottle-only episodes, filter to lookback window."""
    activities = load_activities(Path(export_path))
    events = build_feed_events(activities, merge_window_minutes=None)
    episodes = episodes_as_events(events)

    lookback_start = max(cutoff - timedelta(days=LOOKBACK_DAYS), DATA_FLOOR)
    recent = [e for e in episodes if lookback_start <= e.time <= cutoff]
    recent.sort(key=lambda e: e.time)
    return recent


def compute_gap_profiles(episodes, cutoff):
    """Compute recency-weighted inter-episode gaps, split by day-part.

    Each gap is tagged by the time-of-day of the feed that *starts* the gap
    (the earlier feed). This determines which day-part profile applies when
    projecting forward from a given time.

    Returns:
        (overnight_gaps, daytime_gaps, all_gaps) where each is a list of
        (gap_hours, weight) tuples.
    """
    overnight_gaps: list[tuple[float, float]] = []
    daytime_gaps: list[tuple[float, float]] = []
    all_gaps: list[tuple[float, float]] = []

    for i in range(1, len(episodes)):
        gap_hours = (episodes[i].time - episodes[i - 1].time).total_seconds() / 3600
        if gap_hours < 0.1:
            continue

        start_hour = decimal_hour(episodes[i - 1].time)
        weight = recency_weight(episodes[i].time, cutoff)

        entry = (gap_hours, weight)
        all_gaps.append(entry)
        if is_overnight(start_hour):
            overnight_gaps.append(entry)
        else:
            daytime_gaps.append(entry)

    return overnight_gaps, daytime_gaps, all_gaps


def compute_gap_medians(overnight_gaps, daytime_gaps, all_gaps):
    """Compute weighted median gap per day-part, with fallback to overall.

    Returns:
        (overnight_median, daytime_median, overall_median) — any may be None
        if there's insufficient data.
    """
    all_values = [g for g, _ in all_gaps]
    all_weights = [w for _, w in all_gaps]
    overall = weighted_median(all_values, all_weights)

    if overall is None:
        return None, None, None

    if len(overnight_gaps) >= MIN_DAYPART_GAPS:
        overnight = weighted_median(
            [g for g, _ in overnight_gaps], [w for _, w in overnight_gaps]
        )
    else:
        overnight = overall

    if len(daytime_gaps) >= MIN_DAYPART_GAPS:
        daytime = weighted_median(
            [g for g, _ in daytime_gaps], [w for _, w in daytime_gaps]
        )
    else:
        daytime = overall

    return overnight, daytime, overall


def compute_expected_daily_count(episodes, cutoff):
    """Recency-weighted mean of daily episode counts from recent complete days.

    Only uses complete calendar days (excludes cutoff day, which is partial).
    """
    by_date: dict[str, int] = defaultdict(int)
    for e in episodes:
        by_date[e.time.date()] += 1

    complete_dates = sorted(d for d in by_date if d < cutoff.date())
    if not complete_dates:
        return None

    counts = [float(by_date[d]) for d in complete_dates]
    weights = [
        recency_weight(
            datetime.combine(d, datetime.min.time().replace(hour=12)), cutoff
        )
        for d in complete_dates
    ]
    return weighted_mean(counts, weights)


def compute_volume(episodes, cutoff):
    """Recency-weighted median episode volume, clipped to sane range."""
    volumes = [e.volume_oz for e in episodes if e.volume_oz > 0]
    weights = [recency_weight(e.time, cutoff) for e in episodes if e.volume_oz > 0]
    result = weighted_median(volumes, weights)
    if result is None:
        return DEFAULT_VOLUME_OZ
    return round(min(max(result, VOLUME_CLIP_MIN), VOLUME_CLIP_MAX), 1)


def conditional_remaining(
    gaps_and_weights: list[tuple[float, float]], elapsed: float
) -> float:
    """Estimate time until next feed given elapsed time since last feed.

    Non-parametric conditional survival: filters to gaps longer than elapsed,
    then takes the weighted median of remaining times. This naturally extends
    the prediction when the baby has already waited longer than the median —
    a key improvement over naive (median - elapsed) subtraction, which
    underestimates the gap for evening/bedtime feeds.

    Falls back to MIN_GAP_HOURS if all observed gaps are shorter than elapsed
    (the feed is "overdue").
    """
    surviving = [
        (gap - elapsed, weight)
        for gap, weight in gaps_and_weights
        if gap > elapsed
    ]
    if not surviving:
        return MIN_GAP_HOURS
    values = [r for r, _ in surviving]
    weights = [w for _, w in surviving]
    return max(weighted_median(values, weights) or MIN_GAP_HOURS, MIN_GAP_HOURS)


def project_forward(
    last_episode_time: datetime,
    cutoff: datetime,
    horizon_hours: int,
    overnight_gap: float,
    daytime_gap: float,
    volume_oz: float,
    overnight_gaps_raw: list[tuple[float, float]] | None = None,
    daytime_gaps_raw: list[tuple[float, float]] | None = None,
) -> list[dict]:
    """Step forward from the last episode, placing feeds at day-part gaps.

    The first gap uses a conditional survival estimate (given how long the
    baby has been awake since the last episode). Subsequent gaps use the
    full day-part-appropriate median.

    Args:
        overnight_gaps_raw: Raw (gap_hours, weight) tuples for conditional
            first-feed estimation. If None, falls back to simple subtraction.
        daytime_gaps_raw: Same for daytime.
    """
    horizon_end = cutoff + timedelta(hours=horizon_hours)

    # First feed: conditional estimate given elapsed time
    elapsed = max((cutoff - last_episode_time).total_seconds() / 3600, 0)
    start_hour = decimal_hour(last_episode_time)
    is_night = is_overnight(start_hour)

    if is_night and overnight_gaps_raw:
        remaining = conditional_remaining(overnight_gaps_raw, elapsed)
    elif not is_night and daytime_gaps_raw:
        remaining = conditional_remaining(daytime_gaps_raw, elapsed)
    else:
        # Fallback: simple subtraction
        full_gap = overnight_gap if is_night else daytime_gap
        remaining = max(full_gap - elapsed, MIN_GAP_HOURS)

    current_time = cutoff + timedelta(hours=remaining)
    feeds: list[dict] = []

    while current_time < horizon_end:
        feeds.append(
            {
                "time": current_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "volume_oz": volume_oz,
            }
        )

        # Subsequent gaps: unconditional day-part median
        current_hour = decimal_hour(current_time)
        next_gap = overnight_gap if is_overnight(current_hour) else daytime_gap
        next_gap = max(next_gap, MIN_GAP_HOURS)
        current_time += timedelta(hours=next_gap)

    return feeds


def forecast(
    export_path: str, cutoff_str: str, horizon_hours: int = 24
) -> dict:
    """Produce a feeding forecast using empirical cadence projection.

    Args:
        export_path: Path to Nara Baby export CSV.
        cutoff_str: ISO 8601 forecast cutoff time.
        horizon_hours: Forecast horizon in hours (default 24).

    Returns:
        Dict with 'feeds' list and 'diagnostics' dict.

    Raises:
        RuntimeError: If insufficient data to forecast.
    """
    cutoff = datetime.fromisoformat(cutoff_str)

    # ── Load and validate ─────────────────────────────────────────
    episodes = load_recent_episodes(export_path, cutoff)
    if len(episodes) < MIN_EPISODES:
        raise RuntimeError(
            f"Insufficient data: {len(episodes)} episodes in "
            f"{LOOKBACK_DAYS}-day lookback window (need {MIN_EPISODES})."
        )

    # ── Gap profiles ──────────────────────────────────────────────
    overnight_gaps, daytime_gaps, all_gaps = compute_gap_profiles(episodes, cutoff)
    overnight_median, daytime_median, overall_median = compute_gap_medians(
        overnight_gaps, daytime_gaps, all_gaps
    )
    if overall_median is None:
        raise RuntimeError("No inter-episode gaps found in lookback window.")

    # ── Volume and count ──────────────────────────────────────────
    volume_oz = compute_volume(episodes, cutoff)
    expected_count = compute_expected_daily_count(episodes, cutoff)

    # ── Initial projection ────────────────────────────────────────
    feeds = project_forward(
        episodes[-1].time,
        cutoff,
        horizon_hours,
        overnight_median,
        daytime_median,
        volume_oz,
        overnight_gaps_raw=overnight_gaps,
        daytime_gaps_raw=daytime_gaps,
    )

    # ── Count calibration ─────────────────────────────────────────
    # If the gap-based projection diverges significantly from recent
    # daily episode counts, scale all gaps proportionally and re-project.
    # This preserves the day/night ratio while correcting overall cadence.
    calibrated = False
    if expected_count and expected_count > 0 and len(feeds) > 0:
        ratio = len(feeds) / expected_count
        if abs(ratio - 1.0) > COUNT_CALIBRATION_THRESHOLD:
            scaled_overnight = overnight_median * ratio
            scaled_daytime = daytime_median * ratio
            feeds = project_forward(
                episodes[-1].time,
                cutoff,
                horizon_hours,
                scaled_overnight,
                scaled_daytime,
                volume_oz,
                # Don't pass raw gaps for calibrated re-projection — use
                # scaled medians directly to maintain the count correction
            )
            calibrated = True

    # ── Diagnostics ───────────────────────────────────────────────
    diagnostics = {
        "lookback_days": LOOKBACK_DAYS,
        "recency_half_life_hours": RECENCY_HALF_LIFE_HOURS,
        "episodes_in_window": len(episodes),
        "overnight_gap_count": len(overnight_gaps),
        "daytime_gap_count": len(daytime_gaps),
        "overnight_median_hours": round(overnight_median, 2),
        "daytime_median_hours": round(daytime_median, 2),
        "overall_median_hours": round(overall_median, 2),
        "expected_daily_count": round(expected_count, 1) if expected_count else None,
        "projected_count": len(feeds),
        "count_calibrated": calibrated,
        "volume_oz": volume_oz,
    }

    return {"feeds": feeds, "diagnostics": diagnostics}


# ── CLI Entry Point ───────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Empirical Cadence Projection — agentic inference forecast"
    )
    parser.add_argument("--export", required=True, help="Path to Nara Baby export CSV")
    parser.add_argument("--cutoff", required=True, help="Forecast cutoff (ISO 8601)")
    parser.add_argument(
        "--horizon", type=int, default=24, help="Horizon in hours (default 24)"
    )
    args = parser.parse_args()

    result = forecast(args.export, args.cutoff, args.horizon)

    # Print diagnostics
    diag = result["diagnostics"]
    print("── Diagnostics ──")
    print(f"  Episodes in window:   {diag['episodes_in_window']}")
    print(
        f"  Overnight gaps:       {diag['overnight_gap_count']}  "
        f"median={diag['overnight_median_hours']:.2f}h"
    )
    print(
        f"  Daytime gaps:         {diag['daytime_gap_count']}  "
        f"median={diag['daytime_median_hours']:.2f}h"
    )
    print(f"  Overall median gap:   {diag['overall_median_hours']:.2f}h")
    print(f"  Expected daily count: {diag['expected_daily_count']}")
    print(f"  Count calibrated:     {diag['count_calibrated']}")
    print(f"  Volume:               {diag['volume_oz']} oz")
    print()

    # Print forecast
    feeds = result["feeds"]
    print(f"── Forecast ({len(feeds)} feeds) ──")
    for f in feeds:
        print(f"  {f['time']}  {f['volume_oz']} oz")

    # Write forecast.json (feeds only — matches expected schema)
    output_path = Path(__file__).parent / "forecast.json"
    output = {"feeds": result["feeds"]}
    with open(output_path, "w") as fh:
        json.dump(output, fh, indent=2)
    print(f"\nForecast written to {output_path}")


if __name__ == "__main__":
    main()
