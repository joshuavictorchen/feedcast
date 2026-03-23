# Silas Feeding Forecast

Predicts the next 24 hours of bottle feeds from Nara Baby CSV exports and
renders the result as a Markdown report.

The scope is intentionally narrow:

- predict bottle-feed timing as accurately as possible
- include estimated bottle volume because it is operationally useful
- keep the pipeline simple enough to iterate quickly as new data arrives

## Workflow

1. Drop the latest full-history Nara export into `exports/`.
2. Run `python analyze.py`.
3. Read the report at `report/summary.md`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

LLM forecasts require local `claude` and `codex` CLIs with working auth.

## Usage

```bash
# Full pipeline (scripted models + LLM agents):
.venv/bin/python analyze.py

# Specific export:
.venv/bin/python analyze.py --export-path exports/export_narababy_silas_YYYYMMDD.csv

# Scripted models only (skip LLM agents):
.venv/bin/python analyze.py --skip-agents
```

## Pipeline

Each run:

1. Selects the latest export (or uses `--export-path`)
2. Parses bottle and breastfeeding events from the CSV
3. Runs three scripted models plus a consensus blend
4. Runs Claude and Codex agent forecasts in persistent workspaces
5. Backtests scripted models against historical cutoffs in the current export
6. Compares the previous run's predictions to newly observed actuals
7. Renders a Markdown report with spaghetti plot and updates `tracker.json`

## Forecast Sources

**Scripted models:**

| Model | Approach |
|-------|----------|
| Recent Cadence | Bottle-only recency-weighted interval baseline |
| Phase Nowcast Hybrid | Recursive timing model with local first-gap nowcast |
| Gap-Conditional | Event-level regression rolled forward autoregressively |
| Consensus Blend | Median-timestamp ensemble across the three scripted models |

Methodologies are documented in the report with enough detail to reproduce
from text alone.

**LLM agents:**

| Agent | Model |
|-------|-------|
| Claude Forecast | claude-opus-4-6 (effort: max) |
| Codex Forecast | gpt-5.4 (reasoning: xhigh) |

Both share one prompt and runner. Each gets a persistent, git-tracked
workspace under `agents/`. On every run, each agent must write
`forecast.json` (predictions) and `methodology.md` (what it did).

## Evaluation

- **Current-export backtests**: scripted models replayed across historical
  cutoffs within the current export.
- **Prior-run retrospective**: previous predictions compared to actual feeds
  in the next export.

The featured forecast prefers the consensus blend, falls back to the best
scripted model by backtest rank, and never auto-features an agent.

## Repo Layout

```
analyze.py              CLI entrypoint
data.py                 CSV parsing, domain types, fingerprinting
models/                 Scripted forecasters and consensus blend
agents/                 Shared prompt, runner, and agent workspaces
backtest.py             Temporal backtesting
tracker.py              Run manifests and retrospectives
report.py               Markdown rendering and plots
templates/              Jinja2 report template
tracker.json            Run history
report/                 Latest report (tracked)
ARCHITECTURE.md         Design decisions and invariants
```

## Extending

**Add a scripted model:** implement in `models/`, add to the `MODELS` list in
`models/__init__.py`, include a report-ready methodology string, rerun.

**Iterate on agents:** edit the shared prompt in `agents/prompt/prompt.md`,
or let each agent evolve its own workspace strategy. Rerun.

## Principles

- Feed timing is the success metric. Volume is secondary.
- Prefer simple approaches until complexity clearly earns its keep.
- Let new exports drive iteration. The goal is the next 24 hours, not
  preserving past experiments.
- Simplicity wins unless the forecast improves.
