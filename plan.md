# Simulation Study for Hypothesis-Conformance Testing

**Status**: Planning (post peer review, ready for implementation)

**Methodology**: This is a **simulation study** — a standard statistical
validation technique where a method is evaluated against data generated
from a known **data-generating process (DGP)**. Each model's design
hypothesis implies a generative model of feeding behavior. We make that
generative model explicit, sample from it, and verify two things: that
the model **behaves correctly when its assumptions hold** (specification
test) and that the model **produces accurate forecasts from conforming
data** (forecast accuracy). For models with recoverable generative
parameters, we additionally verify **parameter recovery**. Running the
same synthetic data through the canonical replay pipeline produces the
**canonical/internal divergence decomposition** — the key diagnostic for
the stacked generalization investigation.

The validation target is model-specific because the models differ in
what they expose:

| Model | Validation type | What is verified |
|---|---|---|
| Latent Hunger | Parameter recovery | `SATIETY_RATE` recovery via gap-MAE sweep |
| Survival Hazard | MLE recovery + forecast validation | Shape recovery via analysis MLE; forecast validation via model with fixed shapes |
| Slot Drift | Structural recovery | Slot count, template times, drift direction/magnitude |
| Analog Trajectory | Retrieval/specification test | Correct neighbor retrieval, trajectory-matched forecast |

## Phase 1: Shared infrastructure + Latent Hunger

### 1a. Shared infrastructure

- `tests/simulation/` directory with `conftest.py`.
- **Synthetic `Activity` factory**: converts abstract feed schedules
  `(timestamp, volume_oz)` into `list[Activity]` matching `data.py`
  types. Must produce multi-day histories (≥96h for replay lookback).
- **Temp-export writer**: writes synthetic `list[Activity]` to a
  temporary Nara-format CSV so that `score_model()` and `tune_model()`
  can consume it. Follows the pattern established in
  `tests/test_replay.py:_write_export()`. The replay infrastructure
  requires file-path input — it calls `load_export_snapshot(export_path)`
  internally, so no in-memory wrapper is needed or desirable.
- **Shared assertions**: forecast timing/count accuracy against known
  DGP expectations, parameter recovery within tolerance (for models
  where that applies).
- **`feedcast/research/simulation_study/methodology.md`**: shared
  simulation study methodology (DGP design principles, specification
  test protocol, forecast accuracy protocol, canonical diagnostic
  protocol). This is persistent cross-cutting research methodology, not
  test fixture data. Model-specific DGP details go in per-model
  sections or test docstrings.
- **Fast/slow test split**: default pytest tests use small targeted
  parameter grids and run in seconds. Large sweeps should use the
  replay parallelization already in the repo (`parallel=True` for
  windows, `parallel_candidates=True` with `candidate_workers` for
  candidate grids where appropriate). Tests are marked
  `@pytest.mark.slow` only if their measured wall time exceeds 60
  seconds on the normal local setup. Grid size alone is not enough
  reason to pre-mark a test as slow.

### 1b. Latent Hunger

- **Hypothesis under test**: hunger rises over time; feeds reset it
  proportional to volume. Larger feeds → longer subsequent gaps via
  multiplicative satiety.
- **DGP**: the shipped model predicts gap as
  `gap = HUNGER_THRESHOLD * (1 - exp(-SATIETY_RATE * volume)) / growth_rate`.
  The DGP inverts this: given known `threshold`, `satiety_rate`, and
  `growth_rate`, generate feeds where each gap follows this formula plus
  Gaussian noise. Low noise for forecast accuracy tests; moderate noise
  for parameter recovery tests. Volume drawn from a small fixed set
  (e.g., 2oz, 3oz, 4oz) so the volume-gap relationship is
  unambiguous.
  - Source: `latent_hunger/model.py:85-115` — `_hunger_after_feed()`
    and `_simulate_gap()`.
- **Parameter recovery**: sweep `SATIETY_RATE` using the model's
  internal gap-MAE diagnostic on synthetic data. The gap-MAE optimum
  should land near the DGP's true rate.
- **Forecast accuracy**: run the model at the true `SATIETY_RATE` on
  synthetic history. Verify predicted feed count and timing track DGP
  expectations.
- **Canonical diagnostic**: write synthetic data to a temp export, run
  `tune_model("latent_hunger", ...)` with a targeted `SATIETY_RATE`
  grid. Compare canonical-optimal rate to the DGP truth and to the
  internal gap-MAE optimum. If they agree → real-data divergence is
  about hypothesis fit. If they disagree → divergence is
  pipeline-structural.
- **Realism extension notes**: circadian modulation (non-zero
  `CIRCADIAN_AMPLITUDE`), episode clustering (top-up feeds within the
  cluster boundary), volume drawn from a realistic continuous
  distribution.

**Deliverable**: working test suite for Latent Hunger, shared
infrastructure stabilized, methodology documented. Learnings feed into
Phase 2.

## Phase 2: Survival Hazard

- **Hypothesis under test**: feeding probability increases with elapsed
  time, following a Weibull hazard with distinct day/night regimes.
- **DGP**: sample inter-feed times from
  `Weibull(shape_overnight, scale_overnight)` and
  `Weibull(shape_daytime, scale_daytime)` with known parameters. Key
  DGP invariants:
  - Day-part is assigned from the hour when the gap **starts** (the
    prior feed's time), not when it ends. Mirrors
    `survival_hazard/model.py:276-281`.
  - Overnight: hours ≥20:00 or <08:00. Daytime: 08:00–20:00. Matches
    `OVERNIGHT_START=20`, `DAYTIME_START=8`.
  - First predicted gap uses conditional survival
    (`_weibull_conditional_remaining`): given elapsed time since the
    last feed, remaining time is drawn from the conditional Weibull.
  - Generate ≥7 days (LOOKBACK_DAYS) with enough gaps per day-part to
    exceed `MIN_DAYPART_GAPS=3`.
- **Default test path**: use a deterministic median-path DGP for the
  shipped forecaster tests. The production model predicts Weibull
  medians, so the clean implementation check is against the model's own
  deterministic target rather than one noisy sampled trajectory.
- **Stochastic extension**: add Monte Carlo replication only as a second
  layer when we want calibration-style evidence. If we test against
  sampled trajectories, judge aggregate error across replications, not
  exact timing on a single draw.
- **MLE recovery**: run the analysis-code MLE fitter (not the shipped
  forecaster) on synthetic data. Verify recovered shape parameters
  match the DGP truth. The shipped model uses fixed shapes and only
  estimates scales at runtime — MLE shape recovery lives in
  `survival_hazard/analysis.py`, not `model.py`.
- **Forecast accuracy**: run the shipped model at the true shapes on
  synthetic data. Verify hazard-derived feed time predictions match
  DGP expectations.
- **Canonical diagnostic**: write synthetic data to temp export, run
  `tune_model("survival_hazard", ...)` with a targeted shape grid.
  Compare canonical-optimal shapes to MLE-recovered shapes and DGP
  truth. Use the existing replay parallelization for larger sweeps.
- **Realism extension notes**: smooth day/night transition rather than
  hard boundary, volume-dependent scale modulation, post-feed
  refractory period.

## Phase 3: Slot Drift

- **Hypothesis under test**: feeds follow a recurring daily template;
  slot times drift predictably over successive days.
- **DGP**: fixed daily template (e.g., 7 feeds at known hours) with
  per-slot linear drift (known rate per day) and Gaussian jitter. Key
  DGP invariants:
  - Generate ≥7 days with **exact same feed count every day** so the
    model sees `MIN_COMPLETE_DAYS=3` complete days within the
    `LOOKBACK_DAYS=5` window.
  - Jitter σ must stay well under `MATCH_COST_THRESHOLD_HOURS=1.5h`
    (e.g., σ=0.25h) so that template matching succeeds cleanly.
    Otherwise unmatched-slot fallback behavior dominates the test
    rather than the drift-tracking hypothesis.
  - Drift rate should be small enough that day-to-day slot positions
    remain within the match threshold of each other.
- **Structural recovery**: verify the model identifies the correct slot
  count, recovers approximate template times, and tracks drift
  direction and magnitude. This is structural/behavioral recovery, not
  a single-parameter recovery — Slot Drift does not expose a clean
  "true parameter vector."
- **Forecast accuracy**: run the model on synthetic history. Verify
  next-day predictions extrapolate drift correctly (predicted slot times
  ≈ template + accumulated drift).
- **Canonical diagnostic**: write synthetic data to temp export, run
  `tune_model("slot_drift", ...)` with a targeted grid for
  `DRIFT_WEIGHT_HALF_LIFE_DAYS`, `LOOKBACK_DAYS`,
  `MATCH_COST_THRESHOLD_HOURS`. Use the existing replay parallelization
  for larger sweeps.
- **Realism extension notes**: occasional skipped feeds, variable feed
  count across days, template reorganization (new slot appearing, old
  slot disappearing).

## Phase 4: Analog Trajectory

- **Hypothesis under test**: similar historical feeding states lead to
  similar subsequent feeding patterns. Pattern recurrence is the primary
  signal.
- **DGP**: define 2–3 distinct "state archetypes" (e.g.,
  "post-large-feed morning" vs. "post-small-feed evening") each with a
  characteristic subsequent trajectory. Plant these states repeatedly in
  the history so the model has retrievable analogs. Key DGP invariants:
  - At least `MIN_COMPLETE_STATES=10` complete historical states.
    Completeness requires `has_late_event and len(future_events) >= 3`
    per `analog_trajectory/model.py:230`.
  - At least `MIN_PRIOR_EVENTS=3` events before each state for feature
    computation.
  - At least `K_NEIGHBORS=5` instances of each planted archetype so
    the retrieval has enough examples.
  - Archetype features (last_gap, mean_gap, last_volume, mean_volume,
    sin_hour, cos_hour) must be sufficiently separated in feature
    space that correct neighbor retrieval is unambiguous with the
    shipped `FEATURE_WEIGHTS`.
- **Retrieval/specification test**: verify the model retrieves correct
  analogs (nearest neighbors match the planted archetype) and the
  trajectory blend reflects the planted pattern. This is a
  specification test — "does the model work as designed when its
  assumptions hold?" — not parameter recovery in the classical sense.
- **Forecast accuracy**: run the model on a new occurrence of a known
  archetype. Verify the forecast matches the archetype's expected
  trajectory.
- **Canonical diagnostic**: write synthetic data to temp export, run
  `tune_model("analog_trajectory", ...)` with a targeted grid for the
  default test path and the existing replay parallelization enabled for
  larger sweeps. Do not pre-mark the full research grid as slow based
  on candidate count alone; measure runtime and mark it only if it
  exceeds the 60-second threshold.
- **Realism extension notes**: more archetypes, gradual archetype
  evolution over time, cross-archetype contamination (states that
  partially match multiple archetypes).

## Phase 5: Cross-model synthesis

- Compile the canonical/internal/DGP-truth comparison table across all
  four models.
- For each model, classify the divergence: **pipeline-structural**
  (canonical ≠ internal even on synthetic data) vs. **hypothesis-fit**
  (canonical ≈ internal on synthetic data, diverge on real data).
- Update the stacked generalization open question in the research hub
  with findings.
- Document implications: which models are candidates for
  internal-objective tuning, and whether the blend architecture is the
  binding constraint.

## Design decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| Model order | Latent Hunger → Survival Hazard → Slot Drift → Analog Trajectory | Simplest DGP to most complex. First two have clean mathematical DGPs; latter two need structural design. Latent Hunger has the most dramatic canonical/internal divergence. |
| Sequential execution | One model at a time | Snowball learnings and iterate on shared infrastructure as patterns emerge. |
| DGP complexity | Minimal with realism notes | Low/no noise for forecast accuracy; moderate noise for parameter recovery. Notes on realism extensions for future work. |
| Test format | pytest with measured fast/slow split | Default tests use targeted grids and existing replay parallelism. Mark `slow` only when measured runtime exceeds 60 seconds. |
| Methodology docs | `feedcast/research/simulation_study/methodology.md` | Persistent cross-cutting research methodology, not test fixture data. |
| Canonical diagnostic | Per-model via temp-export writer | Replay infrastructure requires file-path input. Each model's diagnostic is self-contained; findings inform later phases. |
| Validation targets | Model-specific | Parameter recovery (Latent Hunger), MLE recovery + forecast (Survival Hazard), structural recovery (Slot Drift), retrieval/specification (Analog Trajectory). |

## Dependency

This plan is a prerequisite for the **stacked generalization
investigation** (see `feedcast/research/README.md`, top open question).
The canonical/internal divergence decomposition from Phase 5 directly
informs whether models should be tuned to their own native objectives.
