# Simulation Study Methodology

Feedcast validates each scripted model against synthetic data generated
from a known process. The purpose is to verify that the model's
implementation correctly exploits the structure its design assumes,
independently of whether real feeding data conforms to that assumption.

This document describes the shared methodology. Model-specific details
(exact DGP formulas, invariants, validation targets) live in the test
files under `tests/simulation/`.

## Motivation

Each scripted model encodes a hypothesis about what generates the
observed feeding pattern:

| Model | Hypothesis |
|---|---|
| Latent Hunger | Hunger rises over time; feeds reset it proportional to volume. Larger feeds produce longer subsequent gaps via multiplicative satiety. |
| Survival Hazard | Feeding probability increases with elapsed time, following a Weibull hazard with distinct day and night regimes. |
| Slot Drift | Feeds follow a recurring daily template whose slot times drift predictably over successive days. |
| Analog Trajectory | Similar historical feeding states lead to similar subsequent feeding patterns. Pattern recurrence is the primary signal. |

The production forecaster for each model is more complex than the
hypothesis alone: it chains predictions across a 24-hour horizon,
applies episode collapsing, uses recency-weighted fitting, and
interacts with the shared scoring and replay infrastructure. A model
can be correctly designed yet incorrectly implemented, or correctly
implemented yet poorly served by its production pipeline.

Simulation testing isolates the first concern — implementation
correctness — by generating data where the hypothesis is exactly true
and checking whether the model behaves as expected.

## Approach

This is a **simulation study**: a standard statistical validation
technique in which a method is evaluated on data sampled from a known
**data-generating process (DGP)**. The approach has three layers, each
answering a different question:

1. **Specification testing.** Does the model behave correctly when its
   own assumptions hold? This is the most basic check: generate data
   from the model's assumed DGP, run the model, and verify the output
   is consistent with the known generative process. A failure here is
   an implementation bug.

2. **Parameter recovery.** Can the model's fitting procedure recover
   the true DGP parameters? This applies to models that estimate
   parameters from data (e.g., Latent Hunger's satiety rate via
   gap-MAE sweep, Survival Hazard's Weibull shapes via MLE). Not all
   models expose cleanly recoverable parameters — Slot Drift recovers
   template structure, and Analog Trajectory recovers retrieval
   behavior — so the recovery target is model-specific.

3. **Canonical diagnostic.** Does the canonical replay pipeline agree
   with the model's internal diagnostics when the model's assumptions
   are satisfied? This is the most informative layer: it decomposes
   the observed canonical/internal divergence on real data into a
   **pipeline-structural** component (present even on synthetic data)
   and a **hypothesis-fit** component (present only on real data).

## Data-Generating Process Design

### Principles

Each model's DGP is the generative inverse of its forecasting logic.
Where the model predicts `gap = f(state)`, the DGP samples
`gap ~ f(state) + noise`. The DGP should:

- **Match the model's actual implementation**, not an idealized version
  of the hypothesis. The DGP formula is derived from reading the
  model's source code, not from the design document's prose
  description. Design docs describe intent; the code is the contract.

- **Use minimal complexity.** The default DGP includes only the
  hypothesis signal and controlled noise. Circadian effects, episode
  clustering, feed-count variation, and other realism layers are
  documented as extensions but omitted from the default tests. This
  isolates the hypothesis under test from confounding structure.

- **Respect production invariants.** Synthetic data must pass through
  the same parsing and event-construction pipeline as real data.
  Activities must be timestamped after `DATA_FLOOR`, bottle volumes
  must be positive, and breastfeed volumes must match the production
  duration heuristic. The shared `validate_export_activities()`
  function enforces these per-activity constraints. History length
  is a DGP responsibility — each DGP must generate enough data to
  satisfy the model's lookback and minimum-data requirements.

- **Produce enough data for stable estimation.** Each model has minimum
  data requirements (lookback windows, minimum gap counts, minimum
  complete states). The DGP must generate histories that comfortably
  exceed these thresholds.

### Noise regime

The default DGP uses two noise levels:

- **Low noise** for forecast accuracy tests. The model's predictions
  should track the known DGP trajectory closely, so noise is set low
  enough that the expected behavior is unambiguous. Deterministic
  (zero-noise) variants are preferred where the model's forecasting
  logic is itself deterministic (e.g., Survival Hazard predicts
  Weibull medians).

- **Moderate noise** for parameter recovery tests. The fitting
  procedure needs realistic variance in the data to exercise its
  estimation logic, but not so much that recovery becomes unreliable
  with the available sample size.

### Model-specific DGP summary

**Latent Hunger.** The shipped model predicts gap as
`HUNGER_THRESHOLD * (1 - exp(-SATIETY_RATE * volume)) / growth_rate`.
The DGP generates feeds using this formula with known threshold, rate,
and growth rate, plus Gaussian noise. Volume is drawn from a small
fixed set so the volume-gap relationship is unambiguous. Recovery
target: `SATIETY_RATE` via gap-MAE sweep.

**Survival Hazard.** The shipped model fits Weibull scales at runtime
from fixed shape parameters and predicts conditional medians. The DGP
samples inter-feed times from `Weibull(shape, scale)` distributions
with known parameters, using the model's day-part assignment rule
(overnight starts at 20:00, daytime at 08:00; day-part is determined
by the hour when the gap begins, not when it ends). The default test
path uses a deterministic median-path DGP for specification testing of
the shipped forecaster. Recovery target: Weibull shape parameters via
the analysis-code MLE fitter (not the shipped model, which uses fixed
shapes).

**Slot Drift.** The shipped model identifies a daily template via
Hungarian matching and tracks per-slot drift with recency weighting.
The DGP generates a fixed daily template with per-slot linear drift
and Gaussian jitter, held well under the match-cost threshold so
template matching succeeds cleanly. Every synthetic day has the same
feed count. Recovery target: structural — correct slot count,
approximate template times, drift direction and magnitude.

**Analog Trajectory.** The shipped model retrieves historical states
with similar features and blends their subsequent trajectories. The
DGP plants 2-3 distinct state archetypes, each with a characteristic
subsequent feeding pattern, repeated enough times that the model has
sufficient complete analog states. Archetype features are well-
separated in the model's feature space. Recovery target: retrieval
correctness (nearest neighbors match the planted archetype) and
trajectory-matched forecast.

## Validation Protocols

### Specification testing

A specification test verifies that the model produces internally
consistent output when its assumptions are satisfied. The test
generates a synthetic history from the DGP, runs the model's forecast
function at a cutoff within that history, and checks:

- **Feed count**: the number of predicted episodes in the horizon
  matches the DGP's expected count, within a tolerance that accounts
  for boundary effects (feeds near the horizon edge).
- **Timing**: predicted feed times are close to the DGP's expected
  times, within a tolerance that accounts for noise and
  discretization.

Tolerances are set per-model based on the DGP's noise level and the
model's known approximations. The shared `assert_forecast_times_close`
and `assert_datetimes_close` functions enforce these checks with
clear error messages.

A specification test failure means the implementation does not match
the design: the model cannot correctly handle data that conforms to
its own hypothesis. This is always a bug, never a property of the
data.

### Parameter recovery

A parameter recovery test verifies that the model's estimation
procedure can find the true DGP parameters when the data conforms to
the model's assumptions. The test generates synthetic data with known
parameters, runs the estimation procedure, and checks that the
recovered values are close to the truth.

The recovery target is model-specific:

| Model | Recovery mechanism | What is checked |
|---|---|---|
| Latent Hunger | Sweep `SATIETY_RATE` via gap-MAE | Best rate is within tolerance of the DGP's true rate |
| Survival Hazard | MLE fit via analysis code | Recovered shape parameters match the DGP's true shapes |
| Slot Drift | Template extraction + drift tracking | Slot count, template times, and drift magnitude match the DGP |
| Analog Trajectory | Nearest-neighbor retrieval | Retrieved neighbors correspond to the correct planted archetype |

The shared `assert_value_within_tolerance` and
`assert_replay_best_param_within_tolerance` functions check scalar
parameter recovery. Structural recovery (Slot Drift, Analog
Trajectory) uses model-specific assertions.

A parameter recovery failure means the estimation procedure is flawed:
it cannot find the correct answer even when the data is ideal. This
may indicate a bug, a numerical instability, or a fundamental
limitation of the fitting approach.

### Forecast accuracy

A forecast accuracy test verifies that the model produces good
predictions from synthetic data, not just good parameter estimates.
The test generates a synthetic history, runs the full forecast
pipeline (including episode collapsing, chained prediction, and
horizon truncation), and scores the output against the known DGP
future.

This is distinct from specification testing because it exercises the
full production forecast path, not just the core prediction logic.
A model can pass specification tests but fail forecast accuracy if the
production pipeline introduces distortions (e.g., episode collapsing
changes the effective gap distribution, or chained prediction
accumulates error across the horizon).

## Canonical Diagnostic Protocol

The canonical diagnostic answers a specific question: **does the
canonical replay pipeline agree with internal diagnostics when the
model's assumptions are satisfied?**

Three of four models show systematic disagreement between their
internal fitting diagnostics and canonical replay on real data (see
[research hub](../README.md) for the current evidence). The divergence
could be:

- **Pipeline-structural**: the production pipeline's mechanics
  (chained predictions, episode matching, horizon weighting, recency
  decay) systematically distort the relationship between DGP
  parameters and end-to-end forecast quality, even when the model's
  assumptions are correct.
- **Hypothesis-fit**: the model's assumptions do not fully describe
  the real data, and internal diagnostics and canonical replay
  disagree on the best accommodation to that mismatch.

The simulation study disambiguates these by running canonical replay
on synthetic data where the model's assumptions are exactly true:

1. Write the synthetic history to a temporary Nara-format export
   using the shared `write_nara_export()` infrastructure.
2. Run `tune_model()` with a targeted parameter grid on the synthetic
   export.
3. Compare the canonical-optimal parameters to the DGP truth and to
   the internal-diagnostic optimum.

If canonical and internal **agree on synthetic data**: the real-data
divergence is a hypothesis-fit effect. The pipeline is faithfully
transmitting the DGP signal; the disagreement on real data means the
real data does not fully conform to the model's assumptions.

If canonical and internal **disagree on synthetic data**: the
divergence is pipeline-structural. The production pipeline distorts
the parameter-to-forecast-quality mapping even under ideal conditions.
This is informative for the stacked generalization investigation — it
identifies which models are candidates for internal-objective tuning
and whether the ensemble blend needs architectural changes to exploit
model diversity.

### Performance considerations

Canonical diagnostic sweeps use the same replay infrastructure as
production tuning. For models with large parameter grids, enable
candidate-level parallelism (`parallel_candidates=True` with
`candidate_workers`). Candidate parallelism and window parallelism
are mutually exclusive in the current replay runner — candidate mode
disables per-window threading internally.
Tests are marked `@pytest.mark.slow` only when measured wall time
exceeds 60 seconds — grid size alone is not a sufficient reason to
defer a test.

## Test Infrastructure

The simulation test infrastructure lives in `tests/simulation/`:

| Module | Purpose |
|---|---|
| `factories.py` | Synthetic `Activity` construction from abstract feed schedules |
| `export.py` | Nara-format CSV export writer with production-invariant validation |
| `assertions.py` | Shared assertion functions for timing, parameter recovery, and replay payloads |
| `conftest.py` | pytest fixtures for temporary exports and replay output directories |

Model-specific DGP generators and tests are added per-model as the
simulation study progresses. Each model's tests import from the shared
infrastructure and add model-specific DGP logic and assertions.

### Running the tests

```bash
# All simulation tests
.venv/bin/pytest tests/simulation/ -q

# Exclude slow canonical diagnostic sweeps
.venv/bin/pytest tests/simulation/ -q -m "not slow"

# One model's simulation tests
.venv/bin/pytest tests/simulation/test_latent_hunger.py -q
```

## Relationship to Production Evaluation

This methodology complements the canonical evaluation methodology
documented in [`feedcast/evaluation/methodology.md`](../../evaluation/methodology.md).
The two serve different purposes:

| | Production evaluation | Simulation study |
|---|---|---|
| **Data** | Real feeding history | Synthetic data from a known DGP |
| **Question** | How well does the model forecast? | Does the implementation match the design? |
| **Ground truth** | Observed actuals (partially visible) | Known generative process (fully visible) |
| **Failure meaning** | Model is inaccurate on this data | Model has an implementation or design flaw |
| **Scoring** | Canonical scorer (Hungarian matching, horizon weighting) | DGP-aware assertions + canonical scorer for diagnostic layer |

Production evaluation measures forecast quality. Simulation testing
measures implementation correctness. A model should pass simulation
tests before its production evaluation results are trusted — otherwise
production scores may reflect implementation artifacts rather than the
model's true capability.

## Relationship to Stacked Generalization

The canonical diagnostic layer of this simulation study is a direct
prerequisite for the stacked generalization investigation (see
[research hub](../README.md) for the current framing). The cross-model
divergence decomposition — which models show pipeline-structural
divergence vs. hypothesis-fit divergence — determines which models are
candidates for internal-objective tuning and whether the consensus
blend architecture needs model-specific weighting to exploit the
resulting diversity.
