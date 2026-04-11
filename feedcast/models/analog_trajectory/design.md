# Analog Trajectory Design Decisions

## Tunable parameters

All production constants are tuned in `analysis.py`. The shipping gate
is a full canonical replay sweep through `tune_model()`. The local
`full_traj_MAE` sweeps remain diagnostic only. Current values are in
`model.py`; see `research.md` for the evidence behind each choice.

| Parameter | Rationale |
| --------- | --------- |
| HISTORY_MODE | Episode-level history removes cluster noise from the state library, improving both local retrieval quality and canonical replay headline |
| LOOKBACK_HOURS | A 24-hour lookback captures the current feeding rhythm without smoothing across older patterns too aggressively |
| FEATURE_WEIGHTS | Rolling means (mean_gap, mean_volume) are the sharpest retrieval cues on the current export; instantaneous values and hour-of-day are deemphasized |
| K_NEIGHBORS | Balances count accuracy against timing precision under canonical replay |
| RECENCY_HALF_LIFE_HOURS | Broad recency weighting keeps a wide range of historical analogs available, compensating for the shorter lookback window |
| TRAJECTORY_LENGTH_METHOD | Median is more robust than mean on variable-length neighbor trajectories |
| ALIGNMENT | Gap-based blending outperforms time-offset alignment under both diagnostic and canonical evaluation |

## History source

The model builds bottle-only events, then collapses them into feeding
episodes before constructing analog states. This removes cluster-
internal top-ups from the feature space and makes each state represent
one real feeding episode rather than one bottle event.

Episode history improves both local retrieval quality (all diagnostic
metrics) and the canonical replay headline. See `research.md` for
specific numbers.

## Feature selection and weighting

Each state uses six features:

- `last_gap`
- `mean_gap`
- `last_volume`
- `mean_volume`
- `sin_hour`
- `cos_hour`

The shipped weight profile puts the strongest weight on rolling means
(mean_gap, mean_volume), with instantaneous values and hour-of-day
deemphasized. Both the internal diagnostic sweep and canonical replay
now agree on `means_only` as the best weight profile, though they
still differ on lookback and recency. See `research.md` for the
specific comparison.

## Lookback and recency

A 12-hour lookback window focuses rolling means on the most recent
half-day of feeding. Paired with means_only weighting and broad
recency (240h), this captures the current rhythm tightly without
oversmoothing across stale events.

Neighbor weights are `recency / (distance + epsilon)`. The recency
half-life is set broad enough to keep useful analogs available without
letting much older states dominate the blend. See `model.py` for the
current values.

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
