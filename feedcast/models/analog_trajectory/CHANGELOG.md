# Changelog

Tracks behavior-level changes to the Analog Trajectory model. Add newest entries first.

## Retune on new export: longer lookback and gap+hour weighting | 2026-04-10

### Problem

The model's production constants were tuned on
`exports/export_narababy_silas_20260327.csv` (headline 71.3). On the new
export (`exports/export_narababy_silas_20260410.csv`), those constants
degraded to headline 65.4, with timing dropping from 55.4 to 46.8 while
count stayed stable at 93.4. The baby's feeding timing patterns shifted
over the 14-day gap between exports.

### Research

Ran targeted single-axis sweeps to identify which constants moved, then
a combined sweep to find the joint winner, followed by the full
4704-candidate canonical sweep via `analysis.py` to confirm.

Single-axis evidence:

| Axis | Old | New | Headline delta |
|------|-----|-----|----------------|
| LOOKBACK_HOURS | 9 | 24 | +3.4 |
| RECENCY_HALF_LIFE_HOURS | 120 | 240 | +2.8 |
| K_NEIGHBORS | 7 | 7 | 0.0 |

Feature weights required JSON-array overrides. Combined sweep at
LOOKBACK=24 + RECENCY=240:

| Weights | Headline |
|---------|----------|
| hour_emphasis [1,1,1,1,2,2] | 70.7 |
| gap_hour [2,2,0.5,0.5,2,2] | 73.1 |

The full 4704-candidate canonical sweep confirmed the final winner at
RECENCY=120 (marginally better than 240 with the new weights):

| Metric | Old production | New production |
|--------|----------------|----------------|
| Headline | 65.4 | 73.5 |
| Count | 93.4 | 97.1 |
| Timing | 46.8 | 56.5 |

Episode vs raw margin widened to +4.1 (73.5 vs 69.4), up from +2.1 on
the prior export.

### Solution

Ship the full canonical sweep winner:

- `LOOKBACK_HOURS`: 9 → 24
- `FEATURE_WEIGHTS`: hour_emphasis [1,1,1,1,2,2] → gap_hour [2,2,0.5,0.5,2,2]
- `RECENCY_HALF_LIFE_HOURS`: 120 (unchanged)
- `K_NEIGHBORS`: 7 (unchanged)
- `TRAJECTORY_LENGTH_METHOD`: median (unchanged)
- `ALIGNMENT`: gap (unchanged)
- `HISTORY_MODE`: episode (unchanged)

The direction of the changes is consistent with the baby's schedule
consolidating: longer, more regular gaps make gap cadence and
time-of-day sharper retrieval cues, while volume has grown noisier.

## Widened full-grid rerun supersedes the 18h follow-up | 2026-04-09

### Problem

The same-day targeted lookback follow-up that moved runtime to `18h`
only reopened one axis of the canonical surface. That was enough to
check the boundary concern, but not enough to claim the whole analog
regime was settled.

### Research

Ran the widened full canonical sweep on
`exports/export_narababy_silas_20260327.csv` after expanding the low
lookback region in `analysis.py`.

The 4704-candidate rerun selects:

- `HISTORY_MODE=episode`
- `LOOKBACK_HOURS=9`
- `FEATURE_WEIGHTS=hour_emphasis`
- `K_NEIGHBORS=7`
- `RECENCY_HALF_LIFE_HOURS=120`
- `TRAJECTORY_LENGTH_METHOD=median`
- `ALIGNMENT=gap`

Canonical replay comparison:

| Metric | 18h targeted regime | Full-rerun winner | Better? |
|--------|----------------------|-------------------|---------|
| Headline | 70.19 | 71.28 | Full rerun (+1.09) |
| Count | not re-recorded in targeted probe | 93.0 | Full rerun |
| Timing | not re-recorded in targeted probe | 55.4 | Full rerun |

The best raw-history candidate remains materially worse:

| Metric | Raw best | Episode best | Better? |
|--------|----------|--------------|---------|
| Headline | 69.2 | 71.3 | Episode (+2.1) |
| Count | 92.2 | 93.0 | Episode |
| Timing | 52.3 | 55.4 | Episode |

### Solution

Ship the full-rerun winner and treat the earlier 18h result as a useful
intermediate boundary check, not the final answer. The current analog
regime is now backed by a full widened canonical sweep rather than a
targeted one-axis probe.

## Reopen low-lookback boundary and shift to 18h | 2026-04-09

### Problem

The corrected full canonical sweep had moved Analog Trajectory to an
episode-level `12h` lookback regime, but `12h` was also the lowest
lookback tested in that sweep. Under the updated sweep-discipline rule,
that made the shipped lookback only provisionally justified.

### Research

Ran a targeted canonical follow-up on
`exports/export_narababy_silas_20260327.csv` holding the other winning
constants fixed:

- `HISTORY_MODE=episode`
- `FEATURE_WEIGHTS=recent_only`
- `K_NEIGHBORS=5`
- `RECENCY_HALF_LIFE_HOURS=72`
- `TRAJECTORY_LENGTH_METHOD=median`
- `ALIGNMENT=gap`

Reopened only `LOOKBACK_HOURS` over `6`, `9`, `12`, `18`, and `24`:

| Lookback | Headline |
|--------|--------|
| 6h | 68.40 |
| 9h | 69.46 |
| 12h | 69.90 |
| 18h | 70.19 |
| 24h | 69.63 |

The improvement is modest (`+0.29` over `12h`) but real on the same
24-window canonical replay objective.

### Solution

Ship `LOOKBACK_HOURS=18` while keeping the rest of the full-canonical
episode regime unchanged. Also widen the analysis grid so the next full
rerun includes `6`, `9`, and `18` by default instead of assuming the
old lower boundary.

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
