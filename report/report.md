# Silas Feeding Forecast

**Monday, March 23, 2026** · 24 days old · Cutoff: 11:41 AM

## Next Feeds

**Consensus Blend** predicts **9 feeds**
over the next 24 hours, totaling **28.4 oz**.

| Feed | Time | Gap | Volume |
| ---- | ---- | --- | ------ |
| 1 | **1:48 PM** | 2.1h | 2.6 oz |
| 2 | **4:04 PM** | 2.3h | 2.9 oz |
| 3 | **6:02 PM** | 2.0h | 3.1 oz |
| 4 | **9:09 PM** | 3.1h | 3.2 oz |
| 5 | **11:56 PM** | 2.8h | 3.5 oz |
| 6 | **2:38 AM** | 2.7h | 3.6 oz |
| 7 | **5:38 AM** | 3.0h | 3.7 oz |
| 8 | **8:50 AM** | 3.2h | 3.4 oz |
| 9 | **10:52 AM** | 2.0h | 2.4 oz |

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Model Comparison

| Model | Status | First Feed | Feed Times |
| ----- | ------ | ---------- | ---------- |
| Slot Drift | Available | 4:22 PM | 4:22 PM, 6:02 PM, 7:02 PM, 11:20 PM, 1:46 AM, 4:25 AM, 7:39 AM, 11:10 AM |
| Analog Trajectory | Available | 2:18 PM | 2:18 PM, 3:37 PM, 6:02 PM, 9:13 PM, 12:21 AM, 3:09 AM, 6:29 AM, 8:45 AM, 10:13 AM |
| Latent Hunger State | Available | 1:34 PM | 1:34 PM, 4:04 PM, 6:34 PM, 9:04 PM, 11:34 PM, 2:04 AM, 4:34 AM, 7:04 AM, 9:34 AM |
| Recent Cadence | Available | 2:50 PM | 2:50 PM, 5:59 PM, 9:09 PM, 12:18 AM, 3:27 AM, 6:37 AM, 9:46 AM |
| Phase Nowcast Hybrid | Available | 1:46 PM | 1:46 PM, 4:09 PM, 6:44 PM, 9:41 PM, 12:17 AM, 3:05 AM, 5:54 AM, 8:55 AM |
| Gap-Conditional | Available | 1:48 PM | 1:48 PM, 3:30 PM, 5:40 PM, 8:00 PM, 10:52 PM, 2:11 AM, 5:21 AM, 8:29 AM, 10:52 AM |
| Consensus Blend | Featured | 1:48 PM | 1:48 PM, 4:04 PM, 6:02 PM, 9:09 PM, 11:56 PM, 2:38 AM, 5:38 AM, 8:50 AM, 10:52 AM |

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

Uses breastfeed-merged events (45-minute merge window). Currently
affects only 3 of 81 events with negligible volume additions.
Infrastructure is in place for smooth circadian modulation of the
growth rate, but research found no benefit over the multiplicative
model's inherent volume-driven day/night sensitivity — larger
overnight feeds already produce longer predicted gaps.

### Recent Cadence

Bottle-only interval baseline. It keeps only full feeds (>=1.5 oz)
from the last 3 days, computes the gap between consecutive full
feeds, and applies exponential recency weights to those gaps using
the midpoint timestamp of each gap (half-life = 36h). Separately,
it estimates a day-level prior from recent feeds-per-day counts
using exponential day weights (half-life = 2 days), clamps that
rate to 6.5-10.5 feeds/day, and converts it into a target interval
`24 / feeds_per_day`. The final interval estimate is
`clip(0.7 * recent_gap + 0.3 * target_interval, 1.5h, 6.0h)`.

Projection is a constant-gap roll-forward from the latest observed
bottle time. For projected volumes, it builds a 12-bin time-of-day
profile over the last 7 days with exponential recency weighting.
Each bin stores a weighted mean volume; empty bins fall back to the
global weighted average. Each forecast point combines a simple
constant timing rule with a time-of-day volume lookup rather than
trying to model volume causally.

### Phase Nowcast Hybrid

Breastfeed-aware recursive state-space model built on a
Phase-Locked Oscillator (PLO) backbone. Inputs are bottle-centered
events whose effective volume includes breastfeeding logged within
the merge window. The model first estimates a nominal target
interval from the most recent up to 24 events: it computes
recency-weighted observed gaps (half-life = 36h), blends that
70/30 with a feeds-per-day prior derived from day-level weights
(clamped to 6.0-10.5 feeds/day), then clips to 1.5-6.0h.

The PLO initializes its period at that target interval and walks
forward through roughly the last 28 events. For each observed
transition, it predicts the next gap as
`period + 0.5 * (volume - running_avg)`, measures the error
versus the actual gap, and updates the period with filter gain
beta = 0.05. The running average volume updates as 70% old + 30%
new. During forecast rollout, the period mean-reverts 20% toward
the target interval on each step. Projected volume is
`clip(0.65 * tod_bin_mean + 0.35 * running_avg, 0.5, 8.0)`,
where the time-of-day profile is a 12-bin weighted volume profile
over the last 7 days with global-mean fallback for empty bins.

The "nowcast" layer fits a separate weighted linear regression on
the last 5 days of events to predict only the immediate next gap.
Features: volume, previous gap, rolling 3-gap mean, sin(hour),
cos(hour), with exponential sample weights (half-life = 36h). If
the local first-gap estimate is within 30 minutes of the phase
estimate and the latest event is a full feed (>=1.5 oz), the first
gap is blended as 40% phase + 60% regression. All later forecast
points shift by the same delta, preserving the PLO's internal
spacing. Otherwise the raw phase forecast is used unchanged.

### Gap-Conditional

Breastfeed-aware event-level regression. It uses bottle-centered
events whose effective volume includes breastfeeding merged into
the next bottle feed. Training data is the last 5 days of events
(including snacks). For each eligible event, the target is the
observed gap until the following feed. The feature vector is
`[volume, prev_gap, rolling_3gap_mean, sin(hour), cos(hour)]`.
Samples receive exponential recency weights with half-life = 36h,
and coefficients are fitted with weighted normal equations
`(X^T W X)^-1 X^T W y`. The predicted gap is clipped to 1.5-6.0h.

For projection, the model rolls forward autoregressively: each
predicted feed is appended as a synthetic event, using volume from
a 12-bin time-of-day profile built over the same 5-day window with
exponential recency weighting and global-mean fallback for empty
bins. The next gap is predicted from this updated synthetic state
using the same fitted coefficients. That preserves volume-to-gap
feedback across the entire 24-hour forecast horizon instead of
treating each step independently.

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
· Commit: `934b9de (dirty)`
· Generated: 2026-03-24 01:29:32*
