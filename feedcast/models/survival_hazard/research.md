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
| Run date | 2026-04-10 |
| Export | `exports/export_narababy_silas_20260410.csv` |
| Dataset | `sha256:8dc1ea2650b0779b6a342b90aa918bc5bd2d5412bfbef25a2df4a8e1bada504e` |
| Command | `.venv/bin/python -m feedcast.models.survival_hazard.analysis` |
| Canonical headline | 73.3 |
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
| Headline | 73.3 |
| Count | 97.5 |
| Timing | 55.8 |

All 26 windows scored (100% availability).

The production constants were updated from `OVERNIGHT_SHAPE=4.75`,
`DAYTIME_SHAPE=1.75` to `OVERNIGHT_SHAPE=7.5`, `DAYTIME_SHAPE=3.0`
(see `CHANGELOG.md` for provenance). The canonical comparison on the
current export is:

| Metric | Pre-update (`4.75`, `1.75`) | Current (`7.5`, `3.0`) |
|---|---|---|
| Headline | 65.7 | 73.3 |
| Count | 92.3 | 97.5 |
| Timing | 47.3 | 55.8 |

Availability stayed at 26/26. Both count (+5.2) and timing (+8.5)
improved substantially.

The 154-candidate canonical sweep confirms the new constants as
baseline=best. The top of the surface is a flat plateau at
`DAYTIME_SHAPE=3.0` with `OVERNIGHT_SHAPE` ranging from 7.0 to 8.0
(spread 0.15 headline points). `DAYTIME_SHAPE` is the sharper driver:
2.75 and 3.25 are both materially worse.

The weakest canonical windows are concentrated in daytime timing.
The windows with the weakest timing are 2026-04-08 19:51 (timing 21.9),
2026-04-06 15:22 (25.4), and 2026-04-07 14:11 (27.0). Timing remains
the weaker component despite the improvement.

### Diagnostic findings

**Day-part split remains justified:** On the raw bottle-only event
stream, day-part split Weibull beats discrete hazard, plain Weibull,
and naive baselines on one-step walk-forward gap MAE:

| Model | gap1 MAE |
|---|---|
| Naive last-gap | 0.913h |
| Naive mean-3-gaps | 0.837h |
| Discrete hazard | 0.726h |
| Plain Weibull | 0.733h |
| Day-part split Weibull | 0.671h |

This still supports the overnight/daytime regime split in `design.md`.

**Episode history remains essential:** Raw bottle-only events (238)
collapse to 210 episode events, absorbing 28 cluster feeds. The direct
day-part Weibull fits sharpen materially after collapsing:
overnight `4.88 -> 6.00`, daytime `2.85 -> 3.54`. That is the cleanest
evidence that raw feeds were contaminating the gap distribution with
cluster-internal noise.

**Internal fit vs canonical replay have converged materially:** The
episode-level MLE fit prefers shapes (`6.00`, `3.54`), while canonical
replay selects (`7.5`, `3.0`). The gap has narrowed dramatically
compared to the prior export (where canonical was `4.75`/`1.75` vs MLE
`7.23`/`3.42` — a factor-of-two divergence). On the current export,
overnight canonical is slightly higher than MLE (7.5 vs 6.0), while
daytime canonical is slightly lower (3.0 vs 3.54). The two sources of
evidence now broadly agree that the baby's feeding rhythm is regular.

The remaining divergence likely has two contributors:

1. *Data window.* The episode-level fit uses all 209 gaps in the full
   export. Canonical replay optimizes over the last ~96h with 36h
   half-life. The baby's feeding patterns are still shifting with growth,
   so full-history MLE and recent-window replay describe slightly
   different regimes.

2. *Model compensation.* The production forecaster chains deterministic
   medians, re-estimates scale at runtime, and uses conditional survival
   for the first feed. These mechanics can still pull optimal shapes away
   from the MLE, but the effect is smaller than before.

The dramatic narrowing of the MLE/canonical gap between exports is
significant. On the prior export, the factor-of-two divergence raised
the question of whether the model structure itself needed rethinking.
The current convergence suggests the prior divergence was primarily
non-stationarity: the baby's recent patterns have caught up to the
regularity that the full-history MLE always saw.

**Half-life trade-off stays real:** In the episode-level walk-forward
diagnostic, `48h` is best on gap1 MAE (`0.588h`) while the current
`168h` is best on feed-count MAE (`1.03`). This is one reason the model
should not blindly inherit its production settings from a single local
diagnostic metric.

**Volume overlay remains rejected:** On episode-level data, the scalar
AFT volume overlay is statistically significant by LR test (`5.716`),
but every positive beta worsens walk-forward performance relative to the
no-volume baseline. This is a strong repeat of the earlier result:
"volume is real" does not imply "this overlay helps."

**Breastfeed merge still does nothing structural:** Bottle-only and
breastfeed-merged inputs produce identical episode boundaries on the
current export. Only 3 episode volumes change, and because the current
production model does not use a volume covariate, merge policy has no
effect on its forecasts.

**Holdout sanity check: count is perfect, timing lags.** The shipped
model predicts 8 feeds vs. 8 actual in the most recent 24h holdout,
with 1.53h mean timing error on matched pairs. Count is now perfectly
calibrated. Timing errors accumulate through the day, with the largest
errors on overnight feeds (2–3h), consistent with the canonical pattern
of timing as the weaker component.

## Conclusions

**Disposition: Change.** `OVERNIGHT_SHAPE` updated from `4.75` to
`7.5`; `DAYTIME_SHAPE` updated from `1.75` to `3.0`.

The canonical replay evidence is clear: the prior soft shapes
degraded sharply on the new export (headline 65.7, down from 72.7),
and the retune recovers to 73.3 — slightly above the prior export's
best. The improvement is broad: count (+5.2) and timing (+8.5) both
gained substantially.

The most notable finding is that canonical replay and episode-level MLE
have converged. The prior export showed a factor-of-two divergence
(canonical 4.75/1.75 vs MLE 7.2/3.4); the current export shows close
agreement (canonical 7.5/3.0 vs MLE 6.0/3.5). This convergence
suggests the prior divergence was primarily non-stationarity — the
baby's recent patterns have regularized to match what the full-history
MLE always described. The open question about whether the model
structure needed rethinking is partially answered: the divergence was
data-driven, not architecture-driven.

The model's remaining tension is familiar: count is very strong (97.5),
timing is better but still weaker (55.8), and the weakest windows are
concentrated in daytime timing. This is a cross-model pattern, not
specific to Survival Hazard.

## Open questions

### Model-local

- **How stable is the MLE/canonical convergence?** The prior export
  showed a factor-of-two divergence; this export shows close agreement.
  Monitoring across future exports will reveal whether the convergence
  is durable (the baby's patterns have genuinely stabilized) or
  transient (a coincidence of the current data window). If divergence
  returns, the follow-ups from the prior round — windowed MLE and
  component ablation — remain relevant.
- **Should half-life be re-tuned canonically with the new shapes?**
  Episode-level diagnostics still show a gap1/feed-count trade-off
  across `48h-168h`. The current `168h` choice may remain right under
  the canonical metric, but that has not been swept jointly with the
  new shapes.
- **Can volume help under a different formulation?** The scalar AFT
  overlay is decisively bad. A residual or regime-specific volume term
  may still be worth testing, but it should be treated as a new model
  idea, not a tweak to the rejected overlay.

### Cross-cutting

- **Internal vs. canonical metric divergence has narrowed for this
  model:** Episode-level MLE (6.0/3.5) and canonical replay (7.5/3.0)
  now broadly agree, unlike the prior export where they diverged by a
  factor of two. This is worth tracking as a cross-model signal — if
  other models also show convergence on newer data, the stacked
  generalization question may need re-framing.
- **Timing as shared bottleneck:** Timing (55.8) lags count (97.5). This
  pattern persists across all models — see `feedcast/research/README.md`.
