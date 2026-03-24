# Silas Feeding Forecast

**Monday, March 23, 2026** · 24 days old · Cutoff: 11:41 AM

## Next Feeds

**Consensus Blend** predicts **9 feeds**
over the next 24 hours, totaling **29.9 oz**.

| Feed | Time | Gap | Volume |
| ---- | ---- | --- | ------ |
| 1 | **1:52 PM** | 2.2h | 3.3 oz |
| 2 | **4:04 PM** | 2.2h | 3.1 oz |
| 3 | **6:09 PM** | 2.1h | 3.3 oz |
| 4 | **9:04 PM** | 2.9h | 3.5 oz |
| 5 | **11:34 PM** | 2.5h | 3.4 oz |
| 6 | **2:23 AM** | 2.8h | 3.6 oz |
| 7 | **5:11 AM** | 2.8h | 3.5 oz |
| 8 | **8:12 AM** | 3.0h | 3.3 oz |
| 9 | **10:40 AM** | 2.5h | 3.0 oz |

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Model Comparison

| Model | Status | First Feed | Feed Times |
| ----- | ------ | ---------- | ---------- |
| Slot Drift | Available | 4:22 PM | 4:22 PM, 6:02 PM, 7:02 PM, 11:20 PM, 1:46 AM, 4:25 AM, 7:39 AM, 11:10 AM |
| Analog Trajectory | Available | 2:18 PM | 2:18 PM, 3:37 PM, 6:02 PM, 9:13 PM, 12:21 AM, 3:09 AM, 6:29 AM, 8:45 AM, 10:13 AM |
| Latent Hunger State | Available | 1:34 PM | 1:34 PM, 4:04 PM, 6:34 PM, 9:04 PM, 11:34 PM, 2:04 AM, 4:34 AM, 7:04 AM, 9:34 AM |
| Survival Hazard | Available | 1:52 PM | 1:52 PM, 4:04 PM, 6:16 PM, 8:27 PM, 11:34 PM, 2:41 AM, 5:48 AM, 8:55 AM, 11:07 AM |
| Consensus Blend | Featured | 1:52 PM | 1:52 PM, 4:04 PM, 6:09 PM, 9:04 PM, 11:34 PM, 2:23 AM, 5:11 AM, 8:12 AM, 10:40 AM |

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

At forecast time the model summarizes the current state as a feature
vector: recent gap and volume averages plus circular hour-of-day.
It finds the most similar historical states using normalized Euclidean
distance, weights them by a combination of proximity and recency
(3-day half-life), and produces the forecast by averaging their
actual future gap sequences. The predicted gaps are rolled forward
from the cutoff time to produce absolute feed times.

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

Median-timestamp ensemble across the scripted base models. It does
not align forecasts by feed index, because different models may
emit different numbers of future feeds. Instead, on each step it
takes the next unconsumed point from every available model,
computes the median timestamp as an anchor, and forms a cluster
from points within +/- 90 minutes of that anchor.

Points that fall earlier than the cluster window are discarded as
leading outliers. If fewer than two models fall into the current
cluster, the earliest candidate is discarded and the procedure
retries. Once a cluster contains at least two models, the
consensus point uses the median timestamp and mean volume across
that cluster, with its gap measured from the previous consensus
point. The process repeats until fewer than two models have
points left. This lets the blend stay robust when one model
predicts an extra snack feed or drifts earlier/later than the
others.

## Notes

- **Limited data:** 8 days of usable history since
  March 15, 2026. A few minutes of difference can look
  meaningful before enough real retrospectives accumulate.
- **Non-stationarity:** Silas is growing fast. Older runs are still
  useful, but they are not ground truth for the next developmental
  phase.
- **Breastfeeding volumes are estimated:**
  The 0.5 oz/30 min, merged within 45 min heuristic is not measured intake,
  so any model that uses it inherits that uncertainty.
- **Diagnostics artifact:** Detailed model diagnostics are saved
  separately in `diagnostics.yaml` so the main report stays
  readable.

---

*Export: `export_narababy_silas_20260323.csv` · Dataset: `sha256:7b6cdd2f...`
· Commit: `16c6922 (dirty)`
· Generated: 2026-03-24 13:02:24*
