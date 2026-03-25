# Silas Feeding Forecast

**Wednesday, March 25, 2026** · 26 days old · Cutoff: 12:34 AM

## Next Feeds

**Consensus Blend** predicts **10 feeds**
over the next 24 hours, totaling **34.2 oz**.

| Feed | Time | Gap | Volume |
| ---- | ---- | --- | ------ |
| 1 | **3:39 AM** | 3.1h | 3.6 oz |
| 2 | **6:48 AM** | 3.2h | 3.5 oz |
| 3 | **9:58 AM** | 3.2h | 3.5 oz |
| 4 | **12:08 PM** | 2.2h | 3.1 oz |
| 5 | **2:10 PM** | 2.0h | 3.5 oz |
| 6 | **4:15 PM** | 2.1h | 3.1 oz |
| 7 | **6:12 PM** | 1.9h | 3.4 oz |
| 8 | **7:52 PM** | 1.7h | 3.5 oz |
| 9 | **9:28 PM** | 1.6h | 3.5 oz |
| 10 | **11:36 PM** | 2.1h | 3.5 oz |

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Model Comparison

| Model | Status | First Feed | Feed Times |
| ----- | ------ | ---------- | ---------- |
| Slot Drift | Available | 4:35 AM | 4:35 AM, 10:01 AM, 10:46 AM, 12:14 PM, 3:31 PM, 5:59 PM, 7:52 PM, 9:54 PM |
| Analog Trajectory | Available | 3:37 AM | 3:37 AM, 6:35 AM, 9:40 AM, 11:55 AM, 2:10 PM, 4:15 PM, 6:12 PM, 8:11 PM, 10:37 PM |
| Latent Hunger State | Available | 3:07 AM | 3:07 AM, 5:25 AM, 7:43 AM, 10:00 AM, 12:18 PM, 2:35 PM, 4:53 PM, 7:11 PM, 9:28 PM, 11:46 PM |
| Survival Hazard | Available | 3:41 AM | 3:41 AM, 6:48 AM, 9:55 AM, 12:02 PM, 2:08 PM, 4:15 PM, 6:22 PM, 8:29 PM, 11:36 PM |
| Consensus Blend | Featured | 3:39 AM | 3:39 AM, 6:48 AM, 9:58 AM, 12:08 PM, 2:10 PM, 4:15 PM, 6:12 PM, 7:52 PM, 9:28 PM, 11:36 PM |

## Prior Run Retrospective

Comparing prior run `20260325-013519` predictions
against actual bottle feeds observed in the current export
(observed horizon:
24.0h,
coverage: 100%).

| Model | Score | Count | Timing | Pred/Actual/Matched | Status |
| ----- | ----- | ----- | ------ | ------------------- | ------ |
| Slot Drift | 53.3 | 100.0 | 28.4 | 8/8/8 | Full 24h observed |
| Analog Trajectory | 69.1 | 95.6 | 49.9 | 9/8/8 | Full 24h observed |
| Latent Hunger State | 54.0 | 94.6 | 30.9 | 9/8/8 | Full 24h observed |
| Survival Hazard | 59.5 | 95.6 | 37.0 | 9/8/8 | Full 24h observed |
| Consensus Blend | 59.5 | 90.9 | 39.0 | 10/8/8 | Full 24h observed |

## Historical Retrospective Accuracy

Aggregated from stored prior-run retrospectives. These scores
reflect the model versions that made those earlier predictions.

| Model | Comparisons | Full 24h Runs | Mean Score | Mean Count | Mean Timing | Avg Coverage |
| ----- | ----------- | ------------- | ---------- | ---------- | ----------- | ------------ |
| Analog Trajectory | 1 | 1 | 69.1 | 95.6 | 49.9 | 100% |
| Consensus Blend | 1 | 1 | 59.5 | 90.9 | 39.0 | 100% |
| Survival Hazard | 1 | 1 | 59.5 | 95.6 | 37.0 | 100% |
| Latent Hunger State | 1 | 1 | 54.0 | 94.6 | 30.9 | 100% |
| Slot Drift | 1 | 1 | 53.3 | 100.0 | 28.4 | 100% |

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

Combines the four scripted models into one forecast by finding where
a majority of models agree that a feed will happen.

For each predicted feed from any model, the blend looks at what the
other models predict nearby (within a 2-hour window) and asks: do
at least 3 of 4 models place a feed in this region? If so, that
region becomes a candidate consensus feed. Its predicted time is the
median of the contributing models' timestamps, and its volume is the
median of their volumes.

Many overlapping candidates can describe the same real feed, so the
blend picks the best non-overlapping set. Two rules prevent double-
counting: each individual model prediction can only support one
consensus feed, and two consensus feeds cannot be closer than 90
minutes apart. The final schedule is the highest-quality set of
feeds that satisfies both rules.

This approach means the consensus naturally favors feeds where
multiple models agree on timing, while isolated predictions that
only one or two models support are filtered out.

---

*Export: `export_narababy_silas_20260325.csv` · Dataset: `sha256:eb791b62...`
· Commit: `072620e`
· Generated: 2026-03-25 01:56:58*
