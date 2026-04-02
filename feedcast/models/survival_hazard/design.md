# Survival Hazard Design Decisions

This document records stable design choices. Current fitted values,
replay metrics, and dataset-specific measurements live in `analysis.py`
and `artifacts/research_results.txt`. Current production constants live in
`model.py`. The goal here is to explain why the model is shaped this
way, not to duplicate a moving numerical snapshot.

## Weibull family

The Weibull distribution models inter-episode gaps because its shape
parameter directly encodes whether feeding probability increases with
elapsed time. Shape > 1 means the longer since the last feed, the more
likely the next — matching biological reality.

## Episode-level history

The model operates on episode-level history. Raw bottle-only events
are collapsed into feeding episodes via `episodes_as_events()` at
function entry. This removes cluster-internal sub-hour gaps that
would otherwise create a bimodal artifact in a distribution where real
inter-episode gaps are measured in hours.

Inter-episode gaps use anchor-to-anchor timing (first constituent to
first constituent). This overestimates the true inter-hunger gap by
the cluster-internal duration, but the same
overestimation applies to both training (scale estimation) and
prediction (conditional survival elapsed time), so the model is
self-consistent.

Episode volume (sum of constituents) is used for the simulation volume
median. This correctly reflects total intake per feeding episode.

## Day-part split

Overnight and daytime feeding patterns are structurally different, not
just shifted. The model therefore fits separate Weibull distributions
for the configured overnight and daytime periods. Overnight gaps are
more regular and tightly peaked; daytime gaps are broader and more
variable.

The exact current shapes are intentionally not duplicated here. The
current production constants live in `model.py`. The episode-level fit,
walk-forward comparison, and canonical replay tuning are recorded in
`artifacts/research_results.txt` under `EPISODE-LEVEL ANALYSIS`,
`CANONICAL PARAMETER TUNING`, and `FINAL SUMMARY`.

**Descriptive fit vs production tuning:** The episode-level MLE fit
and the canonical replay-tuned production constants serve different
purposes and are tracked separately:

- *Episode-level fit (descriptive):* Fits Weibull shapes directly to
  observed episode gaps across the full export history. Answers: "Is
  the Weibull family appropriate? Is the day-part split structurally
  real? What does the gap distribution actually look like?"
- *Canonical replay (production):* Sweeps shape parameters through
  the actual production forecaster across recent retrospective windows.
  Answers: "Which constants make the shipped system forecast best?"

For this model, replay prefers materially softer shapes than the
episode-level MLE. The divergence is expected — the production
forecaster adds runtime scale estimation, conditional survival, and
deterministic median chaining, so its optimal shapes depend on those
mechanics, not just the raw gap distribution. The episode-level fit
remains valuable as a structural diagnostic and as a reference point
for measuring how far replay is compensating. If the gap keeps growing
with future exports, that signals the model structure may need
rethinking, not just re-tuning.

Provenance details belong in the research artifact and changelog, not
as frozen numbers in this document.

## Fixed shape, runtime scale

Shape reflects structural regularity (changes slowly as the baby
grows). Scale reflects current pace (changes as feeding frequency
shifts). The model fixes shapes from canonical replay tuning and
estimates scale at runtime using the closed-form weighted MLE:

    λ_hat = (Σ w_i × t_i^k / Σ w_i)^(1/k)

Scale is estimated separately for each day-part from same-period
episode gaps within the configured lookback window. Recency weighting
uses the configured half-life from `model.py`. The current lookback and
half-life choices are justified by the episode-level walk-forward sweep
in `artifacts/research_results.txt`. Broad averaging works because episode-level
history is clean — all gaps are real inter-episode gaps, not cluster
noise. The fixed shapes are not copied directly from those same local
fits; canonical replay chooses the shape pair that produces the best
full-horizon forecasts once runtime scale estimation is in the loop.

## Conditional survival for the first feed

The Weibull is not memoryless (unlike the exponential). Having already
waited `t0` hours changes the conditional distribution of the remaining
time:

    t_remaining = λ × ((t0/λ)^k + ln 2)^(1/k) − t0

If the baby fed recently, the next feed is farther away. If it's been
a while, the conditional median is shorter than the unconditional
median.

## Median as point prediction

The median of the survival function is the natural point prediction.
It is more robust than the mean for skewed distributions and avoids
the "early mode" problem of right-skewed densities. The 25th and 75th
percentiles are included in diagnostics as uncertainty bounds.

## Bottle-only events

The model builds bottle-only events locally (no breastfeed merge).

Under episode semantics, merge policy is not purely cosmetic: the
clustering rule's extension arm checks the later feed's
`volume_oz`, so breastfeed merge could theoretically change episode
boundaries. This model keeps a bottle-only local input policy. The
current bottle-only vs breastfeed-merged comparison is documented in
`artifacts/research_results.txt` under `BREASTFEED MERGE POLICY COMPARISON`,
which is the right place for the dataset-specific counts and boundary
comparison.

## Volume covariate: excluded

Volume was tested as the current scalar AFT overlay
(`effective_scale = base_scale × exp(β × volume_oz)`). On episode-level
data, the likelihood ratio test shows a real association between larger
episodes and longer subsequent gaps. However, walk-forward evaluation
with the day-part split shows this tested overlay worsens prediction
accuracy relative to the no-volume baseline.

The day-part split already captures the volume-gap correlation:
overnight episodes are both larger and more regular. Applying a
per-episode volume adjustment on top of per-daypart runtime scale
estimation overfits — it pushes predictions away from the well-
calibrated base scale without adding useful signal. That is enough to
reject this overlay for production. It does not rule out every future
use of volume under a different model structure. The current LR result
and walk-forward sweep live in `artifacts/research_results.txt` under the
episode-level volume sections.

## Day-part boundaries

Circadian analysis shows a clear transition: overnight has longer,
more regular gaps while daytime has shorter, more variable gaps. The
model uses a single configured overnight/daytime boundary pair defined
in `model.py`. The current boundary choice and supporting measurements
are recorded in `artifacts/research_results.txt`; this document keeps the
behavioral rationale rather than duplicating the current cutoff values.
