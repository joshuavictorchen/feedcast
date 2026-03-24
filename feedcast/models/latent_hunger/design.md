# Latent Hunger State Design Decisions

## Multiplicative vs. additive satiety

Research compared two satiety mechanisms:

- **Additive**: hunger -= coefficient × volume. Collapses to a
  constant-gap predictor because the optimizer zeros out hunger after
  every feed (pred_std ≈ 0.25h, gap1_MAE = 0.743h).
- **Multiplicative**: hunger = threshold × exp(−rate × volume).
  Guarantees partial resets so volume always matters (pred_std ≈ 0.73h,
  gap1_MAE = 0.707h).

Multiplicative wins on both accuracy and prediction diversity. The
additive model is a dressed-up constant-gap predictor; the
multiplicative model produces real volume-dependent variation.

## Fixed threshold, fitted growth rate

The hunger threshold is fixed at 1.0. This resolves the scale
redundancy between threshold and growth rate (doubling both produces
identical predictions). Only the growth rate is estimated, from
recent observed (volume, gap) pairs:

    implied_gr = (1 − exp(−satiety_rate × volume)) / actual_gap

The recency-weighted average of implied rates adapts the model to the
baby's current pace. This is cheaper and more transparent than a grid
search at forecast time, and directly tracks trends as the baby grows.

## Satiety rate = 0.386

From the multiplicative grid search (30×30 growth_rate × satiety_rate
combinations, walk-forward evaluation with 72h recency weighting):

| growth_rate | satiety_rate | gap1_MAE | pred_std |
| ----------- | ------------ | -------- | -------- |
| 0.245       | 0.386        | 0.707h   | 0.725h   |
| 0.197       | 0.257        | 0.712h   | 0.788h   |
| 0.245       | 0.360        | 0.713h   | 0.713h   |
| 0.293       | 0.593        | 0.715h   | 0.624h   |

The top-10 results cluster around satiety_rate 0.3–0.6. We chose 0.386
(the outright best) as a fixed structural parameter. Growth rate is
fit at runtime, so only satiety_rate needs to be stable across data
windows.

## Circadian modulation: infrastructure only

Research found circadian_amplitude = 0.0 optimal for the multiplicative
model. The reason: volume already correlates with time of day (bigger
overnight feeds → longer predicted gaps), so explicit circadian
modulation is redundant.

The circadian infrastructure (smooth cosine modulation on growth rate)
is kept in model.py because:
1. As the baby develops stronger day/night patterns, it may activate.
2. The 1.28h circadian spread in the data is real; it's just already
   captured by volume.

## Runtime growth rate estimation

Rather than fixing the growth rate from research, the model estimates
it at forecast time from the lookback window. This matters because:

- Lookback-window sensitivity analysis showed the last 5 days give
  gap1_MAE = 0.628h (vs 0.711h full history).
- Re-fitting on just the last 5 days shifts the implied growth rate
  (0.235 vs 0.203 on full history).
- The baby is growing; feeding pace changes week to week.

The runtime estimation uses the closed-form inverse of the gap
prediction equation, avoiding expensive grid search.

## Lookback = 7 days, half-life = 72 hours

Research showed the last 5 days gives the lowest gap1_MAE, but 7 days
was chosen for stability: 5 days can yield fewer than 40 events in the
fitting window, which makes the recency-weighted growth rate estimate
noisier. The 72-hour half-life within the 7-day window still heavily
emphasizes the most recent patterns — events from 5+ days ago
contribute less than 25% of a recent event's weight. This is a
stability-over-peak-accuracy tradeoff, not a clear win. If the dataset
grows and 5-day windows become more populated, the lookback should be
revisited.

## Simulation volume: lookback-window median

Research compared three volume prediction strategies for single-event
accuracy:

| Strategy | MAE |
| -------- | --- |
| Global median | 0.829 oz |
| Recent-5 median | 0.875 oz |
| Time-of-day mean | 0.934 oz |

Global median wins on single-event MAE, but for forward simulation we
use the median of events within the lookback window. The tradeoff:
slightly worse single-event volume accuracy in exchange for tracking
the trend of increasing feed volumes as the baby grows. Since the
model's growth rate already adapts at runtime, using a trend-adapted
volume keeps both halves of the prediction (timing and volume) on the
same footing.

## Breastfeed merge

Uses the standard 45-minute breastfeed merge heuristic per project
direction. In early data the impact was negligible (very few events
affected, tiny volume additions), but the infrastructure is in place
for when breastfeeding becomes more frequent. See research_results.txt
for current counts.

## Volume-to-gap relationship

This model depends on the shared cross-cutting finding that larger feeds
tend to be followed by longer gaps. See
[`feedcast/research/volume_gap_relationship/findings.md`](../../research/volume_gap_relationship/findings.md)
for the current evidence and committed artifacts.

The multiplicative satiety mechanism encodes that relationship
directly: `exp(-0.386 x 1.0) = 0.68` (small feed, modest reset) vs.
`exp(-0.386 x 4.5) = 0.18` (large feed, deep reset).

## What this model is (and isn't)

Honest assessment: the primary value comes from two things:

1. **Volume sensitivity** — larger feeds predict longer gaps, encoded
   mechanistically rather than as a regression coefficient.
2. **Trend adaptivity** — runtime growth rate estimation tracks the
   baby's changing pace.

The "latent hunger state" framing is mechanistically motivated and
avoids arbitrary snack thresholds, but the hidden state itself isn't
doing heavy lifting. The shared
[`volume_gap_relationship` research](../../research/volume_gap_relationship/findings.md)
currently shows a real but modest effect size: volume helps, but much
of the variance is still driven by factors outside the model's scope
(sleep state, growth spurts, fussiness).

The model beats naive baselines by ~20% and provides a structurally
distinct frame for the ensemble — it reasons about feeding as a
biological process rather than a time series.
