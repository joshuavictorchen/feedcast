# Simulation Study: Cross-Model Findings

## Last run

| Field | Value |
|---|---|
| Date | 2026-04-03 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Test command | `.venv/bin/pytest tests/simulation/ -q` |
| Model research | Per-model `research.md` and `artifacts/research_results.txt` under [`feedcast/models/`](../../models/) |
| Full methodology | [`methodology.md`](methodology.md) |

> **Staleness check:** conclusions depend on both the simulation test
> suite (synthetic-data evidence) and the per-model research outputs
> (real-data evidence). If either has been updated since this date,
> re-read the evidence before relying on these conclusions.

## Overview

Each Feedcast model encodes a different hypothesis about what drives
feeding patterns — mechanistic hunger, hazard-based timing, daily
template drift, or historical pattern recurrence. When tuning these
models on real data, internal diagnostics (gap-MAE, maximum likelihood
estimation, trajectory error) and canonical replay scoring frequently
disagree on optimal constants. This document reports the cross-model
findings from the simulation study, which was designed to answer a
specific question: **is that disagreement caused by the evaluation
pipeline, or by the real data not fully conforming to the model's
hypothesis?**

The distinction matters for the project's highest-priority open
question — whether models should be tuned to their own native
objectives rather than the shared canonical objective. This is a
**stacked generalization** design (Wolpert, 1992): a two-stage
ensemble where individual models optimize their own loss functions and
a meta-learner combines them for the end-to-end objective. See the
[research hub](../README.md#open-questions) for the full framing.

If the pipeline distorts the parameter-to-forecast-quality
relationship, internal tuning would be fighting against pipeline
mechanics — a poor starting point for stacked generalization. If the
pipeline is sound and the disagreement comes from hypothesis
non-conformity, internal tuning would produce more diverse model
outputs — potentially valuable for an ensemble.

### Two types of divergence

When internal diagnostics and canonical replay disagree on optimal
constants, one of two things is happening:

**Hypothesis-fit divergence.** Internal and canonical objectives agree
on synthetic data (where the hypothesis is exactly true) but disagree
on real data. This means the real data doesn't fully conform to the
model's hypothesis, and the two objectives accommodate that mismatch
differently. Internal diagnostics optimize for hypothesis-accurate
local prediction (e.g., "predict the next gap as accurately as
possible given the model's formula"). Canonical replay optimizes for
24-hour forecast quality under the full production pipeline (chained
predictions, episode matching, horizon weighting). The disagreement on
real data is informative — it measures how much each model's
hypothesis approximation affects the two objectives differently.

**Pipeline-structural divergence.** Internal and canonical objectives
disagree even on synthetic data where the hypothesis is exactly true.
This means the production pipeline's mechanics systematically distort
the relationship between model parameters and forecast quality,
regardless of data fit. The disagreement would be a property of the
evaluation machinery, not the data.

## Methods

For each model, the simulation study generated synthetic feeding
histories from a known **data-generating process (DGP)** — a formula
or rule that produces feeding data where the model's hypothesis is
exactly true. It then ran both internal diagnostics and canonical
replay on that synthetic data and compared the results. See
[`methodology.md`](methodology.md) for detailed protocol design,
DGP construction principles, and test infrastructure.

The comparison has three components per model:

1. **Specification test.** Does the model produce correct forecasts
   when its assumptions hold? This verifies the implementation.
2. **Parameter or structural recovery.** Can the fitting procedure
   recover the known DGP parameters or structure? This verifies the
   estimation logic.
3. **Canonical diagnostic.** Does canonical replay agree with internal
   diagnostics on synthetic data? This verifies the pipeline.

Real-data divergence evidence comes from each model's
[research file](../../models/), where internal and canonical results
are documented from analysis on the same export.

## Results

### Latent Hunger

**Hypothesis:** Hunger rises over time; feeds reset it proportional to
volume via multiplicative satiety. Larger feeds produce longer
subsequent gaps.

**Internal diagnostic:** Episode-level gap-MAE sweep — measures
one-step-ahead gap prediction accuracy across satiety rate candidates.

**On synthetic data** (DGP truth: `SATIETY_RATE=0.35`):

- Internal gap-MAE optimum: 0.35 (exact match with DGP truth).
- Canonical replay optimum: 0.35 (exact match with DGP truth).
- Internal and canonical **agree**.

**On real data:**

- Internal gap-MAE optimum: sr ≈ 0.6 (rewards stronger per-feed
  volume differentiation).
- Canonical replay optimum: sr = 0.05 (rewards more uniform gap
  predictions for better episode-count matching).
- Disagreement is large — the two objectives point in opposite
  directions relative to the DGP truth.

**Classification: hypothesis-fit.** The pipeline correctly identifies
the DGP rate on synthetic data. The real-data divergence is driven by
real feeding gaps not following the pure multiplicative satiety
formula. Internal diagnostics accommodate the mismatch by increasing
volume sensitivity; canonical replay accommodates it by dampening
volume sensitivity for more consistent 24-hour trajectories.

**Evidence:**
[Latent Hunger research](../../models/latent_hunger/research.md),
[simulation tests](../../../tests/simulation/test_latent_hunger.py).

### Survival Hazard

**Hypothesis:** Feeding probability increases with elapsed time,
following a Weibull hazard function with distinct overnight and
daytime regimes.

**Internal diagnostic:** Episode-level maximum likelihood estimation
(MLE) of Weibull shape parameters — measures how well the parametric
distribution fits the observed gap data.

**On synthetic data** (DGP truth: overnight shape = 4.5, daytime
shape = 2.0):

- MLE recovery: overnight ≈ 4.5, daytime ≈ 2.0 (within tolerance).
- Canonical replay optimum: overnight ≈ 4.5, daytime ≈ 2.0 (within
  0.25 of DGP truth).
- Internal and canonical **agree**.

**On real data:**

- MLE optimum: overnight = 7.23, daytime = 3.42 (sharper shapes,
  describing the gap distribution).
- Canonical replay optimum: overnight = 4.75, daytime = 1.75 (softer
  shapes, serving the production forecaster's chained-median
  mechanics).
- Disagreement is material — same direction but roughly 2x magnitude
  difference.

**Classification: hypothesis-fit.** The pipeline correctly identifies
DGP shapes on synthetic data. The real-data divergence has two
documented contributing factors: (1) the MLE uses full-history data
while canonical replay uses a 96-hour recency-weighted window, so
they may describe different growth stages; and (2) the production
forecaster chains deterministic Weibull medians with conditional
survival and runtime scale estimation, which is better served by
softer shapes than the true distributional fit suggests.

**Evidence:**
[Survival Hazard research](../../models/survival_hazard/research.md),
[simulation tests](../../../tests/simulation/test_survival_hazard.py).

### Slot Drift

**Hypothesis:** Feeds follow a recurring daily template whose slot
times drift predictably over successive days.

**Internal diagnostic:** Structural recovery — slot count, template
times, and per-slot drift direction and magnitude. Unlike the previous
two models, Slot Drift does not expose a single optimizable scalar
through an internal diagnostic; recovery is structural and behavioral.

**On synthetic data** (DGP: 8 daily slots with known linear drift
rates and bounded Gaussian jitter):

- Structural recovery: correct slot count (8), template times within
  0.02h, drift rates within 0.001 h/day.
- Canonical replay: prefers `LOOKBACK_DAYS=7` and
  `DRIFT_WEIGHT_HALF_LIFE_DAYS=7.0` — longer lookback and slower
  decay, consistent with exploiting the DGP's clean stationary drift.
- Structural recovery and canonical replay are **consistent**: both
  benefit from using more history to average out jitter when the
  underlying drift is truly linear.

**On real data:**

- Canonical replay: prefers `LOOKBACK_DAYS=5` and
  `DRIFT_WEIGHT_HALF_LIFE_DAYS=1.0` — shorter lookback and faster
  decay.
- No documented divergence between internal structural diagnostics and
  canonical replay on real data. (Slot Drift is not listed among the
  three models with explicit internal-canonical disagreement in the
  [research hub](../README.md#open-questions).)

**Classification: pipeline sound; full decomposition incomplete.**
The synthetic evidence establishes that the pipeline makes
structurally sound choices — no pipeline-structural distortion
detected. The shift from longer smoothing (synthetic) to faster
adaptation (real) is consistent with real feeding patterns that are
not stationary linear drift: the baby's schedule reorganizes over
days and weeks, rewarding faster adaptation.

However, Slot Drift does not expose a scalar internal diagnostic that
can be swept and compared to canonical on real data the way Latent
Hunger's gap-MAE or Survival Hazard's MLE can. Without a documented
internal-canonical comparison on real data, the full hypothesis-fit
classification requires an additional assumption: that the structural
diagnostics (template recovery, drift tracking) would agree with
canonical if evaluated on the same axis. This is plausible but not
directly demonstrated.

**Evidence:**
[Slot Drift research](../../models/slot_drift/research.md),
[simulation tests](../../../tests/simulation/test_slot_drift.py).

### Analog Trajectory

**Hypothesis:** Similar historical feeding states produce similar
subsequent feeding patterns. Pattern recurrence is the primary signal.

**Internal diagnostic:** Full-trajectory MAE (`full_traj_MAE`) —
measures nearest-neighbor retrieval quality and trajectory
reconstruction accuracy.

**On synthetic data** (DGP: two distinct state archetypes alternating
across 14 days, both anchored at the same hour so retrieval must use
gap/volume structure, not time-of-day):

- Retrieval recovery: nearest neighbors match the planted archetype
  exactly (distance ≈ 0.0).
- Forecast conformance: predicted times and volumes match the planted
  future within tight tolerance.
- Canonical replay: prefers the focused retrieval regime
  (`LOOKBACK_HOURS=12`, `recent_only` feature weights,
  `K_NEIGHBORS=5`) over blurrier alternatives.
- Retrieval, forecast, and canonical are **consistent**: all reward
  clean archetype separation.

**On real data:**

- Internal (`full_traj_MAE`): prefers `LOOKBACK_HOURS=48`,
  `means_only` feature weights, `RECENCY_HALF_LIFE_HOURS=36`.
- Canonical replay: prefers `LOOKBACK_HOURS=12`, `recent_only`
  feature weights, `RECENCY_HALF_LIFE_HOURS=72`.
- Both agree on the major architectural choices (episode history over
  raw, gap alignment over time-offset). They disagree on specific
  constant values — internal rewards broader context for trajectory
  reconstruction; canonical rewards tighter focus for 24-hour forecast
  quality.

**Classification: hypothesis-fit.** The pipeline correctly identifies
the focused regime on synthetic data where archetypes are clean and
well-separated. On real data, where feeding "archetypes" are
approximate and overlapping, the two objectives optimize for different
aspects of the approximation.

**Evidence:**
[Analog Trajectory research](../../models/analog_trajectory/research.md),
[simulation tests](../../../tests/simulation/test_analog_trajectory.py).

### Cross-model comparison

| Model | Comparison basis | Synthetic result | Real-data divergence | Classification |
|---|---|---|---|---|
| Latent Hunger | `SATIETY_RATE` | Internal = Canonical = DGP (0.35) | Internal (0.6) vs. Canonical (0.05) | Hypothesis-fit |
| Survival Hazard | Weibull shapes | Internal ≈ Canonical ≈ DGP (4.5/2.0) | Internal (7.2/3.4) vs. Canonical (4.75/1.75) | Hypothesis-fit |
| Slot Drift | Structural regime | Recovery ✓; Canonical picks consistent regime | No internal-canonical comparison documented | Pipeline sound; decomposition incomplete |
| Analog Trajectory | Retrieval regime | Retrieval exact; Canonical picks focused regime | Architecture agrees; constants disagree | Hypothesis-fit |

**Evidence quality.** The evidence falls into two tiers:

- **Direct numeric comparison** (Latent Hunger, Survival Hazard):
  both models expose scalar parameters where internal and canonical
  can be compared on the same axis, on both synthetic and real data.
  The classification rests on demonstrated agreement (synthetic) and
  demonstrated disagreement (real).
- **Structural/regime-level comparison** (Slot Drift, Analog
  Trajectory): internal diagnostics measure qualitative recovery
  (correct slots, correct neighbors), and canonical replay picks
  hyperparameters in a different space from the DGP truth. For Analog
  Trajectory, documented real-data disagreement on constant values
  completes the hypothesis-fit picture. For Slot Drift, the real-data
  half of the comparison is missing — no internal-canonical divergence
  is documented, and the model does not expose a scalar diagnostic
  that would make the comparison straightforward.

## Conclusions

**On the current hypothesis-conforming synthetic fixtures, the canonical
replay pipeline shows no structural distortion for the four models.**
Each model's canonical diagnostic on synthetic data either recovers the
DGP parameters directly (Latent Hunger, Survival Hazard) or picks
hyperparameter regimes consistent with correct model behavior (Slot
Drift, Analog Trajectory).

**Three models have confirmed hypothesis-fit divergence.** Latent
Hunger, Survival Hazard, and Analog Trajectory each have documented
internal-canonical disagreement on real data that is absent on
synthetic data — the defining signature of hypothesis-fit divergence.
The real-data disagreement is driven by each model's hypothesis
fitting real feeding data approximately rather than exactly.

**Slot Drift's decomposition is incomplete.** The pipeline is sound
(verified on synthetic data), but no internal-canonical divergence is
documented on real data. The model does not expose a scalar internal
diagnostic comparable to gap-MAE or MLE shapes, which makes the
real-data comparison harder to formulate. The available evidence is
consistent with hypothesis-fit divergence but does not conclusively
establish it.

This overall pattern is expected: the models were designed as
complementary perspectives — mechanistic, hazard-based, template, and
instance-based — not as competing implementations of the same view.

### Implications for stacked generalization

These findings are necessary but not sufficient for the stacked
generalization decision. They establish that **internal tuning is a
coherent intervention**: the pipeline is not distorting the signal, so
models tuned to their own objectives would genuinely express their
hypotheses more strongly. Specifically:

- **Latent Hunger** at sr ≈ 0.6 would produce volume-sensitive gap
  predictions rather than the near-uniform predictions at sr = 0.05.
- **Survival Hazard** at shapes 7.2/3.4 would produce sharper,
  more peaked hazard forecasts rather than the broader predictions at
  4.75/1.75.
- **Analog Trajectory** at lb=48h with means-emphasis would retrieve
  neighbors from a wider context rather than the recent-focused
  retrieval at lb=12h.
- **Slot Drift** is less clear — no internal diagnostic divergence is
  documented on real data. Internal tuning would require first
  defining what "internal-optimal" means for a structural-recovery
  model, which is an open question.

Whether this increased diversity improves ensemble quality is a
separate question. It depends on (a) how much individual accuracy each
model sacrifices by moving away from canonical-optimal constants and
(b) whether the consensus blend can exploit the resulting diversity.
The current blend uses unweighted majority voting — every model counts
equally regardless of context. It has no mechanism for differential
model trust, which limits its ability to benefit from increased
diversity even if that diversity is present.

### Caveats

- The hypothesis-fit / pipeline-structural classification is binary,
  but reality may include small pipeline effects masked by larger
  hypothesis-fit effects. The simulation tests use tolerance bounds;
  agreement within tolerance does not mean zero distortion.
- Slot Drift lacks a scalar internal diagnostic comparable to Latent
  Hunger's gap-MAE or Survival Hazard's MLE, leaving its real-data
  decomposition incomplete. Analog Trajectory's evidence is structural
  but includes documented real-data divergence on constant values.
- The synthetic DGPs are minimal by design — they test the core
  hypothesis without realism layers (circadian modulation, episode
  clustering, realistic volume distributions). More complex DGPs
  might reveal pipeline effects that minimal DGPs do not exercise.

## Open questions

- **Diagnostic experiment.** Re-tune the three confirmed models
  (Latent Hunger, Survival Hazard, Analog Trajectory) to their
  internal-diagnostic optima and score the resulting blend canonically
  without changing the blend architecture. This tests whether
  increased diversity from internal tuning translates to better (or at
  least comparable) ensemble quality under unweighted majority voting.
  See the
  [stacked generalization investigation](../README.md#open-questions)
  for the broader context.
- **Weighted blending.** If the diagnostic experiment shows promise
  (or holds steady), design model-specific weighting into the
  consensus blend. The current unweighted architecture cannot exploit
  differential model quality — every model counts equally regardless
  of context or track record.
- **Slot Drift decomposition.** Define and evaluate an internal
  diagnostic for Slot Drift that can be compared to canonical on real
  data — e.g., a template-recovery quality metric swept across
  `LOOKBACK_DAYS` and `DRIFT_WEIGHT_HALF_LIFE_DAYS`. This would
  either confirm the hypothesis-fit classification or reveal a
  divergence pattern distinct from the other three models.
- **Extended DGPs.** The minimal synthetic DGPs omit realism layers
  that interact with the production pipeline (circadian effects,
  episode clustering, continuous volume distributions). Testing under
  these conditions could surface pipeline effects invisible to the
  current minimal tests.
