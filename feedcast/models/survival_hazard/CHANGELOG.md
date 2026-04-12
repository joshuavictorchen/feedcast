# Changelog

Tracks behavior-level changes to the Survival Hazard model. Add newest entries first.

## Soften overnight shape for less regular recent pattern | 2026-04-11

### Problem

The 20260411(1) export (8 new rows vs the earlier 20260411 export)
shifted the canonical replay landscape. On the prior export version,
the 154-candidate sweep found the surface flat around `OVERNIGHT_SHAPE=7.5`
(best improvement: +0.12). With the new data, `OVERNIGHT_SHAPE=4.0-5.0`
scores +1.2-1.6 better. Per-window analysis shows the improvement
concentrates in the 5 most recent, highest-weight windows (April 10-11),
where the baby's overnight feeding was less regular than the 7-day
lookback predicted.

### Research

Ran three sweep rounds on `exports/export_narababy_silas_20260411(1).csv`:

1. Coarse 20-candidate grid (OVERNIGHT 5.0-8.0, DAYTIME 2.0-3.5):
   best at (5.0, 3.0) — headline 66.815 (+1.19).
2. Fine 27-candidate grid (OVERNIGHT 4.0-6.0 × 0.25, DAYTIME 2.75-3.25):
   best at (4.0, 3.25) — headline 67.261 (+1.64).
3. Extension 15-candidate check (OVERNIGHT 3.0-4.0, DAYTIME 3.0-3.5):
   confirmed 4.0 is the interior optimum; scores drop below 3.5.

The optimum sits on a broad plateau from OVERNIGHT 4.0 to 5.0 (spread
0.45). Selected 4.5 (plateau center) to balance the trend signal against
oscillation risk — the shapes went 4.75 → 7.5 just yesterday.

Half-life sweep (48-240h) showed no improvement (+0.09 max), confirming
the regression is a shape issue, not a scale-estimation issue.

| Metric | Previous (`7.5`, `3.0`) | Updated (`4.5`, `3.0`) | Delta |
|---|---|---|---|
| Headline | 65.6 | 66.9 | +1.3 |
| Count | 93.6 | 93.4 | -0.3 |
| Timing | 47.1 | 48.7 | +1.6 |

Availability unchanged at 25/25.

The 154-candidate analysis sweep confirms the new baseline as near-best
(best improvement: +0.29 at DAYTIME=1.25/OVERNIGHT=3.5, which trades
count for timing). Episode-level MLE remains stable (6.04/3.63).

### Solution

Updated production shape:

- `OVERNIGHT_SHAPE`: `7.5 → 4.5`
- `DAYTIME_SHAPE`: unchanged at `3.0`

The baby's overnight regularity decreased in the latest data, pulling
the canonical optimum from the sharper shapes set yesterday back toward
the MLE range. The change reflects the emerging pattern rather than
chasing the exact replay peak.

## Re-tune shapes for regularizing feeding pattern | 2026-04-10

### Problem

The 20260410 export showed a significant regression on the production
constants (`OVERNIGHT_SHAPE=4.75`, `DAYTIME_SHAPE=1.75`): canonical
headline dropped from 72.7 to 65.7 across 26 windows. The model
overpredicted episode count (9 predicted vs 7 actual in the latest
retrospective) and timing degraded sharply (47.3 vs previous 56.6).
The baby's feeding patterns have shifted toward fewer, more regularly
spaced feeds — consistent with growth — and the soft shapes from the
prior export no longer match.

### Research

Ran three canonical sweep rounds on `exports/export_narababy_silas_20260410.csv`:

1. Initial 99-candidate grid (OVERNIGHT 3.5–7.0, DAYTIME 1.25–3.0):
   best at boundary corner (7.0, 3.0) — headline 73.1.
2. Extended 36-candidate grid (OVERNIGHT 6.5–9.0, DAYTIME 2.5–5.0):
   best at (7.5, 3.0) — headline 73.3. DAYTIME interior (2.5 and 3.5
   both worse). OVERNIGHT peaks at 7.5, drops at 8.5+ and below 6.5.
3. Fine-grained 15-candidate confirmation (OVERNIGHT 7.0–8.0 × 0.25,
   DAYTIME 2.75–3.25 × 0.25): flat plateau from 7.0 to 8.0 at
   DAYTIME=3.0 (spread 0.15 points). Selected 7.5 as plateau center.

The 154-candidate analysis sweep confirms baseline=best (no further
improvement found).

| Metric | Previous (`4.75`, `1.75`) | Updated (`7.5`, `3.0`) | Delta |
|---|---|---|---|
| Headline | 65.7 | 73.3 | +7.6 |
| Count | 92.3 | 97.5 | +5.2 |
| Timing | 47.3 | 55.8 | +8.5 |

Availability unchanged at 26/26.

The episode-level MLE on this export is overnight 6.0, daytime 3.54.
The canonical/MLE gap has narrowed dramatically from the prior export
(where canonical was 4.75/1.75 vs MLE 7.2/3.4) — both now agree the
baby's feeding rhythm is more regular than the prior soft shapes
reflected.

### Solution

Updated production shapes:

- `OVERNIGHT_SHAPE`: `4.75 → 7.5`
- `DAYTIME_SHAPE`: `1.75 → 3.0`

Both shapes moved sharply toward the episode-level MLE, reflecting a
baby whose feeding patterns have regularized with growth. The day-part
split remains intact — overnight is still more regular than daytime.

## Re-tune shape parameters with wider canonical sweep | 2026-03-31

### Problem

The initial canonical tuning grid was too narrow for the current
export. A wider replay run put the best candidate at the lowest-tested
corner of the original grid (`OVERNIGHT_SHAPE=4.0`, `DAYTIME_SHAPE=2.0`),
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
