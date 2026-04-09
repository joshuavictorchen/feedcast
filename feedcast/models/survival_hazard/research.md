# Survival Hazard Research

> `design.md` documents why the model works the way it does.
> `methodology.md` is the report-facing description.
> This file is the evidence: current support and challenges for the
> model's design and constants.

## Overview

Survival Hazard models feeding episodes as survival events with
day-part-specific Weibull hazards. The key research questions are:

1. How well does the model forecast under canonical multi-window
   evaluation?
2. Do canonical replay results support the current
   `OVERNIGHT_SHAPE` and `DAYTIME_SHAPE` constants?
3. Do the internal Weibull fitting diagnostics point in the same
   direction as canonical replay, or do they diverge?
4. Do the volume-covariate and breastfeed-merge checks justify any
   change to the current production design?

## Last run

| Field | Value |
|---|---|
| Run date | 2026-03-31 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.survival_hazard.analysis` |
| Canonical headline | 72.7 |
| Availability | 24/24 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("survival_hazard")`
through the shared replay infrastructure. This produces a multi-window
aggregate (lookback 96h, half-life 36h, episode-boundary cutoffs) that
is directly comparable across all models. The research script enables
`parallel=True` for this path because window-level parallelism is safe
for a fixed constant set and keeps the canonical run practical.

**Canonical tuning** sweeps `OVERNIGHT_SHAPE` and `DAYTIME_SHAPE`
jointly via `tune_model()`. The current sweep is a mixed-resolution
154-candidate grid:

- `OVERNIGHT_SHAPE`: `3.0, 3.5, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0`
- `DAYTIME_SHAPE`: `1.0, 1.25, 1.5, 1.625, 1.75, 1.875, 2.0, 2.5, 3.0, 3.5, 4.0`

The original 40-candidate grid bottomed out at the lowest-tested
corner, so the wider grid is the authoritative search.
Scale is still estimated at runtime; only the fixed shape parameters
are tuned canonically.

### Objective comparison contract

Canonical and internal diagnostics answer different questions.
Canonical evaluation uses the shared replay stack: bottle-only scoring
events, episode-boundary cutoffs over the most recent 96 hours, and the
24-hour headline scorer applied to the shipped chained-median
forecaster. The internal diagnostics fit or test the gap distribution
more directly: weighted MLE on observed gaps and walk-forward gap/count
errors. When these objectives disagree, the comparison is between a
distribution-fit question and a shipped-forecast-quality question, not
between two equivalent estimators of the same target.

### Model-specific diagnostics

**Raw-gap Weibull fits** (Sections 1-4) establish the basic hazard
shape, the overnight/daytime regime split, and the volume-covariate
signal on the uncollapsed bottle-only event stream.

**Discrete hazard comparison** (Section 5) is a historical design
alternative. It asks whether a more flexible non-parametric hazard is
actually needed. On the current export it remains a useful baseline,
not a production contender.

**Raw walk-forward comparisons** (Sections 6-7) compare plain Weibull,
volume-adjusted Weibull, and the day-part split on gap MAE / feed-count
MAE. This is the main local evidence that the overnight/daytime split
is real and helpful.

**24h holdout replay** (Section 8) is a sanity check that the shipped
model can forecast the most recent full day from only prior data.

**Simulation-study constraint:** The analysis MLE and the shipped
forecaster validate different objects. Shape-recovery tests are
cleanest on direct day-part gap samples drawn from known Weibull
distributions. Shipped-forecast and replay tests should use
chronological histories and compare against the deterministic
median-path target, with the first forecast gap anchored to the last
feed's day-part rather than the cutoff wall clock.

**Episode-level analysis** (Section 9) re-runs the key fitting and
walk-forward logic on cluster-collapsed episodes. This is the evidence
for the episode-history design choice in `design.md`.

**Episode-level volume overlay testing** (Section 9) asks a narrower
question than the canonical sweep: not "does volume matter at all,"
but "does the current scalar AFT overlay improve this model?" That is
why this section remains diagnostic even when the likelihood-ratio test
is significant.

**Breastfeed merge policy comparison** (Section 10) checks whether
merging estimated breastfeed volume into bottle events changes episode
boundaries. This matters here because the clustering rule's extension
arm depends on the later event's volume.

## Results

### Canonical findings

The current production constants score:

| Metric | Score |
|---|---|
| Headline | 72.7 |
| Count | 94.3 |
| Timing | 56.6 |

All 24 windows scored (100% availability).

The production constants were updated from `OVERNIGHT_SHAPE=6.54`,
`DAYTIME_SHAPE=3.04` to `OVERNIGHT_SHAPE=4.75`, `DAYTIME_SHAPE=1.75`
(see `CHANGELOG.md` for provenance). The reproduced canonical
comparison on the current export is:

| Metric | Pre-update (`6.54`, `3.04`) | Current (`4.75`, `1.75`) |
|---|---|---|
| Headline | 65.7 | 72.7 |
| Count | 92.8 | 94.3 |
| Timing | 47.4 | 56.6 |

Availability stayed at 24/24. The gain is mostly timing (+9.2), with a
smaller count gain (+1.5).

The widened 154-candidate canonical sweep now confirms the new
constants as baseline=best. The top of the surface is fairly tight, but
informative: the top five candidates all keep `DAYTIME_SHAPE=1.75`
while `OVERNIGHT_SHAPE` ranges from 4.5 to 5.5. That means daytime
shape is the sharper driver of the current ranking, while overnight
shape has a softer local plateau once it is no longer too high.

The weakest canonical windows are concentrated in daytime timing rather
than count. The windows with the weakest timing are 2026-03-24 06:10
(timing 30.9), 2026-03-25 18:36 (32.4), and 2026-03-26 13:56 (35.6).
Even after the retune, timing remains the weaker component.

### Diagnostic findings

**Day-part split remains justified:** On the raw bottle-only event
stream, day-part split Weibull beats discrete hazard, plain Weibull,
and naive baselines on one-step walk-forward gap MAE:

| Model | gap1 MAE |
|---|---|
| Naive last-gap | 0.820h |
| Naive mean-3-gaps | 0.780h |
| Discrete hazard | 0.741h |
| Plain Weibull | 0.747h |
| Day-part split Weibull | 0.678h |

This still supports the overnight/daytime regime split in `design.md`.

**Episode history remains essential:** Raw bottle-only events (121)
collapse to 103 episode events, absorbing 18 cluster feeds. The direct
day-part Weibull fits sharpen materially after collapsing:
overnight `4.44 -> 7.23`, daytime `2.80 -> 3.42`. That is the cleanest
evidence that raw feeds were contaminating the gap distribution with
cluster-internal noise.

**Internal fit vs canonical replay diverge materially:** The
episode-level MLE fit prefers sharper regimes (`7.23`, `3.42`), while
canonical replay prefers softer production shapes (`4.75`, `1.75`).
This divergence has two likely causes:

1. *Data window.* The episode-level fit uses all 102 gaps in the full
   export. Canonical replay optimizes over the last ~96h with 36h
   half-life. If the baby's feeding patterns are shifting (likely at
   this growth stage), full-history MLE and recent-window replay
   describe different regimes. A windowed MLE could isolate how much
   of the divergence is non-stationarity.

2. *Model compensation.* The production forecaster is not a Weibull
   sampler — it chains deterministic medians, re-estimates scale at
   runtime from recent gaps, and uses conditional survival for the
   first feed. Softer shapes may compensate for biases in these
   mechanics (e.g., overly peaked medians compounding over 24h, or
   conditional survival being too sensitive at high shape). Component
   ablation could isolate which mechanic drives this.

The divergence is not a defect in either method. The episode-level
fit describes the gap distribution and confirms structural assumptions
(Weibull family, day-part split). Canonical replay selects constants
that make the shipped forecaster perform best. The gap between them is
diagnostic: it measures how much the production system's mechanics
pull optimal constants away from the data-generating distribution.

**Half-life trade-off stays real:** In the episode-level walk-forward
diagnostic, `48h` is best on gap1 MAE (`0.499h`) while the current
`168h` is best on feed-count MAE (`1.14`). This is one reason the model
should not blindly inherit its production settings from a single local
diagnostic metric.

**Volume overlay remains rejected:** On episode-level data, the scalar
AFT volume overlay is statistically significant by LR test (`6.025`),
but every positive beta worsens walk-forward performance relative to the
no-volume baseline. This is a strong repeat of the earlier result:
"volume is real" does not imply "this overlay helps."

**Breastfeed merge still does nothing structural:** Bottle-only and
breastfeed-merged inputs produce identical episode boundaries on the
current export. Only 3 episode volumes change, and because the current
production model does not use a volume covariate, merge policy has no
effect on its forecasts.

**Holdout sanity check is decent, not perfect:** The shipped model
predicts 9 feeds vs. 10 actual in the most recent 24h holdout, with
0.60h mean timing error on matched pairs. That is consistent with the
canonical story: count is strong, timing improved materially, but the
model is not yet tight enough to eliminate daytime drift.

## Conclusions

**Disposition: Change.** `OVERNIGHT_SHAPE` updated from `6.54` to
`4.75`; `DAYTIME_SHAPE` updated from `3.04` to `1.75`.

The canonical replay evidence is strong enough that this should not be
framed as a marginal tweak. The widened sweep improves headline from
65.7 to 72.7 (+7.0) with no availability loss, and most of that gain is
in timing (+9.2). The day-part split remains correct, but the earlier
production shapes were too sharp for the actual 24-hour forecasting
objective on the current export.

The important design conclusion is narrower than "the Weibull fits were
wrong." They were not. The episode-level MLE fit still correctly says
overnight is more regular than daytime. What changed is the choice of
which evidence is authoritative for production constants. For Survival
Hazard, direct distribution fit and canonical replay do not rank shape
pairs the same way. Production should follow canonical replay.

The model's remaining tension is clear: count is already strong (94.3),
timing is better than before but still weaker (56.6), and the weak
windows are concentrated in daytime timing. The retune materially
improves the model without resolving that structural gap.

## Open questions

### Model-local

- **Why does canonical replay prefer softer shapes than the
  episode-level MLE?** Two concrete follow-ups:
  (a) *Windowed MLE* — re-fit the episode-level Weibull on only the
  most recent ~96h of episode gaps (matching replay's lookback). If
  the MLE shapes move toward `4.75`/`1.75`, the divergence is largely
  non-stationarity, not model compensation.
  (b) *Component ablation* — disable conditional survival, or switch
  from median to mean prediction, and re-run the canonical sweep.
  Whichever change most reduces the MLE/replay gap identifies the
  production mechanic driving the compensation.
- **Should half-life be re-tuned canonically once shapes are stable?**
  Episode-level diagnostics still show a gap1/feed-count trade-off
  across `48h-168h`. The current `168h` choice may remain right under
  the canonical metric, but that has not been swept jointly with the
  new shapes.
- **Can volume help under a different formulation?** The scalar AFT
  overlay is decisively bad. A residual or regime-specific volume term
  may still be worth testing, but it should be treated as a new model
  idea, not a tweak to the rejected overlay.

### Cross-cutting

- **Internal vs. canonical metric divergence:** Episode-level MLE prefers
  shapes 7.2/3.4 while canonical replay selects 4.75/1.75. This is part
  of a broader cross-model pattern — see `feedcast/research/README.md`.
- **Timing as shared bottleneck:** Timing (56.6) lags count (94.3). This
  pattern persists across all five models — see
  `feedcast/research/README.md`.
