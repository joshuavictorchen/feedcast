# Analog Trajectory Design Decisions

## Tunable parameters

All production constants are tuned in `analysis.py`. The shipping gate
is a full canonical replay sweep through `tune_model()`. The local
`full_traj_MAE` sweeps remain diagnostic only. Current values are in
`model.py`; see `research.md` for the evidence behind each choice.

| Parameter | Rationale |
| --------- | --------- |
| HISTORY_MODE | Raw history preserves cluster-internal feeds, which are needed for neighbor matching during periods of cluster feeding. Episode history still wins the local diagnostic decisively, but canonical replay currently favors raw |
| LOOKBACK_HOURS | A 9-hour lookback focuses rolling means on the most recent ~3 feeds, giving the query state a sharp read on the current rhythm rather than a day-long average. Paired with tight recency, this keeps retrieval keyed on the immediate pattern |
| FEATURE_WEIGHTS | Gap_emphasis doubles the weight on last_gap and mean_gap while keeping volume, hour, and baseline features at weight 1. Gap cadence remains the strongest retrieval cue on the current export; volume and time-of-day contribute at baseline rather than being suppressed or elevated |
| K_NEIGHBORS | k=7 wins the current canonical sweep. Broader neighbor averaging smooths individual trajectory noise while gap-emphasis weighting still keeps retrieval selective on cadence |
| RECENCY_HALF_LIFE_HOURS | 36h (~1.5 days) is the interior canonical optimum. Tight recency concentrates neighbor weight on the most recent states, appropriate because the baby's feeding regime has shifted rapidly between exports. A targeted check at [12, 18, 24, 36] confirms 36h is interior |
| TRAJECTORY_LENGTH_METHOD | Median is more robust than mean on variable-length neighbor trajectories |
| ALIGNMENT | Gap alignment leads on the current export by a thin margin (+0.2 headline points vs time_offset). The axis is effectively tied. Gap alignment blends inter-event gaps step-by-step and rolls forward from the cutoff |

## History source

The model builds bottle-only events and uses them directly as analog
states (raw history). Episode-collapsed history still produces cleaner
local retrieval diagnostics (lower trajectory MAE), but canonical replay
currently favors raw history because the baby's recent cluster feeding
creates short-gap events that episode collapse removes. Raw history
preserves these patterns and lets the neighbor search match against
historical cluster feeds.

The raw/episode margin on canonical replay is +1.8 headline points on
the current export (70.5 vs 68.7). This choice has oscillated between
exports and may flip again as the baby's patterns evolve. See
`research.md` for specific numbers.

## Feature selection and weighting

Each state uses six features:

- `last_gap`
- `mean_gap`
- `last_volume`
- `mean_volume`
- `sin_hour`
- `cos_hour`

The shipped weight profile is gap_emphasis (`[2, 2, 1, 1, 1, 1]`),
which doubles the weight on last_gap and mean_gap while keeping all
other features at baseline. Gap cadence remains the dominant retrieval
cue on the current export; the prior gap_hour profile's additional
volume de-emphasis and hour emphasis no longer help. The diagnostic
sweep still prefers means_only, but canonical replay selects
gap_emphasis. See `research.md` for the specific comparison.

## Lookback and recency

A 9-hour lookback window focuses rolling means on the most recent ~3
feeds, giving the query state a sharp read on the current rhythm
rather than a day-long average. Paired with tight recency (36h),
retrieval keys on the baby's immediate pattern rather than smoothing
across longer history.

Neighbor weights are `recency / (distance + epsilon)`. The recency
half-life is set tight (36h, ~1.5 days). With k=7 neighbors, tight
recency concentrates weight on the most recent states while still
retrieving enough candidates for stable blending. A targeted
same-axis check at [12, 18, 24, 36] confirms 36h is an interior
optimum on the current export. See `model.py` for the current values.

## Alignment and trajectory length

The forecast blends neighbor trajectories as inter-event gaps, rolling
forward step-by-step from the cutoff. Gap alignment regains its
historical lead on the current export after a single-export preference
for time_offset. The gap/time_offset margin remains narrow and this axis
continues to be volatile across exports.

Trajectory length is the median neighbor trajectory length. That guards
against unusually short or long neighbor traces and remains the best
canonical choice.

## Metric hierarchy

The model has two research layers:

- **Diagnostic layer:** per-history-mode `full_traj_MAE` sweeps that
  explain retrieval and blending behavior.
- **Shipping layer:** a full canonical replay sweep across all
  production-relevant constants, including `HISTORY_MODE`.

The canonical sweep is the current shipping gate. When the two layers
disagree on a knob setting, canonical replay wins. See `research.md`
for the current comparison.

## Bottle-only events and completeness

The model still uses bottle-only inputs. Breastfeed volume estimation is
too noisy for a similarity-based model that relies on volume as a
distance feature.

A state is complete only if it has at least three future events and at
least one future event at least 20 hours after the anchor. The model
requires at least 10 complete states to forecast.
