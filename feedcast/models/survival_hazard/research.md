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
| Export | `exports/export_narababy_silas_20260411.csv` |
| Dataset | `sha256:138b5d3ad7d106444951acc6c56154bcd1ae94184f58a566f83c032ad41ef5ec` |
| Command | `.venv/bin/python -m feedcast.models.survival_hazard.analysis` |
| Canonical headline | 67.7 |
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

The current production constants (`OVERNIGHT_SHAPE=7.5`,
`DAYTIME_SHAPE=3.0`) score:

| Metric | Score |
|---|---|
| Headline | 67.7 |
| Count | 97.0 |
| Timing | 48.7 |

All 24 windows scored (100% availability). Headline regressed from
73.3 on the prior export (20260410), driven almost entirely by timing
(55.8 → 48.7). Count remained strong (97.5 → 97.0).

The 154-candidate canonical sweep confirms the current constants as
near-best. The best candidate (`OVERNIGHT_SHAPE=8.0`,
`DAYTIME_SHAPE=3.0`) gains only +0.12 headline over baseline. The top
5 candidates span 67.5 to 67.8 — the surface is flat, with no
actionable improvement available from shape changes alone.

The weakest canonical windows are concentrated on 2026-04-10: the two
latest windows (07:40 and 09:45) both score headline 47.8 with timing
22.8–22.9. These carry the highest recency weights (0.96 and 1.0) and
drive the aggregate regression. Earlier windows (April 7–9) score in
the 66–82 range, consistent with prior performance.

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

**Episode history remains essential:** Raw bottle-only events (248)
collapse to 217 episode events, absorbing 31 cluster feeds. The direct
day-part Weibull fits sharpen materially after collapsing:
overnight `4.90 -> 6.04`, daytime `2.59 -> 3.65`. That is the cleanest
evidence that raw feeds were contaminating the gap distribution with
cluster-internal noise.

**Internal fit vs canonical replay remain broadly converged:** The
episode-level MLE fit prefers shapes (`6.04`, `3.65`), while canonical
replay selects (`7.5`, `3.0`). The convergence first observed on the
prior export (where MLE was `6.00`/`3.54`) is durable: MLE shapes
barely moved despite 10 new events. Overnight canonical remains above
MLE (7.5 vs 6.04), daytime canonical remains below (3.0 vs 3.65).

The remaining divergence likely has two contributors:

1. *Data window.* The episode-level fit uses all 216 gaps in the full
   export. Canonical replay optimizes over the last ~96h with 36h
   half-life. The baby's feeding patterns are still shifting with growth,
   so full-history MLE and recent-window replay describe slightly
   different regimes.

2. *Model compensation.* The production forecaster chains deterministic
   medians, re-estimates scale at runtime, and uses conditional survival
   for the first feed. These mechanics pull optimal shapes away from
   the MLE, but the effect is modest and stable across exports.

The stability of convergence across two consecutive exports is
meaningful. On the 20260327 export, MLE/canonical diverged by a
factor of two (MLE 7.2/3.4 vs canonical 4.75/1.75). On 20260410 and
20260411, both sources agree that the baby's rhythm is regular. This
suggests the prior divergence was non-stationarity, not an
architectural deficiency.

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

**Holdout: count regressed, timing improved.** The shipped model
predicts 8 feeds vs. 11 actual in the most recent 24h holdout, with
1.05h mean timing error on the 8 matched pairs. Feed count error is 3
(the model missed 3 feeds). Timing per matched pair improved from 1.53h
on the prior export, but the count miss is a significant regression from
the prior export's perfect 8-vs-8 match. The baby fed more frequently on
this holdout day than the model's 7-day lookback window predicted.

## Conclusions

**Disposition: Keep.** `OVERNIGHT_SHAPE=7.5` and `DAYTIME_SHAPE=3.0`
are retained. The 154-candidate canonical sweep finds no shape change
that meaningfully improves the headline (best improvement: +0.12).

The canonical headline regressed from 73.3 to 67.7 on this export,
driven by timing (55.8 → 48.7) while count stayed strong (97.0). The
regression concentrates in the two most recent windows (April 10, both
scoring 47.8), which carry the highest recency weight. The 24h holdout
confirms the pattern: the model predicted 8 feeds but the baby had 11,
suggesting the baby fed more frequently on that day than the model's
7-day lookback window anticipated.

The regression is not addressable through shape parameter changes. The
sweep surface is flat across all tested shapes. The cause is likely
day-to-day variability (or a short-term frequency increase) that
affects runtime scale estimation, not the fixed shape constants.

The MLE/canonical convergence observed on the prior export is durable:
episode-level MLE (`6.04`, `3.65`) and canonical replay (`7.5`, `3.0`)
continue to broadly agree across consecutive exports. The model's
structural integrity is sound; the regression reflects a harder
prediction window, not a constant mismatch.

## Open questions

### Model-local

- **MLE/canonical convergence is stable across two consecutive
  exports.** The prior factor-of-two divergence (20260327 export) has
  not returned. This makes it more likely that the baby's patterns have
  genuinely stabilized than that the convergence was coincidental. If
  divergence returns on a future export, the follow-ups from the prior
  round (windowed MLE and component ablation) remain relevant.
- **Is the holdout count miss (8 vs 11) a one-day anomaly or a trend?**
  The model predicted 8 feeds on the most recent holdout day; the baby
  had 11. If subsequent exports show a sustained move toward more
  frequent feeding, the runtime scale estimation or lookback window may
  need revisiting. The canonical sweep says shape changes do not help,
  so any adaptation would need to come through scale-related constants
  (`LOOKBACK_DAYS`, `RECENCY_HALF_LIFE_HOURS`) or model structure.
- **Should half-life be re-tuned canonically?** Episode-level
  diagnostics still show a gap1/feed-count trade-off across
  `48h–168h`, but the gap1 difference is now negligible (0.001h). The
  current `168h` has not been swept canonically since the 20260410
  shape retune.
- **Can volume help under a different formulation?** The scalar AFT
  overlay is decisively bad. A residual or regime-specific volume term
  may still be worth testing, but it should be treated as a new model
  idea, not a tweak to the rejected overlay.

### Cross-cutting

- **MLE/canonical convergence is now confirmed across two exports for
  this model:** Episode-level MLE (`6.04`/`3.65`) and canonical replay
  (`7.5`/`3.0`) continue to broadly agree. This is worth tracking as a
  cross-model signal; if other models show similar convergence on newer
  data, the stacked generalization question may need re-framing.
- **Timing as shared bottleneck:** Timing (48.7) lags count (97.0). This
  pattern persists across all models — see `feedcast/research/README.md`.
  The timing regression on this export (55.8 → 48.7) concentrates in the
  most recent windows, which may reflect a particularly variable day
  rather than a structural shift.
