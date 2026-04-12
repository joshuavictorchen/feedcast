# Analog Trajectory Design Decisions

## Tunable parameters

All production constants are tuned in `analysis.py`. The shipping gate
is a full canonical replay sweep through `tune_model()`. The local
`full_traj_MAE` sweeps remain diagnostic only. Current values are in
`model.py`; see `research.md` for the evidence behind each choice.

| Parameter | Rationale |
| --------- | --------- |
| HISTORY_MODE | Raw history preserves cluster-internal feeds, which are needed for neighbor matching during periods of cluster feeding. Episode history still wins the local diagnostic decisively, but canonical replay currently favors raw |
| LOOKBACK_HOURS | An 18-hour lookback captures roughly three-quarters of a day of feeding, stabilizing rolling means without oversmoothing |
| FEATURE_WEIGHTS | Equal weighting lets all features contribute; with raw history, instantaneous values carry signal about recent short-gap patterns that means-only suppresses |
| K_NEIGHBORS | Balances count accuracy against timing precision under canonical replay |
| RECENCY_HALF_LIFE_HOURS | Tighter recency (72h, ~3 days) focuses neighbor weighting on the baby's current feeding cadence |
| TRAJECTORY_LENGTH_METHOD | Median is more robust than mean on variable-length neighbor trajectories |
| ALIGNMENT | Gap-based blending outperforms time-offset alignment under both diagnostic and canonical evaluation |

## History source

The model builds bottle-only events and uses them directly as analog
states (raw history). Episode-collapsed history still produces cleaner
local retrieval diagnostics (lower trajectory MAE), but canonical replay
currently favors raw history because the baby's recent cluster feeding
creates short-gap events that episode collapse removes. Raw history
preserves these patterns and lets the neighbor search match against
historical cluster feeds.

The raw/episode margin on canonical replay is narrow (+0.5 headline
points). This choice has oscillated between exports and may flip again
as the baby's patterns evolve. See `research.md` for specific numbers.

## Feature selection and weighting

Each state uses six features:

- `last_gap`
- `mean_gap`
- `last_volume`
- `mean_volume`
- `sin_hour`
- `cos_hour`

The shipped weight profile is equal (all weights 1.0). With raw history,
instantaneous values carry signal about recent cluster feeding patterns
that means-only weighting suppresses. The diagnostic sweep still prefers
means_only, but canonical replay prefers equal weighting on the current
export. See `research.md` for the specific comparison.

## Lookback and recency

An 18-hour lookback window captures roughly three-quarters of a day of
feeding. Paired with equal weighting and tighter recency (72h), this
stabilizes rolling means without oversmoothing, while recency focuses
the state library on the baby's current feeding cadence.

Neighbor weights are `recency / (distance + epsilon)`. The recency
half-life is set tight (72h, ~3 days) to focus on the most recent
patterns. See `model.py` for the current values.

## Alignment and trajectory length

The forecast blends neighbor trajectories as inter-episode gaps, then
rolls those gaps forward from the cutoff. Time-offset alignment remains
inferior on the current export, including within the best raw-history
surface.

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
