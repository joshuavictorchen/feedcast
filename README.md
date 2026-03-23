# Silas Feeding Forecast

This repo forecasts the next 24 hours of bottle feeds from Nara Baby CSV
exports and turns the result into a single Markdown report.

The scope is intentionally narrow:

- predict bottle-feed timing as accurately as possible
- include estimated bottle volume because it is operationally useful
- keep the pipeline simple enough to iterate on quickly as new exports arrive

## Workflow

1. Drop the latest full-history Nara export into `exports/`.
2. Run `analyze.py`.
3. Read the new report in `report/summary.md`.

The export files are treated as raw input, not durable project history. The
tracked history lives in `tracker.json`, the latest tracked output lives in
`report/`, and older rendered reports are archived into `.report-archive/`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

LLM forecasts require local `claude` and `codex` CLIs with working auth.

## Usage

Default run:

```bash
.venv/bin/python analyze.py
```

Use a specific export:

```bash
.venv/bin/python analyze.py --export-path exports/export_narababy_silas_YYYYMMDD.csv
```

Skip the LLM agents and run the scripted forecasters only:

```bash
.venv/bin/python analyze.py --skip-agents
```

`--skip-agents` is primarily a smoke-test/debugging path. The intended default
run is the full pipeline with both agents enabled.

## What The Pipeline Does

For each run, the pipeline:

1. selects the latest matching export, unless `--export-path` is provided
2. parses bottle feeds and breastfeeding events from the CSV
3. treats the latest recorded feeding activity in that export as the forecast start
4. runs three scripted models plus a scripted consensus blend
5. runs two agent forecasts, each in its own persistent workspace
6. backtests the scripted forecasts within the current export
7. compares the previous run's predictions to newly observed actual feeds
8. renders a single Markdown report and updates `tracker.json`

The forecast horizon is always the next 24 hours.

## Forecast Sources

### Scripted models

- `Recent Cadence`: bottle-only recency-weighted interval baseline
- `Phase Nowcast Hybrid`: breastfeed-aware recursive timing model with a local first-gap nowcast
- `Gap-Conditional`: breastfeed-aware event-level regression rolled forward autoregressively
- `Consensus Blend`: median-timestamp ensemble across the three scripted models

These models are documented in the report with enough methodological detail to
reproduce them from the text alone.

### Agent forecasts

- `Claude Forecast`
- `Codex Forecast`

Both agents share the same prompt and runner. Each gets a persistent workspace
under `agents/`, and those workspaces are tracked in git so their notes,
scripts, and methodology can evolve visibly over time. Each agent can read the
full repo and must write two files on every run:

- `forecast.json`: predicted feeds in a fixed JSON schema
- `methodology.md`: what the agent actually did on that run

The runner does not preprocess the data for the agents beyond telling them:

- which export CSV to use
- which workspace belongs to them

Everything else is up to the agent.

## Evaluation

The repo uses two kinds of evaluation.

- Current-export backtests: scripted models and the consensus blend are replayed
  across historical cutoffs within the current export.
- Prior-run retrospective: the previous run's saved predictions are compared to
  new actual feeds that appear in the next export.

The featured forecast follows a simple rule:

- prefer `Consensus Blend` if it is available
- otherwise fall back to the best scripted model by current-export backtest rank
- never auto-feature an agent forecast

For scripted backtests, the main ranking signal is next-feed timing, not
volume. Coverage matters too: a model that only works on easy cutoffs should
not outrank a model that is slightly less accurate but reliably available.

## Breastfeeding Heuristic

Breastfeeding is not the prediction target. Bottle-feed timing is.

Two scripted models use a lightweight breastfeeding heuristic as an input:
estimated breastfeeding intake is merged into the next bottle if that bottle
starts soon after the breastfeeding session. This changes model features and
projected bottle volume, but timing is still scored against logged bottle-feed
start times.

The current starting assumption is:

- `30 minutes breastfeeding ~= 0.5 oz`
- merge breastfeeding into the next bottle when it starts within `45 minutes`

This is a heuristic, not measured intake, and it is expected to evolve if the
data suggest a better interpretation.

## Modeling Principles

- Optimize for bottle-feed timing first. Volume is useful, but secondary.
- Prefer simple, interpretable approaches until more complexity clearly earns its keep.
- Treat limited data with caution. Directionally sound models matter more than brittle tuning.
- Let new exports drive iteration. The goal is to improve the next 24-hour forecast, not to preserve every past experiment.

## Report

The primary artifact is `report/summary.md`.

It includes:

- the featured forecast at the top
- a spaghetti plot of all available trajectories
- one section per scripted model and agent forecast
- current-export backtest results for the scripted lineup
- retrospective comparison against the previous run, when new actuals exist
- the exact export, dataset fingerprint, and git commit used for the run

## Repo Layout

- `analyze.py`: slim CLI entrypoint
- `data.py`: CSV parsing, domain types, dataset fingerprinting
- `models/`: scripted forecasters and consensus blend
- `agents/`: shared agent runner, prompt, and persistent agent workspaces
- `backtest.py`: temporal backtesting for scripted forecasts
- `tracker.py`: run manifests and prior-run retrospectives
- `report.py`: Markdown rendering and plots
- `templates/summary.md.j2`: report template
- `tracker.json`: tracked run history
- `report/`: latest tracked report

## Extending The System

To add or revise a scripted model:

1. implement it in `models/`
2. add it to the explicit `MODELS` list in `models/__init__.py`
3. make sure its methodology text is report-ready
4. rerun `analyze.py`

To iterate on agent behavior:

1. adjust the shared prompt in `agents/prompt/prompt.md` if the contract or framing should change
2. let each agent evolve its own workspace strategy under `agents/claude/` or `agents/codex/`
3. rerun `analyze.py`

## Design Rules

- The global data floor is March 15, 2026.
- Feed timing is the success metric. Volume supports forecasting and bottle prep but is not used to rank models.
- Raw exports are full-history snapshots. New drops may replace earlier ones operationally without being committed.
- Simplicity wins unless additional complexity clearly improves the forecast.

## Iterating

This repo is meant to evolve as more exports arrive.

- Scripted models can be adjusted or replaced as evidence improves.
- Agents can develop their own repeatable strategies in their workspaces.
- `tracker.json` and the retrospective section make it possible to inspect whether changes are helping.

The goal is not to preserve every experiment. The goal is to maintain a clean,
credible forecasting tool that gets better over time.
