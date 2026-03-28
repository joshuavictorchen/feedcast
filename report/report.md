# Silas Feeding Forecast

**Wednesday, March 25, 2026** · 26 days old · Cutoff: 12:34 AM

## Next Feeds

![Featured Forecast](schedule.png)

## Model Trajectories

![Forecast Trajectories](spaghetti.png)

## Prior Run Retrospective

No new actuals since the prior run
(same dataset: `sha256:eb791b62...`).

## Historical Retrospective Accuracy

No completed retrospective history yet.

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
cost threshold (2 hours) are left unmatched.

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

Uses breastfeed-merged events (45-minute merge window) so that
nearby breastfeed volume is attributed to the next bottle event.
Infrastructure is in place for smooth circadian modulation of the
growth rate, but research found no benefit over the multiplicative
model's inherent volume-driven day/night sensitivity — larger
overnight feeds already produce longer predicted gaps.

### Survival Hazard

Probabilistic model that frames each feeding episode as a survival
event whose likelihood increases with elapsed time. Uses a Weibull
hazard function with a configured overnight/daytime split to capture
the structurally different feeding regimes.

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
survival function.

This methodology intentionally stays at the level of mechanism. Current
fitted values, empirical comparisons, and replay evidence live in
`research_results.txt`. Current production constants live in `model.py`.

### Consensus Blend (featured)

Combines the four scripted models into one forecast by finding where
a majority of models agree that a feed will happen.

Before comparing models, the blend collapses each model's predictions
into feeding episodes. If a model predicts a feed and a nearby top-up
within the cluster window, those predictions become one episode-level
point. This prevents attachment feeds from distorting the vote.

For each episode-level prediction from any model, the blend looks at
what the other models predict nearby (within a 2-hour window) and
asks: do at least 3 of 4 models place a feed in this region? If so,
that region becomes a candidate consensus feed. Its predicted time is
the median of the contributing models' timestamps, and its volume is
the median of their volumes.

Many overlapping candidates can describe the same real feed, so the
blend picks the best non-overlapping set. Two rules prevent double-
counting: each individual model prediction can only support one
consensus feed, and two consensus feeds cannot be closer than 105
minutes apart. The final schedule is the highest-quality set of
feeds that satisfies both rules.

This approach means the consensus naturally favors feeds where
multiple models agree on timing, while isolated predictions that
only one or two models support are filtered out.

---

*Export: `export_narababy_silas_20260325.csv` · Dataset: `sha256:eb791b62...`
· Commit: `ea466c3 (dirty)`
· Generated: 2026-03-27 22:30:53*
