"""Latent Hunger State research: explore volume-gap relationships, circadian
structure, and compare additive vs. multiplicative satiety models with
constant and circadian-modulated growth rates.

Run from the repo root:
    .venv/bin/python -m feedcast.models.latent_hunger.analysis

This script reproduces the data analysis that informs the Latent Hunger
State design. It uses the same export selection, data parsing, and
breastfeed merge heuristic as the model will, so its output matches
what the model would see at the same cutoff.

Update this script and re-run when new exports are available or when
revisiting model assumptions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np

from feedcast.clustering import episodes_as_events
from feedcast.data import (
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
)
from feedcast.models.latent_hunger.model import (
    HUNGER_THRESHOLD,
    LOOKBACK_DAYS,
    MIN_FIT_GAPS,
    MIN_GAP_HOURS,
    RECENCY_HALF_LIFE_HOURS,
    SATIETY_RATE,
    SIM_STEP_HOURS,
    _hunger_after_feed,
    _simulate_gap,
)
from feedcast.replay import score_model, tune_model

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent

# Walk-forward evaluation: minimum events before predictions start.
# Needs MIN_FIT_GAPS + 1 events to produce MIN_FIT_GAPS gaps.
MIN_FIT_EVENTS = MIN_FIT_GAPS + 1


# ====================================================================
# Simulation helpers
# ====================================================================

def _simulate_gap_multiplicative(
    volume_oz: float,
    growth_rate: float,
    satiety_rate: float,
    circadian_amp: float = 0.0,
    circadian_phase_hour: float = 0.0,
    feed_hour: float = 0.0,
) -> float:
    """Predict the gap after a feed using multiplicative satiety.

    Multiplicative model:
      hunger_after_feed = THRESHOLD * exp(-satiety_rate * volume)
      hunger then grows at growth_rate (optionally circadian-modulated)
      until it reaches THRESHOLD again.

    This guarantees partial resets: a feed never fully zeroes hunger,
    so larger feeds produce longer gaps in a principled way.
    """
    # After feeding, hunger drops proportional to volume but never to zero.
    hunger_after = HUNGER_THRESHOLD * math.exp(-satiety_rate * volume_oz)
    remaining = HUNGER_THRESHOLD - hunger_after

    if remaining <= 0:
        return MIN_GAP_HOURS

    if circadian_amp == 0.0:
        # Constant growth: closed-form.
        return max(remaining / growth_rate, MIN_GAP_HOURS)

    # Circadian growth: numerically integrate.
    # growth(t) = growth_rate * (1 + amp * cos(2π(hour(t) - phase) / 24))
    accumulated = 0.0
    t = 0.0
    while accumulated < remaining and t < 24.0:
        current_hour = (feed_hour + t) % 24.0
        rate = growth_rate * (
            1.0 + circadian_amp * math.cos(
                2.0 * math.pi * (current_hour - circadian_phase_hour) / 24.0
            )
        )
        accumulated += max(rate, 0.01) * SIM_STEP_HOURS
        t += SIM_STEP_HOURS
    return max(t, MIN_GAP_HOURS)


def _simulate_gap_additive(
    volume_oz: float,
    elapsed_since_prev: float,
    prev_volume_oz: float,
    growth_rate: float,
    satiety_coeff: float,
    circadian_amp: float = 0.0,
    circadian_phase_hour: float = 0.0,
    feed_hour: float = 0.0,
) -> float:
    """Predict the gap after a feed using additive satiety (baseline).

    Additive model:
      hunger_at_feed ≈ growth_rate * elapsed - satiety_coeff * prev_volume
      hunger_after_feed = max(0, hunger_at_feed - satiety_coeff * volume)
      grow until threshold.

    Included for comparison. Known to collapse to constant-gap prediction
    because optimizer prefers zeroing hunger after each feed.
    """
    hunger_at_feed = growth_rate * elapsed_since_prev - satiety_coeff * prev_volume_oz
    hunger_at_feed = max(0.0, min(hunger_at_feed, HUNGER_THRESHOLD))
    hunger_after = max(0.0, hunger_at_feed - satiety_coeff * volume_oz)
    remaining = HUNGER_THRESHOLD - hunger_after

    if remaining <= 0:
        return MIN_GAP_HOURS

    if circadian_amp == 0.0:
        return max(remaining / growth_rate, MIN_GAP_HOURS)

    accumulated = 0.0
    t = 0.0
    while accumulated < remaining and t < 24.0:
        current_hour = (feed_hour + t) % 24.0
        rate = growth_rate * (
            1.0 + circadian_amp * math.cos(
                2.0 * math.pi * (current_hour - circadian_phase_hour) / 24.0
            )
        )
        accumulated += max(rate, 0.01) * SIM_STEP_HOURS
        t += SIM_STEP_HOURS
    return max(t, MIN_GAP_HOURS)


# ====================================================================
# Walk-forward evaluation
# ====================================================================

def _evaluate_multiplicative(
    events,
    growth_rate: float,
    satiety_rate: float,
    circadian_amp: float = 0.0,
    circadian_phase_hour: float = 0.0,
    recency_half_life_hours: float = RECENCY_HALF_LIFE_HOURS,
    start_index: int = MIN_FIT_EVENTS,
    median_volume: float | None = None,
) -> dict:
    """Walk-forward evaluation of the multiplicative hunger model.

    Returns gap1_mae, gap3_mae, feed_count_mae (all recency-weighted).
    """
    decay = math.log(2) / recency_half_life_hours
    cutoff_time = events[-1].time
    sim_vol = median_volume or float(
        np.median([e.volume_oz for e in events])
    )

    gap1_errors, gap1_weights = [], []
    gap3_errors, gap3_weights = [], []
    fcount_errors, fcount_weights = [], []

    for i in range(start_index, len(events) - 1):
        event = events[i]
        age_hours = (cutoff_time - event.time).total_seconds() / 3600
        weight = math.exp(-decay * max(age_hours, 0))

        # Single-gap prediction.
        predicted = _simulate_gap_multiplicative(
            event.volume_oz, growth_rate, satiety_rate,
            circadian_amp, circadian_phase_hour, hour_of_day(event.time),
        )
        actual = (events[i + 1].time - event.time).total_seconds() / 3600
        gap1_errors.append(abs(predicted - actual))
        gap1_weights.append(weight)

        # 3-gap trajectory prediction.
        if i + 3 < len(events):
            preds = [predicted]
            sim_hour = hour_of_day(event.time) + predicted
            for _ in range(2):
                gap = _simulate_gap_multiplicative(
                    sim_vol, growth_rate, satiety_rate,
                    circadian_amp, circadian_phase_hour, sim_hour % 24.0,
                )
                preds.append(gap)
                sim_hour += gap
            actuals = [
                (events[i + j + 1].time - events[i + j].time).total_seconds() / 3600
                for j in range(3)
            ]
            gap3_errors.append(float(np.mean(np.abs(
                np.array(preds) - np.array(actuals)
            ))))
            gap3_weights.append(weight)

        # 24h feed count.
        sim_t = 0.0
        sim_h = hour_of_day(event.time)
        sim_count = 0
        sim_v = event.volume_oz
        while sim_t < 24.0:
            gap = _simulate_gap_multiplicative(
                sim_v, growth_rate, satiety_rate,
                circadian_amp, circadian_phase_hour, (sim_h + sim_t) % 24.0,
            )
            sim_t += gap
            if sim_t < 24.0:
                sim_count += 1
                sim_v = sim_vol
        actual_count = sum(
            1 for e in events[i + 1:]
            if e.time <= event.time + timedelta(hours=24)
        )
        fcount_errors.append(abs(sim_count - actual_count))
        fcount_weights.append(weight)

    return {
        "gap1_mae": float(np.average(gap1_errors, weights=gap1_weights)) if gap1_errors else float("nan"),
        "gap3_mae": float(np.average(gap3_errors, weights=gap3_weights)) if gap3_errors else float("nan"),
        "feed_count_mae": float(np.average(fcount_errors, weights=fcount_weights)) if fcount_errors else float("nan"),
        "n": len(gap1_errors),
        "pred_std": float(np.std([
            _simulate_gap_multiplicative(
                events[i].volume_oz, growth_rate, satiety_rate,
                circadian_amp, circadian_phase_hour, hour_of_day(events[i].time),
            )
            for i in range(start_index, len(events) - 1)
        ])),
    }


def _evaluate_additive(
    events,
    growth_rate: float,
    satiety_coeff: float,
    circadian_amp: float = 0.0,
    circadian_phase_hour: float = 0.0,
    recency_half_life_hours: float = RECENCY_HALF_LIFE_HOURS,
) -> dict:
    """Walk-forward evaluation of the additive hunger model (baseline)."""
    decay = math.log(2) / recency_half_life_hours
    cutoff_time = events[-1].time
    errors, weights = [], []
    for i in range(MIN_FIT_EVENTS, len(events) - 1):
        event = events[i]
        prev = events[i - 1]
        elapsed = (event.time - prev.time).total_seconds() / 3600
        age = (cutoff_time - event.time).total_seconds() / 3600
        w = math.exp(-decay * max(age, 0))
        predicted = _simulate_gap_additive(
            event.volume_oz, elapsed, prev.volume_oz,
            growth_rate, satiety_coeff,
            circadian_amp, circadian_phase_hour, hour_of_day(event.time),
        )
        actual = (events[i + 1].time - event.time).total_seconds() / 3600
        errors.append(abs(predicted - actual))
        weights.append(w)
    return {
        "gap1_mae": float(np.average(errors, weights=weights)) if errors else float("nan"),
        "n": len(errors),
        "pred_std": float(np.std([
            _simulate_gap_additive(
                events[i].volume_oz,
                (events[i].time - events[i - 1].time).total_seconds() / 3600,
                events[i - 1].volume_oz,
                growth_rate, satiety_coeff,
                circadian_amp, circadian_phase_hour, hour_of_day(events[i].time),
            )
            for i in range(MIN_FIT_EVENTS, len(events) - 1)
        ])),
    }


# ====================================================================
# Main research analysis
# ====================================================================

def main() -> None:
    """Run the full analysis."""
    output_capture = StringIO()

    def log(text: str = "") -> None:
        print(text)
        output_capture.write(text + "\n")

    snapshot = load_export_snapshot()
    cutoff = snapshot.latest_activity_time

    # Use breastfeed merge heuristic per user direction.
    events_merged = build_feed_events(
        snapshot.activities,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    )
    events_bottle_only = build_feed_events(
        snapshot.activities, merge_window_minutes=None,
    )

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log(f"Events (with BF merge): {len(events_merged)}")
    log(f"Events (bottle only):   {len(events_bottle_only)}")
    log()

    events = events_merged

    # ================================================================
    # SECTION 1: Breastfeed merge impact
    # ================================================================
    log("=== BREASTFEED MERGE IMPACT ===")
    log()
    affected = 0
    for merged, bottle in zip(events_merged, events_bottle_only):
        if merged.volume_oz != bottle.volume_oz:
            affected += 1
            log(
                f"  {merged.time.strftime('%m/%d %H:%M')}: "
                f"{bottle.volume_oz:.2f} -> {merged.volume_oz:.2f} oz "
                f"(+{merged.breastfeeding_volume_oz:.2f} oz BF)"
            )
    log(f"\nAffected events: {affected} / {len(events_merged)}")
    log()

    # ================================================================
    # SECTION 2: Volume-to-gap relationship
    # ================================================================
    log("=== VOLUME-TO-GAP RELATIONSHIP ===")
    log()
    volumes, next_gaps = [], []
    for i in range(len(events) - 1):
        volumes.append(events[i].volume_oz)
        next_gaps.append(
            (events[i + 1].time - events[i].time).total_seconds() / 3600
        )
    volumes_arr = np.array(volumes)
    gaps_arr = np.array(next_gaps)

    correlation = float(np.corrcoef(volumes_arr, gaps_arr)[0, 1])
    log(f"Volume-to-next-gap correlation: {correlation:.3f}")
    log(f"Volume: mean={volumes_arr.mean():.2f} std={volumes_arr.std():.2f} "
        f"min={volumes_arr.min():.2f} max={volumes_arr.max():.2f}")
    log(f"Gaps:   mean={gaps_arr.mean():.2f} std={gaps_arr.std():.2f} "
        f"min={gaps_arr.min():.2f} max={gaps_arr.max():.2f}")
    log()
    log("Volume-binned mean gaps:")
    for lo, hi in [(0, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 5.0)]:
        mask = (volumes_arr >= lo) & (volumes_arr < hi)
        if mask.sum() > 0:
            log(f"  [{lo:.1f}, {hi:.1f}) oz: n={mask.sum():>3}, "
                f"mean_gap={gaps_arr[mask].mean():.2f}h, "
                f"std={gaps_arr[mask].std():.2f}h")
    log()

    # ================================================================
    # SECTION 3: Circadian structure
    # ================================================================
    log("=== CIRCADIAN STRUCTURE ===")
    log()
    hours = np.array([hour_of_day(events[i].time) for i in range(len(events) - 1)])
    tod_bins = [
        (0, 4, "00-04"), (4, 8, "04-08"), (8, 12, "08-12"),
        (12, 16, "12-16"), (16, 20, "16-20"), (20, 24, "20-24"),
    ]

    log("Time-of-day binned mean gaps (all history):")
    bin_means = []
    for lo, hi, label in tod_bins:
        mask = (hours >= lo) & (hours < hi)
        if mask.sum() > 0:
            mean_gap = gaps_arr[mask].mean()
            bin_means.append(mean_gap)
            log(f"  {label}: n={mask.sum():>3}, mean_gap={mean_gap:.2f}h, "
                f"std={gaps_arr[mask].std():.2f}h, "
                f"mean_vol={volumes_arr[mask].mean():.2f}oz")
        else:
            bin_means.append(float("nan"))
    valid_means = [m for m in bin_means if not math.isnan(m)]
    if len(valid_means) >= 2:
        log(f"\nCircadian spread: {max(valid_means) - min(valid_means):.2f}h "
            f"(ratio: {max(valid_means) / min(valid_means):.2f})")
    log()

    # Recent 7-day circadian (2h bins).
    log("Recent circadian (last 7 days, 2h bins):")
    week_ago = cutoff - timedelta(days=7)
    recent_mask = np.array([events[i].time >= week_ago for i in range(len(events) - 1)])
    for lo in range(0, 24, 2):
        hi = lo + 2
        mask = recent_mask & (hours >= lo) & (hours < hi)
        if mask.sum() > 0:
            log(f"  {lo:02d}-{hi:02d}: n={mask.sum():>2}, "
                f"mean_gap={gaps_arr[mask].mean():.2f}h, "
                f"mean_vol={volumes_arr[mask].mean():.2f}oz")
    log()

    # ================================================================
    # SECTION 4: Additive vs. Multiplicative satiety — head-to-head
    # ================================================================
    log("=== ADDITIVE VS. MULTIPLICATIVE SATIETY ===")
    log()

    growth_rates = np.linspace(0.1, 1.5, 30)
    satiety_values = np.linspace(0.05, 0.8, 30)

    # Additive grid search.
    best_add = {"gap1_mae": float("inf")}
    best_add_params = (0.0, 0.0)
    for gr in growth_rates:
        for sc in satiety_values:
            result = _evaluate_additive(events, gr, sc)
            if result["gap1_mae"] < best_add["gap1_mae"]:
                best_add = result
                best_add_params = (gr, sc)
    log(f"Best ADDITIVE constant:  gr={best_add_params[0]:.3f} sc={best_add_params[1]:.3f}")
    log(f"  gap1_MAE={best_add['gap1_mae']:.3f}h  pred_std={best_add['pred_std']:.3f}h")
    log()

    # Multiplicative grid search.
    best_mult = {"gap1_mae": float("inf")}
    best_mult_params = (0.0, 0.0)
    all_mult_results = []
    for gr in growth_rates:
        for sr in satiety_values:
            result = _evaluate_multiplicative(events, gr, sr)
            all_mult_results.append((gr, sr, result))
            if result["gap1_mae"] < best_mult["gap1_mae"]:
                best_mult = result
                best_mult_params = (gr, sr)
    gr_m, sr_m = best_mult_params
    log(f"Best MULTIPLICATIVE constant:  gr={gr_m:.3f} sr={sr_m:.3f}")
    log(f"  gap1_MAE={best_mult['gap1_mae']:.3f}h  gap3_MAE={best_mult['gap3_mae']:.3f}h  "
        f"fcount_MAE={best_mult['feed_count_mae']:.2f}  pred_std={best_mult['pred_std']:.3f}h")
    log()

    # Prediction range check: does multiplicative produce real variation?
    log("Multiplicative prediction range (recent 15 events):")
    for i in range(max(MIN_FIT_EVENTS, len(events) - 16), len(events) - 1):
        event = events[i]
        pred = _simulate_gap_multiplicative(
            event.volume_oz, gr_m, sr_m, feed_hour=hour_of_day(event.time),
        )
        actual = (events[i + 1].time - event.time).total_seconds() / 3600
        log(f"  {event.time.strftime('%m/%d %H:%M')} vol={event.volume_oz:.1f}oz "
            f"pred={pred:.2f}h actual={actual:.2f}h err={pred - actual:+.2f}h")
    log()

    # Top 10 multiplicative parameter sets.
    log("Top 10 multiplicative parameter sets:")
    sorted_mult = sorted(all_mult_results, key=lambda x: x[2]["gap1_mae"])
    for gr, sr, res in sorted_mult[:10]:
        log(f"  gr={gr:.3f} sr={sr:.3f}: "
            f"gap1={res['gap1_mae']:.3f}h  gap3={res['gap3_mae']:.3f}h  "
            f"fcount={res['feed_count_mae']:.2f}  pstd={res['pred_std']:.3f}h")
    log()

    # ================================================================
    # SECTION 5: Multiplicative + circadian grid search
    # ================================================================
    log("=== MULTIPLICATIVE + CIRCADIAN ===")
    log()

    circadian_amps = np.linspace(0.0, 0.5, 11)
    circadian_phases = np.linspace(0, 22, 12)

    # Coarse search: best mult params + circadian.
    best_mc = {"gap1_mae": float("inf")}
    best_mc_params = (gr_m, sr_m, 0.0, 0.0)
    for amp in circadian_amps:
        for phase in circadian_phases:
            result = _evaluate_multiplicative(
                events, gr_m, sr_m, amp, phase,
            )
            if result["gap1_mae"] < best_mc["gap1_mae"]:
                best_mc = result
                best_mc_params = (gr_m, sr_m, amp, phase)

    _, _, amp_mc, phase_mc = best_mc_params
    log(f"Best circadian (base gr/sr from constant):")
    log(f"  gr={gr_m:.3f} sr={sr_m:.3f} amp={amp_mc:.3f} phase={phase_mc:.1f}h")
    log(f"  gap1_MAE={best_mc['gap1_mae']:.3f}h  gap3_MAE={best_mc['gap3_mae']:.3f}h  "
        f"fcount_MAE={best_mc['feed_count_mae']:.2f}  pred_std={best_mc['pred_std']:.3f}h")
    log()

    # Joint refinement: search gr/sr neighborhood with best amp/phase.
    best_joint = {"gap1_mae": float("inf")}
    best_joint_params = best_mc_params
    for gr in np.linspace(max(0.1, gr_m - 0.2), gr_m + 0.2, 11):
        for sr in np.linspace(max(0.05, sr_m - 0.2), sr_m + 0.2, 11):
            result = _evaluate_multiplicative(
                events, gr, sr, amp_mc, phase_mc,
            )
            if result["gap1_mae"] < best_joint["gap1_mae"]:
                best_joint = result
                best_joint_params = (gr, sr, amp_mc, phase_mc)

    gr_j, sr_j, amp_j, phase_j = best_joint_params
    log(f"Joint-refined:")
    log(f"  gr={gr_j:.4f} sr={sr_j:.4f} amp={amp_j:.3f} phase={phase_j:.1f}h")
    log(f"  gap1_MAE={best_joint['gap1_mae']:.3f}h  gap3_MAE={best_joint['gap3_mae']:.3f}h  "
        f"fcount_MAE={best_joint['feed_count_mae']:.2f}  pred_std={best_joint['pred_std']:.3f}h")
    log()

    # ================================================================
    # SECTION 6: Lookback window sensitivity
    # ================================================================
    log("=== LOOKBACK WINDOW SENSITIVITY ===")
    log()
    log("How does fitting on only the last N days compare to full history?")
    log("(Using best joint-refined params, varying evaluation window.)")
    log()

    for lookback_days in [3, 5, 7, 14, None]:
        if lookback_days is not None:
            window_start = cutoff - timedelta(days=lookback_days)
            start_idx = next(
                (i for i, e in enumerate(events) if e.time >= window_start),
                MIN_FIT_EVENTS,
            )
            start_idx = max(start_idx, 1)  # Need at least 1 prior event.
            label = f"last {lookback_days:>2}d"
        else:
            start_idx = MIN_FIT_EVENTS
            label = "all data"

        if start_idx >= len(events) - 2:
            log(f"  {label}: too few events")
            continue

        result = _evaluate_multiplicative(
            events, gr_j, sr_j, amp_j, phase_j,
            start_index=start_idx,
        )
        log(f"  {label}: gap1_MAE={result['gap1_mae']:.3f}h  "
            f"gap3_MAE={result['gap3_mae']:.3f}h  "
            f"fcount_MAE={result['feed_count_mae']:.2f}  (n={result['n']})")
    log()

    # Re-fit parameters using only last 5 days to see if trend-adapted
    # params differ from full-history params.
    log("Re-fitting on last 5 days only:")
    window_5d = cutoff - timedelta(days=5)
    start_5d = next(
        (i for i, e in enumerate(events) if e.time >= window_5d),
        MIN_FIT_EVENTS,
    )
    start_5d = max(start_5d, 1)

    best_5d = {"gap1_mae": float("inf")}
    best_5d_params = (0.0, 0.0, 0.0, 0.0)
    for gr in np.linspace(max(0.1, gr_j - 0.3), gr_j + 0.3, 13):
        for sr in np.linspace(max(0.05, sr_j - 0.3), sr_j + 0.3, 13):
            result = _evaluate_multiplicative(
                events, gr, sr, amp_j, phase_j,
                start_index=start_5d,
            )
            if result["gap1_mae"] < best_5d["gap1_mae"]:
                best_5d = result
                best_5d_params = (gr, sr, amp_j, phase_j)

    gr_5, sr_5, _, _ = best_5d_params
    log(f"  Best 5d params: gr={gr_5:.4f} sr={sr_5:.4f} (vs full: gr={gr_j:.4f} sr={sr_j:.4f})")
    log(f"  5d gap1_MAE={best_5d['gap1_mae']:.3f}h  (full params on 5d: see above)")

    # How do 5d-fit params perform on full history vs. full-fit params?
    full_with_5d = _evaluate_multiplicative(events, gr_5, sr_5, amp_j, phase_j)
    log(f"  5d params on full history: gap1_MAE={full_with_5d['gap1_mae']:.3f}h")
    log(f"  Full params on full history: gap1_MAE={best_joint['gap1_mae']:.3f}h")
    log()

    # ================================================================
    # SECTION 7: Most recent 24h prediction quality
    # ================================================================
    log("=== MOST RECENT 24H PREDICTION QUALITY (holdout) ===")
    log()
    log("Simulating a 24h forecast from 24h before cutoff.")
    log("Parameters are re-fit using ONLY events before the anchor (true holdout).")
    log()

    forecast_start = cutoff - timedelta(hours=24)
    # Find the last event before or at forecast_start.
    anchor_idx = max(
        (i for i, e in enumerate(events) if e.time <= forecast_start),
        default=None,
    )
    if anchor_idx is not None and anchor_idx >= MIN_FIT_EVENTS:
        anchor = events[anchor_idx]
        log(f"Anchor event: {anchor.time.strftime('%m/%d %H:%M')} "
            f"vol={anchor.volume_oz:.1f}oz")

        # Re-fit params on pre-anchor data only (true holdout).
        pre_anchor_events = events[:anchor_idx + 1]
        best_holdout = {"gap1_mae": float("inf")}
        best_holdout_params = (gr_m, sr_m)
        for gr in np.linspace(max(0.1, gr_m - 0.3), gr_m + 0.3, 13):
            for sr in np.linspace(max(0.05, sr_m - 0.3), sr_m + 0.3, 13):
                result = _evaluate_multiplicative(pre_anchor_events, gr, sr)
                if result["gap1_mae"] < best_holdout["gap1_mae"]:
                    best_holdout = result
                    best_holdout_params = (gr, sr)

        gr_ho, sr_ho = best_holdout_params
        log(f"Holdout-fit params: gr={gr_ho:.4f} sr={sr_ho:.4f}")
        log(f"  (vs full-data params: gr={gr_m:.4f} sr={sr_m:.4f})")
        log()

        # Simulate forward 24h using holdout params.
        sim_vol = float(np.median([e.volume_oz for e in pre_anchor_events]))
        predicted_times = []
        sim_t = 0.0
        sim_h = hour_of_day(anchor.time)
        sim_v = anchor.volume_oz
        while sim_t < 24.0:
            gap = _simulate_gap_multiplicative(
                sim_v, gr_ho, sr_ho, 0.0, 0.0, (sim_h + sim_t) % 24.0,
            )
            sim_t += gap
            if sim_t < 24.0:
                pred_time = anchor.time + timedelta(hours=sim_t)
                predicted_times.append(pred_time)
                sim_v = sim_vol

        # Actual events in that 24h window.
        actual_times = [
            e.time for e in events
            if anchor.time < e.time <= anchor.time + timedelta(hours=24)
        ]

        log(f"Predicted {len(predicted_times)} feeds, actual {len(actual_times)} feeds")
        log()

        # Pair predictions to actuals (greedy nearest).
        used = set()
        total_error = 0.0
        paired = 0
        for pt in predicted_times:
            best_diff = float("inf")
            best_idx = -1
            for j, at in enumerate(actual_times):
                if j not in used:
                    diff = abs((pt - at).total_seconds() / 3600)
                    if diff < best_diff:
                        best_diff = diff
                        best_idx = j
            if best_idx >= 0:
                used.add(best_idx)
                total_error += best_diff
                paired += 1
                log(f"  pred={pt.strftime('%H:%M')}  "
                    f"actual={actual_times[best_idx].strftime('%H:%M')}  "
                    f"err={best_diff:.2f}h")

        if paired > 0:
            log(f"\nPaired {paired}, mean timing error: {total_error / paired:.2f}h")
            log(f"Feed count error: {abs(len(predicted_times) - len(actual_times))}")
    else:
        log("Not enough pre-anchor events for holdout test.")
    log()

    # ================================================================
    # SECTION 8: Naive baseline comparison
    # ================================================================
    log("=== NAIVE BASELINE COMPARISON ===")
    log()
    decay = math.log(2) / RECENCY_HALF_LIFE_HOURS
    naive_last_e, naive_last_w = [], []
    naive_mean3_e, naive_mean3_w = [], []
    for i in range(MIN_FIT_EVENTS, len(events) - 1):
        actual = (events[i + 1].time - events[i].time).total_seconds() / 3600
        last = (events[i].time - events[i - 1].time).total_seconds() / 3600
        age = (cutoff - events[i].time).total_seconds() / 3600
        w = math.exp(-decay * max(age, 0))
        naive_last_e.append(abs(last - actual))
        naive_last_w.append(w)
        if i >= 3:
            mean3 = np.mean([
                (events[i - j].time - events[i - j - 1].time).total_seconds() / 3600
                for j in range(3)
            ])
            naive_mean3_e.append(abs(mean3 - actual))
            naive_mean3_w.append(w)

    log(f"{'Model':<35} {'gap1_MAE':>10}")
    log("-" * 48)
    log(f"{'Naive last-gap':<35} {float(np.average(naive_last_e, weights=naive_last_w)):>10.3f}")
    log(f"{'Naive mean-3-gaps':<35} {float(np.average(naive_mean3_e, weights=naive_mean3_w)):>10.3f}")
    log(f"{'Additive constant':<35} {best_add['gap1_mae']:>10.3f}")
    log(f"{'Multiplicative constant':<35} {best_mult['gap1_mae']:>10.3f}")
    log(f"{'Multiplicative + circadian':<35} {best_mc['gap1_mae']:>10.3f}")
    log(f"{'Mult + circadian (joint-refined)':<35} {best_joint['gap1_mae']:>10.3f}")
    log()

    # ================================================================
    # SECTION 9: Volume prediction strategy
    # ================================================================
    log("=== VOLUME PREDICTION STRATEGY ===")
    log()
    all_vols = np.array([e.volume_oz for e in events])
    global_median = float(np.median(all_vols))
    log(f"Global median volume: {global_median:.2f} oz")
    log("Recency-weighted volume:")
    for n in [5, 10, 15]:
        if len(events) >= n:
            recent = [events[-i].volume_oz for i in range(1, n + 1)]
            log(f"  Last {n:>2}: median={np.median(recent):.2f} mean={np.mean(recent):.2f}")
    log()

    # ================================================================
    # SECTION 10: Episode-level comparison
    # ================================================================
    log("=== EPISODE-LEVEL COMPARISON ===")
    log()

    episode_events = episodes_as_events(events)
    log(f"Raw events: {len(events)}, Episodes: {len(episode_events)} "
        f"({len(events) - len(episode_events)} feeds collapsed)")
    log()

    # Episode-level volume-gap statistics.
    ep_volumes, ep_gaps = [], []
    for i in range(len(episode_events) - 1):
        ep_volumes.append(episode_events[i].volume_oz)
        ep_gaps.append(
            (episode_events[i + 1].time - episode_events[i].time).total_seconds() / 3600
        )
    ep_volumes_arr = np.array(ep_volumes)
    ep_gaps_arr = np.array(ep_gaps)

    ep_correlation = float(np.corrcoef(ep_volumes_arr, ep_gaps_arr)[0, 1])
    log(f"Volume-gap correlation: {ep_correlation:.3f} (raw: {correlation:.3f})")
    log(f"Episode volume: mean={ep_volumes_arr.mean():.2f} std={ep_volumes_arr.std():.2f} "
        f"min={ep_volumes_arr.min():.2f} max={ep_volumes_arr.max():.2f}")
    log(f"Episode gaps:   mean={ep_gaps_arr.mean():.2f} std={ep_gaps_arr.std():.2f} "
        f"min={ep_gaps_arr.min():.2f} max={ep_gaps_arr.max():.2f}")
    log()

    # Episode-level multiplicative grid search.
    log("Episode-level multiplicative grid search:")
    best_ep_mult = {"gap1_mae": float("inf")}
    best_ep_params = (0.0, 0.0)
    all_ep_results = []
    for gr in growth_rates:
        for sr in satiety_values:
            result = _evaluate_multiplicative(episode_events, gr, sr)
            all_ep_results.append((gr, sr, result))
            if result["gap1_mae"] < best_ep_mult["gap1_mae"]:
                best_ep_mult = result
                best_ep_params = (gr, sr)

    gr_ep, sr_ep = best_ep_params
    log(f"  Best: gr={gr_ep:.3f} sr={sr_ep:.3f}")
    log(f"  gap1_MAE={best_ep_mult['gap1_mae']:.3f}h  gap3_MAE={best_ep_mult['gap3_mae']:.3f}h  "
        f"fcount_MAE={best_ep_mult['feed_count_mae']:.2f}  pred_std={best_ep_mult['pred_std']:.3f}h")
    log()

    log("Top 5 episode-level parameter sets:")
    sorted_ep = sorted(all_ep_results, key=lambda x: x[2]["gap1_mae"])
    for gr, sr, res in sorted_ep[:5]:
        log(f"  gr={gr:.3f} sr={sr:.3f}: "
            f"gap1={res['gap1_mae']:.3f}h  gap3={res['gap3_mae']:.3f}h  "
            f"fcount={res['feed_count_mae']:.2f}  pstd={res['pred_std']:.3f}h")
    log()

    log("Comparison (raw vs episode-level):")
    log(f"  gap1_MAE:     {best_mult['gap1_mae']:.3f}h vs {best_ep_mult['gap1_mae']:.3f}h")
    log(f"  gap3_MAE:     {best_mult['gap3_mae']:.3f}h vs {best_ep_mult['gap3_mae']:.3f}h")
    log(f"  fcount_MAE:   {best_mult['feed_count_mae']:.2f} vs {best_ep_mult['feed_count_mae']:.2f}")
    log(f"  pred_std:     {best_mult['pred_std']:.3f}h vs {best_ep_mult['pred_std']:.3f}h")
    log(f"  Best sr:      {sr_m:.3f} vs {sr_ep:.3f}")
    log()

    # Episode-level sim volume.
    ep_all_vols = np.array([e.volume_oz for e in episode_events])
    log(f"Episode median volume: {float(np.median(ep_all_vols)):.2f} oz "
        f"(raw: {global_median:.2f} oz)")
    log()

    # ================================================================
    # CANONICAL MULTI-WINDOW EVALUATION
    # ================================================================
    log(f"\n{'=' * 60}")
    log("CANONICAL MULTI-WINDOW EVALUATION")
    log(f"{'=' * 60}")
    log()
    log("Production-constant evaluation via score_model (same")
    log("infrastructure as the replay CLI).")
    log()

    canonical = score_model("latent_hunger", export_path=snapshot.export_path)
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
    # CANONICAL PARAMETER TUNING
    # ================================================================
    # Growth rate is estimated at runtime, not a module constant.
    # SATIETY_RATE is the primary tunable constant. Sweep a focused
    # range covering the internal grid search's domain (0.05-0.8).
    log("=== CANONICAL PARAMETER TUNING ===")
    log()
    log("Sweeps SATIETY_RATE via tune_model (multi-window canonical")
    log("scoring). Growth rate is runtime-estimated, not overridable.")
    log()

    tune_result = tune_model(
        "latent_hunger",
        candidates_by_name={
            "SATIETY_RATE": [0.05, 0.1, 0.15, 0.2, 0.25, 0.3,
                             0.35, 0.4, 0.5, 0.6, 0.7, 0.8],
        },
        export_path=snapshot.export_path,
    )
    bl = tune_result["baseline"]
    be = tune_result["best"]
    bl_agg = bl["replay_windows"]["aggregate"]
    be_agg = be["replay_windows"]["aggregate"]
    log(f"Candidates evaluated: {tune_result['search']['evaluated']}")
    log()
    log(f"{'':20} {'Headline':>8} {'Count':>7} {'Timing':>7} {'Windows':>8}")
    log(f"{'Baseline':<20} {bl_agg['headline']:>8.1f} {bl_agg['count']:>7.1f} "
        f"{bl_agg['timing']:>7.1f} "
        f"{bl['replay_windows']['scored_window_count']:>4}/"
        f"{bl['replay_windows']['window_count']}")
    log(f"{'Best':<20} {be_agg['headline']:>8.1f} {be_agg['count']:>7.1f} "
        f"{be_agg['timing']:>7.1f} "
        f"{be['replay_windows']['scored_window_count']:>4}/"
        f"{be['replay_windows']['window_count']}")
    log()
    log(f"Baseline params: {bl['params']}")
    log(f"Best params:     {be['params']}")
    log(f"Headline delta:  {be['headline_delta']:+.3f}")
    log(f"Availability delta: {be['availability_delta']:+d}")
    log()
    log("Top 5 candidates:")
    for rank, cand in enumerate(tune_result["candidates"][:5], 1):
        c_agg = cand["replay_windows"]["aggregate"]
        log(f"  {rank}. {cand['params']}  headline={c_agg['headline']:.1f}  "
            f"count={c_agg['count']:.1f}  timing={c_agg['timing']:.1f}")
    log()

    # ================================================================
    # SECTION 11: Summary
    # ================================================================
    log("=== FINAL SUMMARY ===")
    log()
    log("--- Raw-data exploratory grid search ---")
    log(f"  growth_rate = {gr_j:.4f}")
    log(f"  satiety_rate = {sr_j:.4f}")
    log(f"  circadian_amp = {amp_j:.3f}")
    log(f"  circadian_phase = {phase_j:.1f}h")
    log(f"  gap1_MAE = {best_joint['gap1_mae']:.3f}h")
    log()
    log("--- Episode-level diagnostic optimum ---")
    log(f"  growth_rate = {gr_ep:.4f}")
    log(f"  satiety_rate = {sr_ep:.4f}")
    log(f"  gap1_MAE = {best_ep_mult['gap1_mae']:.3f}h")
    log(f"  gap3_MAE = {best_ep_mult['gap3_mae']:.3f}h")
    log(f"  fcount_MAE = {best_ep_mult['feed_count_mae']:.2f}")
    log(f"  pred_std = {best_ep_mult['pred_std']:.3f}h")
    log()
    log("--- Model implementation (current) ---")
    log(f"  history = episode-level via episodes_as_events()")
    log(f"  satiety_rate = {SATIETY_RATE} (canonical multi-window sweep)")
    log(f"  growth_rate = estimated at runtime from recent episodes")
    log(f"  recency_half_life = {RECENCY_HALF_LIFE_HOURS}h")
    log(f"  circadian_amplitude = 0.0 (infrastructure present, not active)")
    log(f"  sim_volume = lookback-window median of episodes")
    log()
    log("Note: the raw-data and episode-level grid searches above are")
    log("diagnostic walk-forward evaluations using gap1_MAE. Production")
    log("constants are selected by canonical multi-window scoring (see")
    log("CANONICAL PARAMETER TUNING above). The internal diagnostics help")
    log("explain model mechanics but are not the tuning objective.")
    log()
    log("Key findings:")
    log("  1. Additive satiety collapses to constant-gap predictor (pred_std near 0)")
    log("  2. Multiplicative satiety produces meaningful volume-sensitive variation")
    log("  3. Circadian modulation adds no benefit on top of volume sensitivity")
    log("  4. Episode-level data improves all walk-forward metrics (~20%)")
    log("  5. Internal gap1_MAE prefers higher satiety rates (~0.6); canonical")
    log("     multi-window scoring prefers lower rates — metrics disagree on direction")
    log("  6. Volume-gap correlation is weaker at episode level (cluster artifact removed)")
    log("  7. Breastfeed merge has negligible impact on current data")

    # Save results.
    artifacts_dir = OUTPUT_DIR / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    results_path = artifacts_dir / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
