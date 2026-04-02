# Analog Trajectory Design Decisions

## Tunable parameters

All production constants are tuned in `analysis.py`. The shipping gate
is a full canonical replay sweep through `tune_model()`. The local
`full_traj_MAE` sweeps remain diagnostic only. Current values are in
`model.py`; see `research.md` for the evidence behind each choice.

| Parameter | Rationale |
| --------- | --------- |
| HISTORY_MODE | Episode-level history removes cluster noise from the state library, improving both local retrieval quality and canonical replay headline |
| LOOKBACK_HOURS | A short lookback keeps rolling means focused on recent feeding rhythm rather than smoothing across older patterns |
| FEATURE_WEIGHTS | The latest gap and volume are the sharpest similarity signals; rolling means and hour-of-day provide supporting context |
| K_NEIGHBORS | Balances count accuracy against timing precision under canonical replay |
| RECENCY_HALF_LIFE_HOURS | Moderately broad recency weighting keeps useful analogs available without letting much older states dominate |
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

The shipped weight profile emphasizes instantaneous gap and volume over
rolling means, with hour-of-day as a supporting context signal. The
internal diagnostic sweep and canonical replay disagree on the best
profile — canonical replay prefers sharper, more local state matching.
That divergence is why the canonical metric, not `full_traj_MAE`, owns
production constants. See `research.md` for the specific comparison.

## Lookback and recency

A short lookback window complements the weight profile: the model uses
rolling means, but only over recent hours. Longer windows smooth away
changes in rhythm that matter for the next 24-hour forecast.

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

The canonical sweep is authoritative. When the two layers disagree on
a knob setting, canonical replay wins. See `research.md` for the
current comparison.

## Bottle-only events and completeness

The model still uses bottle-only inputs. Breastfeed volume estimation is
too noisy for a similarity-based model that relies on volume as a
distance feature.

A state is complete only if it has at least three future events and at
least one future event at least 20 hours after the anchor. The model
requires at least 10 complete states to forecast.
