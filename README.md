# Silas Feeding Forecasts

This repo forecasts Silas's next 24 hours of bottle feeds from Nara Baby exports and backtests multiple models against the later reality already present in the same export.

The project is intentionally small. The priority is modeling and report quality, not framework work.

## Why This Exists

The main user-facing goal is an actionable forecast: when the next bottle feed is likely to happen, and roughly how large it will be.

Model competition is useful, but secondary. The headliner report exists to surface the best current forecast, not just to maintain a scoreboard.

## Current Workflow

Run:

```bash
.venv/bin/python analyze.py
```

Optional flags:

```bash
.venv/bin/python analyze.py --export-path exports/export_narababy_silas_20260322.csv
.venv/bin/python analyze.py --analysis-time 2026-03-22T13:30:00
```

The script:

1. Selects the newest export in `exports/` by filename date.
2. Parses bottle feeds and breastfeeds from that export.
3. Uses the latest relevant feed activity from that export as the default forecast cutoff.
4. Clamps all usable history to `2026-03-15`.
5. Backtests every model at every bottle-feed cutoff available in the export.
6. Picks a headliner model.
7. Writes a new report set under `reports/<run_id>/`.

## Repo Shape

- `analyze.py`: small entrypoint and CLI
- `forecasting.py`: data loading, model definitions, forecasting, backtesting, headliner selection
- `reporting.py`: PNGs, Markdown reports, metrics JSON, delta-vs-prior-run summaries
- `exports/`: raw Nara Baby exports
- `reports/`: generated report runs

## Hard Invariants

These are not model choices. They are project rules.

- Only the newest export in `exports/` is used for a default run.
- The export is treated as a full-history snapshot.
- No data earlier than `2026-03-15` is considered, period.
- Forecast timing targets are the logged bottle-feed start times.
- The sole evaluation metric is feed timing accuracy. Volume is used by models as a feature and reported for bottle prep, but not used in model ranking.
- The repo should stay simple. Add models and reports, not infrastructure.

## Breastfeeding Heuristic

Breastfeeding is treated as a heuristic input, not measured truth.

Current starting assumption:

- `30 minutes breastfeeding ~= 0.5 oz`
- if a model opts in, estimated breastfeeding intake is added to the following bottle when that bottle starts within `45 minutes` after the breastfeed ends
- this changes model features and projected volume assumptions, but not the timing target

Important:

- this is only a starting point
- future models may ignore it, tighten it, loosen it, or interpret it differently
- report readers should not confuse this estimate with observed intake

## Current Models

### Recent Cadence

Bottle-only baseline. Uses recency-weighted recent full-feed intervals and a time-of-day volume profile.

### Trend Hybrid

Bottle-only baseline. Weighted linear trend on recent intervals plus a time-of-day volume profile. This is the closest descendant of the original one-off script.

### Phase-Locked Oscillator

Breastfeed-aware starting heuristic. A lightweight recursive state-space timing model that lets a larger-than-usual feed push the next forecast later instead of snapping straight back to the rolling mean.

### Phase Nowcast Hybrid

Breastfeed-aware starting heuristic. Uses the phase model as the full-horizon backbone, but blends the first next-feed gap with a local event-state nowcast when both models already agree within a narrow window. This is a deliberate "trust but verify" model for the user's primary metric: next-feed timing.

### Template Match

Breastfeed-aware starting heuristic. Finds the closest historical analog window using recent gaps, volumes, and times of day, then uses what happened next as the projection template.

### Daily Shift

Breastfeed-aware starting heuristic. Builds a recent daily gap template, aligns today's observed cadence to that template, and explicitly carries the schedule across the overnight gap into tomorrow.

### Gap-Conditional

Breastfeed-aware starting heuristic. Weighted event-level regression for the next gap using raw last-feed volume, the previous gap, the recent rolling gap, and cyclical hour-of-day encoding. This version is trained on recent events directly instead of training on full feeds and patching snacks only at inference time.

### Survival (Weibull)

Bottle-only. Fits a Weibull time-to-next-feed distribution with day/night and feed-volume adjustments, then uses the distribution mode as the point forecast.

### Gradient Boosted

Exploratory canary model. A conservative gradient-boosted regressor over per-feed features. Useful as a check on whether extra model capacity is starting to pay off, but not trusted over the simpler models unless it wins on both accuracy and cutoff coverage.

### Satiety Decay

Breastfeed-aware starting heuristic. A physiological model that treats hunger as accumulating linearly over time, with each feed resetting hunger proportional to its volume. Naturally handles snacks (partial reset → shorter gap) and large feeds (full reset → longer gap).

### Consensus Blend

Breastfeed-aware starting heuristic. Blends the robust component models available at a cutoff and groups predictions by time proximity rather than raw forecast index. The current blend intentionally excludes the higher-variance gradient-boosted canary.

## Backtesting Rules

Backtesting uses the same export as both history and future truth.

For each model:

1. Take every bottle feed as a possible cutoff.
2. Forecast the next 24 hours from that cutoff using only prior history.
3. Compare the forecast to the actual later bottle feeds in the export.

Metrics (timing only — volume is not used in model ranking):

- first-feed error: absolute timing error for the next predicted bottle
- full-24h timing MAE: order-preserving sequence alignment across the next 24 hours
- cutoff coverage: how often a model can actually produce a forecast across all eligible cutoffs

The headliner model is chosen by:

1. availability-adjusted recent first-feed MAE
2. full-24h timing MAE
3. overall first-feed MAE

The availability adjustment formula: `adjusted = recent_first_feed_MAE + 40 × max(0, 0.75 − coverage) / 0.75`. Models with ≥75% coverage pay no penalty; models below 75% are penalized proportionally.

This is deliberate. The current actionable forecast matters more than a broad but stale average, but low-coverage models are penalized so they do not win by only working on easy cutoffs.

## Modeling Principles

The data are still limited. That means model direction matters more than squeezing a few minutes out of the current export through brittle tuning.

Current principles:

- prefer interpretable models before high-variance learners
- treat volume as a first-class timing signal
- treat snacks/top-offs carefully, but do not assume one universal heuristic is correct; event-level models currently work better with raw event state, while satiety-style models may aggregate recent clusters with `effective_timing_volume()`
- report cutoff coverage alongside MAE so partial-availability models do not look stronger than they are
- keep flexible ML models as exploratory or "canary" models until they beat the simpler baselines on both accuracy and availability

## Reports

Each run writes a new folder:

```text
reports/<run_id>/
  summary.md              # journal-style report (abstract, methods, results, discussion)
  spaghetti_hero.png      # hero figure: all model trajectories, headliner emphasized
  spaghetti_all.png       # comparison: all models on separate rows
  spaghetti_top5.png      # comparison: top 5 models on separate rows
  headliner_schedule.png  # Apple-style schedule view (days × time-of-day)
  model_scores.png        # backtest comparison bar chart (timing only)
  metrics.json            # machine-readable metrics for run-to-run comparison
  models/
    <model_slug>.md       # per-model report with algorithm, diagnostics, backtest
    <model_slug>.png      # per-model schedule plot
```

`summary.md` is the top-level artifact, structured as:

- **Abstract**: one-paragraph summary with headliner and key forecast
- **Forecast**: next-24h table with times and volumes
- **Model Comparison**: spaghetti plots + timing-only leaderboard
- **Methods**: data description, backtesting protocol, and full algorithmic descriptions of all models (sufficient to reimplement from text alone)
- **Results**: headliner selection rationale, model agreement, key findings
- **Discussion**: limitations and future directions
- **Appendix**: schedule view, individual model page links, delta vs prior run

`metrics.json` is the machine-readable artifact that future sessions should use to compare runs and inspect backtest output.

**Headliner selection** ranks models by: (1) availability-adjusted recent first-feed MAE, (2) full-24h timing MAE, (3) overall first-feed MAE. Volume accuracy is not used in model ranking.

## How To Add A Model

Keep it simple:

1. Add a new forecast function in `forecasting.py`.
2. Register it in `build_model_definitions()`.
3. Decide whether it is bottle-only or whether it uses the current breastfeeding heuristic.
4. Re-run `analyze.py`.

The harness will automatically:

- generate a current forecast
- backtest the new model
- include it in the leaderboard
- create a model page for it

## Notes For Future Claodex Sessions

If a future session is asked to "run the models" or "add a new model", the expected path is:

1. inspect the newest export in `exports/`
2. preserve the `2026-03-15` floor unless the user explicitly changes it
3. keep exact next-feed timing as the primary success metric
4. prefer changes that improve forecast quality, not project machinery
5. update `README.md` if assumptions or evaluation rules change

If a model assumption changes in a meaningful way, document it here and in the model notes so later sessions do not silently compare different definitions of "feed."
