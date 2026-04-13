# Changelog

Tracks behavior-level changes to the Analog Trajectory model. Add newest entries first.

## Retune on new export: gap_hour weighting with shorter lookback and time_offset alignment | 2026-04-12

### Problem

The model's production constants (tuned on
`exports/export_narababy_silas_20260411(1).csv`, headline 68.7) degraded to
headline 65.7 on the new export
(`exports/export_narababy_silas_20260412.csv`). Timing dropped from 52.5
to 48.4 while count stayed stable at 90.4. The most recent replay
windows (Apr 11 evening) scored poorly on timing (35.1), dragging the
recency-weighted aggregate down.

### Research

The full 4704-candidate canonical sweep found a joint combination that
recovers +4.5 headline points:

| Metric | Old production | New production |
|--------|----------------|----------------|
| Headline | 65.7 | 70.2 |
| Count | 90.4 | 91.6 |
| Timing | 48.4 | 54.7 |

The most recent windows improved substantially:

| Window | Old headline | New headline | Old timing | New timing |
|--------|-------------|-------------|------------|------------|
| Apr 11 19:35 | 54.7 | 68.3 | 35.1 | 50.8 |
| Apr 11 20:24 | 53.6 | 66.9 | 35.1 | 50.8 |
| Apr 11 17:18 | 70.4 | 69.4 | 57.2 | 54.9 |

Raw history still leads episode by +2.3 headline points (70.2 vs 67.9).
The top 6 canonical candidates all use gap_hour weighting, and volume
de-emphasis is consistent across the top 10. Time_offset alignment
narrowly leads gap for the first time (+0.4 headline points).

Episode history still wins the local diagnostic decisively (1.070h vs
1.418h full_traj_MAE). The internal/canonical divergence widened further:
they now agree only on history mode (raw), disagreeing on lookback,
weighting, K, recency, trajectory length, and alignment.

RECENCY_HALF_LIFE_HOURS=240 is again a boundary winner in the grid
[36, 72, 120, 240]. See research.md open questions.

### Solution

Ship the full canonical sweep winner:

- `LOOKBACK_HOURS`: 18 -> 9
- `FEATURE_WEIGHTS`: equal [1,1,1,1,1,1] -> gap_hour [2,2,0.5,0.5,2,2]
- `K_NEIGHBORS`: 7 -> 3
- `RECENCY_HALF_LIFE_HOURS`: 72 -> 240
- `ALIGNMENT`: gap -> time_offset
- `TRAJECTORY_LENGTH_METHOD`: median (unchanged)
- `HISTORY_MODE`: raw (unchanged)

The direction of the changes is internally coherent: gap_hour weighting
emphasizes gap cadence and time-of-day as the baby's schedule
consolidates, shorter lookback focuses on the most recent ~3 feeds, fewer
neighbors (k=3) produce sharper predictions, and broader recency keeps
enough historical states available for selective retrieval. The
time_offset alignment flip is the most notable change; the gap/time_offset
margin is narrow (0.4 points) and time_offset has been inferior on every
prior export.

## Retune on new export: raw history with equal weighting and tighter recency | 2026-04-11

### Problem

The model's production constants (tuned on
`exports/export_narababy_silas_20260411.csv`, headline 69.7) degraded to
headline 65.5 on the new export
(`exports/export_narababy_silas_20260411(1).csv`). Timing dropped from
51.7 to 46.8 while count stayed stable at 94.9. The most recent replay
windows (Apr 10 evening) scored catastrophically on timing (16.8),
dragging the recency-weighted aggregate down. These windows contain
cluster feeding (gaps of 1.3h and 0.4h) that the episode model's
collapsed history could not match against.

### Research

The full 4704-candidate canonical sweep found a joint combination that
recovers +3.2 headline points and flips the history mode from episode
back to raw:

| Metric | Old production | New production |
|--------|----------------|----------------|
| Headline | 65.5 | 68.7 |
| Count | 94.9 | 91.2 |
| Timing | 46.8 | 52.5 |

The most recent windows improved dramatically:

| Window | Old headline | New headline | Old timing | New timing |
|--------|-------------|-------------|------------|------------|
| Apr 10 18:33 | 41.0 | 77.2 | 16.8 | 65.5 |
| Apr 10 19:35 | 40.0 | 75.4 | 16.8 | 65.5 |
| Apr 10 12:16 | 59.8 | 75.3 | 41.2 | 65.3 |

The raw-vs-episode canonical margin flipped to raw by +0.5 headline
points (68.7 vs 68.2). Episode history still wins the local diagnostic
decisively (1.070h vs 1.433h full_traj_MAE). The best episode candidate
uses a very different regime from the prior production config
(time_offset alignment, vol_deemphasis weighting, k=3), suggesting the
episode canonical surface shifted substantially.

Count dropped from 94.9 to 91.2. The raw model predicts more events
(including cluster-internal ones), which improves timing but slightly
hurts count precision.

### Solution

Ship the full canonical sweep winner:

- `HISTORY_MODE`: episode -> raw
- `LOOKBACK_HOURS`: 12 -> 18
- `FEATURE_WEIGHTS`: means_only [0.5,2,0.5,2,1,1] -> equal [1,1,1,1,1,1]
- `K_NEIGHBORS`: 5 -> 7
- `RECENCY_HALF_LIFE_HOURS`: 240 -> 72
- `TRAJECTORY_LENGTH_METHOD`: median (unchanged)
- `ALIGNMENT`: gap (unchanged)

The direction of the changes is internally coherent: raw history
preserves cluster-internal feeds that the baby is currently producing,
equal weighting lets instantaneous values (last_gap, last_volume)
contribute signal about short-gap patterns, and tighter recency (72h)
focuses neighbor weighting on the most recent 3 days where cluster
feeding is prominent. The history-mode flip is the most notable change;
the raw/episode margin is narrow (0.5 points) and has oscillated across
exports.

## Retune on new export: means-only weighting with shorter lookback and broader recency | 2026-04-11

### Problem

The model's production constants (tuned on
`exports/export_narababy_silas_20260410.csv`, headline 73.5) degraded to
headline 67.6 on the new export
(`exports/export_narababy_silas_20260411.csv`). Timing dropped from 56.5
to 48.9 while count stayed stable at 96.3. The most recent replay
windows (Apr 10) scored particularly poorly on timing (22-24), dragging
the recency-weighted aggregate down.

### Research

Ran single-axis sweeps on all constants first. Each axis individually
returned the current value as best, indicating the degradation comes from
the data (harder recent windows), not from any single mistuned constant.

The full 4704-candidate canonical sweep via `analysis.py` found a joint
combination that recovers +2.1 headline points:

| Metric | Old production | New production |
|--------|----------------|----------------|
| Headline | 67.6 | 69.7 |
| Count | 96.3 | 95.1 |
| Timing | 48.9 | 51.7 |

The most recent windows improved substantially:

| Window | Old headline | New headline | Old timing | New timing |
|--------|-------------|-------------|------------|------------|
| Apr 10 09:45 | 46.3 | 67.9 | 23.8 | 51.4 |
| Apr 10 07:40 | 47.1 | 71.8 | 22.2 | 55.1 |
| Apr 09 18:16 | 59.0 | 81.3 | 34.8 | 66.1 |

Episode vs raw margin narrowed slightly to +2.6 (69.7 vs 67.1), down
from +4.1 on the prior export.

Note: `RECENCY_HALF_LIFE_HOURS=240` is a boundary winner in the current
grid [36, 72, 120, 240]. Future sweeps should check whether higher
values improve further.

### Solution

Ship the full canonical sweep winner:

- `LOOKBACK_HOURS`: 24 -> 12
- `FEATURE_WEIGHTS`: gap_hour [2,2,0.5,0.5,2,2] -> means_only [0.5,2,0.5,2,1,1]
- `K_NEIGHBORS`: 7 -> 5
- `RECENCY_HALF_LIFE_HOURS`: 120 -> 240
- `TRAJECTORY_LENGTH_METHOD`: median (unchanged)
- `ALIGNMENT`: gap (unchanged)
- `HISTORY_MODE`: episode (unchanged)

The direction of the changes is internally coherent: shorter lookback
focuses rolling means on the most recent half-day, while broader recency
keeps more historical analogs available to compensate. The means_only
profile emphasizes mean_gap and mean_volume over instantaneous values,
which separates analogs better on the current export where the baby's
mean rhythm is more stable than any single recent gap.

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
