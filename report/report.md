# Silas Feeding Forecast

**Friday, April 10, 2026** · 42 days old · Cutoff: 6:33 PM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

The newest data since the prior run is a single feed: 4.8 oz at 6:33 PM, the largest single feed in three days. It stands out against a backdrop of remarkably consistent 3.4-3.5 oz feeds that dominated Apr 10 up to that point and most of Apr 9. Whether this is a one-off hungry feed or the start of volumes ticking back up is worth watching. It does not, on its own, reverse the week's clear downward volume trend, but it is the first feed above 4 oz since yesterday afternoon.

Zooming out to the 7-day window, the main story remains the gradual drop in per-feed volume. Through Apr 4-5, nearly every feed landed at 4.0 oz or higher, and multi-feed episodes (a main bottle followed by a small top-up) appeared regularly. Starting around Apr 8 afternoon, the typical feed settled to 3.0-3.5 oz, and top-up feeds disappeared entirely. The share of feeds reaching 4 oz fell from 100% on Apr 4 to 22% on Apr 9. Despite smaller feeds, daily totals have held in the 28-31 oz range because feed count has stayed the same or even crept up (Apr 9 had 9 episodes). Feed spacing is essentially unchanged across the full week at roughly 3 hours, day and night. Overnight gaps are modestly longer (3.6h vs. 2.8h daytime) but show no sign yet of consolidating into a longer sleep stretch.

| Period | Avg Episode Vol | Daily Intake | Feeds >= 4 oz | Multi-Feed Eps | Avg Gap |
|--------|:-:|:-:|:-:|:-:|:-:|
| Apr 3-7 | 4.0 oz | 28-35 oz | 78% | 4 of 26 (15%) | 3.0 h |
| Apr 8-10 | 3.5 oz | 28-31 oz | 30% | 0 of 23 (0%) | 3.0 h |

## Prior Run Retrospective

Comparing prior run `20260410-180114` predicted episodes
against actual feeding episodes observed in the current export
(observed horizon:
3.1h,
coverage: 13%).

| Model | Score | Count | Timing | Episodes (Pred/Actual/Matched) | Status |
| ----- | ----- | ----- | ------ | ------------------------------ | ------ |
| Slot Drift | 24.8 | 100.0 | 6.1 | 1/1/1 | Partial horizon (3.1h observed) |
| Analog Trajectory | 68.8 | 100.0 | 47.3 | 1/1/1 | Partial horizon (3.1h observed) |
| Latent Hunger State | 83.7 | 100.0 | 70.1 | 1/1/1 | Partial horizon (3.1h observed) |
| Survival Hazard | 79.3 | 100.0 | 62.8 | 1/1/1 | Partial horizon (3.1h observed) |
| Consensus Blend | 81.5 | 100.0 | 66.4 | 1/1/1 | Partial horizon (3.1h observed) |
| Agent Inference | 68.1 | 100.0 | 46.3 | 1/1/1 | Partial horizon (3.1h observed) |
Scores are normalized to the observed window. Coverage shows how much of
the 24-hour horizon has actually resolved so far.

## Historical Retrospective Accuracy

Aggregated from stored prior-run retrospectives. These scores
reflect the model versions that made those earlier predictions.

| Model | Comparisons | Full 24h Runs | Mean Score | Mean Count | Mean Timing | Avg Coverage |
| ----- | ----------- | ------------- | ---------- | ---------- | ----------- | ------------ |
| Survival Hazard | 3 | 1 | 63.7 | 91.4 | 45.1 | 45% |
| Slot Drift | 3 | 1 | 63.6 | 89.0 | 51.3 | 45% |
| Analog Trajectory | 3 | 1 | 62.7 | 96.1 | 41.3 | 45% |
| Agent Inference | 3 | 1 | 61.9 | 96.1 | 40.7 | 45% |
| Consensus Blend | 3 | 1 | 61.5 | 94.9 | 42.6 | 45% |
| Latent Hunger State | 3 | 1 | 60.0 | 88.0 | 42.8 | 45% |

## Methodologies


### Agent Inference

Five-bucket cadence model that forecasts feeding episodes by projecting
forward from recency-weighted gap medians estimated in narrow
time-of-day windows. The model collapses nearby bottle feeds into
feeding episodes using the shared clustering rule, then examines the
most recent 7 days of episode-level history.

For each consecutive pair of episodes, it computes the inter-episode gap
and tags it by the hour of the episode that started the gap. Gaps are
assigned to five buckets: evening (17:00-19:00), pre-sleep
(19:00-22:00), deep night (22:00-04:00), early morning (04:00-07:00),
and daytime (07:00-17:00). Each gap receives a recency weight with a
48-hour exponential half-life, and the weighted median is taken within
each bucket. When the predicted evening feed lands in the 20:00 hour,
the pre-sleep gap is refined with a narrower weighted median built from
historical gaps that also started in the 20:00 hour; the final
pre-sleep estimate blends 40% of that narrow estimate with 60% of the
broader bucket estimate.

Starting from the last observed episode, the model projects each next
feed by applying the bucket-appropriate gap for the predicted feed's
start time. Predicted volume is the recency-weighted median of recent
episode volumes, held at 3.5 oz for all feeds. Total feed count is
anchored to the recency-weighted mean of daily episode counts from
recent complete days, which yields an 8-feed 24-hour schedule for this
run.

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

---

*Export: `export_narababy_silas_20260410(2).csv` · Dataset: `sha256:ff8b0a11...`
· Commit: `d5dc331`
· Generated: 2026-04-10 19:40:41*
