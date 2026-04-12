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
| Run date | 2026-04-11 |
| Export | `exports/export_narababy_silas_20260411(1).csv` |
| Dataset | `sha256:f71d7d136049e997e30fca06c93dd3f65cb1a46b7d37a2e41ed24b71fc9665d7` |
| Command | `.venv/bin/python -m feedcast.models.survival_hazard.analysis` |
| Canonical headline | 66.9 |
| Availability | 25/25 windows (100%) |
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

The current production constants (`OVERNIGHT_SHAPE=4.5`,
`DAYTIME_SHAPE=3.0`) score:

| Metric | Score |
|---|---|
| Headline | 66.9 |
| Count | 93.4 |
| Timing | 48.7 |

All 25 windows scored (100% availability). Headline is lower than the
73.3 achieved on the prior export (20260410) with `OVERNIGHT_SHAPE=7.5`,
but the overnight shape was softened because the baby's overnight
regularity decreased in the most recent data: with the prior shapes
(7.5/3.0), the new export scored only 65.6.

The 154-candidate canonical sweep confirms the current constants as
near-best. The best candidate (`OVERNIGHT_SHAPE=3.5`,
`DAYTIME_SHAPE=1.25`) gains only +0.29 headline over baseline but
trades count (93.4 → 87.6) for timing (48.7 → 52.1). The top
candidates at DT=3.0 span a tight plateau (66.9-67.0) from
OVERNIGHT 4.0 to 5.0.

The weakest canonical windows are concentrated on 2026-04-10: the two
latest windows (18:33 and 19:35) score headline 64.9 and 62.2 with
timing 45.9 and 44.2. These carry the highest recency weights (0.98
and 1.0). Earlier windows (April 9) score in the 50-87 range with
some variability.

### Diagnostic findings

**Day-part split remains justified:** On the raw bottle-only event
stream, day-part split Weibull beats discrete hazard, plain Weibull,
and naive baselines on one-step walk-forward gap MAE:

| Model | gap1 MAE |
|---|---|
| Naive last-gap | 0.937h |
| Naive mean-3-gaps | 0.906h |
| Discrete hazard | 0.758h |
| Plain Weibull | 0.772h |
| Day-part split Weibull | 0.705h |

This still supports the overnight/daytime regime split in `design.md`.

**Episode history remains essential:** Raw bottle-only events (252)
collapse to 221 episode events, absorbing 31 cluster feeds. The direct
day-part Weibull fits sharpen materially after collapsing:
overnight `4.90 -> 6.04`, daytime `2.68 -> 3.63`. That is the cleanest
evidence that raw feeds were contaminating the gap distribution with
cluster-internal noise.

**Internal fit vs canonical replay have re-diverged:** The
episode-level MLE fit prefers shapes (`6.04`, `3.63`), while canonical
replay now selects (`4.5`, `3.0`). On the prior export version
(20260411 without new data), canonical preferred (`7.5`, `3.0`), which
was broadly convergent with MLE. The 8 new data points shifted the
canonical optimum back toward softer overnight shapes.

The re-divergence has a clear proximate cause: the baby's overnight
feeding became less regular in the most recent windows (April 10-11).
The per-window analysis shows the 5 highest-weight windows strongly
favor softer shapes (+5 to +20 headline each vs the prior 7.5), while
older windows (April 7-8) still favor harder shapes. Canonical replay,
which weights recent windows heavily (36h half-life), tracks this shift.
MLE, which uses all 220 gaps equally, does not.

This is consistent with the contributor analysis from prior exports:

1. *Data window.* The episode-level fit uses all 220 gaps in the full
   export. Canonical replay optimizes over the last ~96h with 36h
   half-life. The baby's feeding patterns shift with growth, so
   full-history MLE and recent-window replay describe different regimes.

2. *Model compensation.* The production forecaster chains deterministic
   medians, re-estimates scale at runtime, and uses conditional survival
   for the first feed. These mechanics pull optimal shapes away from
   the MLE.

**Half-life trade-off stays real:** In the episode-level walk-forward
diagnostic, `48h` is best on gap1 MAE (`0.601h`) while the current
`168h` is best on feed-count MAE (`0.95`). The gap1 difference is
negligible (0.001h), while the feed-count advantage of `168h` is
substantial (0.95 vs 1.38).

**Volume overlay remains rejected:** On episode-level data, the scalar
AFT volume overlay is statistically significant by LR test (`7.301`),
but every positive beta worsens walk-forward performance relative to the
no-volume baseline. This is a consistent repeat of prior results:
"volume is real" does not imply "this overlay helps."

**Breastfeed merge still does nothing structural:** Bottle-only and
breastfeed-merged inputs produce identical episode boundaries on the
current export. Only 3 episode volumes change, and because the current
production model does not use a volume covariate, merge policy has no
effect on its forecasts.

**Holdout: count miss persists, timing improved.** The shipped model
predicts 8 feeds vs. 11 actual in the most recent 24h holdout, with
0.56h mean timing error on the 8 matched pairs. Feed count error is 3
(the model missed 3 feeds). Timing per matched pair improved markedly
from the prior export (1.05h → 0.56h), consistent with the softer
overnight shape producing better-timed predictions. The count miss
(8 vs 11) persists, suggesting the baby fed more frequently on this
holdout day than the model's 7-day lookback window predicted. The count
issue is a scale/lookback problem, not a shape problem.

## Conclusions

**Disposition: Re-tuned.** `OVERNIGHT_SHAPE` changed from `7.5` to
`4.5`. `DAYTIME_SHAPE` unchanged at `3.0`.

The 20260411(1) export (8 new rows vs the earlier 20260411 version)
shifted the canonical replay landscape. With the prior shapes (7.5/3.0),
the new export scored 65.6 headline. With the updated shapes (4.5/3.0),
it scores 66.9 (+1.3). The improvement concentrates in the most recent,
highest-weight windows where the baby's overnight regularity decreased.

The overnight shape was set to 4.5 (center of the 4.0-5.0 plateau)
rather than the replay peak (4.0) to hedge against oscillation: the
shapes went 4.75 → 7.5 → 4.5 across three consecutive tuning sessions.
This volatility reflects genuine non-stationarity in the baby's
overnight pattern, not estimation noise.

MLE/canonical have re-diverged: MLE prefers 6.04/3.63, canonical now
prefers 4.5/3.0. The divergence direction reversed (previously canonical
was above MLE for overnight). This instability across exports is a
signal that the baby's overnight regularity is fluctuating, and the
fixed-shape-with-runtime-scale architecture is sensitive to which
windows dominate the canonical aggregate.

## Open questions

### Model-local

- **Overnight shape oscillation (4.75 → 7.5 → 4.5) signals genuine
  non-stationarity.** Three consecutive exports produced substantially
  different canonical optima for overnight shape. The baby's overnight
  regularity is fluctuating, and the model is sensitive to which windows
  dominate the recency-weighted aggregate. If oscillation continues, the
  fixed-shape architecture may need structural changes (e.g., adaptive
  shape estimation, wider lookback for shape).
- **Holdout count miss persists (8 vs 11).** The model consistently
  underpredicts episode count on the most recent holdout day. Half-life
  sweep (48-240h) showed no improvement, so the issue is not recency
  weighting. The baby may be feeding more frequently than the 7-day
  lookback captures. If subsequent exports confirm a sustained frequency
  increase, `LOOKBACK_DAYS` or model structure may need revisiting.
- **Can volume help under a different formulation?** The scalar AFT
  overlay is decisively bad. A residual or regime-specific volume term
  may still be worth testing, but it should be treated as a new model
  idea, not a tweak to the rejected overlay.

### Cross-cutting

- **MLE/canonical convergence has broken for this model.** The
  convergence observed on 20260410/20260411 did not hold: 8 new data
  points shifted the canonical optimum from 7.5 back to 4.5, while
  MLE stayed at 6.04. The prior conclusion that convergence was
  "durable" was premature. This is relevant to the stacked
  generalization question: if canonical optima are this volatile,
  tuning each model to its native objective and letting the ensemble
  weight them may produce more stable results.
- **Timing as shared bottleneck:** Timing (48.7) lags count (93.4).
  This pattern persists across all models — see
  `feedcast/research/README.md`. The overnight shape softening improved
  timing in the most recent windows but did not eliminate the gap.
