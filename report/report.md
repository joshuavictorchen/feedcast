# Silas Feeding Forecast

**Thursday, April 16, 2026** · 48 days old · Cutoff: 8:57 PM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

The three days of new data since the last forecast (Apr 14-16) reinforce 4 oz as the new normal: 26 episodes averaging 3.93 oz, with today (Apr 16) the most consistent day of the week at 4.08 oz per feed and 6 of 7 episodes hitting at least 4 oz. The only standout in the new window is Wednesday evening, where Silas ate three 2-oz "snack" bottles back to back between 5:42 and 7:32 PM (a single 6-oz episode by clustering rules) and then stretched 4.5 hours to the next feed. That was the lone meaningful multi-feed cluster across the new data; otherwise feeds have been clean singles. Today also produced the largest individual bottle of the week (5 oz at 6:03 PM) and the most rhythmic daytime spacing yet, with all gaps falling between 2.6 and 3.6 hours.

In the broader 7-day picture, the early-baseline mid-week dip (avg per-feed volumes of 3.4-3.6 oz on Apr 9-10) has fully resolved, with daily averages now sitting at 3.8-4.1 oz. Daily intake remains stable in a tight 28-34 oz band regardless of episode count, because the baby trades feed size against feed frequency. Overnight gaps are not yet lengthening: the longest deep-night stretches stay around 3.4-3.8 hours every night, and Apr 14-16 actually have slightly tighter night cadence than earlier in the week. There is still no sign of a consolidated long sleep window. Episode clustering has been rare (0-1 per day), and the Apr 15 evening triple is the only cluster of note.

| Day | Episodes | Total | Avg/Ep | >=4 oz | Multi | Day Gap | Night Gap |
|-----|:--------:|:-----:|:------:|:------:|:-----:|:-------:|:---------:|
| Apr 10 | 7 | 29.1 oz | 4.16 oz | 2 of 7 | 1 | 3.0 h | 4.2 h |
| Apr 11 | 7 | 27.3 oz | 3.90 oz | 3 of 7 | 1 | 3.0 h | 3.2 h |
| Apr 12 | 9 | 34.8 oz | 3.86 oz | 6 of 9 | 1 | 2.4 h | 3.5 h |
| Apr 13 | 9 | 35.7 oz | 3.97 oz | 8 of 9 | 0 | 2.6 h | 3.6 h |
| Apr 14 | 9 | 33.9 oz | 3.77 oz | 5 of 9 | 0 | 2.5 h | 3.0 h |
| Apr 15 | 8 | 31.5 oz | 3.94 oz | 4 of 8 | 1 | 3.1 h | 3.1 h |
| **Apr 16** | **7** | **28.5 oz** | **4.08 oz** | **6 of 7** | **1** | **2.9 h** | **3.0 h** |

## Retrospective Accuracy

The "Last Run" column scores prior run `20260413-202434` against actuals observed in the current export (horizon 24.0h, coverage 100%).
The "Historical" column is the weighted mean across 8 stored retrospectives (3 full 24h, avg coverage 67%), reflecting the model versions that made those earlier predictions.
Higher is better (0-100 scale).

| Model | Last Run | Historical |
| ----- | -------: | ---------: |
| Agent Inference | 82.9 | 67.2 |
| Latent Hunger State | 66.7 | 63.1 |
| Analog Trajectory | 63.2 | 59.1 |
| Slot Drift | 62.3 | 59.9 |
| Survival Hazard | 56.3 | 59.3 |
| Consensus Blend | 51.4 | 55.9 |


## Methodologies


### Slot Drift

Daily template model that identifies recurring feeding episode slots
and tracks their drift over time. Instead of predicting individual
gaps, it asks: "what does a typical day look like, and how is it
shifting?"

The model first collapses raw feed history into feeding episodes
(close-together feeds that form a single hunger event). It then
groups episodes into calendar days, determines a canonical slot count
(median daily episode count over the lookback window), and builds a
template of slot positions by taking the median hour-of-day for each
ordinal position across days that match the canonical count. Each
day's episodes are aligned to the template using the Hungarian
algorithm with circular time-of-day distance. Episodes that exceed a
cost threshold are left unmatched.

After alignment, each slot has a position history across days. A
recency-weighted linear regression estimates the current position and
drift rate for each slot. The forecast projects
today's unfilled slots and tomorrow's full schedule, with one
additional day of drift applied to tomorrow's positions. Volume is a
recency-weighted per-slot average.

Uses bottle-only events (no breastfeed merge). Breastfeeding volume
is estimated, not measured, and Slot Drift is primarily a timing model.

### Analog Trajectory

Instance-based forecasting that asks "when have we seen a feeding
episode like this before, and what happened next?" Instead of fitting a
global function, the model stores historical episode states with their
subsequent observed trajectories and retrieves the closest analogs at
forecast time.

The model first builds bottle-only events, then collapses close-together
feeds into feeding episodes. Each episode state is summarized by six
features: last gap, rolling mean gap, last volume, rolling mean volume,
and circular hour-of-day (`sin_hour`, `cos_hour`). The rolling means use
a configurable lookback window.

Similarity is weighted Euclidean distance with per-feature weights that
currently give hour-of-day the strongest influence, while gap and
volume remain available as supporting context. The model retrieves the K
nearest historical states and weights them by both proximity and
recency.

The forecast is produced by blending neighbor gap sequences step by
step. Gaps are rolled forward from the cutoff to generate predicted feed
times, and per-step volumes are weighted averages from the same neighbor
trajectories. The number of predicted steps is the median neighbor
trajectory length.

The model requires at least 10 complete historical states. A state is
complete only if it has at least 3 future events and at least one future
event at least 20 hours after the anchor episode. This is a practical
completeness rule, not a literal requirement that the full future be
observed for all 24 hours.

### Latent Hunger State

Mechanistic model that treats hunger as a hidden variable rising over
time and partially reset by each feed. A larger feed drives hunger
lower, so the next feed takes longer. The model simulates this process
forward to produce a 24-hour schedule.

The satiety reset is multiplicative: after a feeding episode of V
ounces, hunger drops to threshold × exp(−rate × V). This guarantees
partial resets — no feed fully zeroes hunger — so volume always
influences the predicted gap. The growth rate (how fast hunger
rebuilds) is estimated from recent episodes using a recency-weighted
average, allowing the model to track the baby's changing metabolic
pace.

The model operates on episode-level history: close-together feeds are
collapsed into single feeding episodes before growth rate estimation,
volume computation, and hunger state tracking. This removes
cluster-internal noise (e.g., top-up feeds) that would otherwise
contaminate the growth rate signal.

At forecast time the model computes the current hunger level from the
last observed episode and elapsed time, then simulates forward: hunger
grows until it crosses the threshold, a feed fires at the simulation
median volume, hunger resets, and the cycle repeats.

Future feed volumes are not modeled as a separate trajectory. The
forward simulation uses the recency-median episode volume for each
predicted feed.

Uses breastfeed-merged events so that nearby breastfeed volume is
attributed to the next bottle event.
Infrastructure is in place for smooth circadian modulation of the
growth rate, but research found no benefit over the multiplicative
model's inherent volume-driven day/night sensitivity — larger
overnight feeds already produce longer predicted gaps.

### Survival Hazard

Hazard-based point-forecast model that frames each feeding episode as a
survival event whose likelihood increases with elapsed time. Uses a
Weibull hazard function with a configured overnight/daytime split to
capture the structurally different feeding regimes.

Raw bottle feeds are collapsed into feeding episodes, removing
cluster-internal gaps that would otherwise contaminate the gap
distribution. All model computation — scale estimation, conditional
survival, simulation — operates on episode-level data.

Overnight episodes follow a higher-shape Weibull: more regular timing
with tighter clustering around the median. Daytime episodes follow a
lower-shape Weibull: more variable timing with a broader spread. The
scale parameter for each period is estimated at runtime from recent
same-period episode gaps, allowing the model to track the baby's
changing pace.

The forecast uses the median of the Weibull survival function as the
point prediction — the time at which there is a 50% probability the
next feed has occurred. The first predicted feed accounts for the time
already elapsed since the last observed episode using the conditional
survival function. Later predicted feeds chain deterministic Weibull
medians rather than sampling from the full distribution.

This methodology intentionally stays at the level of mechanism. Current
fitted values, empirical comparisons, and replay evidence live in
`artifacts/research_results.txt`. Current production constants live in `model.py`.

### Consensus Blend (featured)

Combines the scripted models into one forecast by finding where
a majority of models agree that a feed will happen.

Before comparing models, the blend collapses each model's predictions
into feeding episodes. If a model predicts a feed and a nearby top-up
within the cluster window, those predictions become one episode-level
point. This prevents attachment feeds from distorting the vote.

For each episode-level prediction from any model, the blend looks at
what the other models predict nearby (within a configurable search
window) and asks: do a strict majority of models place a feed in this
region? If so, that region becomes a candidate consensus feed. Its
predicted time is the median of the contributing models' timestamps,
and its volume is the median of their volumes.

Many overlapping candidates can describe the same real feed, so the
blend picks the best non-overlapping set. Two rules prevent double-
counting: each individual model prediction can only support one
consensus feed, and two consensus feeds cannot be closer than the
configured conflict window. The final schedule is the highest-quality set of
feeds that satisfies both rules.

This approach means the consensus naturally favors feeds where
multiple models agree on timing, while isolated predictions that
only one or two models support are filtered out.

### Agent Inference

Four-bucket cadence projection. The model collapses raw bottle feeds
into feeding episodes using the shared clustering rule (73-minute base
gap, 80-minute extension for small top-ups), then examines the most
recent 7 days of episode-level history with exponential recency
weighting (48-hour half-life).

Each inter-episode gap is tagged by the clock hour of the feed that
starts the gap and assigned to one of four sub-periods: evening
(19:00-22:00), deep night (22:00-03:00), early morning (03:00-07:00),
and daytime (07:00-19:00). Within each sub-period, the recency-weighted
median of observed gaps yields a characteristic gap duration. For this
run: evening 3.82h, deep night 3.74h, early morning 2.64h, daytime
2.59h.

Starting from the cutoff, the forecast steps forward by applying the
sub-period gap that matches the clock hour of each predicted feed's
start. Feed count (8 episodes over 24 hours) aligns with the
recency-weighted daily episode count of 8.1. Volume is a flat 4.0 oz
per predicted episode, the recency-weighted median across recent
episode volumes.

---

*Export: `export_narababy_silas_20260416.csv` · Dataset: `sha256:383bff93...`
· Commit: `76e97cf`
· Generated: 2026-04-16 23:00:34*
