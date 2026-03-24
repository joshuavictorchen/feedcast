# Survival Hazard

Probabilistic model that frames each feeding as a survival event whose
likelihood increases with elapsed time. Uses a Weibull hazard function
with separate shapes for overnight and daytime periods to capture the
structurally different feeding regimes.

Overnight feeds (20:00–08:00) follow a high-shape Weibull: very
regular timing with tight clustering around the median gap. Daytime
feeds (08:00–20:00) follow a lower-shape Weibull: more variable timing
with a broader distribution. The scale parameter for each period is
estimated at runtime from recent same-period gaps, allowing the model
to track the baby's changing pace.

The forecast uses the median of the Weibull survival function as the
point prediction — the time at which there is a 50% probability the
next feed has occurred. The first predicted feed accounts for the time
already elapsed since the last observed feed using the conditional
survival function.

Uses bottle-only events (no breastfeed merge). Volume was tested as a
covariate but was not statistically significant and did not improve
walk-forward accuracy beyond the day-part split.
