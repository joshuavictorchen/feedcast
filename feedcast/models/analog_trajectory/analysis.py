"""Analog Trajectory research: diagnostics plus full canonical tuning.

Run from the repo root:
    .venv/bin/python -m feedcast.models.analog_trajectory.analysis

This script keeps the local retrieval/blending diagnostics that explain
how the analog model behaves, but production constants are selected by
full canonical multi-window replay via ``tune_model()``. History source
(``raw`` vs ``episode``), alignment, lookback, feature weights, recency,
neighbor count, and trajectory-length method all participate in the
canonical sweep.

Update ``model.py`` after reviewing results, then re-run this script so
the baseline matches production.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np

from feedcast.clustering import episodes_as_events
from feedcast.data import (
    FeedEvent,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.models.analog_trajectory.model import (
    ALIGNMENT,
    FEATURE_WEIGHTS,
    HISTORY_MODE,
    K_NEIGHBORS,
    LOOKBACK_HOURS,
    MIN_PRIOR_EVENTS,
    RECENCY_HALF_LIFE_HOURS,
    TRAJECTORY_COMPLETENESS_HOURS,
    TRAJECTORY_LENGTH_METHOD,
    _state_features,
)
from feedcast.replay import score_model, tune_model

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent

# --- Parameter grids ---

LOOKBACK_HOURS_GRID = [6, 9, 12, 18, 24, 48, 72]

WEIGHT_PROFILES: dict[str, list[float]] = {
    # last_gap, mean_gap, last_volume, mean_volume, sin_hour, cos_hour
    "equal": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    "gap_emphasis": [2.0, 2.0, 1.0, 1.0, 1.0, 1.0],
    "hour_emphasis": [1.0, 1.0, 1.0, 1.0, 2.0, 2.0],
    "vol_deemphasis": [1.0, 1.0, 0.5, 0.5, 1.0, 1.0],
    "gap_hour": [2.0, 2.0, 0.5, 0.5, 2.0, 2.0],
    "recent_only": [2.0, 0.5, 2.0, 0.5, 1.0, 1.0],
    "means_only": [0.5, 2.0, 0.5, 2.0, 1.0, 1.0],
}

K_GRID = [3, 5, 7]
RECENCY_HALF_LIFE_GRID = [36, 72, 120, 240]
TRAJECTORY_LENGTH_METHODS = ["median", "mean"]
ALIGNMENT_OPTIONS = ["gap", "time_offset"]
HISTORY_MODES = ["raw", "episode"]

# Minimum test index: need enough prior complete states for meaningful evaluation.
MIN_TEST_INDEX = 10

FEATURE_NAMES = [
    "last_gap",
    "mean_gap",
    "last_volume",
    "mean_volume",
    "sin_hour",
    "cos_hour",
]


def main() -> None:
    """Run the analog research workflow."""
    output_capture = StringIO()

    def log(text: str = "") -> None:
        print(text)
        output_capture.write(text + "\n")

    snapshot = load_export_snapshot()
    cutoff = snapshot.latest_activity_time
    raw_events = build_feed_events(snapshot.activities, merge_window_minutes=None)
    episode_events = episodes_as_events(raw_events)
    events_by_mode = {
        "raw": raw_events,
        "episode": episode_events,
    }

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Raw bottle events: {len(raw_events)}")
    log(f"Episode events: {len(episode_events)}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log()

    sweep_by_mode = {
        history_mode: _run_internal_sweep(events, cutoff, history_mode)
        for history_mode, events in events_by_mode.items()
    }

    log("=== STATE LIBRARY COMPARISON ===")
    log()
    log(f"{'Mode':<8} {'Events':>6} {'Complete':>9} {'Incomplete':>11}")
    for history_mode in HISTORY_MODES:
        sweep = sweep_by_mode[history_mode]
        log(
            f"{history_mode:<8} {len(sweep['events']):>6} "
            f"{len(sweep['states']):>9} {sweep['incomplete_count']:>11}"
        )
    log()

    for history_mode in HISTORY_MODES:
        _log_feature_statistics(log, sweep_by_mode[history_mode])
        _log_internal_sweep(log, sweep_by_mode[history_mode])

    log(f"\n{'=' * 60}")
    log("CANONICAL MULTI-WINDOW EVALUATION")
    log(f"{'=' * 60}")
    log()
    log("Production-constant evaluation via score_model (same")
    log("infrastructure as the replay CLI).")
    log()

    canonical = score_model("analog_trajectory", export_path=snapshot.export_path)
    rw = canonical["replay_windows"]
    agg = rw["aggregate"]
    log(
        f"Aggregate:  headline={agg['headline']:.1f}  count={agg['count']:.1f}  "
        f"timing={agg['timing']:.1f}"
    )
    log(
        f"Windows:    {rw['scored_window_count']} scored / {rw['window_count']} total "
        f"({rw['availability_ratio'] * 100:.1f}% availability)"
    )
    log(f"Half-life:  {rw['half_life_hours']}h  Lookback: {rw['lookback_hours']}h")
    log()
    _log_per_window_breakdown(log, rw)

    log(f"\n{'=' * 60}")
    log("CANONICAL PARAMETER TUNING")
    log(f"{'=' * 60}")
    log()
    log("Full canonical sweep via tune_model with candidate-parallel replay.")
    log(
        "History mode, alignment, lookback, feature weights, K, recency "
        "half-life, and trajectory-length method all participate."
    )
    log()

    tune_result = tune_model(
        "analog_trajectory",
        candidates_by_name={
            "LOOKBACK_HOURS": LOOKBACK_HOURS_GRID,
            "FEATURE_WEIGHTS": list(WEIGHT_PROFILES.values()),
            "K_NEIGHBORS": K_GRID,
            "RECENCY_HALF_LIFE_HOURS": RECENCY_HALF_LIFE_GRID,
            "TRAJECTORY_LENGTH_METHOD": TRAJECTORY_LENGTH_METHODS,
            "ALIGNMENT": ALIGNMENT_OPTIONS,
            "HISTORY_MODE": HISTORY_MODES,
        },
        export_path=snapshot.export_path,
        parallel_candidates=True,
    )
    _log_canonical_tuning(log, tune_result)

    best_params = tune_result["best"]["params"]
    best_history_mode = best_params["HISTORY_MODE"]
    best_internal = _find_internal_result(
        sweep_by_mode[best_history_mode]["results"], best_params
    )

    log(f"\n{'=' * 60}")
    log("RAW VS EPISODE COMPARISON")
    log(f"{'=' * 60}")
    log()
    best_lookback = int(best_params["LOOKBACK_HOURS"])
    log(
        f"Feature comparison at canonical-best lookback ({best_lookback}h) and "
        f"canonical-best history mode ({best_history_mode})."
    )
    log()
    log(
        f"{'Feature':<15} {'Raw Mean':>10} {'Ep Mean':>10} "
        f"{'Raw Std':>10} {'Ep Std':>10}"
    )
    raw_matrix = sweep_by_mode["raw"]["feature_matrices"][best_lookback]
    episode_matrix = sweep_by_mode["episode"]["feature_matrices"][best_lookback]
    for index, name in enumerate(FEATURE_NAMES):
        raw_col = raw_matrix[:, index]
        episode_col = episode_matrix[:, index]
        log(
            f"{name:<15} {raw_col.mean():>10.3f} {episode_col.mean():>10.3f} "
            f"{raw_col.std():>10.3f} {episode_col.std():>10.3f}"
        )
    log()
    for history_mode in HISTORY_MODES:
        sweep = sweep_by_mode[history_mode]
        internal_best = sweep["best"]
        canonical_best = _find_best_candidate_for_history(
            tune_result["candidates"], history_mode
        )
        canonical_agg = canonical_best["replay_windows"]["aggregate"]
        log(f"{history_mode.title()} history:")
        log(
            f"  Internal best: full_traj_MAE={internal_best['full_traj_mae']:.3f}h  "
            f"gap1={sweep['gap1_mae']:.3f}h  traj3={sweep['traj3_mae']:.3f}h  "
            f"config={_format_internal_config(internal_best)}"
        )
        log(
            f"  Canonical best: headline={canonical_agg['headline']:.1f}  "
            f"count={canonical_agg['count']:.1f}  timing={canonical_agg['timing']:.1f}  "
            f"config={_format_canonical_config(canonical_best['params'])}"
        )
        log()

    log(f"\n{'=' * 60}")
    log("RECENT STATE NEIGHBOR QUALITY")
    log(f"{'=' * 60}")
    log()
    log(
        "Last 10 complete states under the canonical-best configuration. "
        f"Internal full_traj_MAE for this config: {best_internal['full_traj_mae']:.3f}h."
    )
    log()
    _log_recent_state_neighbor_quality(
        log,
        sweep_by_mode[best_history_mode],
        best_params,
    )

    log(f"\n{'=' * 60}")
    log("FINAL SUMMARY")
    log(f"{'=' * 60}")
    log()
    log("--- Current production baseline ---")
    log(f"  history_mode = {HISTORY_MODE}")
    log(f"  lookback_hours = {LOOKBACK_HOURS}")
    log(
        f"  feature_weights = {_weight_profile_name(FEATURE_WEIGHTS)} "
        f"{_format_weight_values(FEATURE_WEIGHTS)}"
    )
    log(f"  k_neighbors = {K_NEIGHBORS}")
    log(f"  recency_half_life_hours = {RECENCY_HALF_LIFE_HOURS}")
    log(f"  trajectory_length_method = {TRAJECTORY_LENGTH_METHOD}")
    log(f"  alignment = {ALIGNMENT}")
    log()
    log("--- Canonical replay tuning ---")
    baseline = tune_result["baseline"]
    best = tune_result["best"]
    baseline_agg = baseline["replay_windows"]["aggregate"]
    best_agg = best["replay_windows"]["aggregate"]
    log(f"  Baseline headline: {baseline_agg['headline']:.3f}")
    log(f"  Best headline:     {best_agg['headline']:.3f}")
    log(f"  Baseline params:   {_format_canonical_config(baseline['params'])}")
    log(f"  Best params:       {_format_canonical_config(best['params'])}")
    log(f"  Headline delta:    {best['headline_delta']:+.3f}")
    log(f"  Availability delta:{best['availability_delta']:+d}")
    log()
    log("--- Internal diagnostic bests ---")
    for history_mode in HISTORY_MODES:
        sweep = sweep_by_mode[history_mode]
        log(
            f"  {history_mode}: full_traj_MAE={sweep['best']['full_traj_mae']:.3f}h  "
            f"gap1={sweep['gap1_mae']:.3f}h  traj3={sweep['traj3_mae']:.3f}h"
        )
    log()
    log("Production constants are selected by canonical multi-window replay.")
    log(
        "The internal full_traj_MAE sweeps remain diagnostic: they explain "
        "retrieval quality and history-mode tradeoffs, but they do not ship constants."
    )

    artifacts_dir = OUTPUT_DIR / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    results_path = artifacts_dir / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


def _run_internal_sweep(
    events: list[FeedEvent],
    cutoff: datetime,
    history_mode: str,
) -> dict[str, Any]:
    """Run the local retrieval/blending sweep for one history source."""
    states, incomplete_count = _build_state_library(events, cutoff)
    feature_matrices = {
        lookback: np.array(
            [_state_features(events, s["index"], lookback) for s in states]
        )
        for lookback in LOOKBACK_HOURS_GRID
    }

    results: list[dict[str, Any]] = []
    for lookback in LOOKBACK_HOURS_GRID:
        feat_matrix = feature_matrices[lookback]
        for weight_name, weight_values in WEIGHT_PROFILES.items():
            for k in K_GRID:
                for half_life in RECENCY_HALF_LIFE_GRID:
                    for traj_method in TRAJECTORY_LENGTH_METHODS:
                        for alignment in ALIGNMENT_OPTIONS:
                            mae, n_cases = _evaluate_blending(
                                states,
                                feat_matrix,
                                weight_values,
                                k,
                                half_life,
                                traj_method,
                                alignment,
                            )
                            results.append(
                                {
                                    "history_mode": history_mode,
                                    "lookback": lookback,
                                    "weights_name": weight_name,
                                    "weights": weight_values,
                                    "k": k,
                                    "half_life": half_life,
                                    "traj_method": traj_method,
                                    "alignment": alignment,
                                    "full_traj_mae": mae,
                                    "n": n_cases,
                                }
                            )
    results.sort(key=lambda result: result["full_traj_mae"])

    best = results[0]
    gap1_mae, traj3_mae, n_cases = _evaluate_neighbors(
        states,
        feature_matrices[best["lookback"]],
        best["weights"],
        best["k"],
        recency_half_life=best["half_life"],
    )
    return {
        "history_mode": history_mode,
        "events": events,
        "states": states,
        "incomplete_count": incomplete_count,
        "feature_matrices": feature_matrices,
        "results": results,
        "best": best,
        "gap1_mae": gap1_mae,
        "traj3_mae": traj3_mae,
        "n_cases": n_cases,
    }


def _build_state_library(
    events: list[FeedEvent],
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """Build complete states plus the incomplete-state count."""
    states: list[dict[str, Any]] = []
    incomplete_count = 0
    for index in range(MIN_PRIOR_EVENTS, len(events)):
        event = events[index]
        if event.time > cutoff:
            break

        future_end = event.time + timedelta(hours=24)
        future_events = [
            candidate
            for candidate in events[index + 1 :]
            if candidate.time <= future_end
        ]
        has_late_event = any(
            candidate.time
            >= event.time + timedelta(hours=TRAJECTORY_COMPLETENESS_HOURS)
            for candidate in events[index + 1 :]
        )
        if has_late_event and len(future_events) >= 3:
            states.append(
                {
                    "index": index,
                    "time": event.time,
                    "future_events": future_events,
                    "future_count": len(future_events),
                }
            )
        else:
            incomplete_count += 1
    return states, incomplete_count


def _log_feature_statistics(log, sweep: dict[str, Any]) -> None:
    """Write per-lookback feature statistics for one history mode."""
    history_mode = sweep["history_mode"]
    for lookback in LOOKBACK_HOURS_GRID:
        matrix = sweep["feature_matrices"][lookback]
        log(f"=== FEATURE STATISTICS ({history_mode}, {lookback}h lookback) ===")
        log()
        log(f"{'Feature':<15} {'Mean':>8} {'Std':>8} {'Min':>8} {'Max':>8}")
        for index, name in enumerate(FEATURE_NAMES):
            column = matrix[:, index]
            log(
                f"{name:<15} {column.mean():>8.3f} {column.std():>8.3f} "
                f"{column.min():>8.3f} {column.max():>8.3f}"
            )
        log()


def _log_internal_sweep(log, sweep: dict[str, Any]) -> None:
    """Write the diagnostic sweep summary for one history mode."""
    history_mode = sweep["history_mode"]
    total_configs = (
        len(LOOKBACK_HOURS_GRID)
        * len(WEIGHT_PROFILES)
        * len(K_GRID)
        * len(RECENCY_HALF_LIFE_GRID)
        * len(TRAJECTORY_LENGTH_METHODS)
        * len(ALIGNMENT_OPTIONS)
    )
    log(f"=== INTERNAL DIAGNOSTIC SWEEP ({history_mode}, {total_configs} configs) ===")
    log("(fold-causal normalization, full-trajectory evaluation)")
    log()
    log("Top 10 configurations by full_traj_MAE:")
    log()
    for rank, result in enumerate(sweep["results"][:10], 1):
        log(
            f"{rank:>3}. lb={result['lookback']:>2}h  "
            f"w={result['weights_name']:<15s}  "
            f"k={result['k']}  hl={result['half_life']:>3}h  "
            f"len={result['traj_method']:<7s}  align={result['alignment']:<12s}  "
            f"full_traj_MAE={result['full_traj_mae']:.3f}h  (n={result['n']})"
        )
    log()
    best = sweep["best"]
    log("Best diagnostic configuration:")
    log(f"  LOOKBACK_HOURS = {best['lookback']}")
    log(f"  FEATURE_WEIGHTS = {best['weights']}  # {best['weights_name']}")
    log(f"  K_NEIGHBORS = {best['k']}")
    log(f"  RECENCY_HALF_LIFE_HOURS = {best['half_life']}")
    log(f"  TRAJECTORY_LENGTH_METHOD = {best['traj_method']}")
    log(f"  ALIGNMENT = {best['alignment']}")
    log(f"  HISTORY_MODE = {history_mode}")
    log(f"  full_traj_MAE = {best['full_traj_mae']:.3f}h")
    log(f"  traj3_MAE = {sweep['traj3_mae']:.3f}h")
    log(f"  gap1_MAE = {sweep['gap1_mae']:.3f}h")
    log()


def _log_per_window_breakdown(log, replay_windows: dict[str, Any]) -> None:
    """Write canonical per-window metrics."""
    log("Per-window breakdown:")
    log(f"  {'Cutoff':<22} {'Weight':>7} {'Head':>7} {'Count':>7} {'Time':>7}  Status")
    for window in replay_windows["per_window"]:
        if window["score"] is not None:
            score = window["score"]
            log(
                f"  {window['cutoff']:<22} {window['weight']:>7.4f} "
                f"{score['headline']:>7.1f} {score['count']:>7.1f} "
                f"{score['timing']:>7.1f}  {window['status']}"
            )
        else:
            log(
                f"  {window['cutoff']:<22} {window['weight']:>7.4f} "
                f"{'--':>7} {'--':>7} {'--':>7}  {window['status']}"
            )
    log()


def _log_canonical_tuning(log, tune_result: dict[str, Any]) -> None:
    """Write the canonical tuning summary."""
    baseline = tune_result["baseline"]
    best = tune_result["best"]
    baseline_agg = baseline["replay_windows"]["aggregate"]
    best_agg = best["replay_windows"]["aggregate"]
    log(f"Candidates evaluated: {tune_result['search']['evaluated']}")
    log()
    log(f"{'':20} {'Headline':>8} {'Count':>7} {'Timing':>7} {'Windows':>8}")
    log(
        f"{'Baseline':<20} {baseline_agg['headline']:>8.1f} "
        f"{baseline_agg['count']:>7.1f} {baseline_agg['timing']:>7.1f} "
        f"{baseline['replay_windows']['scored_window_count']:>4}/"
        f"{baseline['replay_windows']['window_count']}"
    )
    log(
        f"{'Best':<20} {best_agg['headline']:>8.1f} "
        f"{best_agg['count']:>7.1f} {best_agg['timing']:>7.1f} "
        f"{best['replay_windows']['scored_window_count']:>4}/"
        f"{best['replay_windows']['window_count']}"
    )
    log()
    log(f"Baseline params: {baseline['params']}")
    log(f"Best params:     {best['params']}")
    log(f"Headline delta:  {best['headline_delta']:+.3f}")
    log(f"Availability delta: {best['availability_delta']:+d}")
    log()
    log("Top 10 candidates:")
    for rank, candidate in enumerate(tune_result["candidates"][:10], 1):
        aggregate = candidate["replay_windows"]["aggregate"]
        log(
            f"  {rank}. {candidate['params']}  headline={aggregate['headline']:.1f}  "
            f"count={aggregate['count']:.1f}  timing={aggregate['timing']:.1f}"
        )
    log()
    log("Best candidate by history mode:")
    for history_mode in HISTORY_MODES:
        candidate = _find_best_candidate_for_history(
            tune_result["candidates"], history_mode
        )
        aggregate = candidate["replay_windows"]["aggregate"]
        log(
            f"  {history_mode}: headline={aggregate['headline']:.1f}  "
            f"count={aggregate['count']:.1f}  timing={aggregate['timing']:.1f}  "
            f"{_format_canonical_config(candidate['params'])}"
        )
    log()


def _log_recent_state_neighbor_quality(
    log,
    sweep: dict[str, Any],
    params: dict[str, Any],
) -> None:
    """Write last-10-state neighbor diagnostics for one configuration."""
    states = sweep["states"]
    events = sweep["events"]
    lookback = int(params["LOOKBACK_HOURS"])
    feature_weights = params["FEATURE_WEIGHTS"]
    k_neighbors = int(params["K_NEIGHBORS"])
    recency_half_life = float(params["RECENCY_HALF_LIFE_HOURS"])
    feat_matrix = sweep["feature_matrices"][lookback]
    sqrt_weights = np.sqrt(np.array(feature_weights, dtype=float))
    decay = np.log(2) / recency_half_life

    for test_offset in range(-10, 0):
        abs_index = len(states) + test_offset
        test_state = states[abs_index]
        train_features = feat_matrix[:abs_index]
        means = train_features.mean(axis=0)
        stds = train_features.std(axis=0)
        stds[stds == 0] = 1.0

        train_normed = (train_features - means) / stds * sqrt_weights
        test_normed = (feat_matrix[abs_index] - means) / stds * sqrt_weights

        candidates = [
            (index, float(np.linalg.norm(test_normed - train_normed[index])))
            for index in range(abs_index)
        ]
        candidates.sort(key=lambda item: item[1])
        nearest = candidates[:k_neighbors]

        actual_gaps: list[float] = []
        for step in range(min(3, len(test_state["future_events"]))):
            previous = (
                test_state["time"]
                if step == 0
                else test_state["future_events"][step - 1].time
            )
            actual_gaps.append(
                (test_state["future_events"][step].time - previous).total_seconds()
                / 3600
            )

        neighbor_trajectories: list[list[float]] = []
        neighbor_weights: list[float] = []
        for neighbor_index, neighbor_distance in nearest:
            neighbor_state = states[neighbor_index]
            gaps: list[float] = []
            for step in range(min(3, len(neighbor_state["future_events"]))):
                previous = (
                    neighbor_state["time"]
                    if step == 0
                    else neighbor_state["future_events"][step - 1].time
                )
                gaps.append(
                    (
                        neighbor_state["future_events"][step].time - previous
                    ).total_seconds()
                    / 3600
                )
            if len(gaps) == 3:
                neighbor_trajectories.append(gaps)
                age_hours = (
                    test_state["time"] - neighbor_state["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age_hours, 0)))
                neighbor_weights.append(recency / (neighbor_distance + 0.01))

        if neighbor_trajectories and len(actual_gaps) == 3:
            average_neighbor = np.average(
                neighbor_trajectories, weights=neighbor_weights, axis=0
            )
            error = np.abs(np.array(actual_gaps) - average_neighbor)
            log(
                f"State at {test_state['time'].strftime('%m/%d %H:%M')} "
                f"(gap={_state_features(events, test_state['index'], lookback)[0]:.1f}h "
                f"vol={events[test_state['index']].volume_oz:.1f}oz):"
            )
            log(f"  Actual gaps: {[f'{gap:.2f}' for gap in actual_gaps]}")
            log(f"  NN avg gaps: {[f'{gap:.2f}' for gap in average_neighbor]}")
            log(f"  Abs errors:  {[f'{gap:.2f}' for gap in error]}")
            log(f"  Neighbors: {[f'd={distance:.2f}' for _, distance in nearest]}")
            log()


def _find_internal_result(
    results: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Return the internal-sweep row matching a canonical candidate."""
    target_weights = np.array(params["FEATURE_WEIGHTS"], dtype=float)
    for rank, result in enumerate(results, 1):
        if (
            result["lookback"] == params["LOOKBACK_HOURS"]
            and np.allclose(result["weights"], target_weights)
            and result["k"] == params["K_NEIGHBORS"]
            and result["half_life"] == params["RECENCY_HALF_LIFE_HOURS"]
            and result["traj_method"] == params["TRAJECTORY_LENGTH_METHOD"]
            and result["alignment"] == params["ALIGNMENT"]
        ):
            enriched = dict(result)
            enriched["rank"] = rank
            return enriched
    raise ValueError(f"No matching internal result found for params {params!r}.")


def _find_best_candidate_for_history(
    candidates: list[dict[str, Any]],
    history_mode: str,
) -> dict[str, Any]:
    """Return the highest-ranked canonical candidate for one history mode."""
    for candidate in candidates:
        if candidate["params"]["HISTORY_MODE"] == history_mode:
            return candidate
    raise ValueError(f"No canonical candidate found for history mode {history_mode!r}.")


def _format_internal_config(config: dict[str, Any]) -> str:
    """Return a compact string for one internal-sweep configuration."""
    return (
        f"lb={config['lookback']}h, "
        f"w={config['weights_name']}, "
        f"k={config['k']}, "
        f"hl={config['half_life']}h, "
        f"len={config['traj_method']}, "
        f"align={config['alignment']}"
    )


def _format_canonical_config(params: dict[str, Any]) -> str:
    """Return a compact string for one canonical candidate."""
    return (
        f"history={params['HISTORY_MODE']}, "
        f"lb={params['LOOKBACK_HOURS']}h, "
        f"w={_weight_profile_name(params['FEATURE_WEIGHTS'])}, "
        f"k={params['K_NEIGHBORS']}, "
        f"hl={params['RECENCY_HALF_LIFE_HOURS']}h, "
        f"len={params['TRAJECTORY_LENGTH_METHOD']}, "
        f"align={params['ALIGNMENT']}"
    )


def _weight_profile_name(weights: Any) -> str:
    """Return the profile label for a weight vector."""
    candidate = np.array(weights, dtype=float)
    for name, values in WEIGHT_PROFILES.items():
        if np.allclose(candidate, np.array(values, dtype=float)):
            return name
    return "custom"


def _format_weight_values(weights: Any) -> list[float]:
    """Return one weight vector as plain Python floats for logging."""
    return [float(value) for value in np.array(weights, dtype=float)]


def _evaluate_neighbors(
    states: list[dict[str, Any]],
    feat_matrix: np.ndarray,
    feature_weights: list[float],
    k: int,
    recency_half_life: float,
) -> tuple[float, float, int]:
    """Leave-one-out evaluation of neighbor retrieval accuracy.

    Normalization is fold-causal: for each test state at index i,
    mean/std are computed from states [0, i) only.

    Returns:
        Tuple of (gap1_mae, traj3_mae, n_test_cases).
    """
    decay = np.log(2) / recency_half_life
    sqrt_w = np.sqrt(np.array(feature_weights))
    errors_gap1: list[float] = []
    errors_traj3: list[float] = []

    for test_idx in range(MIN_TEST_INDEX, len(states)):
        test_state = states[test_idx]

        train_features = feat_matrix[:test_idx]
        means = train_features.mean(axis=0)
        stds = train_features.std(axis=0)
        stds[stds == 0] = 1.0

        train_weighted = (train_features - means) / stds * sqrt_w
        test_weighted = (feat_matrix[test_idx] - means) / stds * sqrt_w

        candidates = [
            (index, float(np.linalg.norm(test_weighted - train_weighted[index])))
            for index in range(test_idx)
        ]
        candidates.sort(key=lambda item: item[1])
        nearest = candidates[:k]

        gaps1: list[float] = []
        weights1: list[float] = []
        for neighbor_index, neighbor_distance in nearest:
            neighbor_state = states[neighbor_index]
            if neighbor_state["future_events"]:
                gap = (
                    neighbor_state["future_events"][0].time - neighbor_state["time"]
                ).total_seconds() / 3600
                gaps1.append(gap)
                age = (
                    test_state["time"] - neighbor_state["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                weights1.append(recency / (neighbor_distance + 0.01))

        if gaps1:
            predicted = float(np.average(gaps1, weights=weights1))
            actual = (
                test_state["future_events"][0].time - test_state["time"]
            ).total_seconds() / 3600
            errors_gap1.append(abs(actual - predicted))

        actual_gaps_3: list[float] = []
        for step in range(min(3, len(test_state["future_events"]))):
            previous = (
                test_state["time"]
                if step == 0
                else test_state["future_events"][step - 1].time
            )
            actual_gaps_3.append(
                (test_state["future_events"][step].time - previous).total_seconds()
                / 3600
            )

        trajectories: list[list[float]] = []
        trajectory_weights: list[float] = []
        for neighbor_index, neighbor_distance in nearest:
            neighbor_state = states[neighbor_index]
            gaps: list[float] = []
            for step in range(min(3, len(neighbor_state["future_events"]))):
                previous = (
                    neighbor_state["time"]
                    if step == 0
                    else neighbor_state["future_events"][step - 1].time
                )
                gaps.append(
                    (
                        neighbor_state["future_events"][step].time - previous
                    ).total_seconds()
                    / 3600
                )
            if len(gaps) == 3:
                trajectories.append(gaps)
                age = (
                    test_state["time"] - neighbor_state["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                trajectory_weights.append(recency / (neighbor_distance + 0.01))

        if trajectories and len(actual_gaps_3) == 3:
            average_neighbor = np.average(
                trajectories, weights=trajectory_weights, axis=0
            )
            errors_traj3.append(
                float(np.mean(np.abs(np.array(actual_gaps_3) - average_neighbor)))
            )

    gap1_mae = float(np.mean(errors_gap1)) if errors_gap1 else float("nan")
    traj3_mae = float(np.mean(errors_traj3)) if errors_traj3 else float("nan")
    return gap1_mae, traj3_mae, len(errors_gap1)


def _evaluate_blending(
    states: list[dict[str, Any]],
    feat_matrix: np.ndarray,
    feature_weights: list[float],
    k: int,
    recency_half_life: float,
    traj_length_method: str,
    alignment: str,
) -> tuple[float, int]:
    """Evaluate trajectory blending with different length/alignment methods.

    Uses full neighbor trajectories so the trajectory-length method is
    exercised against realistic lengths.

    Returns:
        Tuple of (full_traj_mae, n_test_cases).
    """
    decay = np.log(2) / recency_half_life
    sqrt_w = np.sqrt(np.array(feature_weights))
    errors: list[float] = []

    for test_idx in range(MIN_TEST_INDEX, len(states)):
        test_state = states[test_idx]

        train_features = feat_matrix[:test_idx]
        means = train_features.mean(axis=0)
        stds = train_features.std(axis=0)
        stds[stds == 0] = 1.0

        train_weighted = (train_features - means) / stds * sqrt_w
        test_weighted = (feat_matrix[test_idx] - means) / stds * sqrt_w

        candidates = [
            (index, float(np.linalg.norm(test_weighted - train_weighted[index])))
            for index in range(test_idx)
        ]
        candidates.sort(key=lambda item: item[1])
        nearest = candidates[:k]

        gap_trajectories: list[list[float]] = []
        offset_trajectories: list[list[float]] = []
        weights: list[float] = []
        for neighbor_index, neighbor_distance in nearest:
            neighbor_state = states[neighbor_index]
            gap_traj: list[float] = []
            offset_traj: list[float] = []
            for step, future_event in enumerate(neighbor_state["future_events"]):
                previous = (
                    neighbor_state["time"]
                    if step == 0
                    else neighbor_state["future_events"][step - 1].time
                )
                gap_traj.append((future_event.time - previous).total_seconds() / 3600)
                offset_traj.append(
                    (future_event.time - neighbor_state["time"]).total_seconds() / 3600
                )
            if gap_traj:
                gap_trajectories.append(gap_traj)
                offset_trajectories.append(offset_traj)
                age = (
                    test_state["time"] - neighbor_state["time"]
                ).total_seconds() / 3600
                recency = float(np.exp(-decay * max(age, 0)))
                weights.append(recency / (neighbor_distance + 0.01))

        if not gap_trajectories:
            continue

        trajectory_lengths = [len(traj) for traj in gap_trajectories]
        if traj_length_method == "median":
            predicted_length = int(np.median(trajectory_lengths))
        else:
            predicted_length = int(np.mean(trajectory_lengths))

        if predicted_length <= 0:
            continue

        weight_array = np.array(weights)
        predicted_offsets: list[float] = []
        cumulative_gap = 0.0

        for step in range(predicted_length):
            step_values: list[float] = []
            step_weights: list[float] = []
            for traj_index, gap_traj in enumerate(gap_trajectories):
                if step < len(gap_traj):
                    if alignment == "gap":
                        step_values.append(gap_traj[step])
                    else:
                        step_values.append(offset_trajectories[traj_index][step])
                    step_weights.append(float(weight_array[traj_index]))

            if not step_values:
                break

            blended = float(np.average(step_values, weights=np.array(step_weights)))
            if alignment == "gap":
                cumulative_gap += blended
                predicted_offsets.append(cumulative_gap)
            else:
                predicted_offsets.append(blended)

        actual_offsets = [
            (future_event.time - test_state["time"]).total_seconds() / 3600
            for future_event in test_state["future_events"]
        ]

        if not predicted_offsets or not actual_offsets:
            continue

        compare_length = min(len(predicted_offsets), len(actual_offsets))
        predicted_array = np.array(predicted_offsets[:compare_length])
        actual_array = np.array(actual_offsets[:compare_length])
        errors.append(float(np.mean(np.abs(predicted_array - actual_array))))

    return (
        float(np.mean(errors)) if errors else float("nan"),
        len(errors),
    )


if __name__ == "__main__":
    main()
