# Changelog

Tracks hypothesis, method, and conclusion changes for the simulation study. Add newest entries first.

## Compile cross-model synthesis and classify divergence | 2026-04-03

### Problem

Per-model simulation results existed but had not been compared across
models or connected to the stacked generalization question. Each
model's research file documented its own synthetic findings, but no
cross-model comparison table or divergence classification existed.

### Solution

Created `research.md` with per-model findings, a cross-model
comparison table, and divergence classifications. Three models (Latent
Hunger, Survival Hazard, Analog Trajectory) confirmed hypothesis-fit
divergence. Slot Drift's pipeline is sound but full decomposition is
incomplete — the model lacks a scalar internal diagnostic for
real-data comparison. Updated the stacked generalization open question
in the research hub with findings and next investigation.

Export: `exports/export_narababy_silas_20260327.csv`.

## Implement simulation study infrastructure and per-model tests | 2026-04-02

### Problem

Models had not been validated against synthetic data conforming to
their own hypotheses. No way to decompose the observed
internal-canonical divergence into pipeline-structural vs.
hypothesis-fit components.

### Solution

Created `methodology.md` documenting DGP design principles,
validation protocols, and the canonical diagnostic framework.
Implemented shared test infrastructure (`tests/simulation/`: factories,
export writer, assertions, conftest) and model-specific DGP generators
and tests for all four base models. All 18 simulation tests passing.

Export: `exports/export_narababy_silas_20260327.csv`.
