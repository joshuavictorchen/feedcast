# Silas Feeding Forecast

**Friday, March 27, 2026** · 28 days old · Cutoff: 9:00 PM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

Over the past two weeks, Silas's feeding pattern has shifted noticeably toward larger, more regular feeds. In the first week (Mar 14–20), episodes averaged 3.2 oz and daily intake ranged from 24–27 oz; by the second week (Mar 21–27), episodes averaged 3.8 oz and daily intake climbed to 29–33 oz. The last three days are especially striking — feeds have locked in at a near-uniform 4.0 oz, with daily totals around 30–33 oz. Feed spacing has held steady at roughly 3 hours throughout, but the feeds themselves are getting cleaner: multi-feed episodes (a main bottle plus a top-up within ~70 minutes) dropped from 18% of episodes in week 1 to 12% in week 2, and the last two full days (Mar 25–26) had almost none. The baby is consolidating into distinct, well-separated feeds rather than snacking.

A consistent day/night rhythm hasn't firmly established yet — overnight gaps (11 PM–7 AM) are running about 3.0–3.5 hours, similar to daytime. There are occasional longer overnight stretches (a 5.2-hour gap appeared on the night of Mar 23, and a 4.3-hour gap on Mar 24), but these are intermittent rather than a reliable pattern. The steadiest signal right now is the overall regularity: the last three days look like a metronome compared to earlier in the window, when afternoon cluster-feeds and short-gap top-ups were common.

| Period | Avg Episode Vol | Daily Intake | Multi-Feed Episodes | Avg Gap |
|--------|:-:|:-:|:-:|:-:|
| Mar 14–20 (week 1) | 3.2 oz | 24–27 oz | 18% | 3.0 h |
| Mar 21–27 (week 2) | 3.8 oz | 29–33 oz | 12% | 3.0 h |
| Mar 25–27 (last 3d) | 4.0 oz | 29–33 oz | 4% | 3.1 h |

## Prior Run Retrospective

No new actuals since the prior run
(same dataset: `sha256:11840296...`).

## Historical Retrospective Accuracy

Aggregated from stored prior-run retrospectives. These scores
reflect the model versions that made those earlier predictions.

| Model | Comparisons | Full 24h Runs | Mean Score | Mean Count | Mean Timing | Avg Coverage |
| ----- | ----------- | ------------- | ---------- | ---------- | ----------- | ------------ |
| Slot Drift | 1 | 1 | 69.0 | 91.9 | 51.8 | 100% |
| Survival Hazard | 1 | 1 | 67.0 | 87.0 | 51.5 | 100% |
| Latent Hunger State | 1 | 1 | 63.3 | 85.7 | 46.8 | 100% |
| Consensus Blend | 1 | 1 | 62.4 | 81.3 | 47.9 | 100% |
| Analog Trajectory | 1 | 1 | 60.0 | 81.3 | 44.3 | 100% |

## Methodologies


### Agent Inference

Empirical Cadence Projection. The agent runs a non-parametric forecasting
model that projects forward from recent inter-episode gap patterns. Gaps
are split by day-part (overnight vs. daytime) and weighted toward the
most recent 2–3 days. The first predicted feed uses a conditional
survival estimate based on elapsed time since the last episode; subsequent
feeds step forward at the day-part-appropriate gap median. A count
calibration step adjusts overall spacing if the projected feed count
diverges significantly from recent daily episode counts. The model
and its constants are maintained in a persistent workspace and may be
evolved by agents across runs.

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

*Export: `export_narababy_silas_20260327.csv` · Dataset: `sha256:11840296...`
· Commit: `ea33bd7`
· Generated: 2026-04-10 11:48:15*
