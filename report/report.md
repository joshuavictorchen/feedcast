# Silas Feeding Forecast

**Saturday, April 11, 2026** · 43 days old · Cutoff: 9:45 AM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

The newest data covers five episodes overnight into the morning of Apr 11. The headline is the return of multi-feed episodes after a five-day absence. The evening feed at 6:33 PM on Apr 10 (the 4.8 oz bottle flagged in the prior report) turned out to be the start of a three-feed cluster: 4.8 oz, then a 1.5 oz top-up 79 minutes later, then another 1.0 oz 24 minutes after that, totaling 7.3 oz across 1 hour 43 minutes. That is the largest single episode in the entire 7-day window by a wide margin. The morning of Apr 11 also produced a two-feed episode (3.5 oz at 9:12 AM + 1.0 oz formula top-up at 9:45 AM). Between Apr 6 and Apr 9, every episode was a clean single feed; the clustering that was common on Apr 4-5 had vanished entirely. Two multi-feed episodes in the span of 15 hours is a notable reappearance. Meanwhile, the four overnight gaps following the large evening episode ran 4.1, 4.1, 3.7, and 2.7 hours, averaging 3.7 hours. The two 4.1-hour gaps (to the 10:38 PM and 2:46 AM feeds) are the longest overnight stretches of the week, suggesting the big episode bought some extra satiety.

Across the full 7-day baseline, the dominant trend remains a shift to smaller per-feed volumes. Through Apr 4-7, the average episode landed around 4.0 oz with most episodes hitting 4 oz or above (78% of episodes on Apr 4-5, still over 85% on Apr 7). Starting Apr 8, the typical episode dropped to 3.0-3.5 oz, and the share reaching 4 oz fell sharply (Apr 9: only 2 of 9 episodes). Daily totals have held steady at 28-35 oz because the baby is simply eating more frequently during the day. Feed spacing has been remarkably stable all week at a 3.2-hour median overall, with a clean day/night split: 2.7 hours between daytime feeds and 3.7 hours overnight. There is no sign of a longer consolidated nighttime sleep stretch emerging; the overnight cadence is steady at roughly 3.5-4 hours.

| Period | Avg Episode Vol | Daily Intake | Feeds >= 4 oz | Multi-Feed Eps | Overnight Gap |
|--------|:-:|:-:|:-:|:-:|:-:|
| Apr 4-7 | 4.0 oz | 28-34 oz | 22 of 28 (79%) | 4 of 28 (14%) | 3.5 h |
| Apr 8-10 (pre-cutoff) | 3.5 oz | 29-31 oz | 8 of 22 (36%) | 0 of 22 (0%) | 3.7 h |
| Newest (Apr 10 eve - Apr 11 morn) | 4.7 oz* | on pace | 3 of 5 (60%) | 2 of 5 (40%) | 3.7 h |

*Newest average skewed by the 7.3 oz three-feed cluster; standalone feeds are 3.5-4.5 oz.

## Retrospective Accuracy

The "Last Run" column scores prior run `20260410-192301` against actuals observed in the current export (horizon 15.2h, coverage 63%).
The "Historical" column is the weighted mean across 4 stored retrospectives (1 full 24h, avg coverage 50%), reflecting the model versions that made those earlier predictions.
Higher is better (0-100 scale).

| Model | Last Run | Historical |
| ----- | -------: | ---------: |
| Latent Hunger State | 69.3 | 63.0 |
| Slot Drift | 57.2 | 61.5 |
| Agent Inference | 48.7 | 57.6 |
| Consensus Blend | 42.8 | 55.4 |
| Survival Hazard | 41.3 | 56.4 |
| Analog Trajectory | 34.5 | 53.5 |

Last Run scores are normalized to the observed window. Count and timing
breakdowns are in `diagnostics.yaml`.

## Methodologies


### Agent Inference

Slot-anchored cadence model that forecasts feeding episodes by combining
time-of-day slot medians with gap-level verification against a five-bucket
inter-episode gap profile.

The model collapses nearby bottle feeds into feeding episodes using the
shared clustering rule, then examines the most recent 7 days of
episode-level history. It assigns each episode to one of eight daily
slots based on its time of day (mid-morning, lunch, afternoon, evening,
pre-bed, first wake, deep night, morning wake). Within each slot, the
recency-weighted median clock time (48-hour exponential half-life) gives
the typical time that feed occurs.

These slot medians anchor the forecast to the baby's daily rhythm rather
than cascading gaps forward from the last episode. Each predicted feed
lands near the historical median for its slot, so a timing error in one
feed does not propagate to subsequent feeds.

Gap-level verification uses a five-bucket profile (daytime 07:00-17:00,
evening 17:00-19:00, pre-sleep 19:00-22:00, deep night 22:00-04:00,
early morning 04:00-07:00) computed from recency-weighted inter-episode
gaps. The forecast is checked to ensure each gap between consecutive
predictions falls within the plausible range for its time-of-day bucket.

Feed count is anchored to the recency-weighted mean of daily episode
counts from recent complete days (7.7 for this run, rounded to 8).
Whether a pre-bed feed is included depends on its recent frequency: it
appeared on 4 of the last 5 complete evenings, so it is included.
Predicted volume is 3.5 oz per episode, the modal volume across recent
episodes.

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

*Export: `export_narababy_silas_20260411.csv` · Dataset: `sha256:138b5d3a...`
· Commit: `6466f4d`
· Generated: 2026-04-11 12:55:44*
