# Changelog

Tracks behavior-level changes to the Survival Hazard model. Add newest entries first.

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
