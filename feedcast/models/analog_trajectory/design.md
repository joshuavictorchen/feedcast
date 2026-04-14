# Analog Trajectory Design Decisions

## Tunable parameters

All production constants are tuned in `analysis.py`. The shipping gate
is a full canonical replay sweep through `tune_model()`. The local
`full_traj_MAE` sweeps remain diagnostic only. Current values are in
`model.py`; see `research.md` for the evidence behind each choice.

| Parameter | Rationale |
| --------- | --------- |
| HISTORY_MODE | Raw history preserves cluster-internal feeds, which are needed for neighbor matching during periods of cluster feeding. Episode history still wins the local diagnostic decisively, but canonical replay currently favors raw |
| LOOKBACK_HOURS | A 24-hour lookback captures roughly a full day of feeds, giving rolling means a stable daily base. Paired with gap_hour weighting and broader recency, the longer window smooths intra-day variation while gap_hour weighting keeps retrieval keyed on cadence and time-of-day |
| FEATURE_WEIGHTS | Gap_hour weighting emphasizes gap cadence and time-of-day over volume. As the baby's schedule consolidates, temporal regularity is a stronger retrieval cue than feed size |
| K_NEIGHBORS | k=5 balances sharpness with coverage, providing enough neighbor diversity to handle daily cadence variations while still favoring cadence-similar states |
| RECENCY_HALF_LIFE_HOURS | Broader recency (240h, ~10 days) keeps enough historical states available for selective (k=3) retrieval. Distance-based neighbor selection handles recency naturally |
| TRAJECTORY_LENGTH_METHOD | Median is more robust than mean on variable-length neighbor trajectories |
| ALIGNMENT | Gap alignment leads on the current export, regaining its historical position after a single-export time_offset preference. Gap alignment blends inter-event gaps step-by-step and rolls forward from the cutoff |

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

The shipped weight profile is gap_hour (`[2, 2, 0.5, 0.5, 2, 2]`),
which emphasizes gap cadence and time-of-day while de-emphasizing volume.
On the current export, the baby's feeding schedule is consolidating,
making temporal regularity (gap rhythm and time-of-day) the strongest
retrieval cues. Volume carries less discriminating signal. The diagnostic
sweep still prefers means_only, but canonical replay selects gap_hour on
the current export. See `research.md` for the specific comparison.

## Lookback and recency

A 24-hour lookback window captures roughly a full day of feeds, giving
rolling means a stable daily base. Paired with gap_hour weighting and
broader recency (240h), the longer lookback smooths intra-day variation
while gap_hour weighting keeps retrieval keyed on cadence and
time-of-day. Broader recency keeps enough historical states available
for selective (k=5) retrieval.

Neighbor weights are `recency / (distance + epsilon)`. The recency
half-life is set broad (240h, ~10 days). With k=5 neighbors,
distance-based selection naturally favors recent, cadence-similar states;
the broader half-life avoids starving the retrieval pool. See `model.py`
for the current values.

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
