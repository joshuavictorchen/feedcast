"""Analog Trajectory research: analyze state library quality and neighbor accuracy.

Run from the repo root:
    .venv/bin/python -m feedcast.models.analog_trajectory.research

This script reproduces the data analysis that informed the Analog
Trajectory design. It uses the same export selection, data parsing,
and tuning constants as the model itself, so its output matches what
the model would see at the same cutoff.

Update this script and re-run when new exports are available or when
revisiting model assumptions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np

from feedcast.data import (
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
)
from feedcast.models.analog_trajectory.model import (
    K_NEIGHBORS,
    MIN_PRIOR_EVENTS,
    RECENCY_HALF_LIFE_HOURS,
    TRAJECTORY_COMPLETENESS_HOURS,
    _state_features,
)

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent


def main() -> None:
    """Run the analysis using the same data window as the model."""
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

    # --- Build state library ---
    log("=== STATE LIBRARY ===")
    log()

    complete_states = []
    incomplete_count = 0
    for index in range(MIN_PRIOR_EVENTS, len(events)):
        event = events[index]
        if event.time > cutoff:
            break

        features = _state_features(events, index)
        future_end = event.time + timedelta(hours=24)
        future_events = [e for e in events[index + 1 :] if e.time <= future_end]
        has_late_event = any(
            e.time >= event.time + timedelta(hours=TRAJECTORY_COMPLETENESS_HOURS)
            for e in events[index + 1 :]
        )

        if has_late_event and len(future_events) >= 3:
            complete_states.append(
                {
                    "index": index,
                    "time": event.time,
                    "features": features,
                    "future_events": future_events,
                    "future_count": len(future_events),
                }
            )
        else:
            incomplete_count += 1

    log(f"Complete states: {len(complete_states)}")
    log(f"Incomplete states: {incomplete_count}")
    log()

    # --- Feature statistics ---
    feature_names = [
        "last_gap", "mean_gap_3", "last_volume",
        "mean_volume_3", "sin_hour", "cos_hour",
    ]
    feat_matrix = np.array([s["features"] for s in complete_states])
    feat_means = feat_matrix.mean(axis=0)
    feat_stds = feat_matrix.std(axis=0)
    feat_stds[feat_stds == 0] = 1.0

    log("=== FEATURE STATISTICS ===")
    log()
    log(f"{'Feature':<15} {'Mean':>8} {'Std':>8} {'Min':>8} {'Max':>8}")
    for i, name in enumerate(feature_names):
        log(
            f"{name:<15} {feat_means[i]:>8.3f} {feat_stds[i]:>8.3f} "
            f"{feat_matrix[:, i].min():>8.3f} {feat_matrix[:, i].max():>8.3f}"
        )
    log()

    # --- Feature combination comparison ---
    log("=== FEATURE COMBINATION COMPARISON ===")
    log()

    combos = {
        "gap+vol+mean+hour": [0, 1, 2, 3, 4, 5],
        "gap_mean+vol_mean+hour": [1, 3, 4, 5],
        "all+feeds_today": None,  # special case
        "gap+vol+hour": [0, 2, 4, 5],
        "gap+vol": [0, 2],
        "gap+vol+feeds_today": None,  # special case
    }

    # Build feeds_today feature for special cases.
    feeds_today_array = np.array(
        [
            sum(
                1 for e in events[: s["index"] + 1]
                if e.time.date() == s["time"].date()
            )
            for s in complete_states
        ],
        dtype=float,
    )

    for combo_name, indices in combos.items():
        for k in [3, 5, 7]:
            if indices is not None:
                sub_matrix = feat_matrix[:, indices]
            elif "feeds_today" in combo_name and "all" in combo_name:
                sub_matrix = np.column_stack([feat_matrix, feeds_today_array])
            else:
                sub_matrix = np.column_stack(
                    [feat_matrix[:, [0, 2]], feeds_today_array]
                )

            sub_means = sub_matrix.mean(axis=0)
            sub_stds = sub_matrix.std(axis=0)
            sub_stds[sub_stds == 0] = 1.0
            sub_normed = (sub_matrix - sub_means) / sub_stds

            errors_gap1: list[float] = []
            errors_traj3: list[float] = []

            for test_idx in range(10, len(complete_states)):
                test_state = complete_states[test_idx]
                test_vec = sub_normed[test_idx]

                candidates = [
                    (i, float(np.linalg.norm(test_vec - sub_normed[i])))
                    for i in range(test_idx)
                ]
                candidates.sort(key=lambda x: x[1])
                nearest = candidates[:k]

                # Gap1 error.
                actual_gap1 = (
                    test_state["future_events"][0].time - test_state["time"]
                ).total_seconds() / 3600
                nn_gaps = [
                    (
                        complete_states[ni]["future_events"][0].time
                        - complete_states[ni]["time"]
                    ).total_seconds()
                    / 3600
                    for ni, _ in nearest
                    if complete_states[ni]["future_events"]
                ]
                if nn_gaps:
                    errors_gap1.append(abs(actual_gap1 - float(np.mean(nn_gaps))))

                # Traj3 error.
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
                for ni, _ in nearest:
                    ns = complete_states[ni]
                    ng: list[float] = []
                    for j in range(min(3, len(ns["future_events"]))):
                        prev = (
                            ns["time"]
                            if j == 0
                            else ns["future_events"][j - 1].time
                        )
                        ng.append(
                            (ns["future_events"][j].time - prev).total_seconds()
                            / 3600
                        )
                    if len(ng) == 3:
                        nn_trajs.append(ng)

                if nn_trajs and len(actual_gaps_3) == 3:
                    avg_nn = np.mean(nn_trajs, axis=0)
                    errors_traj3.append(
                        float(
                            np.mean(
                                np.abs(np.array(actual_gaps_3) - avg_nn)
                            )
                        )
                    )

            mae_g1 = float(np.mean(errors_gap1)) if errors_gap1 else float("nan")
            mae_t3 = float(np.mean(errors_traj3)) if errors_traj3 else float("nan")
            log(
                f"{combo_name:<25s} k={k}: "
                f"gap1_MAE={mae_g1:.3f}h  traj3_MAE={mae_t3:.3f}h  "
                f"(n={len(errors_gap1)})"
            )
    log()

    # --- Weighting approach comparison ---
    log("=== WEIGHTING APPROACH COMPARISON ===")
    log()

    feat_normed = (feat_matrix - feat_means) / feat_stds
    decay = np.log(2) / RECENCY_HALF_LIFE_HOURS

    for approach in ["simple_avg", "dist_weighted", "recency+dist_weighted"]:
        for k in [3, 5, 7]:
            errors: list[float] = []
            for test_idx in range(10, len(complete_states)):
                test_state = complete_states[test_idx]
                test_vec = feat_normed[test_idx]

                candidates = [
                    (i, float(np.linalg.norm(test_vec - feat_normed[i])))
                    for i in range(test_idx)
                ]
                candidates.sort(key=lambda x: x[1])
                nearest = candidates[:k]

                nn_gaps: list[float] = []
                nn_weights: list[float] = []
                for ni, nd in nearest:
                    ns = complete_states[ni]
                    if ns["future_events"]:
                        gap = (
                            ns["future_events"][0].time - ns["time"]
                        ).total_seconds() / 3600
                        nn_gaps.append(gap)

                        if approach == "simple_avg":
                            nn_weights.append(1.0)
                        elif approach == "dist_weighted":
                            nn_weights.append(1.0 / (nd + 0.01))
                        else:
                            age = (
                                test_state["time"] - ns["time"]
                            ).total_seconds() / 3600
                            recency = float(np.exp(-decay * max(age, 0)))
                            nn_weights.append(recency / (nd + 0.01))

                if nn_gaps:
                    pred = float(np.average(nn_gaps, weights=nn_weights))
                    actual = (
                        test_state["future_events"][0].time - test_state["time"]
                    ).total_seconds() / 3600
                    errors.append(abs(actual - pred))

            log(
                f"{approach:<25s} k={k}: "
                f"gap1_MAE={float(np.mean(errors)):.3f}h  (n={len(errors)})"
            )
    log()

    # --- Gap-based vs time-offset trajectory alignment ---
    log("=== GAP-BASED VS TIME-OFFSET ALIGNMENT (k=5) ===")
    log()

    feat_normed_align = (feat_matrix - feat_means) / feat_stds
    gap_errors: list[float] = []
    offset_errors: list[float] = []

    for test_idx in range(10, len(complete_states)):
        test_state = complete_states[test_idx]
        test_vec = feat_normed_align[test_idx]
        candidates = [
            (i, float(np.linalg.norm(test_vec - feat_normed_align[i])))
            for i in range(test_idx)
        ]
        candidates.sort(key=lambda x: x[1])
        nearest = candidates[:5]

        # Actual first 3 gaps.
        actual_gaps_align: list[float] = []
        for j in range(min(3, len(test_state["future_events"]))):
            prev = (
                test_state["time"]
                if j == 0
                else test_state["future_events"][j - 1].time
            )
            actual_gaps_align.append(
                (test_state["future_events"][j].time - prev).total_seconds() / 3600
            )

        # Actual first 3 time offsets from state.
        actual_offsets: list[float] = [
            (fe.time - test_state["time"]).total_seconds() / 3600
            for fe in test_state["future_events"][:3]
        ]

        # Gap-based: average gaps, compare.
        nn_gap_trajs: list[list[float]] = []
        for ni, _ in nearest:
            ns = complete_states[ni]
            ng: list[float] = []
            for j in range(min(3, len(ns["future_events"]))):
                prev = ns["time"] if j == 0 else ns["future_events"][j - 1].time
                ng.append((ns["future_events"][j].time - prev).total_seconds() / 3600)
            if len(ng) == 3:
                nn_gap_trajs.append(ng)

        # Time-offset: average absolute offsets, compare.
        nn_offset_trajs: list[list[float]] = []
        for ni, _ in nearest:
            ns = complete_states[ni]
            offsets = [
                (fe.time - ns["time"]).total_seconds() / 3600
                for fe in ns["future_events"][:3]
            ]
            if len(offsets) == 3:
                nn_offset_trajs.append(offsets)

        if nn_gap_trajs and len(actual_gaps_align) == 3:
            avg_gaps = np.mean(nn_gap_trajs, axis=0)
            gap_errors.append(
                float(np.mean(np.abs(np.array(actual_gaps_align) - avg_gaps)))
            )

        if nn_offset_trajs and len(actual_offsets) == 3:
            avg_offsets = np.median(nn_offset_trajs, axis=0)
            offset_errors.append(
                float(np.mean(np.abs(np.array(actual_offsets) - avg_offsets)))
            )

    log(
        f"Gap-based alignment:    traj3_MAE={np.mean(gap_errors):.3f}h "
        f"(n={len(gap_errors)})"
    )
    log(
        f"Time-offset alignment:  traj3_MAE={np.mean(offset_errors):.3f}h "
        f"(n={len(offset_errors)})"
    )
    log()

    # --- Leave-one-out neighbor quality ---
    log("=== RECENT STATE NEIGHBOR QUALITY (last 10 complete states) ===")
    log()

    feat_normed = (feat_matrix - feat_means) / feat_stds
    for test_idx in range(-10, 0):
        test_state = complete_states[test_idx]
        test_vec = feat_normed[test_idx]
        abs_idx = len(complete_states) + test_idx

        candidates = [
            (i, float(np.linalg.norm(test_vec - feat_normed[i])))
            for i in range(abs_idx)
        ]
        candidates.sort(key=lambda x: x[1])
        nearest = candidates[:K_NEIGHBORS]

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
        nn_w: list[float] = []
        for ni, nd in nearest:
            ns = complete_states[ni]
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
                nn_w.append(recency / (nd + 0.01))

        if nn_trajs and len(actual_gaps) == 3:
            avg_nn = np.average(nn_trajs, weights=nn_w, axis=0)
            err = np.abs(np.array(actual_gaps) - avg_nn)
            log(
                f"State at {test_state['time'].strftime('%m/%d %H:%M')} "
                f"(gap={test_state['features'][0]:.1f}h "
                f"vol={test_state['features'][2]:.1f}oz):"
            )
            log(f"  Actual gaps: {[f'{g:.2f}' for g in actual_gaps]}")
            log(f"  NN avg gaps: {[f'{g:.2f}' for g in avg_nn]}")
            log(f"  Abs errors:  {[f'{e:.2f}' for e in err]}")
            log(
                f"  Neighbors: {[f'd={nd:.2f}' for _, nd in nearest]}"
            )
            log()

    # Save results alongside the script.
    results_path = OUTPUT_DIR / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"Results saved to {results_path}")


if __name__ == "__main__":
    main()
