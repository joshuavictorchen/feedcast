# Changelog

Tracks behavior-level changes to the Latent Hunger model. Add newest entries first.

## Raise SATIETY_RATE from 0.05 to 0.55 | 2026-04-10

### Problem

On the `20260410` export, production `SATIETY_RATE=0.05` scored headline
65.8 — a drop from 66.9 on the prior export. The canonical optimum
shifted upward: every rate from 0.3 to 0.8 outperformed 0.05. This
confirmed the open question in `research.md` that the low-rate preference
was export-specific rather than structural.

### Research

Ran a combined 19-candidate sweep (0.02–0.8) on the 20260410 export via
`run_replay.py`. The landscape:

| sr | headline | count | timing |
|----|----------|-------|--------|
| 0.05 (prior) | 65.798 | 96.254 | 45.526 |
| 0.3 | 66.103 | 96.334 | 45.935 |
| 0.5 | 66.265 | 95.281 | 46.727 |
| **0.55** | **66.308** | **95.280** | **46.807** |
| 0.6 | 66.302 | 95.278 | 46.817 |
| 0.8 | 66.339 | 95.232 | 46.932 |

Broad plateau from sr=0.5 to sr=0.8 (all within 0.074 headline points).
A half-life sweep at sr=0.55 (72–240h) showed negligible sensitivity
(0.2 points across the range); RECENCY_HALF_LIFE_HOURS kept at 168.

### Solution

Set `SATIETY_RATE=0.55`. Chosen interior to the plateau for robustness.
Headline +0.51 (65.80→66.31), timing +1.28 (45.53→46.81), count −0.97
(96.25→95.28). All 26 windows scored at 100% availability.

The improvement comes from stronger volume sensitivity: at sr=0.55 the
satiety effect is 0.42 for 1oz and 0.89 for 4oz (a 2.1× ratio with
large absolute effects), versus 0.05 and 0.18 at the prior sr=0.05
(a 3.7× ratio but small absolute effects). Larger absolute gap
differentiation improves timing on the canonical metric.

## Tune SATIETY_RATE from canonical multi-window sweep | 2026-03-31

### Problem

Production `SATIETY_RATE=0.257` was fitted on episode-level walk-forward
`gap1_mae`. Canonical multi-window evaluation (the authoritative metric)
had not been used for parameter selection.

### Solution

Ran 12-candidate canonical sweep via `tune_model()`. Best candidate
`SATIETY_RATE=0.05` improves headline +0.550 (66.3→66.9), driven by
count accuracy (+1.4). All 24 windows scored at 100% availability for
all candidates. The tuning surface is shallow (top 5 span 0.5 points).

The model retains meaningful volume sensitivity at sr=0.05 (satiety
effect scales ~3.7x from 1oz to 4oz), but the lower rate produces more
uniform gap predictions that score better on canonical episode-count
matching. Internal diagnostics (gap1_MAE) prefer higher satiety rates
(~0.6), but the canonical metric is authoritative. See `research.md`
for the full analysis.

## Add canonical multi-window evaluation and tuning | 2026-03-28

### Problem

Research script selected parameters by minimizing internal `gap1_mae`
(walk-forward gap error), a different metric than the canonical
`score_forecast()` used by replay and the tracker. Parameter choices
optimized for single-gap accuracy may not optimize full 24h trajectory
quality (episode count, timing, horizon weighting).

### Solution

Added two canonical sections to research.py:

1. **Canonical evaluation** — calls `score_model("latent_hunger")` with
   production constants. Reports aggregate headline/count/timing scores
   across multi-window evaluation with per-window breakdown.

2. **Canonical parameter tuning** — calls `tune_model()` to sweep
   `SATIETY_RATE` (0.05–0.8, 12 candidates) via multi-window canonical
   scoring. Growth rate is runtime-estimated and not overridable.
   Candidates ranked by availability tier first, then headline score.

Existing internal diagnostics (walk-forward gap1/gap3/fcount MAE,
additive vs multiplicative comparison, circadian analysis) are preserved
as diagnostic tools that explain *why* a parameter set works.

No model behavior change — only the research script is modified.

## Switch to episode-level history and re-tune parameters | 2026-03-27

### Problem

Growth rate estimation used raw consecutive-feed pairs. Cluster-internal
pairs (e.g., 3.0 oz feed → 50-min gap → 1.0 oz top-up) produced
artificially high implied growth rates from short gaps, biasing the
weighted average upward and predicting gaps shorter than the real
inter-episode rhythm. The satiety rate and recency half-life were both
tuned on this contaminated signal.

### Research

Episode-level grid search showed substantial improvements across all
walk-forward metrics: gap1_MAE 0.779 → 0.623 (−20%), gap3_MAE 0.823 →
0.655, fcount_MAE 1.75 → 1.41. Optimal satiety rate shifted from 0.800
(raw) to 0.257 (episode). Replay parameter sweeps confirmed the episode
× half-life interaction: raw data degrades at longer half-lives (73.4 →
64.7) while episode data improves (68.3 → 77.5).

### Solution

Three synergistic changes:

1. **Episode-level history** via `episodes_as_events()` — growth rate
   estimation, sim volume, and current hunger state all use inter-episode
   signals.
2. **SATIETY_RATE 0.386 → 0.257** — re-tuned on episode-level data.
   Lower rate fits the real volume-gap relationship without cluster
   inflation.
3. **RECENCY_HALF_LIFE_HOURS 48 → 168** — with cluster noise removed,
   growth rate estimation benefits from broader averaging. 168h =
   LOOKBACK_DAYS × 24, giving 50% weight at the lookback boundary.

Replay gate (`20260325` export, 03/24→03/25 window):

| Metric | Baseline (raw) | Episode-level | Delta |
|--------|----------------|---------------|-------|
| Headline | 73.351 | 78.471 | +5.120 |
| Count F1 | 94.242 | 100.0 | +5.758 |
| Timing | 57.091 | 61.576 | +4.485 |
| Episodes | 10/9/9 | 9/9/9 | perfect |

## Weight recent history more aggressively in growth-rate fitting | 2026-03-25

### Problem

Latest-24h replay on `exports/export_narababy_silas_20260323.csv` showed the
current `RECENCY_HALF_LIFE_HOURS=72` setting was reacting too slowly to recent
feeding pace changes. Baseline replay scored 70.024, while the best candidate
in the tested sweep reached 73.121.

### Solution

Reduce `RECENCY_HALF_LIFE_HOURS` from `72` to `48` so the growth-rate estimate
leans harder on the most recent events. On the latest 24h replay, that kept the
same predicted feed count but improved timing accuracy enough to raise the
headline score by 3.097 points.
