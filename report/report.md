# Silas Feeding Forecast

**Saturday, April 11, 2026** · 43 days old · Cutoff: 7:35 PM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Trend Insights

The newest data covers four afternoon and evening episodes on Apr 11 (1:01 PM through 7:35 PM), all clean single-bottle feeds. This is a notable contrast to the prior 15-hour window, which saw two multi-feed clusters (the large 7.3 oz triple at 6:33 PM on Apr 10 and a 4.5 oz feed-plus-top-up at 9:12 AM on Apr 11). The clustering reappearance flagged in the last report turned out to be short-lived. Per-episode volumes are also nudging back up: two of the four afternoon feeds hit 4.5 oz, the first single-bottle feeds at or above 4 oz since the morning of Apr 8. The afternoon gaps ran tighter than usual (1.8, 2.5, and 2.3 hours after an initial 3.8-hour stretch), pulling the day's average spacing below the week's daytime median of 2.7 hours. That compression may reflect catch-up hunger after the longer post-morning gap, or simply an active afternoon. The final feed at 7:35 PM was 4.5 oz of formula with gripe water noted.

Across the full 7-day baseline, the most visible trend is a volume dip and partial recovery. Apr 4-7 averaged 4.0 oz per episode with 79% of episodes reaching 4 oz or above. Starting Apr 8, the typical feed dropped to 3.0-3.5 oz and the share hitting 4 oz fell to 28%. Apr 11 sits between the two at 3.9 oz with 43% at 4 oz or above. Daily totals have been stable throughout at 28-35 oz because the baby simply eats more often when individual feeds are smaller. Feed spacing shows a clean and steady day/night split: 2.7 hours between daytime feeds and 3.6 hours overnight, with no sign of the overnight gap lengthening toward a longer consolidated sleep stretch. Episode clustering has been episodic rather than trending: three multi-feed episodes on Apr 5, zero from Apr 6-9, two on Apr 10-11 morning, then clean again all afternoon.

| Period | Avg Episode Vol | Episodes >= 4 oz | Multi-Feed Eps | Daily Intake | Daytime Gap | Overnight Gap |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|
| Apr 4-7 | 4.0 oz | 79% | 3 of 24 (12%) | 28-35 oz | 2.7 h | 3.5 h |
| Apr 8-10 | 3.5 oz | 28% | 1 of 23 (4%) | 29-31 oz | 2.6 h | 3.8 h |
| Apr 11 (through 7:35 PM) | 3.9 oz | 43% | 1 of 7 (14%) | 27 oz (on pace) | 2.6 h | 3.7 h |

## Retrospective Accuracy

The "Last Run" column scores prior run `20260411-121840` against actuals observed in the current export (horizon 9.8h, coverage 41%).
The "Historical" column is the weighted mean across 5 stored retrospectives (1 full 24h, avg coverage 48%), reflecting the model versions that made those earlier predictions.
Higher is better (0-100 scale).

| Model | Last Run | Historical |
| ----- | -------: | ---------: |
| Analog Trajectory | 73.2 | 57.2 |
| Survival Hazard | 72.9 | 59.5 |
| Consensus Blend | 60.5 | 56.4 |
| Latent Hunger State | 57.9 | 62.1 |
| Slot Drift | 55.5 | 60.4 |
| Agent Inference | 54.3 | 57.0 |

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

Empirical cadence projection with sub-period overnight gap refinement. The model collapses raw bottle feeds into feeding episodes using the shared clustering rule (73-minute base gap, 80-minute extension for small top-ups), then examines the most recent 7 days of episode-level history with exponential recency weighting (48-hour half-life).

The baseline algorithm (`model.py`) computes recency-weighted median inter-episode gaps for two day-parts: overnight (19:00-07:00) and daytime (07:00-19:00). For the first predicted feed after the cutoff, it applies non-parametric conditional survival estimation (filtering to gaps longer than elapsed time since the last episode, then taking the weighted median of remaining times). Subsequent feeds step forward using the unconditional day-part median. Count calibration scales all gaps proportionally if the projected feed count diverges more than 30% from the recency-weighted mean of recent daily episode counts.

The agent layer refines the overnight gap by splitting it into sub-periods derived from the most recent 2-3 nights of episode data: evening-to-first-night (~3.4h from the conditional survival estimate), deep night from 22:00-04:00 starts (~4.0h, reflecting the recent trend toward longer mid-sleep stretches), early morning from 03:00-07:00 starts (~3.7h), and the pre-daytime transition from 06:00-09:00 starts (~2.7h). Daytime gaps use the baseline model's recency-weighted median of 2.5 hours. This sub-period refinement addresses the model's documented weakness of a single overnight median that averages together structurally different gap regimes.

Feed count (8 episodes over 24 hours) matches the recency-weighted daily episode count of 7.9. Volume is a flat 3.8 oz per predicted episode, the recency-weighted median across recent episode volumes.

---

*Export: `export_narababy_silas_20260411(1).csv` · Dataset: `sha256:f71d7d13...`
· Commit: `d313d6f`
· Generated: 2026-04-11 20:40:48*
