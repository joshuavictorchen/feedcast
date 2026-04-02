"""Survival / Hazard model research: explore gap distributions, Weibull fits,
discrete-time hazard baselines, and the effect of volume/day-part covariates.

Run from the repo root:
    .venv/bin/python -m feedcast.models.survival_hazard.analysis

This script reproduces the data analysis that informs the Survival / Hazard
model design. It uses the same export selection and data parsing as the model,
with bottle-only events as the production default. Episode-level analysis
re-derives key findings on cluster-collapsed data.

Update this script and re-run when new exports are available or when
revisiting model assumptions.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from feedcast.clustering import episodes_as_events
from feedcast.data import (
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
)
from feedcast.models.survival_hazard.model import (
    DAYTIME_SHAPE,
    LOOKBACK_DAYS,
    MIN_FIT_GAPS,
    OVERNIGHT_SHAPE,
    RECENCY_HALF_LIFE_HOURS,
    _estimate_scale,
    _is_overnight,
    _weibull_conditional_remaining,
    _weibull_median,
    _weibull_quantile,
)
from feedcast.replay import score_model, tune_model

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent


# ====================================================================
# Weibull helpers
# ====================================================================

def _weibull_neg_log_likelihood(
    params: list[float],
    gaps: np.ndarray,
    weights: np.ndarray,
    volumes: np.ndarray | None = None,
) -> float:
    """Weighted negative log-likelihood for a Weibull model.

    params:
      [log_shape, log_scale] for baseline model
      [log_shape, log_scale, beta] for model with volume covariate

    The Weibull PDF is: f(t) = (k/λ) * (t/λ)^(k-1) * exp(-(t/λ)^k)
    With volume covariate (accelerated failure time):
      λ_i = scale * exp(beta * vol_i)
    """
    log_shape = params[0]
    log_scale = params[1]
    k = math.exp(log_shape)
    base_scale = math.exp(log_scale)

    if len(params) > 2:
        beta = params[2]
        scales = base_scale * np.exp(beta * volumes)
    else:
        scales = np.full_like(gaps, base_scale)

    # Clamp gaps to avoid log(0).
    t = np.maximum(gaps, 0.01)

    # Log-likelihood: log(k/λ) + (k-1)*log(t/λ) - (t/λ)^k
    log_lik = (
        np.log(k) - np.log(scales)
        + (k - 1) * (np.log(t) - np.log(scales))
        - (t / scales) ** k
    )
    return -float(np.sum(weights * log_lik))


def _weibull_median(shape: float, scale: float) -> float:
    """Median of a Weibull distribution: scale * (ln 2)^(1/shape)."""
    return scale * math.log(2) ** (1.0 / shape)


def _weibull_quantile(shape: float, scale: float, p: float) -> float:
    """p-th quantile of a Weibull: scale * (-ln(1-p))^(1/shape)."""
    return scale * (-math.log(1.0 - p)) ** (1.0 / shape)


def _weibull_conditional_median(
    shape: float, scale: float, elapsed: float,
) -> float:
    """Remaining time to median given elapsed time since last feed.

    P(T > t0 + t_rem | T > t0) = 0.5
    => ((t0+t_rem)/λ)^k - (t0/λ)^k = ln 2
    => t_rem = λ * ((t0/λ)^k + ln 2)^(1/k) - t0
    """
    if elapsed <= 0:
        return _weibull_median(shape, scale)
    total = scale * ((elapsed / scale) ** shape + math.log(2)) ** (1.0 / shape)
    return max(total - elapsed, 0.01)


def _fit_weibull(
    gaps: np.ndarray,
    weights: np.ndarray,
    volumes: np.ndarray | None = None,
    with_volume: bool = False,
) -> dict:
    """Fit a Weibull model via weighted MLE.

    Returns dict with shape, scale, beta (if with_volume), and neg_loglik.
    """
    # Initial guesses from method of moments.
    mean_gap = float(np.average(gaps, weights=weights))
    std_gap = float(np.sqrt(np.average((gaps - mean_gap) ** 2, weights=weights)))
    # Rough Weibull shape from coefficient of variation.
    cv = std_gap / mean_gap if mean_gap > 0 else 0.5
    init_shape = max(0.5, 1.0 / cv) if cv > 0 else 2.0
    init_scale = mean_gap

    if with_volume and volumes is not None:
        x0 = [math.log(init_shape), math.log(init_scale), 0.0]
        result = minimize(
            _weibull_neg_log_likelihood, x0,
            args=(gaps, weights, volumes),
            method="Nelder-Mead",
            options={"maxiter": 5000, "xatol": 1e-6, "fatol": 1e-6},
        )
        k = math.exp(result.x[0])
        lam = math.exp(result.x[1])
        beta = result.x[2]
        return {
            "shape": k, "scale": lam, "beta": beta,
            "neg_loglik": result.fun, "success": result.success,
        }
    else:
        x0 = [math.log(init_shape), math.log(init_scale)]
        result = minimize(
            _weibull_neg_log_likelihood, x0,
            args=(gaps, weights, None),
            method="Nelder-Mead",
            options={"maxiter": 5000, "xatol": 1e-6, "fatol": 1e-6},
        )
        k = math.exp(result.x[0])
        lam = math.exp(result.x[1])
        return {
            "shape": k, "scale": lam,
            "neg_loglik": result.fun, "success": result.success,
        }


# Alias for backward compatibility within research script.
_estimate_scale_closed_form = _estimate_scale


# ====================================================================
# Discrete-time hazard helpers
# ====================================================================

# Bin edges in hours. Last bin is open-ended.
DISCRETE_BIN_EDGES = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]


def _fit_discrete_hazard(
    gaps: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Estimate discrete-time hazard rates per bin.

    For each bin, hazard = P(feed in bin | survived to bin).
    Uses weighted counts with Laplace smoothing.

    Returns array of hazard rates, one per bin (last bin is catch-all).
    """
    n_bins = len(DISCRETE_BIN_EDGES)  # Includes the final open-ended bin.
    # Count weighted events and survivors per bin.
    event_counts = np.zeros(n_bins)
    at_risk = np.zeros(n_bins)

    for gap, w in zip(gaps, weights):
        for b in range(n_bins):
            lo = DISCRETE_BIN_EDGES[b]
            hi = DISCRETE_BIN_EDGES[b + 1] if b + 1 < n_bins else float("inf")
            if gap > lo:
                at_risk[b] += w
            if lo <= gap < hi:
                event_counts[b] += w
                break

    # Laplace smoothing.
    alpha = 0.5
    hazard = (event_counts + alpha) / (at_risk + 2 * alpha)
    # Clamp to [0.01, 0.99] to avoid degenerate survival.
    hazard = np.clip(hazard, 0.01, 0.99)
    return hazard


def _discrete_median(hazard: np.ndarray) -> float:
    """Median survival time from discrete hazard rates."""
    survival = 1.0
    for b in range(len(hazard)):
        lo = DISCRETE_BIN_EDGES[b]
        hi = DISCRETE_BIN_EDGES[b + 1] if b + 1 < len(DISCRETE_BIN_EDGES) else lo + 2.0
        mid = (lo + hi) / 2.0
        survival *= (1.0 - hazard[b])
        if survival <= 0.5:
            return mid
    # If survival never drops to 0.5, return the last bin edge + 1.
    return DISCRETE_BIN_EDGES[-1] + 1.0


# ====================================================================
# Walk-forward evaluation
# ====================================================================

def _walk_forward_weibull(
    events,
    shape: float,
    volume_beta: float = 0.0,
    lookback_days: int = 7,
    recency_half_life_hours: float = RECENCY_HALF_LIFE_HOURS,
    use_volume: bool = False,
) -> dict:
    """Walk-forward evaluation of the Weibull model.

    For each event from MIN_FIT_GAPS onward:
    1. Fit the scale parameter from the lookback window (shape is fixed).
    2. Predict the gap using median survival time.
    3. Compare to actual gap.

    Returns gap1_mae, gap3_mae, feed_count_mae.
    """
    decay = math.log(2) / recency_half_life_hours
    cutoff_time = events[-1].time

    gap1_errors, gap1_weights = [], []
    gap3_errors, gap3_weights = [], []
    fcount_errors, fcount_weights = [], []

    for i in range(MIN_FIT_GAPS + 1, len(events) - 1):
        event = events[i]
        lookback_start = event.time - timedelta(days=lookback_days)

        # Gather gaps in lookback window for scale estimation.
        fit_gaps = []
        fit_weights = []
        for j in range(i):
            if events[j].time < lookback_start:
                continue
            if j + 1 > i:
                break
            gap = (events[j + 1].time - events[j].time).total_seconds() / 3600
            if gap <= 0:
                continue
            age = (event.time - events[j].time).total_seconds() / 3600
            w = math.exp(-decay * max(age, 0))
            fit_gaps.append(gap)
            fit_weights.append(w)

        if len(fit_gaps) < MIN_FIT_GAPS:
            continue

        fit_gaps_arr = np.array(fit_gaps)
        fit_weights_arr = np.array(fit_weights)

        # Estimate scale from recent gaps.
        scale = _estimate_scale_closed_form(fit_gaps_arr, fit_weights_arr, shape)

        # Adjust scale for volume if applicable.
        if use_volume and volume_beta != 0.0:
            effective_scale = scale * math.exp(volume_beta * event.volume_oz)
        else:
            effective_scale = scale

        # Predict gap (median survival time).
        predicted = _weibull_median(shape, effective_scale)

        actual = (events[i + 1].time - event.time).total_seconds() / 3600
        age_from_cutoff = (cutoff_time - event.time).total_seconds() / 3600
        weight = math.exp(-decay * max(age_from_cutoff, 0))

        gap1_errors.append(abs(predicted - actual))
        gap1_weights.append(weight)

        # 3-gap trajectory.
        if i + 3 < len(events):
            sim_vol = float(np.median([e.volume_oz for e in events[max(0, i - 10):i + 1]]))
            preds = [predicted]
            for _ in range(2):
                if use_volume and volume_beta != 0.0:
                    eff_s = scale * math.exp(volume_beta * sim_vol)
                else:
                    eff_s = scale
                preds.append(_weibull_median(shape, eff_s))
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
        sim_count = 0
        sim_v = event.volume_oz
        while sim_t < 24.0:
            if use_volume and volume_beta != 0.0:
                eff_s = scale * math.exp(volume_beta * sim_v)
            else:
                eff_s = scale
            gap = _weibull_median(shape, eff_s)
            sim_t += gap
            if sim_t < 24.0:
                sim_count += 1
                sim_v = float(np.median([e.volume_oz for e in events[max(0, i - 10):i + 1]]))
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
    }


# ====================================================================
# Main research
# ====================================================================

def main() -> None:
    """Run the full analysis."""
    output_capture = StringIO()

    def log(text: str = "") -> None:
        print(text)
        output_capture.write(text + "\n")

    snapshot = load_export_snapshot()
    cutoff = snapshot.latest_activity_time

    # Production uses bottle-only events. Breastfeed-merged is a labeled
    # secondary comparison for the volume covariate section only.
    events_bottle = build_feed_events(
        snapshot.activities, merge_window_minutes=None,
    )
    events_merged = build_feed_events(
        snapshot.activities,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    )

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log(f"Events (bottle only):   {len(events_bottle)}")
    log(f"Events (with BF merge): {len(events_merged)}")
    log()

    # Default: bottle-only (matches production input policy).
    events = events_bottle

    # ================================================================
    # SECTION 1: Gap distribution
    # ================================================================
    log("=== GAP DISTRIBUTION ===")
    log()
    gaps = []
    volumes = []
    hours_arr = []
    for i in range(len(events) - 1):
        gap = (events[i + 1].time - events[i].time).total_seconds() / 3600
        gaps.append(gap)
        volumes.append(events[i].volume_oz)
        hours_arr.append(hour_of_day(events[i].time))

    gaps_np = np.array(gaps)
    volumes_np = np.array(volumes)
    hours_np = np.array(hours_arr)

    log(f"Total gaps: {len(gaps_np)}")
    log(f"Mean: {gaps_np.mean():.3f}h  Std: {gaps_np.std():.3f}h")
    log(f"Min: {gaps_np.min():.3f}h  Max: {gaps_np.max():.3f}h")
    log(f"Median: {float(np.median(gaps_np)):.3f}h")
    log(f"Coefficient of variation: {gaps_np.std() / gaps_np.mean():.3f}")
    log()

    # Histogram.
    log("Gap histogram (0.5h bins):")
    for lo in np.arange(0, 5.5, 0.5):
        hi = lo + 0.5
        count = np.sum((gaps_np >= lo) & (gaps_np < hi))
        bar = "#" * int(count)
        log(f"  [{lo:.1f}, {hi:.1f}): {count:>3} {bar}")
    overflow = np.sum(gaps_np >= 5.5)
    log(f"  [5.5+):   {overflow:>3} {'#' * int(overflow)}")
    log()

    # ================================================================
    # SECTION 2: Weibull fit — baseline (no covariates)
    # ================================================================
    log("=== WEIBULL FIT — BASELINE ===")
    log()

    # Fit on all data with uniform weights.
    fit_all = _fit_weibull(gaps_np, np.ones(len(gaps_np)))
    k_all = fit_all["shape"]
    lam_all = fit_all["scale"]
    med_all = _weibull_median(k_all, lam_all)
    log(f"All data (uniform weights):")
    log(f"  shape={k_all:.4f}  scale={lam_all:.4f}")
    log(f"  median={med_all:.3f}h  mean={lam_all * math.gamma(1 + 1/k_all):.3f}h")
    log(f"  neg_loglik={fit_all['neg_loglik']:.2f}  success={fit_all['success']}")
    log()

    # Fit with recency weighting.
    decay = math.log(2) / RECENCY_HALF_LIFE_HOURS
    rec_weights = np.array([
        math.exp(-decay * (cutoff - events[i].time).total_seconds() / 3600)
        for i in range(len(gaps_np))
    ])
    fit_rec = _fit_weibull(gaps_np, rec_weights)
    k_rec = fit_rec["shape"]
    lam_rec = fit_rec["scale"]
    med_rec = _weibull_median(k_rec, lam_rec)
    log(f"Recency-weighted (half-life={RECENCY_HALF_LIFE_HOURS}h):")
    log(f"  shape={k_rec:.4f}  scale={lam_rec:.4f}")
    log(f"  median={med_rec:.3f}h  mean={lam_rec * math.gamma(1 + 1/k_rec):.3f}h")
    log()

    # Recent-only fits.
    for days in [5, 7]:
        start = cutoff - timedelta(days=days)
        mask = np.array([events[i].time >= start for i in range(len(gaps_np))])
        if mask.sum() >= MIN_FIT_GAPS:
            fit_d = _fit_weibull(gaps_np[mask], rec_weights[mask])
            k_d = fit_d["shape"]
            lam_d = fit_d["scale"]
            log(f"Last {days}d (recency-weighted):")
            log(f"  shape={k_d:.4f}  scale={lam_d:.4f}  "
                f"median={_weibull_median(k_d, lam_d):.3f}h  (n={mask.sum()})")
    log()

    # Interpretation.
    log(f"Shape > 1 means hazard increases with elapsed time.")
    log(f"  shape={k_rec:.2f}: {'increasing' if k_rec > 1 else 'decreasing/constant'} hazard")
    log(f"  This means: {'the longer you wait, the more likely the next feed' if k_rec > 1 else 'no clear increasing pattern'}")
    log()

    # ================================================================
    # SECTION 3: Weibull + volume covariate
    # ================================================================
    log("=== WEIBULL + VOLUME COVARIATE ===")
    log()

    fit_vol = _fit_weibull(gaps_np, rec_weights, volumes_np, with_volume=True)
    k_v = fit_vol["shape"]
    lam_v = fit_vol["scale"]
    beta_v = fit_vol["beta"]
    log(f"Recency-weighted with volume covariate:")
    log(f"  shape={k_v:.4f}  scale={lam_v:.4f}  beta={beta_v:.4f}")
    log(f"  success={fit_vol['success']}")
    log()

    # What beta means: positive beta => bigger volume => larger scale => longer gap.
    log(f"Volume effect on median gap:")
    for vol in [1.0, 2.0, 3.0, 4.0]:
        eff_scale = lam_v * math.exp(beta_v * vol)
        log(f"  vol={vol:.1f}oz: effective_scale={eff_scale:.3f}  "
            f"median={_weibull_median(k_v, eff_scale):.3f}h")
    log()

    # Likelihood ratio test: does volume improve the fit?
    lr_stat = 2 * (fit_rec["neg_loglik"] - fit_vol["neg_loglik"])
    log(f"Likelihood ratio test (1 df):")
    log(f"  baseline neg_loglik: {fit_rec['neg_loglik']:.2f}")
    log(f"  +volume neg_loglik:  {fit_vol['neg_loglik']:.2f}")
    log(f"  LR statistic: {lr_stat:.3f} (>3.84 for p<0.05)")
    log(f"  Volume {'significant' if lr_stat > 3.84 else 'not significant'} at p<0.05")
    log()

    # ================================================================
    # SECTION 4: Weibull + day-part covariate
    # ================================================================
    log("=== WEIBULL BY DAY-PART ===")
    log()

    # Fit separate Weibull models for overnight (20-08) vs daytime (08-20).
    night_mask = (hours_np >= 20) | (hours_np < 8)
    day_mask = ~night_mask

    for label, mask in [("Overnight (20-08)", night_mask), ("Daytime (08-20)", day_mask)]:
        if mask.sum() >= MIN_FIT_GAPS:
            fit_dp = _fit_weibull(gaps_np[mask], rec_weights[mask])
            log(f"{label} (n={mask.sum()}):")
            log(f"  shape={fit_dp['shape']:.4f}  scale={fit_dp['scale']:.4f}  "
                f"median={_weibull_median(fit_dp['shape'], fit_dp['scale']):.3f}h")
    log()

    # ================================================================
    # SECTION 5: Discrete-time hazard comparison
    # ================================================================
    log("=== DISCRETE-TIME HAZARD ===")
    log()

    hazard = _fit_discrete_hazard(gaps_np, rec_weights)
    log("Discrete hazard rates (recency-weighted):")
    for b in range(len(hazard)):
        lo = DISCRETE_BIN_EDGES[b]
        hi = DISCRETE_BIN_EDGES[b + 1] if b + 1 < len(DISCRETE_BIN_EDGES) else float("inf")
        bar = "#" * int(hazard[b] * 50)
        log(f"  [{lo:>4.1f}, {hi if hi < 100 else 'inf':>4}): h={hazard[b]:.3f} {bar}")

    disc_med = _discrete_median(hazard)
    log(f"\nDiscrete median survival: {disc_med:.3f}h")
    log(f"Weibull median survival: {med_rec:.3f}h")
    log()

    # Walk-forward comparison: discrete vs Weibull gap1 MAE.
    log("Walk-forward discrete-time hazard (gap1 MAE):")
    disc_errors, disc_weights_arr = [], []
    for i in range(MIN_FIT_GAPS + 1, len(events) - 1):
        event = events[i]
        lookback_start = event.time - timedelta(days=7)
        fit_g, fit_w = [], []
        for j in range(i):
            if events[j].time < lookback_start:
                continue
            if j + 1 > i:
                break
            g = (events[j + 1].time - events[j].time).total_seconds() / 3600
            if g <= 0:
                continue
            age = (event.time - events[j].time).total_seconds() / 3600
            fit_w.append(math.exp(-decay * max(age, 0)))
            fit_g.append(g)
        if len(fit_g) < MIN_FIT_GAPS:
            continue
        h = _fit_discrete_hazard(np.array(fit_g), np.array(fit_w))
        pred = _discrete_median(h)
        actual = (events[i + 1].time - event.time).total_seconds() / 3600
        age_from_cutoff = (cutoff - event.time).total_seconds() / 3600
        disc_errors.append(abs(pred - actual))
        disc_weights_arr.append(math.exp(-decay * max(age_from_cutoff, 0)))

    disc_mae = float(np.average(disc_errors, weights=disc_weights_arr)) if disc_errors else float("nan")
    log(f"  Discrete gap1_MAE: {disc_mae:.3f}h (n={len(disc_errors)})")
    log()

    # ================================================================
    # SECTION 6: Walk-forward Weibull comparison
    # ================================================================
    log("=== WALK-FORWARD WEIBULL COMPARISON ===")
    log()

    # Baseline (no volume).
    for k_test in [k_rec, k_all]:
        label = f"shape={k_test:.3f}"
        for days in [5, 7]:
            result = _walk_forward_weibull(
                events, k_test, lookback_days=days, use_volume=False,
            )
            log(f"Weibull {label} lookback={days}d: "
                f"gap1={result['gap1_mae']:.3f}h  gap3={result['gap3_mae']:.3f}h  "
                f"fcount={result['feed_count_mae']:.2f}  (n={result['n']})")
    log()

    # With volume.
    for k_test in [k_v, k_rec]:
        label = f"shape={k_test:.3f}"
        for beta_test in [beta_v, beta_v * 0.5]:
            result_v = _walk_forward_weibull(
                events, k_test, volume_beta=beta_test,
                lookback_days=7, use_volume=True,
            )
            log(f"Weibull+vol {label} beta={beta_test:.3f}: "
                f"gap1={result_v['gap1_mae']:.3f}h  gap3={result_v['gap3_mae']:.3f}h  "
                f"fcount={result_v['feed_count_mae']:.2f}")
    log()

    # ================================================================
    # SECTION 6b: Day-part split walk-forward
    # ================================================================
    log("=== DAY-PART SPLIT WALK-FORWARD ===")
    log()

    # Fit separate overnight/daytime shapes.
    night_mask_all = (hours_np >= 20) | (hours_np < 8)
    day_mask_all = ~night_mask_all
    fit_night = _fit_weibull(gaps_np[night_mask_all], rec_weights[night_mask_all])
    fit_day = _fit_weibull(gaps_np[day_mask_all], rec_weights[day_mask_all])
    k_night = fit_night["shape"]
    k_day = fit_day["shape"]

    # Walk-forward with day-part split: use appropriate shape/scale per event.
    daypart_errors, daypart_weights_wf = [], []
    daypart_g3_errors, daypart_g3_weights = [], []
    daypart_fc_errors, daypart_fc_weights = [], []

    for i in range(MIN_FIT_GAPS + 1, len(events) - 1):
        event = events[i]
        h = hour_of_day(event.time)
        is_night = h >= 20 or h < 8
        shape_i = k_night if is_night else k_day
        lookback_start = event.time - timedelta(days=7)

        # Gather same-daypart gaps for scale estimation.
        fit_g, fit_w = [], []
        for j in range(i):
            if events[j].time < lookback_start:
                continue
            if j + 1 > i:
                break
            hj = hour_of_day(events[j].time)
            j_night = hj >= 20 or hj < 8
            if j_night != is_night:
                continue
            g = (events[j + 1].time - events[j].time).total_seconds() / 3600
            if g <= 0:
                continue
            age = (event.time - events[j].time).total_seconds() / 3600
            fit_w.append(math.exp(-decay * max(age, 0)))
            fit_g.append(g)

        if len(fit_g) < 3:
            # Fall back to all gaps if too few same-daypart.
            fit_g, fit_w = [], []
            for j in range(i):
                if events[j].time < lookback_start:
                    continue
                if j + 1 > i:
                    break
                g = (events[j + 1].time - events[j].time).total_seconds() / 3600
                if g <= 0:
                    continue
                age = (event.time - events[j].time).total_seconds() / 3600
                fit_w.append(math.exp(-decay * max(age, 0)))
                fit_g.append(g)

        if len(fit_g) < MIN_FIT_GAPS:
            continue

        scale_i = _estimate_scale_closed_form(
            np.array(fit_g), np.array(fit_w), shape_i,
        )
        predicted = _weibull_median(shape_i, scale_i)
        actual = (events[i + 1].time - event.time).total_seconds() / 3600
        age_from_cutoff = (cutoff - event.time).total_seconds() / 3600
        w = math.exp(-decay * max(age_from_cutoff, 0))

        daypart_errors.append(abs(predicted - actual))
        daypart_weights_wf.append(w)

        # Estimate both daypart scales for this event's lookback window.
        night_g, night_w, day_g, day_w = [], [], [], []
        for j in range(i):
            if events[j].time < lookback_start or j + 1 > i:
                continue
            g = (events[j + 1].time - events[j].time).total_seconds() / 3600
            if g <= 0:
                continue
            age = (event.time - events[j].time).total_seconds() / 3600
            wt = math.exp(-decay * max(age, 0))
            hj = hour_of_day(events[j].time)
            if hj >= 20 or hj < 8:
                night_g.append(g)
                night_w.append(wt)
            else:
                day_g.append(g)
                day_w.append(wt)
        # Use all gaps as fallback for either daypart.
        all_g_arr = np.array(night_g + day_g) if night_g or day_g else np.array(fit_g)
        all_w_arr = np.array(night_w + day_w) if night_w or day_w else np.array(fit_w)
        night_scale = (
            _estimate_scale(np.array(night_g), np.array(night_w), k_night)
            if len(night_g) >= 3
            else _estimate_scale(all_g_arr, all_w_arr, k_night)
        )
        day_scale = (
            _estimate_scale(np.array(day_g), np.array(day_w), k_day)
            if len(day_g) >= 3
            else _estimate_scale(all_g_arr, all_w_arr, k_day)
        )

        def _dp_scale(hour: float) -> float:
            return night_scale if (hour >= 20 or hour < 8) else day_scale

        # 3-gap trajectory with daypart switching using proper scales.
        if i + 3 < len(events):
            preds = [predicted]
            sim_hour = h + predicted
            for _ in range(2):
                sh = sim_hour % 24.0
                sn = sh >= 20 or sh < 8
                s_shape = k_night if sn else k_day
                preds.append(_weibull_median(s_shape, _dp_scale(sh)))
                sim_hour += preds[-1]
            actuals_3 = [
                (events[i + j + 1].time - events[i + j].time).total_seconds() / 3600
                for j in range(3)
            ]
            daypart_g3_errors.append(float(np.mean(np.abs(
                np.array(preds) - np.array(actuals_3)
            ))))
            daypart_g3_weights.append(w)

        # 24h feed count with daypart switching using proper scales.
        sim_t = 0.0
        sim_count = 0
        sim_h = h
        while sim_t < 24.0:
            sh = (sim_h + sim_t) % 24.0
            sn = sh >= 20 or sh < 8
            s_shape = k_night if sn else k_day
            gap = _weibull_median(s_shape, _dp_scale(sh))
            sim_t += gap
            if sim_t < 24.0:
                sim_count += 1
        actual_count = sum(
            1 for e in events[i + 1:]
            if e.time <= event.time + timedelta(hours=24)
        )
        daypart_fc_errors.append(abs(sim_count - actual_count))
        daypart_fc_weights.append(w)

    daypart_g1 = float(np.average(daypart_errors, weights=daypart_weights_wf))
    daypart_g3 = float(np.average(daypart_g3_errors, weights=daypart_g3_weights)) if daypart_g3_errors else float("nan")
    daypart_fc = float(np.average(daypart_fc_errors, weights=daypart_fc_weights)) if daypart_fc_errors else float("nan")
    log(f"Day-part split Weibull:")
    log(f"  overnight shape={k_night:.3f}  daytime shape={k_day:.3f}")
    log(f"  gap1_MAE={daypart_g1:.3f}h  gap3_MAE={daypart_g3:.3f}h  "
        f"fcount_MAE={daypart_fc:.2f}  (n={len(daypart_errors)})")
    log()

    # ================================================================
    # SECTION 7: Naive baseline comparison
    # ================================================================
    log("=== NAIVE BASELINE COMPARISON ===")
    log()
    naive_last_e, naive_last_w = [], []
    naive_mean3_e, naive_mean3_w = [], []
    for i in range(MIN_FIT_GAPS + 1, len(events) - 1):
        actual = (events[i + 1].time - events[i].time).total_seconds() / 3600
        last = (events[i].time - events[i - 1].time).total_seconds() / 3600
        age = (cutoff - events[i].time).total_seconds() / 3600
        w = math.exp(-decay * max(age, 0))
        naive_last_e.append(abs(last - actual))
        naive_last_w.append(w)
        if i >= 3:
            m3 = np.mean([
                (events[i - j].time - events[i - j - 1].time).total_seconds() / 3600
                for j in range(3)
            ])
            naive_mean3_e.append(abs(m3 - actual))
            naive_mean3_w.append(w)

    # Pick best Weibull config for the summary.
    best_wb = _walk_forward_weibull(events, k_rec, lookback_days=7)
    best_wb_vol = _walk_forward_weibull(
        events, k_v, volume_beta=beta_v, lookback_days=7, use_volume=True,
    )

    log(f"{'Model':<40} {'gap1_MAE':>10}")
    log("-" * 53)
    log(f"{'Naive last-gap':<40} {float(np.average(naive_last_e, weights=naive_last_w)):>10.3f}")
    log(f"{'Naive mean-3-gaps':<40} {float(np.average(naive_mean3_e, weights=naive_mean3_w)):>10.3f}")
    log(f"{'Discrete-time hazard':<40} {disc_mae:>10.3f}")
    log(f"{'Weibull baseline (no vol)':<40} {best_wb['gap1_mae']:>10.3f}")
    log(f"{'Weibull + volume':<40} {best_wb_vol['gap1_mae']:>10.3f}")
    log(f"{'Day-part split Weibull':<40} {daypart_g1:>10.3f}")
    log()

    # ================================================================
    # SECTION 8: Most recent 24h holdout
    # ================================================================
    log("=== MOST RECENT 24H PREDICTION (holdout, shipped model) ===")
    log()
    log("Reproduces the shipped model exactly: fixed shapes from constants,")
    log("runtime scale estimation, conditional survival for first feed.")
    log("Only pre-holdout-cutoff events are used (true holdout).")
    log()

    # The holdout simulates what the model would do if the real cutoff
    # were 24h earlier. The "holdout cutoff" is cutoff - 24h. The last
    # event before that is the anchor. Elapsed time from anchor to the
    # holdout cutoff drives the conditional survival for the first feed.
    holdout_cutoff = cutoff - timedelta(hours=24)
    anchor_idx = max(
        (i for i, e in enumerate(events) if e.time <= holdout_cutoff),
        default=None,
    )
    if anchor_idx is not None and anchor_idx >= MIN_FIT_GAPS + 1:
        anchor = events[anchor_idx]
        pre_events = events[:anchor_idx + 1]
        elapsed = (holdout_cutoff - anchor.time).total_seconds() / 3600

        # Use FIXED shapes from model constants (not re-fit).
        ho_k_night = OVERNIGHT_SHAPE
        ho_k_day = DAYTIME_SHAPE

        # Estimate scales from pre-holdout gaps using the model's
        # lookback window and recency weighting, exactly as model.py does.
        lookback_start = holdout_cutoff - timedelta(days=LOOKBACK_DAYS)
        night_g, night_w, day_g, day_w = [], [], [], []
        all_g, all_w = [], []
        for j in range(len(pre_events) - 1):
            ev = pre_events[j]
            if ev.time < lookback_start:
                continue
            g = (pre_events[j + 1].time - ev.time).total_seconds() / 3600
            if g <= 0:
                continue
            age = (holdout_cutoff - ev.time).total_seconds() / 3600
            wt = math.exp(-decay * max(age, 0))
            h_ev = hour_of_day(ev.time)
            all_g.append(g)
            all_w.append(wt)
            if _is_overnight(h_ev):
                night_g.append(g)
                night_w.append(wt)
            else:
                day_g.append(g)
                day_w.append(wt)

        all_g_np = np.array(all_g) if all_g else np.array([2.5])
        all_w_np = np.array(all_w) if all_w else np.array([1.0])
        ho_night_scale = (
            _estimate_scale(np.array(night_g), np.array(night_w), ho_k_night)
            if len(night_g) >= 3
            else _estimate_scale(all_g_np, all_w_np, ho_k_night)
        )
        ho_day_scale = (
            _estimate_scale(np.array(day_g), np.array(day_w), ho_k_day)
            if len(day_g) >= 3
            else _estimate_scale(all_g_np, all_w_np, ho_k_day)
        )

        log(f"Holdout cutoff: {holdout_cutoff.strftime('%m/%d %H:%M')}")
        log(f"Last event (anchor): {anchor.time.strftime('%m/%d %H:%M')} "
            f"vol={anchor.volume_oz:.1f}oz")
        log(f"Elapsed since anchor: {elapsed:.2f}h")
        log(f"Overnight: shape={ho_k_night} scale={ho_night_scale:.4f} "
            f"median={_weibull_median(ho_k_night, ho_night_scale):.3f}h")
        log(f"Daytime:   shape={ho_k_day} scale={ho_day_scale:.4f} "
            f"median={_weibull_median(ho_k_day, ho_day_scale):.3f}h")
        log()

        # First feed: conditional survival anchored to last_event.time,
        # exactly as model.py does.
        anchor_hour = hour_of_day(anchor.time)
        first_shape = ho_k_night if _is_overnight(anchor_hour) else ho_k_day
        first_scale = ho_night_scale if _is_overnight(anchor_hour) else ho_day_scale
        time_to_first = _weibull_conditional_remaining(first_shape, first_scale, elapsed)
        log(f"First feed: conditional remaining = {time_to_first:.3f}h "
            f"(daypart={'overnight' if _is_overnight(anchor_hour) else 'daytime'})")
        log()

        # Simulate 24h forward from holdout_cutoff with day-part switching.
        predicted_times = []
        feed_time = holdout_cutoff + timedelta(hours=time_to_first)
        horizon_end = holdout_cutoff + timedelta(hours=24)
        while feed_time < horizon_end:
            predicted_times.append(feed_time)
            feed_hour = hour_of_day(feed_time)
            s_shape = ho_k_night if _is_overnight(feed_hour) else ho_k_day
            s_scale = ho_night_scale if _is_overnight(feed_hour) else ho_day_scale
            gap = _weibull_median(s_shape, s_scale)
            feed_time = feed_time + timedelta(hours=gap)

        actual_times = [
            e.time for e in events
            if holdout_cutoff < e.time <= horizon_end
        ]
        log(f"Predicted {len(predicted_times)} feeds, actual {len(actual_times)} feeds")
        log()

        # Pair predictions to actuals.
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
    # SECTION 9: Episode-level analysis
    # ================================================================
    log("=== EPISODE-LEVEL ANALYSIS ===")
    log()
    log("Cluster-internal gaps (50-70 min) contaminate the raw gap distribution.")
    log("Episode grouping collapses close-together feeds into single episodes,")
    log("removing these artifacts. This section re-derives all key findings on")
    log("episode-level data for comparison with the raw baseline above.")
    log()

    # Convert bottle-only events to episodes.
    episode_events = episodes_as_events(events)
    log(f"Raw events: {len(events)}")
    log(f"Episode events: {len(episode_events)}")
    log(f"Collapsed: {len(events) - len(episode_events)} feeds absorbed into clusters")
    log()

    # Episode-level gaps and per-daypart counts.
    ep_gaps = []
    ep_volumes = []
    ep_hours = []
    for i in range(len(episode_events) - 1):
        gap = (episode_events[i + 1].time - episode_events[i].time).total_seconds() / 3600
        ep_gaps.append(gap)
        ep_volumes.append(episode_events[i].volume_oz)
        ep_hours.append(hour_of_day(episode_events[i].time))

    ep_gaps_np = np.array(ep_gaps)
    ep_volumes_np = np.array(ep_volumes)
    ep_hours_np = np.array(ep_hours)

    ep_night_mask = (ep_hours_np >= 20) | (ep_hours_np < 8)
    ep_day_mask = ~ep_night_mask

    log(f"Episode gap distribution:")
    log(f"  Total gaps: {len(ep_gaps_np)}")
    log(f"  Mean: {ep_gaps_np.mean():.3f}h  Std: {ep_gaps_np.std():.3f}h")
    log(f"  Min: {ep_gaps_np.min():.3f}h  Max: {ep_gaps_np.max():.3f}h")
    log(f"  Median: {float(np.median(ep_gaps_np)):.3f}h")
    log(f"  Overnight gaps: {ep_night_mask.sum()}")
    log(f"  Daytime gaps: {ep_day_mask.sum()}")
    log()

    # Compare raw vs episode gap histograms.
    log("Episode gap histogram (0.5h bins):")
    for lo in np.arange(0, 5.5, 0.5):
        hi = lo + 0.5
        count = np.sum((ep_gaps_np >= lo) & (ep_gaps_np < hi))
        bar = "#" * int(count)
        log(f"  [{lo:.1f}, {hi:.1f}): {count:>3} {bar}")
    overflow = np.sum(ep_gaps_np >= 5.5)
    log(f"  [5.5+):   {overflow:>3} {'#' * int(overflow)}")
    log()

    # Fit Weibull shapes per daypart on episode-level data.
    ep_rec_weights = np.array([
        math.exp(-decay * (cutoff - episode_events[i].time).total_seconds() / 3600)
        for i in range(len(ep_gaps_np))
    ])

    log("Episode-level Weibull fits by day-part:")
    ep_fit_results = {}
    for label, mask, dp_name in [
        ("Overnight (20-08)", ep_night_mask, "overnight"),
        ("Daytime (08-20)", ep_day_mask, "daytime"),
    ]:
        if mask.sum() >= MIN_FIT_GAPS:
            fit_dp = _fit_weibull(ep_gaps_np[mask], ep_rec_weights[mask])
            ep_fit_results[dp_name] = fit_dp
            log(f"  {label} (n={mask.sum()}):")
            log(f"    shape={fit_dp['shape']:.4f}  scale={fit_dp['scale']:.4f}  "
                f"median={_weibull_median(fit_dp['shape'], fit_dp['scale']):.3f}h")
        else:
            log(f"  {label}: too few gaps ({mask.sum()}) for fit")
    log()

    # Compare raw vs episode shapes.
    log("Raw vs episode shape comparison:")
    log(f"  {'Daypart':<15} {'Raw shape':>10} {'Ep shape':>10} {'Delta':>8}")
    if "overnight" in ep_fit_results:
        raw_k = k_night
        ep_k = ep_fit_results["overnight"]["shape"]
        log(f"  {'Overnight':<15} {raw_k:>10.3f} {ep_k:>10.3f} {ep_k - raw_k:>+8.3f}")
    if "daytime" in ep_fit_results:
        raw_k = k_day
        ep_k = ep_fit_results["daytime"]["shape"]
        log(f"  {'Daytime':<15} {raw_k:>10.3f} {ep_k:>10.3f} {ep_k - raw_k:>+8.3f}")
    log()

    # Episode-level walk-forward with day-part split.
    ep_k_night = ep_fit_results.get("overnight", {}).get("shape", k_night)
    ep_k_day = ep_fit_results.get("daytime", {}).get("shape", k_day)

    log("Episode-level day-part walk-forward:")

    # Sweep half-lives on episode data.
    for half_life in [48, 72, 120, 168]:
        ep_decay_hl = math.log(2) / half_life
        ep_dp_errors, ep_dp_weights_wf = [], []
        ep_dp_fc_errors, ep_dp_fc_weights = [], []

        for i in range(MIN_FIT_GAPS + 1, len(episode_events) - 1):
            event = episode_events[i]
            h = hour_of_day(event.time)
            is_night = h >= 20 or h < 8
            shape_i = ep_k_night if is_night else ep_k_day
            lookback_start = event.time - timedelta(days=LOOKBACK_DAYS)

            # Gather same-daypart episode gaps for scale estimation.
            fit_g, fit_w = [], []
            for j in range(i):
                if episode_events[j].time < lookback_start:
                    continue
                if j + 1 > i:
                    break
                hj = hour_of_day(episode_events[j].time)
                j_night = hj >= 20 or hj < 8
                if j_night != is_night:
                    continue
                g = (episode_events[j + 1].time - episode_events[j].time).total_seconds() / 3600
                if g <= 0:
                    continue
                age = (event.time - episode_events[j].time).total_seconds() / 3600
                fit_w.append(math.exp(-ep_decay_hl * max(age, 0)))
                fit_g.append(g)

            if len(fit_g) < 3:
                # Fall back to all episode gaps.
                fit_g, fit_w = [], []
                for j in range(i):
                    if episode_events[j].time < lookback_start:
                        continue
                    if j + 1 > i:
                        break
                    g = (episode_events[j + 1].time - episode_events[j].time).total_seconds() / 3600
                    if g <= 0:
                        continue
                    age = (event.time - episode_events[j].time).total_seconds() / 3600
                    fit_w.append(math.exp(-ep_decay_hl * max(age, 0)))
                    fit_g.append(g)

            if len(fit_g) < MIN_FIT_GAPS:
                continue

            scale_i = _estimate_scale_closed_form(
                np.array(fit_g), np.array(fit_w), shape_i,
            )
            predicted = _weibull_median(shape_i, scale_i)
            actual = (episode_events[i + 1].time - event.time).total_seconds() / 3600
            age_from_cutoff = (cutoff - event.time).total_seconds() / 3600
            w = math.exp(-ep_decay_hl * max(age_from_cutoff, 0))

            ep_dp_errors.append(abs(predicted - actual))
            ep_dp_weights_wf.append(w)

            # Estimate both daypart scales for feed count simulation.
            night_g, night_w, day_g, day_w = [], [], [], []
            for j in range(i):
                if episode_events[j].time < lookback_start or j + 1 > i:
                    continue
                g = (episode_events[j + 1].time - episode_events[j].time).total_seconds() / 3600
                if g <= 0:
                    continue
                age = (event.time - episode_events[j].time).total_seconds() / 3600
                wt = math.exp(-ep_decay_hl * max(age, 0))
                hj = hour_of_day(episode_events[j].time)
                if hj >= 20 or hj < 8:
                    night_g.append(g)
                    night_w.append(wt)
                else:
                    day_g.append(g)
                    day_w.append(wt)

            all_ep_g = np.array(night_g + day_g) if night_g or day_g else np.array(fit_g)
            all_ep_w = np.array(night_w + day_w) if night_w or day_w else np.array(fit_w)
            night_scale = (
                _estimate_scale(np.array(night_g), np.array(night_w), ep_k_night)
                if len(night_g) >= 3
                else _estimate_scale(all_ep_g, all_ep_w, ep_k_night)
            )
            day_scale = (
                _estimate_scale(np.array(day_g), np.array(day_w), ep_k_day)
                if len(day_g) >= 3
                else _estimate_scale(all_ep_g, all_ep_w, ep_k_day)
            )

            # 24h episode count simulation.
            sim_t = 0.0
            sim_count = 0
            while sim_t < 24.0:
                sh = (h + sim_t) % 24.0
                sn = sh >= 20 or sh < 8
                s_shape = ep_k_night if sn else ep_k_day
                s_scale = night_scale if sn else day_scale
                gap = _weibull_median(s_shape, s_scale)
                sim_t += gap
                if sim_t < 24.0:
                    sim_count += 1
            actual_count = sum(
                1 for e in episode_events[i + 1:]
                if e.time <= event.time + timedelta(hours=24)
            )
            ep_dp_fc_errors.append(abs(sim_count - actual_count))
            ep_dp_fc_weights.append(w)

        ep_g1 = float(np.average(ep_dp_errors, weights=ep_dp_weights_wf)) if ep_dp_errors else float("nan")
        ep_fc = float(np.average(ep_dp_fc_errors, weights=ep_dp_fc_weights)) if ep_dp_fc_errors else float("nan")
        marker = " <-- current" if half_life == RECENCY_HALF_LIFE_HOURS else ""
        log(f"  half_life={half_life:>3}h: gap1_MAE={ep_g1:.3f}h  "
            f"fcount_MAE={ep_fc:.2f}  (n={len(ep_dp_errors)}){marker}")

    log()

    # Episode-level volume covariate: LR significance test.
    log("Episode-level volume covariate — significance test:")
    ep_fit_base = _fit_weibull(ep_gaps_np, ep_rec_weights)
    ep_fit_vol = _fit_weibull(ep_gaps_np, ep_rec_weights, ep_volumes_np, with_volume=True)
    ep_lr_stat = 2 * (ep_fit_base["neg_loglik"] - ep_fit_vol["neg_loglik"])
    ep_mle_beta = ep_fit_vol["beta"]
    log(f"  Baseline: shape={ep_fit_base['shape']:.4f}  scale={ep_fit_base['scale']:.4f}")
    log(f"  +Volume:  shape={ep_fit_vol['shape']:.4f}  scale={ep_fit_vol['scale']:.4f}  "
        f"beta={ep_mle_beta:.4f}")
    log(f"  LR statistic: {ep_lr_stat:.3f} (>3.84 for p<0.05)")
    log(f"  Volume {'significant' if ep_lr_stat > 3.84 else 'not significant'} at p<0.05")
    log()

    # Episode-level volume covariate: walk-forward with day-part split.
    # Sweep beta values at the best half-life (168h) to test the current
    # scalar AFT overlay: effective_scale = base_scale * exp(beta * volume_oz).
    log("Episode-level volume walk-forward (tested AFT overlay, day-part split, half-life=168h):")
    best_half_life = 168
    ep_decay_vol = math.log(2) / best_half_life

    for beta_test in [0.0, 0.03, 0.06, ep_mle_beta, 0.12, 0.15]:
        vol_g1_errors, vol_g1_weights = [], []
        vol_fc_errors, vol_fc_weights = [], []

        for i in range(MIN_FIT_GAPS + 1, len(episode_events) - 1):
            event = episode_events[i]
            h = hour_of_day(event.time)
            is_night = h >= 20 or h < 8
            shape_i = ep_k_night if is_night else ep_k_day
            lookback_start = event.time - timedelta(days=LOOKBACK_DAYS)

            # Same-daypart scale estimation (no volume in scale fit).
            fit_g, fit_w = [], []
            for j in range(i):
                if episode_events[j].time < lookback_start:
                    continue
                if j + 1 > i:
                    break
                hj = hour_of_day(episode_events[j].time)
                j_night = hj >= 20 or hj < 8
                if j_night != is_night:
                    continue
                g = (episode_events[j + 1].time - episode_events[j].time).total_seconds() / 3600
                if g <= 0:
                    continue
                age = (event.time - episode_events[j].time).total_seconds() / 3600
                fit_w.append(math.exp(-ep_decay_vol * max(age, 0)))
                fit_g.append(g)

            if len(fit_g) < 3:
                fit_g, fit_w = [], []
                for j in range(i):
                    if episode_events[j].time < lookback_start:
                        continue
                    if j + 1 > i:
                        break
                    g = (episode_events[j + 1].time - episode_events[j].time).total_seconds() / 3600
                    if g <= 0:
                        continue
                    age = (event.time - episode_events[j].time).total_seconds() / 3600
                    fit_w.append(math.exp(-ep_decay_vol * max(age, 0)))
                    fit_g.append(g)

            if len(fit_g) < MIN_FIT_GAPS:
                continue

            base_scale = _estimate_scale_closed_form(
                np.array(fit_g), np.array(fit_w), shape_i,
            )
            # AFT volume adjustment on the current episode's volume.
            effective_scale = base_scale * math.exp(beta_test * event.volume_oz)
            predicted = _weibull_median(shape_i, effective_scale)
            actual = (episode_events[i + 1].time - event.time).total_seconds() / 3600
            age_from_cutoff = (cutoff - event.time).total_seconds() / 3600
            w = math.exp(-ep_decay_vol * max(age_from_cutoff, 0))

            vol_g1_errors.append(abs(predicted - actual))
            vol_g1_weights.append(w)

            # Both daypart scales for feed count sim.
            night_g, night_w, day_g, day_w = [], [], [], []
            for j in range(i):
                if episode_events[j].time < lookback_start or j + 1 > i:
                    continue
                g = (episode_events[j + 1].time - episode_events[j].time).total_seconds() / 3600
                if g <= 0:
                    continue
                age = (event.time - episode_events[j].time).total_seconds() / 3600
                wt = math.exp(-ep_decay_vol * max(age, 0))
                hj = hour_of_day(episode_events[j].time)
                if hj >= 20 or hj < 8:
                    night_g.append(g)
                    night_w.append(wt)
                else:
                    day_g.append(g)
                    day_w.append(wt)

            all_g_v = np.array(night_g + day_g) if night_g or day_g else np.array(fit_g)
            all_w_v = np.array(night_w + day_w) if night_w or day_w else np.array(fit_w)
            n_scale = (
                _estimate_scale(np.array(night_g), np.array(night_w), ep_k_night)
                if len(night_g) >= 3
                else _estimate_scale(all_g_v, all_w_v, ep_k_night)
            )
            d_scale = (
                _estimate_scale(np.array(day_g), np.array(day_w), ep_k_day)
                if len(day_g) >= 3
                else _estimate_scale(all_g_v, all_w_v, ep_k_day)
            )

            sim_vol = float(np.median([e.volume_oz for e in episode_events[max(0, i - 10):i + 1]]))
            sim_t = 0.0
            sim_count = 0
            while sim_t < 24.0:
                sh = (h + sim_t) % 24.0
                sn = sh >= 20 or sh < 8
                s_shape = ep_k_night if sn else ep_k_day
                s_base = n_scale if sn else d_scale
                s_eff = s_base * math.exp(beta_test * sim_vol)
                gap = _weibull_median(s_shape, s_eff)
                sim_t += gap
                if sim_t < 24.0:
                    sim_count += 1
            actual_count = sum(
                1 for e in episode_events[i + 1:]
                if e.time <= event.time + timedelta(hours=24)
            )
            vol_fc_errors.append(abs(sim_count - actual_count))
            vol_fc_weights.append(w)

        vol_g1 = float(np.average(vol_g1_errors, weights=vol_g1_weights)) if vol_g1_errors else float("nan")
        vol_fc = float(np.average(vol_fc_errors, weights=vol_fc_weights)) if vol_fc_errors else float("nan")
        mle_marker = " <-- MLE" if abs(beta_test - ep_mle_beta) < 0.001 else ""
        base_marker = " <-- no-volume baseline" if beta_test == 0.0 else ""
        log(f"  beta={beta_test:.3f}: gap1_MAE={vol_g1:.3f}h  "
            f"fcount_MAE={vol_fc:.2f}  (n={len(vol_g1_errors)})"
            f"{mle_marker}{base_marker}")
    log()
    log("Conclusion: the tested scalar AFT volume overlay is not shipped.")
    log("The LR signal is real, but this formulation worsens walk-forward")
    log("accuracy at every positive beta. This rejects the current overlay,")
    log("not every possible future use of volume.")
    log()

    # ================================================================
    # SECTION 10: Breastfeed merge policy comparison
    # ================================================================
    log("=== BREASTFEED MERGE POLICY COMPARISON ===")
    log()
    log("The clustering rule's 80-minute extension arm checks the later")
    log("feed's volume_oz. Breastfeed merge increases a bottle event's")
    log("volume, which could change episode boundaries. This section")
    log("compares bottle-only vs breastfeed-merged episode structures.")
    log()

    merged_episode_events = episodes_as_events(events_merged)
    log(f"Bottle-only episodes: {len(episode_events)}")
    log(f"Merged episodes:      {len(merged_episode_events)}")
    log(f"Episode count differs: {'YES' if len(episode_events) != len(merged_episode_events) else 'no'}")
    log()

    # Compare episode boundaries.
    boundary_diffs = 0
    volume_diffs = 0
    min_len = min(len(episode_events), len(merged_episode_events))
    for idx in range(min_len):
        bo = episode_events[idx]
        mg = merged_episode_events[idx]
        if bo.time != mg.time:
            boundary_diffs += 1
            if boundary_diffs <= 5:
                log(f"  Boundary diff at idx {idx}: "
                    f"bottle={bo.time.strftime('%m/%d %H:%M')} "
                    f"merged={mg.time.strftime('%m/%d %H:%M')}")
        if abs(bo.volume_oz - mg.volume_oz) > 0.01:
            volume_diffs += 1

    log(f"Boundary differences: {boundary_diffs}")
    log(f"Volume differences: {volume_diffs} (out of {min_len} episodes)")
    log()

    if boundary_diffs > 0 or len(episode_events) != len(merged_episode_events):
        # Episode structures differ — run full comparison.
        mg_gaps = []
        mg_hours = []
        for i in range(len(merged_episode_events) - 1):
            gap = (merged_episode_events[i + 1].time - merged_episode_events[i].time).total_seconds() / 3600
            mg_gaps.append(gap)
            mg_hours.append(hour_of_day(merged_episode_events[i].time))

        mg_gaps_np = np.array(mg_gaps)
        mg_hours_np = np.array(mg_hours)
        mg_night = (mg_hours_np >= 20) | (mg_hours_np < 8)
        mg_day = ~mg_night
        mg_rec_w = np.array([
            math.exp(-decay * (cutoff - merged_episode_events[i].time).total_seconds() / 3600)
            for i in range(len(mg_gaps_np))
        ])

        log("Merged-input episode Weibull fits by day-part:")
        for label, mask in [("Overnight", mg_night), ("Daytime", mg_day)]:
            if mask.sum() >= MIN_FIT_GAPS:
                fit_dp = _fit_weibull(mg_gaps_np[mask], mg_rec_w[mask])
                log(f"  {label} (n={mask.sum()}): shape={fit_dp['shape']:.4f}  "
                    f"scale={fit_dp['scale']:.4f}  "
                    f"median={_weibull_median(fit_dp['shape'], fit_dp['scale']):.3f}h")
        log()
    else:
        log("Episode boundaries are identical. Merge policy does not affect")
        log("episode structure on this dataset. Only episode volumes differ,")
        log("and this model is volume-free, so merge policy has no effect on")
        log("Survival Hazard predictions.")
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

    canonical = score_model(
        "survival_hazard",
        export_path=snapshot.export_path,
        parallel=True,
    )
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
    # Sweep OVERNIGHT_SHAPE and DAYTIME_SHAPE jointly. These are the
    # key structural parameters; scale is estimated at runtime.
    #
    # The original 8x5 grid (4.0-8.0 overnight, 2.0-4.0 daytime) hit the
    # lowest-tested corner, so the canonical sweep now uses a wider,
    # mixed-resolution grid with extra density around the replay-favored
    # region. This keeps the research artifact self-contained: the final
    # recommendation is reproducible from one recorded sweep, not from
    # ad hoc follow-up probes.
    log("=== CANONICAL PARAMETER TUNING ===")
    log()
    log("Sweeps OVERNIGHT_SHAPE and DAYTIME_SHAPE via tune_model")
    log("(multi-window canonical scoring). Scale is runtime-estimated.")
    log()

    tune_result = tune_model(
        "survival_hazard",
        candidates_by_name={
            "OVERNIGHT_SHAPE": [
                3.0, 3.5, 4.0, 4.25, 4.5, 4.75, 5.0,
                5.25, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0,
            ],
            "DAYTIME_SHAPE": [
                1.0, 1.25, 1.5, 1.625, 1.75, 1.875, 2.0,
                2.5, 3.0, 3.5, 4.0,
            ],
        },
        export_path=snapshot.export_path,
        parallel=True,
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
    log("--- Raw pre-episode baseline ---")
    log(f"  Overnight shape: {k_night:.4f}  Daytime shape: {k_day:.4f}")
    log(f"  Day-part walk-forward gap1_MAE: {daypart_g1:.3f}h")
    log(f"  Volume significant: {'yes' if lr_stat > 3.84 else 'no'} (LR={lr_stat:.3f})")
    log()
    log("--- Episode-level MLE (descriptive fit) ---")
    if "overnight" in ep_fit_results and "daytime" in ep_fit_results:
        log(f"  Overnight shape: {ep_fit_results['overnight']['shape']:.4f}  "
            f"Daytime shape: {ep_fit_results['daytime']['shape']:.4f}")
    log(f"  Volume significant: {'yes' if ep_lr_stat > 3.84 else 'no'} "
        f"(LR={ep_lr_stat:.3f})")
    log()
    log("--- Canonical replay tuning ---")
    log(f"  Baseline headline: {bl_agg['headline']:.3f}")
    log(f"  Best headline:     {be_agg['headline']:.3f}")
    log(f"  Baseline params:   {bl['params']}")
    log(f"  Best params:       {be['params']}")
    log()
    log("Model implementation uses:")
    log(f"  Episode-level history via episodes_as_events()")
    log(f"  Day-part split Weibull")
    log(f"    Adopted shapes: overnight={OVERNIGHT_SHAPE}, daytime={DAYTIME_SHAPE}")
    log(f"    (canonical replay-selected; episode-level MLE differs — see design.md)")
    log(f"  Scale estimated at runtime from same-daypart episode gaps")
    log(f"  Recency half-life: {RECENCY_HALF_LIFE_HOURS}h")
    log(f"  Median survival time for point predictions")
    log(f"  Conditional survival for first predicted feed")
    log(f"  Bottle-only events (no breastfeed merge)")

    # Save results.
    artifacts_dir = OUTPUT_DIR / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    results_path = artifacts_dir / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
