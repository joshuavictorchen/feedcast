# Changelog

Tracks behavior-level changes to the Analog Trajectory model. Add newest entries first.

## Full canonical retune adopts episode history | 2026-04-01

### Problem

The model was still shipping the old raw-history configuration:

- `HISTORY_MODE=raw`
- `LOOKBACK_HOURS=72`
- `FEATURE_WEIGHTS=hour_emphasis`
- `K_NEIGHBORS=7`
- `RECENCY_HALF_LIFE_HOURS=36`
- `TRAJECTORY_LENGTH_METHOD=median`
- `ALIGNMENT=gap`

That configuration had only been partially pressure-tested against the
true ship metric. The earlier research path still treated
`full_traj_MAE` as a shortlist metric for canonical validation, and the
reopened raw-vs-episode question needed to be answered under the same
canonical replay objective as every other production constant.

Research also exposed a correctness bug: `LOOKBACK_HOURS` had been
captured as a Python default argument in `_state_features()`, so replay
overrides were not actually changing the forecast lookback. The final
sweep below is post-fix and is the authoritative result.

### Research

Ran the corrected research script on
`exports/export_narababy_silas_20260327.csv` with:

- two diagnostic 1344-config `full_traj_MAE` sweeps (`raw` and `episode`)
- one full 2688-config canonical replay sweep across all production
  parameters, including `HISTORY_MODE`

Canonical replay comparison:

| Metric | Old production | New production | Better? |
|--------|----------------|----------------|---------|
| Headline | 63.54 | 69.90 | New (+6.36) |
| Count | 88.19 | 93.80 | New (+5.61) |
| Timing | 46.11 | 52.80 | New (+6.69) |
| Availability | 24/24 | 24/24 | Tie |

Best corrected raw-history candidate:

| Metric | Raw best | Episode best | Better? |
|--------|----------|--------------|---------|
| Headline | 69.2 | 69.9 | Episode (+0.7) |
| Count | 92.2 | 93.8 | Episode |
| Timing | 52.3 | 52.8 | Episode |

Diagnostic retrieval quality also still prefers episode history:

| Metric | Raw best diagnostic | Episode best diagnostic | Better? |
|--------|---------------------|-------------------------|---------|
| full_traj_MAE | 1.696h | 1.126h | Episode |
| gap1_MAE | 0.785h | 0.659h | Episode |
| traj3_MAE | 0.802h | 0.621h | Episode |

Final canonical winner:

- `HISTORY_MODE=episode`
- `LOOKBACK_HOURS=12`
- `FEATURE_WEIGHTS=recent_only [2, 0.5, 2, 0.5, 1, 1]`
- `K_NEIGHBORS=5`
- `RECENCY_HALF_LIFE_HOURS=72`
- `TRAJECTORY_LENGTH_METHOD=median`
- `ALIGNMENT=gap`

### Solution

Shipped the full-canonical winner and retired the analog-specific
two-stage proxy gate. The model now builds states from episode-level
history, uses a shorter 12-hour lookback, emphasizes the latest gap and
volume, broadens recency weighting to 72 hours, and keeps gap-based
alignment with median trajectory length.

Also fixed the lookback override bug by making `_state_features()`
read `LOOKBACK_HOURS` at call time rather than at import time.

## Episode-level history evaluated, not shipped | 2026-03-26

### Problem

Cluster-internal feeds (top-ups, continuations) create spurious states
with short gaps and low volumes that pollute neighbor retrieval features
(`last_gap`, `mean_gap`, `last_volume`, `mean_volume`).

### Research

Updated `research.py` to compare raw vs. episode state libraries and
feature quality. The question: does grouping raw feeds into episodes
before building states produce cleaner similarity features and better
neighbor retrieval?

| Metric | Raw | Episode | Better? |
|--------|-----|---------|---------|
| Events | 97 | 80 | Episode — 17 cluster-internal feeds removed |
| Complete states | 84 | 70 | Raw has more, but episode states are cleaner |
| Mean gap (last_gap) | 2.50h | 2.99h | Episode — no artificial sub-hour cluster gaps |
| Gap std | 1.01 | 0.78 | Episode — tighter, less noisy |
| Mean volume (last_volume) | 2.87oz | 3.44oz | Episode — sums reflect real intake |
| Volume std | 1.10 | 0.78 | Episode — tighter |
| gap1 MAE (fold-causal) | 0.770h | 0.656h | Episode — 15% more accurate |
| traj3 MAE (fold-causal) | 0.764h | 0.670h | Episode — 12% more accurate |

Replay (ship gate, 20260325 export, 03/24→03/25 window):

| Metric | Raw | Episode | Better? |
|--------|-----|---------|---------|
| Headline | 68.22 | 66.65 | Raw (-1.57) |
| Count F1 | 100.0 | 91.16 | Raw (episode predicted 7, actual was 9) |
| Timing | 46.55 | 48.73 | Episode (matched episodes more accurate) |

### Solution

Despite strong research metrics, **replay headline dropped**. The
episode model under-predicted episode count because episode-level
trajectories are shorter (fewer events per trajectory), so blended
forecasts produce fewer predictions. The count score drop outweighed
the timing improvement.

**Decision: not shipped.** Model continues to use raw feed history.
The episode-level comparison is preserved in `research.py`. A
possible future fix: decouple trajectory length estimation from
per-neighbor event count (e.g., derive from episode-level daily
counts rather than median neighbor trajectory length).
