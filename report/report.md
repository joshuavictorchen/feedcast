# Silas Feeding Forecast

**Monday, April 13, 2026** · 45 days old · Cutoff: 7:15 PM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

Apr 13 adds a full day of new data (8 episodes since the prior run's 8:24 PM cutoff on Apr 12), and the standout is consistency. Seven of eight episodes landed at 4.0 oz or above, with six hitting exactly 4.0 oz. That kind of uniformity is unusual for this baby; most days show at least 1-2 feeds in the low 3s. The only sub-4 feed was a 3.5 oz bottle at 2:50 PM, which still comfortably clears the mid-week lows. There were zero multi-feed episodes, meaning every bottle was a clean single feed with no top-ups needed. Total intake through 7:15 PM sits at 31.7 oz, on track to finish in the 32-34 oz range. Daytime gaps averaged 2.4 hours, a touch more relaxed than Apr 12's compressed 2.2 hours, suggesting the baby is eating well at a natural pace rather than catching up. Overnight followed the now-familiar shape: a 3.8-hour gap from the last evening feed to midnight, a 4.0-hour gap to the 4 AM feed, then a shorter 3.0-hour stretch to 7:10 AM.

Across the 7-day baseline, the mid-week volume dip (Apr 8-9 averaged 3.4-3.6 oz per episode) has clearly resolved: Apr 12-13 are back at 3.9-4.0 oz averages and both posted 4+ oz feeds two-thirds of the time or better. Daily intake has remained stable at 27-35 oz throughout the week regardless of per-feed volume, because the baby simply eats more often when individual bottles are smaller (Apr 6 and Apr 9 both ran 9+ episodes to compensate for more small feeds). Episode clustering has been minimal all week, with 0-1 multi-feed episodes per day and none at all on four of the eight days. The day/night rhythm is steady but not yet lengthening: overnight gaps average 3.6 hours, daytime gaps average 2.5 hours, and the longest single gap of the week was Apr 11's 4.7-hour evening stretch. There is no sign yet of a consolidated sleep window beyond 4-5 hours.

| Day | Episodes | Total Intake | Avg Vol/Ep | Episodes >= 4 oz | Multi-Feed | Day Gap | Night Gap |
|-----|:--------:|:------------:|:----------:|:-----------------:|:----------:|:-------:|:---------:|
| Apr 6 | 10 | 34.6 oz | 3.5 oz | 6 of 10 (60%) | 0 | 2.1 h | 3.2 h |
| Apr 7 | 7 | 27.8 oz | 4.0 oz | 6 of 7 (86%) | 0 | 2.7 h | 3.4 h |
| Apr 8 | 8 | 28.7 oz | 3.6 oz | 4 of 8 (50%) | 0 | 2.5 h | 3.6 h |
| Apr 9 | 9 | 30.8 oz | 3.4 oz | 2 of 9 (22%) | 0 | 2.8 h | 3.4 h |
| Apr 10 | 7 | 29.2 oz | 4.2 oz | 2 of 7 (29%) | 1 | 3.0 h | 4.2 h |
| Apr 11 | 7 | 27.3 oz | 3.9 oz | 3 of 7 (43%) | 1 | 2.6 h | 3.8 h |
| Apr 12 | 9 | 34.7 oz | 3.9 oz | 6 of 9 (67%) | 1 | 2.2 h | 3.8 h |
| **Apr 13** | **8** | **31.7 oz** | **4.0 oz** | **7 of 8 (88%)** | **0** | **2.4 h** | **3.6 h** |

## Retrospective Accuracy

The "Last Run" column scores prior run `20260412-224447` against actuals observed in the current export (horizon 22.9h, coverage 95%).
The "Historical" column is the weighted mean across 7 stored retrospectives (2 full 24h, avg coverage 62%), reflecting the model versions that made those earlier predictions.
Higher is better (0-100 scale).

| Model | Last Run | Historical |
| ----- | -------: | ---------: |
| Agent Inference | 82.2 | 63.8 |
| Latent Hunger State | 63.9 | 62.4 |
| Slot Drift | 57.9 | 59.3 |
| Analog Trajectory | 56.9 | 58.2 |
| Survival Hazard | 55.5 | 60.0 |
| Consensus Blend | 54.2 | 56.8 |

Last Run scores are normalized to the observed window. Count and timing
breakdowns are in `diagnostics.yaml`.

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

Empirical cadence projection with four-bucket day-part split. The model collapses raw bottle feeds into feeding episodes using the shared clustering rule (73-minute base gap, 80-minute extension for small top-ups), then examines the most recent 7 days of episode-level history with exponential recency weighting (48-hour half-life).

Inter-episode gaps are classified into four sub-periods by the clock hour of the feed that starts the gap: evening (19:00-22:00, weighted median 3.77h), deep night (22:00-03:00, weighted median 4.03h), early morning (03:00-07:00, weighted median 2.95h), and daytime (07:00-19:00, weighted median 2.31h). The forecast steps forward from the cutoff, applying the sub-period gap corresponding to each predicted feed's clock hour. This four-bucket split addresses the documented weakness of the two-bucket baseline, which blends structurally different overnight regimes into a single median: the evening-to-first-night transition, consistent deep-night wake intervals, and shorter pre-dawn gaps.

Feed count (8 episodes over 24 hours) aligns with the recency-weighted daily episode count of 7.7, an improvement over the two-bucket baseline which produced 7 episodes. Volume is a flat 4.0 oz per predicted episode, the recency-weighted median across recent episode volumes.

---

*Export: `export_narababy_silas_20260413.csv` · Dataset: `sha256:1820a6f3...`
· Commit: `a1d53a2`
· Generated: 2026-04-13 20:53:29*
