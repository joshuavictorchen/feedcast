"""Analog Trajectory research: sweep tunable parameters and find best configuration.

Run from the repo root:
    .venv/bin/python -m feedcast.models.analog_trajectory.research

This script jointly sweeps all tunable parameters (lookback window,
feature weights, K neighbors, recency half-life, trajectory length
method, alignment method) using leave-one-out evaluation on the
historical state library.

All parameters are searched in a single joint grid — no staged
optimization — so the result is the best configuration found in the
full search space (subject to the grid resolution).

Normalization is fold-causal: each test state is normalized using only
states that precede it in time, so no future information leaks into the
distance computation.

Update the constants in model.py to match the top configuration after
reviewing results.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np

from feedcast.clustering import episodes_as_events
from feedcast.data import (
    build_feed_events,
    load_export_snapshot,
)
from feedcast.models.analog_trajectory.model import (
    MIN_PRIOR_EVENTS,
    TRAJECTORY_COMPLETENESS_HOURS,
    _state_features,
)

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent

# --- Parameter grids ---

LOOKBACK_HOURS_GRID = [12, 24, 48, 72]

WEIGHT_PROFILES: dict[str, list[float]] = {
    # last_gap, mean_gap, last_volume, mean_volume, sin_hour, cos_hour
    "equal":          [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    "gap_emphasis":   [2.0, 2.0, 1.0, 1.0, 1.0, 1.0],
    "hour_emphasis":  [1.0, 1.0, 1.0, 1.0, 2.0, 2.0],
    "vol_deemphasis": [1.0, 1.0, 0.5, 0.5, 1.0, 1.0],
    "gap_hour":       [2.0, 2.0, 0.5, 0.5, 2.0, 2.0],
    "recent_only":    [2.0, 0.5, 2.0, 0.5, 1.0, 1.0],
    "means_only":     [0.5, 2.0, 0.5, 2.0, 1.0, 1.0],
}

K_GRID = [3, 5, 7]

RECENCY_HALF_LIFE_GRID = [36, 72, 120, 240]

TRAJECTORY_LENGTH_METHODS = ["median", "mean"]

# Minimum test index: need enough prior complete states for meaningful evaluation.
MIN_TEST_INDEX = 10


def main() -> None:
    """Run the joint parameter sweep."""
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
    log(f"Total bottle events: {len(events)}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log()

    # --- Build raw state library (features computed per-lookback below) ---
    raw_states: list[dict] = []
    incomplete_count = 0
    for index in range(MIN_PRIOR_EVENTS, len(events)):
        event = events[index]
        if event.time > cutoff:
            break
        future_end = event.time + timedelta(hours=24)
        future_events = [e for e in events[index + 1 :] if e.time <= future_end]
        has_late_event = any(
            e.time >= event.time + timedelta(hours=TRAJECTORY_COMPLETENESS_HOURS)
            for e in events[index + 1 :]
        )
        if has_late_event and len(future_events) >= 3:
            raw_states.append(
                {
                    "index": index,
                    "time": event.time,
                    "future_events": future_events,
                    "future_count": len(future_events),
                }
            )
        else:
            incomplete_count += 1

    log("=== STATE LIBRARY ===")
    log()
    log(f"Complete states: {len(raw_states)}")
    log(f"Incomplete states: {incomplete_count}")
    log()

    # Pre-compute feature matrices for each lookback window.
    feature_matrices: dict[int, np.ndarray] = {}
    for lookback in LOOKBACK_HOURS_GRID:
        feature_matrices[lookback] = np.array(
            [_state_features(events, s["index"], lookback) for s in raw_states]
        )

    # --- Feature statistics ---
    feature_names = [
        "last_gap", "mean_gap", "last_volume", "mean_volume",
        "sin_hour", "cos_hour",
    ]
    for lookback in LOOKBACK_HOURS_GRID:
        matrix = feature_matrices[lookback]
        log(f"=== FEATURE STATISTICS ({lookback}h lookback) ===")
        log()
        log(f"{'Feature':<15} {'Mean':>8} {'Std':>8} {'Min':>8} {'Max':>8}")
        for i, name in enumerate(feature_names):
            col = matrix[:, i]
            log(
                f"{name:<15} {col.mean():>8.3f} {col.std():>8.3f} "
                f"{col.min():>8.3f} {col.max():>8.3f}"
            )
        log()

    # ===================================================================
    # JOINT PARAMETER SWEEP
    # All parameters searched in a single grid. The objective is
    # full_traj_MAE from _evaluate_blending (fold-causal, full
    # trajectories, matching production behavior).
    # ===================================================================
    total_configs = (
        len(LOOKBACK_HOURS_GRID)
        * len(WEIGHT_PROFILES)
        * len(K_GRID)
        * len(RECENCY_HALF_LIFE_GRID)
        * len(TRAJECTORY_LENGTH_METHODS)
        * 2  # gap, time_offset
    )
    log(f"=== JOINT PARAMETER SWEEP ({total_configs} configurations) ===")
    log("(fold-causal normalization, full-trajectory evaluation)")
    log()

    results: list[dict] = []

    for lookback in LOOKBACK_HOURS_GRID:
        feat_matrix = feature_matrices[lookback]

        for weight_name, weight_values in WEIGHT_PROFILES.items():
            for k in K_GRID:
                for half_life in RECENCY_HALF_LIFE_GRID:
                    for traj_method in TRAJECTORY_LENGTH_METHODS:
                        for alignment in ["gap", "time_offset"]:
                            mae, n = _evaluate_blending(
                                raw_states, feat_matrix, weight_values,
                                k, half_life, traj_method, alignment,
                            )
                            results.append(
                                {
                                    "lookback": lookback,
                                    "weights_name": weight_name,
                                    "weights": weight_values,
                                    "k": k,
                                    "half_life": half_life,
                                    "traj_method": traj_method,
                                    "alignment": alignment,
                                    "full_traj_mae": mae,
                                    "n": n,
                                }
                            )

    results.sort(key=lambda r: r["full_traj_mae"])

    # Print top 20 configurations.
    log("Top 20 configurations by full_traj_MAE:")
    log()
    for rank, r in enumerate(results[:20], 1):
        log(
            f"{rank:>3}. lb={r['lookback']:>2}h  w={r['weights_name']:<15s}  "
            f"k={r['k']}  hl={r['half_life']:>3}h  "
            f"len={r['traj_method']:<7s}  align={r['alignment']:<12s}  "
            f"full_traj_MAE={r['full_traj_mae']:.3f}h  (n={r['n']})"
        )
    log()

    # ===================================================================
    # BEST CONFIGURATION
    # ===================================================================
    best = results[0]

    # Also compute gap1/traj3 for the best config (reference metrics).
    best_feat = feature_matrices[best["lookback"]]
    gap1_mae, traj3_mae, n = _evaluate_neighbors(
        raw_states, best_feat, best["weights"], best["k"],
        recency_half_life=best["half_life"],
    )

    log("=== BEST CONFIGURATION ===")
    log()
    log(f"LOOKBACK_HOURS = {best['lookback']}")
    log(f"FEATURE_WEIGHTS = {best['weights']}  # {best['weights_name']}")
    log(f"K_NEIGHBORS = {best['k']}")
    log(f"RECENCY_HALF_LIFE_HOURS = {best['half_life']}")
    log(f"TRAJECTORY_LENGTH_METHOD = \"{best['traj_method']}\"")
    log(f"Alignment = {best['alignment']}")
    log()
    log(f"full_traj MAE = {best['full_traj_mae']:.3f}h")
    log(f"traj3 MAE     = {traj3_mae:.3f}h")
    log(f"gap1 MAE      = {gap1_mae:.3f}h")
    log()

    # ===================================================================
    # DIAGNOSTICS: Recent state neighbor quality with best config
    # Uses fold-causal normalization per test state.
    # ===================================================================
    log("=== RECENT STATE NEIGHBOR QUALITY (last 10 complete states) ===")
    log()

    best_lookback = best["lookback"]
    sqrt_weights = np.sqrt(np.array(best["weights"]))
    decay = np.log(2) / best["half_life"]

    for test_offset in range(-10, 0):
        abs_idx = len(raw_states) + test_offset
        test_state = raw_states[abs_idx]

        # Fold-causal normalization: only use states before this one.
        train_features = best_feat[:abs_idx]
        means = train_features.mean(axis=0)
        stds = train_features.std(axis=0)
        stds[stds == 0] = 1.0

        train_normed = (train_features - means) / stds * sqrt_weights
        test_normed = (best_feat[abs_idx] - means) / stds * sqrt_weights

        candidates = [
            (i, float(np.linalg.norm(test_normed - train_normed[i])))
            for i in range(abs_idx)
        ]
        candidates.sort(key=lambda x: x[1])
        nearest = candidates[: best["k"]]

        actual_gaps: list[float] = []
        for j in range(min(3, len(test_state["future_events"]))):
            prev = (
                test_state["time"]
                if j == 0
                else test_state["future_events"][j - 1].time
            )
            actual_gaps.append(
                (test_state["future_events"][j].time - prev).total_seconds() / 3600
            )

        nn_trajs: list[list[float]] = []
        nn_weights: list[float] = []
        for ni, nd in nearest:
            ns = raw_states[ni]
            ng: list[float] = []
            for j in range(min(3, len(ns["future_events"]))):
                prev = (
                    ns["time"] if j == 0 else ns["future_events"][j - 1].time
                )
                ng.append(
                    (ns["future_events"][j].time - prev).total_seconds() / 3600
                )
            if len(ng) == 3:
                nn_trajs.append(ng)
                age = (
                    test_state["time"] - ns["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                nn_weights.append(recency / (nd + 0.01))

        if nn_trajs and len(actual_gaps) == 3:
            avg_nn = np.average(nn_trajs, weights=nn_weights, axis=0)
            err = np.abs(np.array(actual_gaps) - avg_nn)
            log(
                f"State at {test_state['time'].strftime('%m/%d %H:%M')} "
                f"(gap={_state_features(events, test_state['index'], best_lookback)[0]:.1f}h "
                f"vol={events[test_state['index']].volume_oz:.1f}oz):"
            )
            log(f"  Actual gaps: {[f'{g:.2f}' for g in actual_gaps]}")
            log(f"  NN avg gaps: {[f'{g:.2f}' for g in avg_nn]}")
            log(f"  Abs errors:  {[f'{e:.2f}' for e in err]}")
            log(f"  Neighbors: {[f'd={nd:.2f}' for _, nd in nearest]}")
            log()

    # --- Episode-level comparison ---
    # Compare raw vs. episode state library and feature distributions.
    log(f"\n{'=' * 60}")
    log("EPISODE-LEVEL COMPARISON")
    log(f"{'=' * 60}")
    log()

    episode_events = episodes_as_events(events)
    log(f"Raw bottle events:   {len(events)}")
    log(f"Episode events:      {len(episode_events)}")
    log(f"Events collapsed:    {len(events) - len(episode_events)}")
    log()

    # Build episode state library for comparison.
    ep_states: list[dict] = []
    ep_incomplete = 0
    for index in range(MIN_PRIOR_EVENTS, len(episode_events)):
        event = episode_events[index]
        if event.time > cutoff:
            break
        future_end = event.time + timedelta(hours=24)
        future_events = [
            e for e in episode_events[index + 1:] if e.time <= future_end
        ]
        has_late = any(
            e.time >= event.time + timedelta(hours=TRAJECTORY_COMPLETENESS_HOURS)
            for e in episode_events[index + 1:]
        )
        if has_late and len(future_events) >= 3:
            ep_states.append({
                "index": index,
                "time": event.time,
                "future_events": future_events,
                "future_count": len(future_events),
            })
        else:
            ep_incomplete += 1

    log(f"Raw complete states:     {len(raw_states)}")
    log(f"Episode complete states:  {len(ep_states)}")
    log()

    # Feature statistics comparison for best lookback.
    best_lookback = best["lookback"]
    ep_feat_matrix = np.array(
        [_state_features(episode_events, s["index"], best_lookback)
         for s in ep_states]
    )
    raw_feat_matrix = feature_matrices[best_lookback]

    log(f"=== FEATURE COMPARISON ({best_lookback}h lookback, best config) ===")
    log()
    log(f"{'Feature':<15} {'Raw Mean':>10} {'Ep Mean':>10} {'Raw Std':>10} {'Ep Std':>10}")
    for i, name in enumerate(feature_names):
        raw_col = raw_feat_matrix[:, i]
        ep_col = ep_feat_matrix[:, i]
        log(
            f"{name:<15} {raw_col.mean():>10.3f} {ep_col.mean():>10.3f} "
            f"{raw_col.std():>10.3f} {ep_col.std():>10.3f}"
        )
    log()

    # Quick evaluation with best config on episode data.
    ep_gap1, ep_traj3, ep_n = _evaluate_neighbors(
        ep_states, ep_feat_matrix, best["weights"], best["k"],
        recency_half_life=best["half_life"],
    )
    log(f"Episode-level evaluation (best config, fold-causal):")
    log(f"  gap1 MAE  = {ep_gap1:.3f}h  (raw: {gap1_mae:.3f}h)")
    log(f"  traj3 MAE = {ep_traj3:.3f}h  (raw: {traj3_mae:.3f}h)")
    log(f"  n = {ep_n}  (raw: {n})")
    log()

    # Save results alongside the script.
    results_path = OUTPUT_DIR / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"Results saved to {results_path}")


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _evaluate_neighbors(
    states: list[dict],
    feat_matrix: np.ndarray,
    feature_weights: list[float],
    k: int,
    recency_half_life: float,
) -> tuple[float, float, int]:
    """Leave-one-out evaluation of neighbor retrieval accuracy.

    Normalization is fold-causal: for each test state at index i,
    mean/std are computed from states [0, i) only.

    Returns (gap1_mae, traj3_mae, n_test_cases).
    """
    decay = np.log(2) / recency_half_life
    sqrt_w = np.sqrt(np.array(feature_weights))
    errors_gap1: list[float] = []
    errors_traj3: list[float] = []

    for test_idx in range(MIN_TEST_INDEX, len(states)):
        test_state = states[test_idx]

        # Fold-causal normalization: stats from prior states only.
        train_features = feat_matrix[:test_idx]
        means = train_features.mean(axis=0)
        stds = train_features.std(axis=0)
        stds[stds == 0] = 1.0

        train_weighted = (train_features - means) / stds * sqrt_w
        test_weighted = (feat_matrix[test_idx] - means) / stds * sqrt_w

        # Find k nearest among earlier states.
        candidates = [
            (i, float(np.linalg.norm(test_weighted - train_weighted[i])))
            for i in range(test_idx)
        ]
        candidates.sort(key=lambda x: x[1])
        nearest = candidates[:k]

        # Gap1 error: weighted prediction of first gap.
        nn_gaps1: list[float] = []
        nn_weights: list[float] = []
        for ni, nd in nearest:
            ns = states[ni]
            if ns["future_events"]:
                gap = (
                    ns["future_events"][0].time - ns["time"]
                ).total_seconds() / 3600
                nn_gaps1.append(gap)
                age = (
                    test_state["time"] - ns["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                nn_weights.append(recency / (nd + 0.01))

        if nn_gaps1:
            pred = float(np.average(nn_gaps1, weights=nn_weights))
            actual = (
                test_state["future_events"][0].time - test_state["time"]
            ).total_seconds() / 3600
            errors_gap1.append(abs(actual - pred))

        # Traj3 error: weighted prediction of first 3 gaps.
        actual_gaps_3: list[float] = []
        for j in range(min(3, len(test_state["future_events"]))):
            prev = (
                test_state["time"]
                if j == 0
                else test_state["future_events"][j - 1].time
            )
            actual_gaps_3.append(
                (test_state["future_events"][j].time - prev).total_seconds()
                / 3600
            )

        nn_trajs: list[list[float]] = []
        nn_traj_weights: list[float] = []
        for ni, nd in nearest:
            ns = states[ni]
            ng: list[float] = []
            for j in range(min(3, len(ns["future_events"]))):
                prev = (
                    ns["time"] if j == 0 else ns["future_events"][j - 1].time
                )
                ng.append(
                    (ns["future_events"][j].time - prev).total_seconds() / 3600
                )
            if len(ng) == 3:
                nn_trajs.append(ng)
                age = (
                    test_state["time"] - ns["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                nn_traj_weights.append(recency / (nd + 0.01))

        if nn_trajs and len(actual_gaps_3) == 3:
            avg_nn = np.average(nn_trajs, weights=nn_traj_weights, axis=0)
            errors_traj3.append(
                float(np.mean(np.abs(np.array(actual_gaps_3) - avg_nn)))
            )

    gap1_mae = float(np.mean(errors_gap1)) if errors_gap1 else float("nan")
    traj3_mae = float(np.mean(errors_traj3)) if errors_traj3 else float("nan")
    return gap1_mae, traj3_mae, len(errors_gap1)


def _evaluate_blending(
    states: list[dict],
    feat_matrix: np.ndarray,
    feature_weights: list[float],
    k: int,
    recency_half_life: float,
    traj_length_method: str,
    alignment: str,
) -> tuple[float, int]:
    """Evaluate trajectory blending with different length/alignment methods.

    Uses full neighbor trajectories (not truncated to 3 steps) so the
    trajectory length method is exercised against realistic lengths.
    The forecast length is determined by the method (median or mean of
    full neighbor trajectory lengths), matching production behavior.

    Returns (full_traj_mae, n_test_cases).
    """
    decay = np.log(2) / recency_half_life
    sqrt_w = np.sqrt(np.array(feature_weights))
    errors: list[float] = []

    for test_idx in range(MIN_TEST_INDEX, len(states)):
        test_state = states[test_idx]

        # Fold-causal normalization.
        train_features = feat_matrix[:test_idx]
        means = train_features.mean(axis=0)
        stds = train_features.std(axis=0)
        stds[stds == 0] = 1.0

        train_weighted = (train_features - means) / stds * sqrt_w
        test_weighted = (feat_matrix[test_idx] - means) / stds * sqrt_w

        candidates = [
            (i, float(np.linalg.norm(test_weighted - train_weighted[i])))
            for i in range(test_idx)
        ]
        candidates.sort(key=lambda x: x[1])
        nearest = candidates[:k]

        # Build FULL trajectories from each neighbor (not truncated).
        nn_gap_trajs: list[list[float]] = []
        nn_offset_trajs: list[list[float]] = []
        nn_weights: list[float] = []
        for ni, nd in nearest:
            ns = states[ni]
            # Full gap trajectory.
            gaps: list[float] = []
            for j in range(len(ns["future_events"])):
                prev = (
                    ns["time"] if j == 0 else ns["future_events"][j - 1].time
                )
                gaps.append(
                    (ns["future_events"][j].time - prev).total_seconds() / 3600
                )
            # Full offset trajectory.
            offsets = [
                (fe.time - ns["time"]).total_seconds() / 3600
                for fe in ns["future_events"]
            ]
            if gaps:
                nn_gap_trajs.append(gaps)
                nn_offset_trajs.append(offsets)
                age = (
                    test_state["time"] - ns["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                nn_weights.append(recency / (nd + 0.01))

        if not nn_gap_trajs:
            continue

        # Determine forecast length from full trajectory lengths, matching
        # the production model's logic in _blend_trajectories.
        trajs = nn_gap_trajs if alignment == "gap" else nn_offset_trajs
        traj_lengths = [len(t) for t in trajs]
        if traj_length_method == "median":
            forecast_length = int(np.median(traj_lengths))
        else:
            forecast_length = int(np.mean(traj_lengths))

        # Actual trajectory (up to forecast_length steps).
        actual: list[float] = []
        for j in range(min(forecast_length, len(test_state["future_events"]))):
            if alignment == "gap":
                prev = (
                    test_state["time"]
                    if j == 0
                    else test_state["future_events"][j - 1].time
                )
                actual.append(
                    (test_state["future_events"][j].time - prev).total_seconds()
                    / 3600
                )
            else:
                actual.append(
                    (test_state["future_events"][j].time - test_state["time"]).total_seconds()
                    / 3600
                )

        if not actual:
            continue

        # Blend step by step with weighted average (matching production).
        step_errors: list[float] = []
        for step in range(len(actual)):
            step_vals: list[float] = []
            step_w: list[float] = []
            for traj_idx, traj in enumerate(trajs):
                if step < len(traj):
                    step_vals.append(traj[step])
                    step_w.append(nn_weights[traj_idx])
            if step_vals:
                pred = float(np.average(step_vals, weights=step_w))
                step_errors.append(abs(actual[step] - pred))

        if step_errors:
            errors.append(float(np.mean(step_errors)))

    mae = float(np.mean(errors)) if errors else float("nan")
    return mae, len(errors)


if __name__ == "__main__":
    main()
