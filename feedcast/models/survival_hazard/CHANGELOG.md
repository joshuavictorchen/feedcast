# Changelog

Tracks behavior-level changes to the Survival Hazard model. Add newest entries first.

## Re-tune shape parameters with wider canonical sweep | 2026-03-31

### Problem

The initial canonical tuning grid from Phase 3 was too narrow for the
current export. A fresh Phase 4.3 replay run put the best candidate at
the lowest-tested corner (`OVERNIGHT_SHAPE=4.0`, `DAYTIME_SHAPE=2.0`),
which meant the evidence did not support stopping there. The production
constants (`6.54`, `3.04`) were materially behind the canonical replay
objective on the current dataset.

### Research

Re-ran `research.py` on `exports/export_narababy_silas_20260327.csv`
with `parallel=True` enabled for canonical score/tune calls. Expanded
the canonical sweep to a mixed-resolution 154-candidate grid:

- `OVERNIGHT_SHAPE`: `3.0, 3.5, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0`
- `DAYTIME_SHAPE`: `1.0, 1.25, 1.5, 1.625, 1.75, 1.875, 2.0, 2.5, 3.0, 3.5, 4.0`

Best canonical result:

- Baseline (`6.54`, `3.04`): headline `65.672`, count `92.810`, timing `47.417`
- Winner (`4.75`, `1.75`): headline `72.653`, count `94.347`, timing `56.572`
- Delta: `+6.981` headline, `+1.537` count, `+9.155` timing
- Availability: unchanged at `24/24`

The episode-level MLE fit remains much higher (`7.2296`, `3.4225` on
the current export), so the direct gap-distribution fit and the
canonical 24h forecast objective still diverge for this model.

### Solution

Updated production shapes:

- `OVERNIGHT_SHAPE`: `6.54 → 4.75`
- `DAYTIME_SHAPE`: `3.04 → 1.75`

The day-part split stays intact — overnight remains more regular than
daytime — but both regimes are softened because canonical replay favors
better 24-hour trajectory matching over the sharper episode-level MLE
fit.

## Add canonical multi-window evaluation and tuning | 2026-03-28

### Problem

Research script selected parameters by minimizing internal `gap1_mae`
(walk-forward gap error), a different metric than the canonical
`score_forecast()` used by replay and the tracker. Shape parameters
optimized for single-gap accuracy may not optimize full 24h trajectory
quality (episode count, timing, horizon weighting).

### Solution

Added two canonical sections to research.py:

1. **Canonical evaluation** — calls `score_model("survival_hazard")` with
   production constants. Reports aggregate headline/count/timing scores
   across multi-window evaluation with per-window breakdown.

2. **Canonical parameter tuning** — calls `tune_model()` to jointly sweep
   `OVERNIGHT_SHAPE` (4.0–8.0, 8 values) and `DAYTIME_SHAPE` (2.0–4.0,
   5 values) = 40 candidates via multi-window canonical scoring. Scale is
   runtime-estimated and not overridable. Candidates ranked by availability
   tier first, then headline score.

Existing internal diagnostics (walk-forward gap1/gap3/fcount MAE, Weibull
fits, discrete hazard comparison, day-part analysis, volume covariate
tests) are preserved as diagnostic tools.

No model behavior change — only the research script is modified.

## Episode-level history and re-tuned parameters | 2026-03-27

### Problem

Cluster-internal gaps (50–70 min top-ups) contaminated the Weibull gap
distribution. The raw dataset included 17 cluster-internal feeds that
biased shape estimates downward and scale estimates toward shorter gaps.
The overnight shape was particularly affected: 3.92 on raw data vs 6.54
on episode-level data. The half-life of 72h was too short for the now-
cleaner episode-level gaps.

### Research

Updated `research.py` to add episode-level analysis (Section 9):
- Fixed the production/research input mismatch (was breastfeed-merged,
  now bottle-only matching production).
- 97 raw events collapse to 80 episode events.
- Episode-level Weibull fits show higher shapes because cluster-
  internal short gaps no longer depress regularity. Research-best
  under 168h weighting: overnight 6.74, daytime 3.20.
- Walk-forward gap1_MAE by half-life: 48h=0.686, 72h=0.663,
  120h=0.644, **168h=0.636** (best). Same Latent Hunger pattern:
  broader averaging works with clean data.
- Volume covariate: LR test significant on episode data (LR=6.65,
  beta=0.091), but the tested scalar AFT overlay degrades predictions
  at every positive beta in day-part walk-forward. Not shipped.

### Solution

Switched to episode-level history via `episodes_as_events()` at
function entry. All model computation (scale estimation, conditional
survival, simulation volume) operates on episode-level data.

Re-tuned parameters:
- `OVERNIGHT_SHAPE`: 7.31 → 6.54 (episode-level fit from initial
  research run under 72h weighting; research-best under 168h is
  6.74, but 6.54 scores better in replay: 81.458 vs 80.584)
- `DAYTIME_SHAPE`: 2.33 → 3.04 (same provenance; research-best
  under 168h is 3.20)
- `RECENCY_HALF_LIFE_HOURS`: 72 → 168 (walk-forward best)

Diagnostics key renamed: `total_fit_gaps` → `total_fit_episode_gaps`.

Replay gate (20260325 export, 03/24→03/25 window):

| Metric | Baseline (raw) | Episode-level | Delta |
|--------|----------------|---------------|-------|
| Headline | 80.029 | 81.458 | +1.429 |
| Count F1 | 95.41 | 100.0 | +4.59 |
| Timing | 67.128 | 66.354 | -0.774 |
| Episodes | 10/9/9 | 9/9/9 | perfect |
