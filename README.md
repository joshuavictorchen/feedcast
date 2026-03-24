# Feedcast

Feedcast predicts the next 24 hours of bottle feeds for a newborn from
Nara Baby app exports, using an ensemble of scripted forecasting models
and LLM agents. Feed timing is the primary target. Each run scores the
prior run's predictions against what actually happened. No backtesting.

*Built by a tired dad with Claude and Codex between bottle feedings.
Coordinated via [claodex](https://github.com/joshuavictorchen/claodex).
My wife mentioned missing the sense of daily structure we used to have
before Silas was born. Predicting feedings felt like a practical place
to start. It also gave me a reason to experiment with agentic engineering.*

## Latest Forecast

![Featured Forecast](report/schedule.png)

*Latest committed featured forecast.
Full report: [report/report.md](report/report.md).*

Reports are committed as markdown in the repo. The latest forecast is
always right here.

## The Forecasting Challenge

The only input is feeding history: timestamps and volumes from a
baby-tracking app. Sleep, growth spurts, and developmental leaps all
affect when a baby eats, but none of that is in the data. The baby is
growing fast, so patterns shift week to week.

That said, there are real patterns. Larger feeds tend to be followed by
longer gaps, and the daily feed count stays fairly stable even as timing
shifts. The models try to find that structure in a small, shifting
dataset.

## Forecast Sources

**Scripted models** run deterministically from the event history:

| Model | Approach |
| ----- | -------- |
| Slot Drift | Daily template with per-slot drift tracking and Hungarian matching |
| Analog Trajectory | Instance-based ML: finds similar historical states and averages their futures |
| Latent Hunger State | Mechanistic hidden state: hunger rises over time, feeds reset it proportional to volume |
| Recent Cadence | Recency-weighted interval between full feeds, rolled forward at constant gap |
| Phase Nowcast Hybrid | Phase-locked oscillator backbone with local regression nowcast for the first gap |
| Gap-Conditional | Weighted linear regression on event state, rolled forward autoregressively |
| Consensus Blend | Median-timestamp ensemble across the scripted models |

**LLM agents** get the export CSV, a shared prompt, and a persistent workspace:

| Agent | Model |
| ----- | ----- |
| Claude Forecast | claude-opus-4-6 (effort: max) |
| Codex Forecast | gpt-5.4 (reasoning: xhigh) |

Each agent writes `forecast.json` and `methodology.md` to its workspace.
Stale outputs are deleted before each invocation so a failed run cannot
reuse prior results. Agents are excluded from the consensus blend and are
never auto-featured.

## Pipeline

| Step | Description |
| ---- | ----------- |
| Parse Activities | Filter feeding events from the raw CSV export |
| Build Events | Create bottle-centered events with optional breastfeed volume merging (per model) |
| Run Models | Execute scripted models independently |
| Consensus Blend | Median-timestamp ensemble across scripted models |
| Select Featured | Choose the consensus blend, or fall back to a static tiebreaker |
| Run Agents | Claude and Codex produce independent forecasts (optional) |
| Retrospective | Score the prior run's predictions against newly observed actuals |
| Render Report | Generate the markdown report, charts, and diagnostics |
| Save Tracker | Persist predictions and retrospective to `tracker.json` |

## Evaluation

There is no historical backtesting. The only accuracy signal is **prospective
performance**: each run compares the prior run's predictions to the actual
feeds observed in the new export. Over time, these results accumulate in
`tracker.json` and are aggregated into a historical accuracy table in the
report.

The featured forecast defaults to the consensus blend. If it's unavailable,
the pipeline falls back to a static tiebreaker. Reruns against the same
dataset replace the latest tracker entry instead of appending another copy.

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

1. Drop the latest Nara export into `exports/`.
2. Run `.venv/bin/python scripts/run_forecast.py`.
3. Read the report at `report/report.md`.

```bash
# Full pipeline (scripted models + LLM agents):
.venv/bin/python scripts/run_forecast.py

# Specific export:
.venv/bin/python scripts/run_forecast.py --export-path exports/export_narababy_silas_YYYYMMDD.csv

# Scripted models only (skip LLM agents):
.venv/bin/python scripts/run_forecast.py --skip-agents
```

LLM agent forecasts run `claude` and `codex` as local CLI tools with
working auth (cheaper and easier than invoking API calls).
Use `--skip-agents` if they're unavailable.

Each run updates these artifacts:

- `report/report.md` — the human-readable forecast report
- `report/schedule.png` — the featured schedule chart
- `report/spaghetti.png` — the all-model trajectory chart
- `report/diagnostics.yaml` — structured model diagnostics
- `tracker.json` — stored predictions and retrospective history

## Repo Layout

```text
scripts/
  run_forecast.py              CLI entrypoint
feedcast/
  pipeline.py                  End-to-end orchestration
  data.py                      CSV parsing, domain types, fingerprinting
  models/                      Scripted forecasters and consensus blend
    notes.md                   Brainstorm notes, observations, and model ideas
    shared.py                  Shared utilities used across models
    slot_drift/                Daily template with per-slot drift
      model.py                 Model implementation
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
    analog_trajectory/         Instance-based ML from similar states
      model.py                 Model implementation
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
    latent_hunger/             Mechanistic hidden hunger state
      model.py                 Model implementation
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
  agents.py                    Agent runner (points to repo-level agents/)
  tracker.py                   Run persistence and retrospectives
  report.py                    Markdown rendering and atomic report swap
  plots.py                     Schedule and trajectory chart generation
  templates/
    report.md.j2               Jinja2 report template
agents/
  run.sh                       Shell dispatcher for Claude/Codex CLIs
  prompt/prompt.md             Shared agent prompt
  claude/                      Claude persistent workspace
  codex/                       Codex persistent workspace
exports/                       Raw Nara CSV drops (untracked)
report/                        Latest report (tracked, committed)
tracker.json                   Run history with predictions and retrospectives
```

## Working with Models

**Start here:** Read `feedcast/models/notes.md` first. It contains domain
observations, the working theory behind the model lineup, cross-cutting
design considerations, and open questions. It is the orientation document
for anyone working on models.

**Model directory convention:** Each model lives in its own subdirectory
under `feedcast/models/` with a standard set of files:

| File | Purpose |
| ---- | ------- |
| `model.py` | Implementation. Exports `MODEL_NAME`, `MODEL_SLUG`, `MODEL_METHODOLOGY`, and a forecast function with signature `(history, cutoff, horizon_hours) -> Forecast`. Tuning constants live here, not in `shared.py`. |
| `methodology.md` | Report-facing text. Content before the first `##` heading is loaded by `load_methodology()` and rendered into the forecast report. |
| `design.md` | Design decisions and rationale. Documents why the model works the way it does. |
| `research.py` | Repeatable data analysis. Run with `.venv/bin/python -m feedcast.models.<name>.research`. Uses the same export selection, data parsing, and constants as the model so its output matches what the model sees. |
| `research_results.txt` | Saved output from the research script. Committed for reproducibility. |

**Add a model:** Create the subdirectory with the files above, then add a
`ModelSpec` entry to `feedcast/models/__init__.py`. See `slot_drift/` or
`analog_trajectory/` as reference implementations.

**Remove a model:** Delete its `ModelSpec` from the `MODELS` list. Optionally
delete the directory.

**Tune parameters:** Keep model-specific constants in the model file that
uses them. Reserve `feedcast/models/shared.py` for reusable utilities that
are not model concepts.

**Change the featured default:** Set `FEATURED_DEFAULT` in
`feedcast/models/__init__.py` to any available model slug.

## Working with Agents

**Edit the shared prompt:** Modify `agents/prompt/prompt.md`. Both agents
receive the same prompt, prepended with the resolved export path and workspace
path.

**Iterate on one agent's strategy:** Each agent's workspace persists across
runs. Agents can keep durable strategy notes in separate workspace files.

**Add or swap an agent:** Edit the `AGENTS` list in `feedcast/agents.py` and
add a corresponding case to `agents/run.sh`.

## Design Decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| Scripted models | Distinct conceptual frames | Template, instance-based ML, mechanistic, and gap-regression approaches for ensemble diversity |
| Ensemble | Consensus uses scripted models only | Agents excluded until retrospectives demonstrate consistent value |
| Featured forecast | Consensus > static tiebreaker | Simple default; manually overridable via `FEATURED_DEFAULT` |
| Agent failure | Fail fast | Use `--skip-agents` to work around; no silent fallback |
| Model registration | Explicit `MODELS` list | No auto-discovery; you see what runs by reading one list |
| Report tracking | `report/` and `tracker.json` committed | One workspace; latest report always accessible; tracker keeps the latest run per dataset rather than every retry |
| Exports | Untracked raw drops | Reproducibility via `tracker.json` dataset fingerprints |
| Report write | Atomic swap with rollback | If rendering fails, the prior report is preserved |

## Principles

- Feed timing is the success metric. Volume is secondary.
- Prefer simple approaches until complexity clearly earns its keep.
- Let new exports drive iteration. The goal is the next 24 hours.
- Simplicity wins unless the forecast improves.
