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
| Run date | 2026-04-16 |
| Export | `exports/export_narababy_silas_20260416.csv` |
| Dataset | `sha256:383bff93af3fbf40ff86f1eccecd6d2fefd9a4b7d5093eb1b37174f552ac6e74` |
| Command | `.venv/bin/python -m feedcast.models.survival_hazard.analysis` |
| Canonical headline | 68.5 |
| Availability | 26/26 windows (100%) |
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
198-candidate grid:

- `OVERNIGHT_SHAPE`: `2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0`
- `DAYTIME_SHAPE`: `1.0, 1.25, 1.5, 1.625, 1.75, 1.875, 2.0, 2.5, 3.0, 3.5, 4.0`

The grid has been extended downward on overnight twice: the original
40-candidate grid bottomed out on the low corner, and the 20260416
sweep found the optimum below the 154-candidate grid's OS=3.0 floor.
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

The current production constants (`OVERNIGHT_SHAPE=2.75`,
`DAYTIME_SHAPE=1.625`) score:

| Metric | Score |
|---|---|
| Headline | 68.5 |
| Count | 91.3 |
| Timing | 51.7 |

All 26 windows scored (100% availability). Headline improved +4.57
over the prior constants (6.0/1.75) on the same export. Timing gained
+7.07, count traded -1.94. The improvement concentrates on the
highest-weight recent windows: the top two windows by weight
(04-15T20:57 and 04-15T17:42) went from headline 45.8 and 45.5 to 72.4
and 64.3 respectively.

The 198-candidate canonical sweep identifies the current constants as
the unambiguous best. Top 5 all sit at OS=2.75 across DT=1.5-1.875,
spread 0.7 headline. The chosen point is interior in both dimensions:
OS=2.5 scores 67.76 and OS=3.0 scores 67.79; DT=1.5 scores 67.98 and
DT=1.75 scores 67.86.

The weakest canonical windows are concentrated on 2026-04-13 (55.2
headline at 00:10). The strongest windows are April 13-14 mid-day,
with timing scores in the 60-71 range. The highest-weight window
(04-15T20:57, weight 1.0) scores 72.4 headline.

### Diagnostic findings

**Day-part split remains justified:** On the raw bottle-only event
stream, day-part split Weibull beats discrete hazard, plain Weibull,
and naive baselines on one-step walk-forward gap MAE:

| Model | gap1 MAE |
|---|---|
| Naive last-gap | 0.940h |
| Naive mean-3-gaps | 0.906h |
| Discrete hazard | 0.763h |
| Plain Weibull | 0.774h |
| Day-part split Weibull | 0.697h |

This still supports the overnight/daytime regime split in `design.md`
at the distribution level, even though the production shapes are now
much softer than the raw-fit values would suggest.

**Episode history remains essential:** Raw bottle-only events (298)
collapse to 263 episode events, absorbing 35 cluster feeds. The direct
day-part Weibull fits sharpen materially after collapsing:
overnight `5.25 -> 6.02`, daytime `2.56 -> 3.03`. That remains the
cleanest evidence that raw feeds were contaminating the gap
distribution with cluster-internal noise.

**Internal fit and canonical replay have re-diverged sharply:** The
episode-level MLE fit prefers shapes (`6.02`, `3.03`), while canonical
replay selects (`2.75`, `1.625`). The overnight gap is now 3.3 shape
units, the largest in the tracked history. This is a reversal of the
20260413 convergence (MLE 5.96, canonical 6.0), and the MLE itself has
not moved meaningfully (5.96 -> 6.02). All of the divergence comes
from canonical replay preferring much softer shapes over the last 96h.

The contributor analysis from prior exports still applies, but with a
sharper distinction:

1. *Data window.* The episode-level fit uses all 262 gaps in the full
   export. Canonical replay optimizes over the last ~96h with 36h
   half-life. On this export the recent 96h appears to deviate
   markedly from the full-history distribution: recent feeds are more
   irregular than the baby's long-run pattern.

2. *Model compensation.* The production forecaster chains deterministic
   medians, re-estimates scale at runtime, and uses conditional survival
   for the first feed. Softer shapes dampen the conditional-survival
   response to elapsed time, which helps when recent inter-episode gaps
   are variable enough that the MLE's sharper peaks misfire.

**Half-life trade-off stays real:** In the episode-level walk-forward
diagnostic, the current `168h` is best on both gap1 MAE (`0.631h`) and
feed-count MAE (`0.90`). All half-lives score within 0.02h on gap1,
but `168h` is materially better on feed-count vs `48h` (`0.90` vs
`1.31`).

**Volume overlay remains rejected:** On episode-level data, the scalar
AFT volume overlay is statistically significant by LR test (`14.643`,
stronger than the prior export's 6.76), but every positive beta worsens
walk-forward performance relative to the no-volume baseline. The
stronger LR signal does not translate to better forecasts under the
tested formulation.

**Breastfeed merge still does nothing structural:** Bottle-only and
breastfeed-merged inputs produce identical episode boundaries on the
current export. Only a handful of episode volumes differ, and because
the current production model does not use a volume covariate, merge
policy has no effect on its forecasts.

**Holdout: large timing error on an irregular day.** The shipped model
predicts 10 feeds vs 9 actual in the most recent 24h holdout (count
error 1), with 2.86h mean timing error on the 9 matched pairs. The
first-feed prediction missed by 1.9h (22:04 predicted vs 23:59 actual),
and that error propagates through the deterministic median chain. The
holdout day appears unusually irregular compared with the 96h window
used in canonical evaluation: the same constants produce large timing
gains on the aggregate (51.7 vs 44.7 baseline) while fitting this
single day poorly. The canonical aggregate is the shipping gate, not
this single day.

## Conclusions

**Disposition: Re-tuned.** `OVERNIGHT_SHAPE` changed from `6.0` to
`2.75`. `DAYTIME_SHAPE` changed from `1.75` to `1.625`.

The 20260416 export (28 new rows vs the 20260413 export) shifted the
canonical replay landscape toward much softer shapes. With the prior
constants (6.0/1.75), the new export scored 63.95 headline. With the
updated constants (2.75/1.625), it scores 68.52 (+4.57). Timing drove
the gain (+7.07), count traded slightly (-1.94). The improvement
concentrates on the highest-weight recent windows, where the baseline
scored in the 45-57 range and the new constants score 64-72.

The chosen point is a clear interior optimum on a tight plateau: the
top 5 candidates all sit at OS=2.75 across DT=1.5-1.875 (spread 0.7
headline). Neighbors at OS=2.5 and OS=3.0 score 0.7 below. The plateau
is noticeably narrower than in prior tunes.

The overnight shape (2.75) diverges sharply from the episode-level MLE
(6.02). The 20260413 conjecture that overnight 6.0 was "structurally
anchored" by MLE convergence does not survive this export: the MLE has
barely moved (5.96 to 6.02) but canonical replay has dropped 3.3 shape
units. Either the recent 96h of feeding is genuinely more irregular
than the long-run distribution, or the production forecaster's chain
dynamics interact with recent gap variability in a way the MLE does
not capture.

The daytime shape (1.625) is a mild softening of the prior 1.75 value
and sits in the same plateau region that has been favored since
20260327 (DT=1.75 then and at intermediate exports). DT=1.625 was
already a grid value; canonical selects it over 1.75 by 0.7 headline.

The overnight oscillation (4.75 to 7.5 to 4.5 to 6.0 to 2.75) has not
stabilized. Each new export in the last month has shifted the
canonical overnight optimum by at least 1.5 shape units, and the
absolute range has widened. This is a live signal that the
fixed-shape architecture may be chasing short-window variability that
a shape-adaptive or longer-window formulation could absorb more
stably.

## Open questions

### Model-local

- **Overnight shape oscillation has widened.** The sequence
  (4.75 to 7.5 to 4.5 to 6.0 to 2.75) now spans 4.75 shape units. The
  prior export's convergence with MLE did not hold. If subsequent
  exports continue to move overnight by >=1.5 units per week, the
  fixed-shape architecture is the likely root cause. Candidate
  interventions: adaptive shape estimation within the lookback window,
  a longer lookback for shape (to absorb week-to-week noise), or a
  shape floor/ceiling to bound the oscillation. Each changes the
  model structure, not just a constant.
- **Holdout fit is poor on an apparently irregular day.** The shipped
  model predicts 10 vs 9 actual feeds with 2.86h mean timing error, a
  large regression from prior holdouts (0.54h on 20260413). The
  first-feed conditional-survival miss (~1.9h) propagates through the
  deterministic median chain. This is one day and should not
  independently drive tuning, but if successive holdouts show the same
  pattern, the chained-median forecaster may be too brittle when the
  first gap is mis-predicted.
- **Can volume help under a different formulation?** The scalar AFT
  overlay is decisively bad (LR 14.6 significant, but every positive
  beta worsens walk-forward). A residual or regime-specific volume
  term may still be worth testing, but it should be treated as a new
  model idea, not a tweak to the rejected overlay.

### Cross-cutting

- **MLE/canonical divergence has widened to 3.3 shape units on
  overnight.** Last export saw these two approaches agree (MLE 5.96,
  production 6.0). This export has them disagreeing more than at any
  point in the tracked history (MLE 6.02, production 2.75). The
  daytime gap also widened (MLE 3.03, production 1.625). This
  strengthens rather than weakens the case that internal tuning and
  canonical tuning optimize different objectives for this model. The
  stacked-generalization question in `feedcast/research/README.md` is
  directly relevant: if canonical-optimal shapes are this far from the
  distribution fit, the production forecaster's chained-median
  mechanics may be doing most of the work, and a simpler or
  shape-adaptive forecaster could match performance without the
  divergence.
- **Timing as shared bottleneck:** Timing (51.7) lags count (91.3).
  This pattern persists across all models, see
  `feedcast/research/README.md`. The current tune improved timing by
  7.1 points at the cost of 1.9 count points. That timing-vs-count
  tradeoff is rewarded by the geometric-mean headline in the current
  distribution.
