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

## Satiety rate = 0.257

Re-tuned in Phase 5d after switching to episode-level history. The
original value of 0.386 was fitted on raw (cluster-contaminated) data
where short intra-cluster gaps inflated the apparent satiety sensitivity.

Episode-level grid search (30×30 growth_rate × satiety_rate, walk-forward
evaluation on inter-episode gaps):

| growth_rate | satiety_rate | gap1_MAE | pred_std |
| ----------- | ------------ | -------- | -------- |
| 0.197       | 0.257        | 0.623h   | 0.470h   |
| 0.148       | 0.153        | 0.626h   | 0.513h   |
| 0.245       | 0.386        | 0.635h   | 0.388h   |

The top results shifted lower (sr 0.15–0.39) compared to the raw-data
grid (sr 0.3–0.6). The surface is shallow — sr=0.386 is only 0.012h
worse than sr=0.257 — but the episode-level optimal is a cleaner fit
to the real volume-gap relationship. Cross-validated on replay:
sr=0.257 consistently outperforms sr=0.386 by ~1 point at each
half-life value.

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

## Lookback = 7 days, half-life = 168 hours

Re-tuned in Phase 5d alongside the episode-level switch. The previous
value (48h, reduced from 72h) was needed to track the feeding pace
through cluster noise — aggressive recency weighting cut through the
bimodal artifact of intra-cluster short gaps mixed with real
inter-episode gaps.

With episode-level history, the noise is removed: all gaps are real
inter-episode gaps. The growth rate estimate benefits from broader
averaging. A 168h half-life (= LOOKBACK_DAYS × 24h) gives 50% weight
at the lookback boundary — roughly equal weighting across the full
window with modest recency bias.

Replay sweeps confirmed the interaction: raw data degrades at longer
half-lives (headline 73.4 → 64.7 at HL=168), while episode data
improves (68.3 → 77.5 at HL=168). The two changes are synergistic:
episodes remove noise, enabling the model to benefit from broader
averaging. (These numbers are from replay parameter overrides, not
from the research script.)

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

## Cluster relationship

The model operates on episode-level history (Phase 5d). Raw feed events
are collapsed into episodes via `episodes_as_events()` before growth
rate estimation, simulation volume computation, and current hunger state
calculation. This removes cluster-internal pairs that contaminate the
model's core signal.

**Mechanism of contamination:** Each consecutive event pair yields an
implied growth rate = `satiety_effect / actual_gap`. A cluster-internal
pair (e.g., 3.0 oz → 50-min gap → 1.0 oz top-up) produces an
artificially high implied rate from the short gap. This biases the
weighted average upward, causing the model to predict shorter gaps than
the baby's real inter-episode rhythm. With episode-level data, the
cluster becomes one 4.0 oz episode and the growth rate is estimated from
the real inter-episode gap.

**Impact:** Episode-level inputs improved all research metrics (gap1_MAE
0.779 → 0.623, −20%) and enabled re-tuning of both SATIETY_RATE (0.386
→ 0.257) and RECENCY_HALF_LIFE_HOURS (48 → 168). Replay headline
improved +5.1 points (73.4 → 78.5) with perfect episode count.

## Volume-to-gap relationship

This model depends on the shared cross-cutting finding that larger feeds
tend to be followed by longer gaps. See
[`feedcast/research/volume_gap_relationship/findings.md`](../../research/volume_gap_relationship/findings.md)
for the current evidence and committed artifacts.

The multiplicative satiety mechanism encodes that relationship
directly: `exp(-0.257 x 1.0) = 0.77` (small feed, modest reset) vs.
`exp(-0.257 x 4.5) = 0.32` (large feed, deep reset). At episode level,
the volume-gap correlation is 0.285 — weaker than the raw-data
correlation (0.323) because some of the apparent signal came from
cluster artifacts.

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
