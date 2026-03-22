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
- Primary evaluation metric is exact next-feed timing error.
- Secondary evaluation metric is per-feed volume accuracy.
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

### Daily Shift

Breastfeed-aware starting heuristic. Treats recent days as feed-slot templates, estimates feeds-per-day, and projects the schedule forward as those slots drift day to day.

### Consensus Blend

Breastfeed-aware starting heuristic. Blends whichever base models are available at a cutoff and groups predictions by time proximity rather than raw forecast index.

## Backtesting Rules

Backtesting uses the same export as both history and future truth.

For each model:

1. Take every bottle feed as a possible cutoff.
2. Forecast the next 24 hours from that cutoff using only prior history.
3. Compare the forecast to the actual later bottle feeds in the export.

Metrics:

- first-feed error: absolute timing error for the next predicted bottle
- full-24h timing MAE: order-preserving sequence alignment across the next 24 hours
- volume MAE: volume error on matched forecast/actual feeds

The headliner model is chosen by:

1. recent first-feed MAE
2. overall first-feed MAE
3. volume MAE

This is deliberate. The current actionable forecast matters more than a broad but stale average.

## Reports

Each run writes a new folder:

```text
reports/<run_id>/
  summary.md
  headliner_schedule.png
  model_scores.png
  metrics.json
  models/
    recent_cadence.md
    recent_cadence.png
    trend_hybrid.md
    trend_hybrid.png
    daily_shift.md
    daily_shift.png
    consensus_blend.md
    consensus_blend.png
```

`summary.md` is the top-level artifact.

It includes:

- the headliner forecast
- the model leaderboard
- delta vs the most recent prior run in `reports/`
- links to model-specific pages

`metrics.json` is the machine-readable artifact that future sessions should use to compare runs and inspect backtest output.

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
