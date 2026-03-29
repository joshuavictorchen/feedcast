# Changelog

Tracks behavior-level changes to the Latent Hunger model. Add newest entries first.

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
