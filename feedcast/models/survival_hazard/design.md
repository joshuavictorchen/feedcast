# Survival Hazard Design Decisions

## Weibull family

The Weibull distribution is a natural choice for inter-feed gaps
because its shape parameter directly encodes whether the hazard
(instantaneous feeding probability) increases with elapsed time.
Shape > 1 means the longer since the last feed, the more likely the
next — which matches the biological reality. Research confirmed
shape ≈ 3.1 on the full dataset.

## Day-part split

The strongest finding: overnight and daytime feeding patterns are
structurally different, not just shifted.

| Period | Shape | Scale | Median gap |
| ------ | ----- | ----- | ---------- |
| Overnight (20-08) | 7.31 | 3.28h | 3.12h |
| Daytime (08-20)   | 2.33 | 2.52h | 2.15h |

Overnight shape of 7.31 means feeds are very regular — the gap
distribution is tightly peaked. Daytime shape of 2.33 means feeds are
more variable, with a broader spread. This isn't just a scale
difference; it's a fundamentally different pattern.

Walk-forward results:

| Model | gap1 MAE |
| ----- | -------- |
| Naive last-gap | 0.883h |
| Weibull baseline | 0.808h |
| Weibull + volume | 0.844h |
| Day-part split | 0.690h |

The day-part split is the strongest single-gap predictor, beating all
alternatives by a wide margin.

## Volume covariate: excluded

Volume was tested as a proportional hazards covariate (scale × exp(β × vol)).
The likelihood ratio test was not significant (LR = 2.15, needs > 3.84
for p < 0.05). Walk-forward with the full MLE beta (0.077) actually
hurt performance (0.844h vs 0.808h baseline). A damped beta (0.039)
helped the single-Weibull model (0.749h), but the day-part split
(0.690h) subsumes most of what volume was capturing — overnight feeds
are both bigger and more regular.

## Fixed shape, runtime scale

The Weibull shape is a structural parameter that reflects how regular
the feeding pattern is. It changes slowly. The scale parameter reflects
the current pace, which changes as the baby grows.

Like Latent Hunger State, the model fixes the structural parameter
from research and estimates the pace at runtime:

  λ_hat = (Σ w_i × t_i^k / Σ w_i)^(1/k)

This is the closed-form weighted MLE for Weibull scale given fixed
shape. It estimates scale separately for each day-part from the
corresponding recent gaps.

## Conditional survival for the first feed

The Weibull is not memoryless (unlike the exponential). Having already
waited `t0` hours changes the conditional distribution of the remaining
time. The model uses:

  t_remaining = λ × ((t0/λ)^k + ln 2)^(1/k) − t0

This correctly accounts for elapsed time: if the baby fed recently,
the next feed is farther away; if it's been a while, the conditional
median is shorter than the unconditional median.

## Median as point prediction

The median of the survival function is the natural point prediction for
a hazard model. It's more robust than the mean for skewed distributions
and avoids the "early mode" problem of right-skewed densities. The 25th
and 75th percentiles are included in diagnostics as uncertainty bounds.

## Bottle-only events

Since volume is not a covariate, there's no reason to include the noisy
breastfeed estimates. Consistent with Slot Drift and Analog Trajectory.

## Day-part boundaries: 20:00 / 08:00

Chosen from the circadian analysis in both LHS and survival research.
The data shows a clear transition: 20:00–08:00 has longer, more regular
gaps (mean ~3.2h) while 08:00–20:00 has shorter, more variable gaps
(mean ~2.1h). The boundary at 08:00 also aligns with the morning
feeding cluster observed in the recent 7-day data.
