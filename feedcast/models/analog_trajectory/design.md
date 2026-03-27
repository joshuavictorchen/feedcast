# Analog Trajectory Design Decisions

## Tunable parameters

All model parameters are tunable via research.py. The research script
jointly sweeps all parameters in a single grid (no staged optimization)
using leave-one-out evaluation with fold-causal normalization and
full-trajectory comparison. Run research after new exports to validate
or update constants in model.py.

| Parameter | Value | Rationale |
| --------- | ----- | --------- |
| LOOKBACK_HOURS | 72 | 3-day rolling mean provides stable context |
| FEATURE_WEIGHTS | hour_emphasis [1,1,1,1,2,2] | Hour-of-day is the strongest similarity signal |
| K_NEIGHBORS | 7 | Consistent top performer across weight profiles |
| RECENCY_HALF_LIFE_HOURS | 36 | Patterns shift fast enough that 36h beats longer half-lives |
| TRAJECTORY_LENGTH_METHOD | median | Slight edge (0.748h) over mean (0.753h) on full trajectories |

## Feature selection

The feature vector uses six dimensions:
- **last_gap** (instantaneous): gap before this event
- **mean_gap** (72h lookback): mean of gaps within lookback window
- **last_volume** (instantaneous): volume of this event
- **mean_volume** (72h lookback): mean volume within lookback window
- **sin_hour**, **cos_hour**: circular hour-of-day encoding

Feature weights control per-dimension influence on neighbor distance.
The "hour_emphasis" profile (2.0 for sin/cos hour, 1.0 for all others)
reflects that time-of-day is the strongest similarity signal in this
dataset. Gap and volume features contribute equally at lower weight.

The top 20 configurations (out of 1,344 tested) are tightly clustered
(0.748–0.763h full_traj_MAE), with k=7 and half_life=36h appearing in
all of them. Lookback and weight profiles vary more, suggesting the
model is relatively robust to those choices.

## Recency + distance weighting

The combined weight is `recency / (distance + epsilon)` with a 36-hour
half-life. Prior research also tested simple averaging and distance-only
weighting; recency+distance was best. The current script optimizes the
half-life parameter within the recency+distance approach.

## Gap-based trajectory alignment

Gap-based alignment (average gap sequences, then roll forward) significantly
outperforms time-offset alignment (average absolute times). Gap-based
full_traj MAE was 0.748h vs time-offset at ~1.96h across all top configs.

## Trajectory length

The forecast uses the median trajectory length across neighbors. Median
(0.748h) slightly outperforms mean (0.753h) and guards against outlier
trajectories with unusual event counts.

## Cluster relationship

The model currently uses raw feed history, including cluster-internal
feeds. Research showed episode-level history substantially
improves feature quality and neighbor retrieval accuracy, but the
episode model under-predicts because episode-level trajectories are
shorter. The median trajectory length (which controls how many
predictions to emit) drops when trajectories contain fewer events.
The replay headline degraded and the change was not shipped.

The model tolerates cluster noise reasonably well because
time-of-day features (the dominant similarity signal) are unaffected
by clustering, and the gap/volume features, while noisier with raw
feeds, still produce acceptable neighbor matches. Evaluation
collapses both predictions and actuals into episodes before scoring
so the model is not penalized for predicting cluster
internal structure.

A future path: decouple the trajectory length decision from
per-neighbor event count to avoid under-prediction with episode
inputs.

## Bottle-only events

Builds bottle-only events locally (no breastfeed merge). Breastfeeding
volume estimation is noisy and the model uses volume as a similarity
feature, not a causal input. Adding noise to similarity computation
would degrade neighbor quality. Revisit if the bottle/breast mix
changes significantly.

## Minimum completeness threshold

A state needs a future event at least 20 hours out to be "complete."
This ensures trajectories represent a full daily cycle, not just a
few hours before the export was taken. The model needs at least 10
complete states to produce a forecast. Revisit if availability
becomes an issue with future data.
