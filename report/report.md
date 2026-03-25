# Silas Feeding Forecast

**Monday, March 23, 2026** · 24 days old · Cutoff: 11:41 AM

## Next Feeds

**Consensus Blend** predicts **10 feeds**
over the next 24 hours, totaling **34.7 oz**.

| Feed | Time | Gap | Volume |
| ---- | ---- | --- | ------ |
| 1 | **1:41 PM** | 2.0h | 3.5 oz |
| 2 | **4:04 PM** | 2.4h | 3.5 oz |
| 3 | **6:09 PM** | 2.1h | 3.5 oz |
| 4 | **8:40 PM** | 2.5h | 3.7 oz |
| 5 | **11:34 PM** | 2.9h | 3.5 oz |
| 6 | **2:04 AM** | 2.5h | 3.5 oz |
| 7 | **4:25 AM** | 2.3h | 3.5 oz |
| 8 | **6:51 AM** | 2.4h | 3.5 oz |
| 9 | **9:10 AM** | 2.3h | 3.5 oz |
| 10 | **11:07 AM** | 2.0h | 3.1 oz |

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Model Comparison

| Model | Status | First Feed | Feed Times |
| ----- | ------ | ---------- | ---------- |
| Slot Drift | Available | 4:22 PM | 4:22 PM, 6:02 PM, 7:02 PM, 11:20 PM, 1:46 AM, 4:25 AM, 7:39 AM, 11:10 AM |
| Analog Trajectory | Available | 1:41 PM | 1:41 PM, 3:33 PM, 5:34 PM, 8:52 PM, 12:16 AM, 3:23 AM, 6:38 AM, 9:10 AM, 10:52 AM |
| Latent Hunger State | Available | 1:34 PM | 1:34 PM, 4:04 PM, 6:34 PM, 9:04 PM, 11:34 PM, 2:04 AM, 4:34 AM, 7:04 AM, 9:34 AM |
| Survival Hazard | Available | 1:52 PM | 1:52 PM, 4:04 PM, 6:16 PM, 8:27 PM, 11:34 PM, 2:41 AM, 5:48 AM, 8:55 AM, 11:07 AM |
| Consensus Blend | Featured | 1:41 PM | 1:41 PM, 4:04 PM, 6:09 PM, 8:40 PM, 11:34 PM, 2:04 AM, 4:25 AM, 6:51 AM, 9:10 AM, 11:07 AM |

## Prior Run Retrospective

No new actuals since the prior run
(same dataset: `sha256:7b6cdd2f...`).

## Historical Retrospective Accuracy

No completed retrospective history yet.

## Methodologies


### Slot Drift

Daily template model that identifies recurring feed slots and tracks
their drift over time. Instead of predicting individual gaps, it asks:
"what does a typical day look like, and how is it shifting?"

The model groups recent history into calendar days, determines a
canonical slot count (median daily feed count over the lookback
window), and builds a template of slot positions by taking the median
hour-of-day for each ordinal position across days that match the
canonical count. Each day's feeds are then aligned to the template
using the Hungarian algorithm with circular time-of-day distance.
Feeds that exceed a cost threshold (2 hours) are left unmatched,
which naturally handles cluster feeds and extras without special-casing.

After alignment, each slot has a position history across days. A
recency-weighted linear regression (half-life 3 days) estimates the
current position and drift rate for each slot. The forecast projects
today's unfilled slots and tomorrow's full schedule, with one
additional day of drift applied to tomorrow's positions. Volume is a
recency-weighted per-slot average.

Uses bottle-only events (no breastfeed merge). Breastfeeding volume
is estimated, not measured, and Slot Drift is primarily a timing model.

### Analog Trajectory

Instance-based forecasting that asks "when have we seen a state like
this before, and what happened next?" Instead of fitting a global
function, the model treats each historical feed event as a reference
state with a known 24-hour future trajectory.

At forecast time the model summarizes the current state as a six-
dimensional feature vector: last gap and last volume (instantaneous),
rolling mean gap and rolling mean volume (computed over a 72-hour
lookback window), and circular hour-of-day (sin/cos encoding). It
finds the most similar historical states using weighted Euclidean
distance with per-feature weights that emphasize hour-of-day over
gap and volume. Neighbors are weighted by a combination of proximity
and recency (36-hour half-life), and the forecast is produced by
averaging their actual future gap sequences. The predicted gaps are
rolled forward from the cutoff time to produce absolute feed times.

Volume predictions use per-step weighted averages from the same
neighbor trajectories. This lets volume reflect what actually happened
in analogous situations rather than relying on a global time-of-day
profile.

Uses bottle-only events (no breastfeed merge). The model needs at
least 10 historical states whose trajectories extend at least 20
hours past the state time (with at least 3 future events) to
produce a forecast.

### Latent Hunger State

Mechanistic model that treats hunger as a hidden variable rising over
time and partially reset by each feed. A larger feed drives hunger
lower, so the next feed takes longer. The model simulates this process
forward to produce a 24-hour schedule.

The satiety reset is multiplicative: after a feed of V ounces, hunger
drops to threshold × exp(−rate × V). This guarantees partial resets —
no feed fully zeroes hunger — so volume always influences the predicted
gap. The growth rate (how fast hunger rebuilds) is estimated from
recent events using a recency-weighted average, allowing the model to
track the baby's changing metabolic pace.

At forecast time the model computes the current hunger level from the
last observed feed and elapsed time, then simulates forward: hunger
grows until it crosses the threshold, a feed fires at the simulation
median volume, hunger resets, and the cycle repeats.

Uses breastfeed-merged events (45-minute merge window) so that
nearby breastfeed volume is attributed to the next bottle event.
Infrastructure is in place for smooth circadian modulation of the
growth rate, but research found no benefit over the multiplicative
model's inherent volume-driven day/night sensitivity — larger
overnight feeds already produce longer predicted gaps.

### Survival Hazard

Probabilistic model that frames each feeding as a survival event whose
likelihood increases with elapsed time. Uses a Weibull hazard function
with separate shapes for overnight and daytime periods to capture the
structurally different feeding regimes.

Overnight feeds (20:00–08:00) follow a high-shape Weibull: very
regular timing with tight clustering around the median gap. Daytime
feeds (08:00–20:00) follow a lower-shape Weibull: more variable timing
with a broader distribution. The scale parameter for each period is
estimated at runtime from recent same-period gaps, allowing the model
to track the baby's changing pace.

The forecast uses the median of the Weibull survival function as the
point prediction — the time at which there is a 50% probability the
next feed has occurred. The first predicted feed accounts for the time
already elapsed since the last observed feed using the conditional
survival function.

Uses bottle-only events (no breastfeed merge). Volume was tested as a
covariate but was not statistically significant and did not improve
walk-forward accuracy beyond the day-part split.

### Consensus Blend (featured)

Majority-vote ensemble across the scripted base models. The blend
builds immutable candidate feed slots around each predicted point,
including majority-sized subsets of the available models, then solves
an exact set-packing problem to choose one non-overlapping feed
sequence.

Each candidate uses the median timestamp and median volume of its
contributing model predictions. The exact selector enforces two hard
rules: one model prediction can only support one consensus feed, and
two candidate feeds inside the conflict window cannot both survive.

This keeps the blend from reusing the same evidence twice while still
letting tight majority agreement compete directly against wider
all-model agreement.

---

*Export: `export_narababy_silas_20260323.csv` · Dataset: `sha256:7b6cdd2f...`
· Commit: `0f9b43f (dirty)`
· Generated: 2026-03-25 01:35:19*
