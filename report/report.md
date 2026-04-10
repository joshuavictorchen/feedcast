# Silas Feeding Forecast

**Friday, April 10, 2026** · 42 days old · Cutoff: 9:52 AM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

The most notable shift in the last two weeks is a volume dip over the past three days. From Mar 27 through Apr 6, Silas's episodes averaged right at 4.0 oz, with 79% of feeds coming in at 4 oz or more. Starting Apr 7 and especially Apr 8–10, the typical feed has dropped to 3.0–3.5 oz — only 44% of episodes are hitting 4 oz now. Daily totals haven't fallen as sharply (still 28–31 oz on full days) because he's compensating with slightly more frequent feeds, but the per-feed size is distinctly smaller. Whether this is a brief dip or the start of a new baseline is worth watching over the next few days.

On the structural side, feeds continue to consolidate. Multi-feed episodes — a main bottle followed by a top-up within about an hour — dropped from 13% in the first week to 7% in the second, and the last four days have had almost none. He's eating in clean, single-feed episodes now rather than snacking. Feed spacing remains remarkably stable at a 3-hour average throughout the window, day and night. Overnight gaps are still in the 3–4.5 hour range with no reliable trend toward longer stretches; the longest gaps (4.7h on Apr 4, 5.1h on Apr 5) remain one-offs rather than a consistent pattern. One oddity: Apr 6 featured a midday cluster of three small feeds (2.5, 2.0, 1.8 oz between 9:30 AM and 12:40 PM), which is the opposite of the consolidation trend — but it hasn't recurred since.

| Period | Avg Episode Vol | Daily Intake | Multi-Feed Episodes | Avg Gap |
|--------|:-:|:-:|:-:|:-:|
| Mar 27 – Apr 2 (week 1) | 4.0 oz | 29–37 oz | 13% | 3.0 h |
| Apr 3 – Apr 9 (week 2) | 3.8 oz | 28–35 oz | 7% | 3.0 h |
| Apr 7 – Apr 10 (last 3–4d) | 3.6 oz | 28–31 oz | 0% | 3.0 h |

## Prior Run Retrospective

Comparing prior run `20260410-113849` predicted episodes
against actual feeding episodes observed in the current export
(observed horizon:
24.0h,
coverage: 100%).

| Model | Score | Count | Timing | Episodes (Pred/Actual/Matched) | Status |
| ----- | ----- | ----- | ------ | ------------------------------ | ------ |
| Slot Drift | 64.4 | 93.2 | 44.6 | 6/7/6 | Full 24h observed |
| Analog Trajectory | 57.6 | 94.3 | 35.1 | 8/7/7 | Full 24h observed |
| Latent Hunger State | 54.0 | 91.8 | 31.7 | 8/7/7 | Full 24h observed |
| Survival Hazard | 55.5 | 87.3 | 35.3 | 9/7/7 | Full 24h observed |
| Consensus Blend | 49.0 | 92.5 | 25.9 | 8/7/7 | Full 24h observed |
| Agent Inference | 55.1 | 94.3 | 32.2 | 8/7/7 | Full 24h observed |

## Historical Retrospective Accuracy

Aggregated from stored prior-run retrospectives. These scores
reflect the model versions that made those earlier predictions.

| Model | Comparisons | Full 24h Runs | Mean Score | Mean Count | Mean Timing | Avg Coverage |
| ----- | ----------- | ------------- | ---------- | ---------- | ----------- | ------------ |
| Slot Drift | 1 | 1 | 64.4 | 93.2 | 44.6 | 100% |
| Analog Trajectory | 1 | 1 | 57.6 | 94.3 | 35.1 | 100% |
| Survival Hazard | 1 | 1 | 55.5 | 87.3 | 35.3 | 100% |
| Agent Inference | 1 | 1 | 55.1 | 94.3 | 32.2 | 100% |
| Latent Hunger State | 1 | 1 | 54.0 | 91.8 | 31.7 | 100% |
| Consensus Blend | 1 | 1 | 49.0 | 92.5 | 25.9 | 100% |

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

*Export: `export_narababy_silas_20260410.csv` · Dataset: `sha256:8dc1ea26...`
· Commit: `e357ed6`
· Generated: 2026-04-10 13:53:49*
