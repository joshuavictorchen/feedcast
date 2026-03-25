# Feedcast

Feedcast predicts the next 24 hours of bottle feeds for a newborn from
Nara Baby app exports, using an ensemble of scripted forecasting models
and LLM agents. Feed timing is the primary target. Each run scores the
prior run's predictions against what actually happened.

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

That said, there are patterns worth testing. Current cross-cutting
research suggests larger feeds are often followed by longer gaps, but
the effect is modest and should be treated as one signal among several.
The models try to find whatever structure the data actually supports in
a small, shifting dataset.

## Forecast Sources

**Scripted models** run deterministically from the event history:

| Model | Approach |
| ----- | -------- |
| Slot Drift | Daily template with per-slot drift tracking and Hungarian matching |
| Analog Trajectory | Instance-based ML: finds similar historical states and averages their futures |
| Latent Hunger State | Mechanistic hidden state: hunger rises over time, feeds reset it proportional to volume |
| Survival Hazard | Day-part Weibull hazard: feeding probability increases with elapsed time |
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

When a new export arrives, Feedcast scores the previous run's predicted
bottle feeds against the bottle feeds now visible in that export. Those
retrospective results are stored in `tracker.json` and aggregated into a
historical accuracy table in the report.

The headline score is the geometric mean of a weighted count F1 (did you
predict the right number of feeds?) and a weighted timing score (how close
were the timestamps?). Both components weight earlier feeds more heavily.
Partial horizons are scored on the observed window only and reported with
explicit coverage. Full methodology:
[`feedcast/evaluation/methodology.md`](feedcast/evaluation/methodology.md).

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

## Replay And Tuning

Feedcast includes a replay-based tuning tool for scripted models. It tests
parameter values against the latest 24 hours of known actuals and ranks them
by forecast accuracy, so model developers can make data-driven parameter
choices. Define sweep candidates in a YAML file, run replay against a model,
and use the ranked results to decide whether to change its constants.

Replay is a directional tool for recent-pattern fitting, not robust
out-of-sample validation. Full usage and examples:
[`feedcast/replay/README.md`](feedcast/replay/README.md).

## Repo Layout

```text
scripts/
  run_forecast.py              CLI entrypoint
feedcast/
  pipeline.py                  End-to-end orchestration
  data.py                      CSV parsing, domain types, fingerprinting
  research/                    Cross-cutting research for models and agents
    index.md                   Research hub and table of contents
    volume_gap_relationship/   Feed volume vs. subsequent gap
      analysis.py              Repeatable data analysis
      findings.md              Concise write-up with methods and conclusions
      artifacts/               Committed outputs used by the write-up
  models/                      Scripted forecasters and consensus blend
    shared.py                  Shared utilities used across models
    slot_drift/                Daily template with per-slot drift
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
    analog_trajectory/         Instance-based ML from similar states
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
    latent_hunger/             Mechanistic hidden hunger state
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
    survival_hazard/           Day-part Weibull survival model
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      research.py              Repeatable data analysis
      research_results.txt     Saved research output
  evaluation/                  Retrospective forecast scoring
    scoring.py                 Shared scorer (Hungarian matching, weighted F1 + timing)
    methodology.md             Scoring design rationale and parameter choices
  replay/                      Latest-24h replay scoring and tuning
    runner.py                  Replay and tune models against the latest 24 hours
    results.py                 Local replay artifact persistence
    README.md                  Usage, tuning examples, and Python API
  agents/                      LLM agent workspaces, prompt, and runner
    __init__.py                Agent orchestration and output validation
    run.sh                     Shell dispatcher for Claude/Codex CLIs
    prompt/prompt.md           Shared agent prompt
    claude/                    Claude persistent workspace
      CHANGELOG.md             Reverse-chronological behavior changes
    codex/                     Codex persistent workspace
      CHANGELOG.md             Reverse-chronological behavior changes
  tracker.py                   Run persistence and retrospectives
  report.py                    Markdown rendering and atomic report swap
  plots.py                     Schedule and trajectory chart generation
  templates/
    report.md.j2               Jinja2 report template
exports/                       Raw Nara CSV drops (untracked)
report/                        Latest report (tracked, committed)
tracker.json                   Run history with predictions and retrospectives
```

## Working with Research

**Start here for cross-cutting context:** Read
[`feedcast/research/index.md`](feedcast/research/index.md). It is the
shared research hub for repo-wide findings, current hypotheses, and open
questions.

Research is advisory, not binding. Models and agents may use these
findings when helpful, but they are free to ignore them if a different
approach is better supported.

**Research directory convention:** Each research article lives in its own
subdirectory under `feedcast/research/` with a standard set of files:

| File | Purpose |
| ---- | ------- |
| `findings.md` | Concise write-up with hypothesis, methods, results, and conclusion. |
| `analysis.py` | Repeatable data analysis. Run with `.venv/bin/python -m feedcast.research.<name>.analysis`. Uses shared data loading so results stay aligned with the repo's current event construction. |
| `artifacts/` | Committed outputs used to support the write-up. Keep them reproducible and easy to inspect. |

**Update research:** Re-run the article's `analysis.py`, commit refreshed
artifacts, and update `findings.md` and `index.md` if the conclusion
changes.

## Working with Models

**Start with the research hub, then read model-local docs:** Shared
findings and open questions now live in
[`feedcast/research/index.md`](feedcast/research/index.md). After that,
read the specific model's `design.md`, `methodology.md`, and
`research.py` if you are changing that model.

**Model directory convention:** Each model lives in its own subdirectory
under `feedcast/models/` with a standard set of files:

| File | Purpose |
| ---- | ------- |
| `model.py` | Implementation. Exports `MODEL_NAME`, `MODEL_SLUG`, `MODEL_METHODOLOGY`, and a forecast function with signature `(history, cutoff, horizon_hours) -> Forecast`. Tuning constants live here, not in `shared.py`. |
| `CHANGELOG.md` | Reverse-chronological behavior log. Update it whenever the model's behavior, assumptions, or tuning changes. Use a one-line summary with `Problem` and `Solution` sections. |
| `methodology.md` | Report-facing text. Content before the first `##` heading is loaded by `load_methodology()` and rendered into the forecast report. |
| `design.md` | Design decisions and rationale. Documents why the model works the way it does. |
| `research.py` | Repeatable data analysis. Run with `.venv/bin/python -m feedcast.models.<name>.research`. Uses the same export selection, data parsing, and constants as the model so its output matches what the model sees. |
| `research_results.txt` | Saved output from the research script. Committed for reproducibility. |

**Update a model:** When you change a model's behavior, assumptions, or
tuning, add a new top entry to that model's `CHANGELOG.md`. If the change
updates cross-cutting evidence, shared hypotheses, or open questions
across models, update the relevant article under `feedcast/research/`
and `feedcast/research/index.md` too.

**Add a model:** Create the subdirectory with the files above, then add a
`ModelSpec` entry to `feedcast/models/__init__.py`. See `slot_drift/` or
`analog_trajectory/` as reference implementations.

**Remove a model:** Delete its `ModelSpec` from the `MODELS` list. Optionally
delete the directory.

**Tune parameters:** Keep model-specific constants in the model file that
uses them. Reserve `feedcast/models/shared.py` for reusable utilities that
are not model concepts.

**Replay a model against the latest observed 24 hours:** Run
`.venv/bin/python scripts/run_replay.py <slug>`. Add `KEY=VALUE` args
to test with overridden constants.

**Tune a model against the latest observed 24 hours:** Define candidates
in a YAML file and run `.venv/bin/python scripts/run_replay.py <slug> sweep.yaml`.
See [`feedcast/replay/README.md`](feedcast/replay/README.md) for details.

**Change the featured default:** Set `FEATURED_DEFAULT` in
`feedcast/models/__init__.py` to any available model slug.

## Working with Agents

**Edit the shared prompt:** Modify `feedcast/agents/prompt/prompt.md`. Both
agents receive the same prompt, prepended with the resolved export path and
workspace path.

**Iterate on one agent's strategy:** Each agent's workspace persists across
runs. Agents can keep durable strategy notes in separate workspace files.

**Use shared research if helpful:** Agents may inspect
`feedcast/research/` as optional reference material. Its findings can
inform an approach, but they are not requirements.

**Update an agent:** When you change an agent's behavior or instructions,
add a new top entry to that agent's `CHANGELOG.md`.

**Add or swap an agent:** Edit the `AGENTS` list in `feedcast/agents/__init__.py`
and add a corresponding case to `feedcast/agents/run.sh`.

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
