# Analog Trajectory Design Decisions

## Feature selection

The feature vector uses six dimensions: last gap, rolling 3-gap mean,
last volume, rolling 3-volume mean, and circular hour-of-day (sin/cos).

Research (see research.py) tested seven feature combinations against
leave-one-out MAE on the first predicted gap. Results:

| Features | k=3 MAE | k=5 MAE |
| -------- | ------- | ------- |
| gap+vol+mean+hour | 0.780h | 0.738h |
| gap_mean+vol_mean+hour | 0.762h | 0.815h |
| all (+ feeds_today) | 0.777h | 0.770h |
| gap+vol+hour | 0.840h | 0.808h |
| gap+vol | 0.873h | 0.876h |

The six-feature set (gap+vol+mean+hour) with k=5 had the lowest
gap1 MAE (0.738h) and traj3 MAE (0.766h). Adding feeds_today did
not help; it adds a feature that changes discretely within a day
and doesn't generalize well across days.

## K=5 neighbors

For the chosen six-feature set (gap+vol+mean+hour), k=5 had the
lowest gap1 MAE (0.738h) and traj3 MAE (0.766h). Some other feature
combos favored k=7, but k=5 was best or near-best across most
configurations. k=3 is too sensitive to individual states; k=7
starts averaging over dissimilar states given the small library
size (~69 states).

## Recency + distance weighting

Three weighting approaches were tested:

| Approach | k=5 MAE |
| -------- | ------- |
| recency + distance | 0.735h |
| simple average | 0.738h |
| distance only | 0.745h |

The combined weight is `recency / (distance + epsilon)` where recency
uses a 72-hour (3-day) half-life. The improvement over simple averaging
is modest but consistent, and the mechanism is sound: recent states
better reflect the baby's current pattern.

## Gap-based vs time-offset trajectory averaging

Two alignment approaches were tested:

- **Gap-based**: average the gap sequences, then roll forward
- **Time-offset**: average absolute time offsets from each state

Gap-based MAE was 0.735h vs time-offset at 1.367h. Gap-based is much
better because trajectories with different starting times but similar
cadences align well by gap, but poorly by absolute offset.

## Trajectory length

The forecast uses the median trajectory length across neighbors. This
avoids being pulled by unusually long or short trajectories. With
current data, most states have 7-9 future events in 24 hours.

## Bottle-only events

Uses bottle-only events (merge_window_minutes=None). Breastfeeding
volume estimation is noisy and the model uses volume as a similarity
feature, not a causal input. Adding noise to similarity computation
would degrade neighbor quality.

## Minimum completeness threshold

A state needs a future event at least 20 hours out to be "complete."
This ensures trajectories represent a full daily cycle, not just a
few hours before the export was taken.
