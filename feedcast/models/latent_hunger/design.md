# Latent Hunger State Design Decisions

## Multiplicative vs. additive satiety

Two satiety mechanisms were compared:

- **Additive**: hunger -= coefficient × volume. Collapses to a
  constant-gap predictor because the optimizer zeros out hunger after
  every feed.
- **Multiplicative**: hunger = threshold × exp(−rate × volume).
  Guarantees partial resets so volume always matters.

Multiplicative wins on both accuracy and prediction diversity. The
additive model is a dressed-up constant-gap predictor; the
multiplicative model produces real volume-dependent variation. See
`research.py` and `research_results.txt` for current numbers.

## Fixed threshold, fitted growth rate

The hunger threshold is fixed at 1.0. This resolves the scale
redundancy between threshold and growth rate (doubling both produces
identical predictions). Only the growth rate is estimated, from
recent observed (volume, gap) pairs:

    implied_gr = (1 − exp(−satiety_rate × volume)) / actual_gap

The recency-weighted average of implied rates adapts the model to the
baby's current pace. This is cheaper and more transparent than a grid
search at forecast time, and directly tracks trends as the baby grows.

## Satiety rate

The satiety rate is a fixed structural parameter selected by canonical
multi-window tuning (`tune_model()` sweep). Growth rate is fit at
runtime, so only the satiety rate needs to be stable across data
windows. See `research.md` for the canonical sweep results.

The canonical sweep selects a lower satiety rate (0.05) than the
internal walk-forward diagnostic prefers (~0.6). The tuning surface is
shallow — nearby values produce similar accuracy. The model retains
meaningful volume sensitivity at this rate (satiety effect scales ~3.7x
from 1oz to 4oz), but the absolute effects are smaller than at higher
rates, producing more uniform gap predictions that score better on
canonical episode-count matching.

The current value is recorded in `model.py`.

## Circadian modulation: infrastructure only

Research found circadian amplitude = 0.0 optimal for the
multiplicative model. Volume already correlates with time of day
(bigger overnight feeds → longer predicted gaps), so explicit
circadian modulation is redundant.

The circadian infrastructure (smooth cosine modulation on growth rate)
is kept in `model.py` because the baby may develop stronger day/night
patterns as growth continues.

## Runtime growth rate estimation

Rather than fixing the growth rate from research, the model estimates
it at forecast time from the lookback window. The baby is growing, so
feeding pace changes week to week. Runtime estimation uses the
closed-form inverse of the gap prediction equation, avoiding expensive
grid search.

## Lookback window and recency half-life

The lookback window is 7 days. This balances having enough data points
for a stable estimate against the need to track changing feeding pace.

The recency half-life is set to LOOKBACK_DAYS × 24 hours, giving 50%
weight at the lookback boundary — roughly equal weighting across the
full window with modest recency bias.

This broad averaging works because the model operates on episode-level
history, where all gaps are real inter-episode gaps. Without episode
collapse, the noise from cluster-internal short gaps would require
much more aggressive recency weighting to track the true feeding pace.
Replay sweeps confirmed this interaction: shorter half-lives help on
raw data but hurt on episode data, and vice versa.

## Episode-level history

Raw feed events are collapsed into episodes via `episodes_as_events()`
before growth rate estimation, simulation volume computation, and
current hunger state tracking. This removes cluster-internal pairs
that contaminate the model's core signal.

**Why it matters:** Each consecutive event pair yields an implied
growth rate = `satiety_effect / actual_gap`. A cluster-internal pair
(e.g., a 3 oz feed followed by a 1 oz top-up 50 minutes later)
produces an artificially high implied rate from the short gap. This
biases the weighted average upward, causing the model to predict
shorter gaps than the real inter-episode rhythm. Episode-level data
collapses the cluster into one episode, and the growth rate is
estimated from real inter-episode gaps only.

The satiety rate and recency half-life were both re-tuned after the
episode switch. The three changes are synergistic — see the CHANGELOG
for the specific before/after numbers.

**Cutoff-adjacent episodes:** The model treats an episode's full
volume as landing at the episode's canonical timestamp (first
constituent feed). This means a cluster that spans, say, 20:16–22:15
is treated as a single 6+ oz feed at 20:16, with elapsed time measured
from 20:16. This is consistent: the growth rate was estimated from
the same canonical-timestamp-to-canonical-timestamp gaps. However, if
the cutoff falls inside an in-progress cluster (after the anchor but
before the top-up), the model sees the anchor as the last event with
only its own volume, not the eventual episode volume. This is
acceptable because we cannot observe future top-ups at forecast time.
If cutoff-adjacent clusters become a pattern that degrades forecasts,
revisit whether the model should adjust the last episode's volume
estimate.

## Simulation volume: lookback-window median

The simulation volume is the median of episode volumes in the lookback
window. This tracks the trend of increasing feed volumes as the baby
grows. Since the model's growth rate already adapts at runtime, using
a trend-adapted volume keeps both halves of the prediction (timing and
volume) on the same footing.

## Breastfeed merge

Uses the standard 45-minute breastfeed merge heuristic. In early data
the impact was negligible (very few events affected, tiny volume
additions), but the infrastructure is in place for when breastfeeding
becomes more frequent. See `research_results.txt` for current counts.

## Volume-to-gap relationship

This model depends on the shared cross-cutting finding that larger
feeds tend to be followed by longer gaps. See
[`feedcast/research/volume_gap_relationship/findings.md`](../../research/volume_gap_relationship/findings.md)
for the current evidence.

The multiplicative satiety mechanism encodes that relationship
directly: a small feed produces a modest hunger reset, a large feed
produces a deep reset. At episode level, the volume-gap correlation
is weaker than at the raw-feed level because some of the apparent
signal in raw data came from cluster artifacts. See
`research_results.txt` for current correlation values.

## What this model is (and isn't)

The primary value comes from two things:

1. **Volume sensitivity** — larger feeds predict longer gaps, encoded
   mechanistically rather than as a regression coefficient.
2. **Trend adaptivity** — runtime growth rate estimation tracks the
   baby's changing pace.

The "latent hunger state" framing is mechanistically motivated and
avoids arbitrary snack thresholds, but the hidden state itself isn't
doing heavy lifting. Volume helps, but much of the variance is still
driven by factors outside the model's scope (sleep state, growth
spurts, fussiness).

The model provides a structurally distinct frame for the ensemble — it
reasons about feeding as a biological process rather than a time
series.
