# Feedcast

Feedcast explores next-24-hour bottle-feed forecasting from Nara Baby
app exports — and uses that problem as a compact testbed for agentic
engineering. Each run, CLI agents (Claude or Codex) analyze recent
feeding trends, assess and tune the scripted forecasting models, and
produce an independent forecast. Everything happens on a review branch;
the human decides what ships. Feed timing is the primary target, and
each run scores the prior run's predictions against what actually
happened.

*Built by a tired dad with Claude and Codex between bottle feedings.
Coordinated via [claodex](https://github.com/joshuavictorchen/claodex).
My wife mentioned missing the sense of daily structure we used to have
before Silas was born. Predicting feedings felt like a practical place
to start. It also gave me a reason to experiment with agentic
engineering — agents that maintain a repo's own models.*

## Scope

Feedcast is primarily an experiment in agentic engineering and model
maintenance, using newborn feeding data as a compact real-world testbed.
The forecasts, replay tooling, and research artifacts are best read as
outputs from a single-subject experimental system on a small,
fast-changing dataset. This repository is not a validated forecasting
product or decision-support tool.

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

### Feeds vs. Episodes

Not every recorded feed is an independent hunger event. Consecutive
bottle feeds that occur close together — a large feed followed by a
small top-up, for example — often form a single **feeding episode**.
Feedcast uses a deterministic working rule to group raw feeds into
episodes (see
[`feedcast/research/feed_clustering/`](feedcast/research/feed_clustering/)).
The current rule was derived from hand-labeled bottle-feed boundaries
with a conservative objective that prioritizes avoiding false collapses.
Evaluation scores at the episode level: both predictions and actuals
are collapsed into episodes before matching. Models receive raw feed
events and decide independently how to handle episodes in their logic.
The rule should be re-checked periodically as new exports accumulate.

## Forecast Sources

**Scripted models** run deterministically from the event history:

| Model | Approach |
| ----- | -------- |
| Slot Drift | Daily template with per-slot drift tracking and Hungarian matching |
| Analog Trajectory | Instance-based ML: finds similar historical states and averages their futures |
| Latent Hunger State | Mechanistic hidden state: hunger rises over time, feeds reset it proportional to volume |
| Survival Hazard | Day-part Weibull hazard: feeding probability increases with elapsed time |
| Consensus Blend | Exact majority-vote selector across the scripted models |

Each scripted model encodes a distinct hypothesis about the
data-generating process. Production constants are currently tuned
end-to-end: canonical replay selects every model's constants by
optimizing the same forecast-quality objective. An open research
question ([research hub](feedcast/research/README.md)) asks whether a
**stacked generalization** design (Wolpert, 1992) — models tuned to
their own native objectives, ensemble tuned to the end-to-end
objective — would produce a stronger ensemble by preserving
hypothesis-specific model diversity rather than homogenizing toward a
shared optimum.

**LLM agent inference** (`feedcast/agents/`): A CLI agent (Claude or
Codex) produces an independent forecast using freeform reasoning. The
agent receives the export CSV, a persistent workspace, and full read
access to the repo. Agent forecasts are excluded from the consensus
blend and scored by the same retrospective evaluation as scripted models.

Beyond forecasting, agents also participate earlier in the pipeline:
analyzing recent feeding trends (published in the report) and assessing
each scripted model's fit to current patterns, tuning constants when
warranted. See [Pipeline](#pipeline) for the full flow.

## Pipeline

Each run creates an isolated review branch. All mutations — model
tuning, forecasts, report — happen there. Nothing touches the working
branch without a human merge.

```
Pre-flight
  ├── Verify clean git state
  ├── Resolve export (--export-path or latest in exports/)
  └── Create review branch  →  feedcast/YYYYMMDD-HHMMSS

Trend Insights                                   [agent · skippable]
  └── Analyze 7–14 days of feeding patterns
      → summary held in memory for the report

Model Tuning                                     [agent ×4 parallel · skippable]
  └── One agent per scripted model assesses recent
      performance and tunes constants when warranted

Tuning Commit
  └── Commit all tuning changes (provenance SHA for tracker)

Execute                                          [parallel]
  ├── Scripted models  →  consensus blend  →  featured selection
  └── Agent inference  →  independent forecast   [skippable]

Finalize
  ├── Score prior predictions against new actuals
  ├── Render report with trend insights and all forecasts
  ├── Update tracker.json
  └── Commit results
```

Two commits per run: the tuning commit records the code state that
produced the forecasts; the results commit packages outputs. The tuning
commit SHA is the provenance recorded in the tracker and report.

## Event Construction

Feedcast separates **model-local event construction** from **canonical
evaluation inputs**. The distinction matters because models are free to
shape their inputs however they want, but they are all scored against
the same ground truth.

**Canonical evaluation inputs** (used by the scorer, tracker, replay,
and model research scripts): Bottle-only feed events built with
`build_feed_events(activities, merge_window_minutes=None)`. Breastfeed
volume is excluded because breastfeed volume estimates are too noisy to
anchor timing accuracy measurements against. Both actuals and
predictions are collapsed into feeding episodes before matching (see
[Feeds vs. Episodes](#feeds-vs-episodes)).

**Model-local inputs** (built independently by each model): Each model
receives raw `list[Activity]` and constructs its own feed events. Some
models merge nearby breastfeed volume into bottle feeds (using a
non-`None` merge window) to inform their predictions. Others use
bottle-only events. This choice affects what the model sees as input —
it does not affect what the model is scored against.

The separation is intentional. Models choose the input representation
that helps them forecast best, but evaluation holds them all to the
same standard: bottle-only actuals, episode-collapsed, scored with the
shared methodology.

## Evaluation

When a new export arrives, Feedcast scores the previous run's predicted
feeding episodes against the episodes now visible in that export. Both
predictions and actuals are collapsed into episodes before scoring (see
[Feeds vs. Episodes](#feeds-vs-episodes) above). Retrospective results
are stored in `tracker.json` and aggregated into a historical accuracy
table in the report.

The headline score is the geometric mean of a weighted count F1 (did you
predict the right number of episodes?) and a weighted timing score (how
close were the timestamps?). Both components weight earlier episodes more
heavily. Partial horizons are scored on the observed window only and
reported with explicit coverage. These weights, cutoffs, and guardrails
are current scoring assumptions rather than settled truths. Full
methodology:
[`feedcast/evaluation/methodology.md`](feedcast/evaluation/methodology.md).

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The full pipeline requires a configured `claude` or `codex` CLI. For
scripted-only runs, pass `--no-agents`.

1. Drop the latest Nara export into `exports/`.
2. Run `.venv/bin/python scripts/run_forecast.py`.
3. Review the branch and report at `report/report.md`.

The pipeline requires a clean git working tree. It creates a
`feedcast/YYYYMMDD-HHMMSS` branch, commits tuning changes and results
there, and leaves the branch for manual review.

```bash
# Full pipeline (agents analyze, tune, and forecast):
.venv/bin/python scripts/run_forecast.py

# Specific export:
.venv/bin/python scripts/run_forecast.py --export-path exports/export_narababy_silas_YYYYMMDD.csv

# Use Codex instead of Claude:
.venv/bin/python scripts/run_forecast.py --agent codex

# Skip all agent steps for a fast scripted-only run:
.venv/bin/python scripts/run_forecast.py --no-agents
```

Each run updates these artifacts on the review branch:

- `report/report.md` — the human-readable forecast report
- `report/agent-insights.md` — agent trend analysis (when insights enabled)
- `report/schedule.png` — the featured schedule chart
- `report/spaghetti.png` — the all-model trajectory chart
- `report/diagnostics.yaml` — structured model diagnostics
- `tracker.json` — stored predictions and retrospective history

## Replay And Tuning

Feedcast includes a replay-based evaluation and tuning tool for scripted
models. It generates retrospective cutoff points across recent history,
reruns a model at each cutoff, scores each forecast against the now-known
actuals, and aggregates results with recency weighting. Define sweep
candidates in a YAML file, run replay against a model, and use the
ranked results to decide whether to change its constants.

Replay is a directional tool for recent-pattern fitting, not robust
out-of-sample validation. Full usage and examples:
[`feedcast/replay/README.md`](feedcast/replay/README.md).

## Repo Layout

```text
scripts/
  run_forecast.py              CLI entrypoint
skills/                        Agent skill definitions
  trend_insights/
    prompt.md                  Feeding trend analysis task
  model_tuning/
    prompt.md                  Model assessment and tuning task
  research_review/
    prompt.md                  Model-research alignment review (manual)
feedcast/
  pipeline.py                  End-to-end orchestration
  agent_runner.py              Agent CLI invocation and forecast validation
  data.py                      CSV parsing, domain types, fingerprinting
  clustering.py                Episode grouping rule and FeedEpisode type
  research/                    Cross-cutting research for models and agents
    README.md                  Research hub and table of contents
    volume_gap_relationship/   Feed volume vs. subsequent gap
      analysis.py              Repeatable data analysis
      research.md              Current conclusions and evidence
      CHANGELOG.md             Conclusion and method evolution log
      artifacts/               Committed outputs used by the write-up
    feed_clustering/           Episode boundary rule derivation
      analysis.py              Repeatable data analysis
      research.md              Current conclusions and evidence
      CHANGELOG.md             Conclusion and method evolution log
      labels.yaml              Hand-labeled feed boundaries
      artifacts/               Committed outputs used by the write-up
    simulation_study/          Simulation study for hypothesis-conformance testing
      research.md              Cross-model findings and divergence classification
      methodology.md           Shared DGP design, validation protocols, canonical diagnostic
      CHANGELOG.md             Conclusion and method evolution log
  models/                      Scripted forecasters and consensus blend
    shared.py                  Shared utilities used across models
    slot_drift/                Daily template with per-slot drift
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      analysis.py              Repeatable data analysis
      research.md              Evidence and canonical evaluation results
      artifacts/               Committed analysis outputs
    analog_trajectory/         Instance-based ML from similar states
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      analysis.py              Repeatable data analysis
      research.md              Evidence and canonical evaluation results
      artifacts/               Committed analysis outputs
    latent_hunger/             Mechanistic hidden hunger state
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      analysis.py              Repeatable data analysis
      research.md              Evidence and canonical evaluation results
      artifacts/               Committed analysis outputs
    survival_hazard/           Day-part Weibull survival model
      model.py                 Model implementation
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      analysis.py              Repeatable data analysis
      research.md              Evidence and canonical evaluation results
      artifacts/               Committed analysis outputs
    consensus_blend/           Majority-vote ensemble across scripted models
      model.py                 Production exact selector
      CHANGELOG.md             Reverse-chronological behavior changes
      methodology.md           Report methodology text
      design.md                Design decisions and rationale
      analysis.py              Production evaluation and selector sweeps
      research.md              Evidence and canonical evaluation results
      artifacts/               Committed analysis outputs
  evaluation/                  Retrospective forecast scoring
    scoring.py                 Shared scorer (Hungarian matching, weighted F1 + timing)
    methodology.md             Scoring design rationale and parameter choices
  replay/                      Multi-window replay scoring and tuning
    runner.py                  Replay and tune models across retrospective windows
    results.py                 Local replay artifact persistence
    README.md                  Usage, tuning examples, and Python API
  agents/                      LLM agent inference workspace
    prompt.md                  Agent inference prompt
    design.md                  Design decisions and rationale
    methodology.md             Report-facing methodology text
    CHANGELOG.md               Reverse-chronological behavior changes
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
[`feedcast/research/README.md`](feedcast/research/README.md). It is the
shared research hub for repo-wide findings, current hypotheses, and open
questions.

Research is advisory, not binding. Models and agents may use these
findings when helpful, but they are free to ignore them if a different
approach is better supported.

**Research directory convention:** Both cross-cutting and model research
use the same file names. See the research hub README for the full convention,
document structure, and workflow — including where cross-cutting and
model research differ. The core files are:

| File | Purpose |
| ---- | ------- |
| `research.md` | Current conclusions. Written from first principles with a staleness box for mechanical freshness detection. |
| `analysis.py` | Repeatable analysis. Run as a Python module (see the research hub README for exact commands). |
| `artifacts/` | Committed outputs (tables, charts, CSVs) referenced by `research.md`. |
| `CHANGELOG.md` | Reverse-chronological evolution log. One-line summary with date, Problem/Solution sections, optional Research section. Same format for all CHANGELOGs. |

## Working with Models

**Start with the research hub, then read model-local docs:** Shared
findings and open questions now live in
[`feedcast/research/README.md`](feedcast/research/README.md). After that,
read the specific model's `research.md`, `design.md`, `methodology.md`,
and `analysis.py` if you are changing that model.

**Model directory convention:** Each model lives in its own subdirectory
under `feedcast/models/` with a standard set of files:

| File | Purpose |
| ---- | ------- |
| `model.py` | Implementation. Exports `MODEL_NAME`, `MODEL_SLUG`, `MODEL_METHODOLOGY`, and a forecast function with signature `(activities, cutoff, horizon_hours) -> Forecast`. Each model receives raw `list[Activity]` and builds its own events locally (breastfeed merge policy, episode collapsing, cutoff filtering). Tuning constants live here, not in `shared.py`. |
| `CHANGELOG.md` | Reverse-chronological behavior log. Update it whenever the model's behavior, assumptions, or tuning changes. Use a one-line summary with `Problem` and `Solution` sections. |
| `methodology.md` | Report-facing text. Content before the first `##` heading is loaded by `load_methodology()` and rendered into the forecast report. |
| `design.md` | Design decisions and rationale. Documents why the model works the way it does. |
| `analysis.py` | Repeatable data analysis. Run with `.venv/bin/python -m feedcast.models.<name>.analysis`. Shares the same export selection, core data parsing, and production constants as the model. Some scripts also run explicitly labeled diagnostic views that differ from production inputs or objectives. |
| `research.md` | Evidence document. Current support and challenges for the model's design and constants. Standard template: overview, last canonical run box, methods (canonical + diagnostic), results (canonical + diagnostic), conclusions with disposition, labeled open questions (model-local + cross-cutting). |
| `artifacts/` | Committed outputs (`research_results.txt` and any other generated files) referenced by `research.md`. |

**Update a model:** When you change a model's behavior, assumptions, or
tuning, add a new top entry to that model's `CHANGELOG.md`. If the change
updates cross-cutting evidence, shared hypotheses, or open questions
across models, update the relevant article under `feedcast/research/`
and `feedcast/research/README.md` too.

**Add a model:** Create the subdirectory with the files above, then add a
`ModelSpec` entry to `feedcast/models/__init__.py`. See `slot_drift/` or
`analog_trajectory/` as reference implementations.

**Remove a model:** Delete its `ModelSpec` from the `MODELS` list. Optionally
delete the directory.

**Tune parameters:** Keep model-specific constants in the model file that
uses them. Reserve `feedcast/models/shared.py` for reusable utilities that
are not model concepts. The research-tuning pipeline is advisory:
`analysis.py` and `tune_model()` produce evidence and recommendations,
but they do not modify production constants automatically. Updating
`model.py` is a deliberate step with a `CHANGELOG.md` entry explaining
what changed and why.

**Replay a model across retrospective windows:** Run
`.venv/bin/python scripts/run_replay.py <slug>`. Add `KEY=VALUE` args
to test with overridden constants.

**Tune a model with a parameter sweep:** Define candidates in a YAML
file and run `.venv/bin/python scripts/run_replay.py <slug> sweep.yaml`.
See [`feedcast/replay/README.md`](feedcast/replay/README.md) for details.

**Change the featured default:** Set `FEATURED_DEFAULT` in
`feedcast/models/__init__.py` to any available model slug.

## Working with Agents

Agents participate in three pipeline steps:

1. **Trend insights** — analyze 7–14 days of feeding history and write a
   parent-facing summary of recent patterns. Published in the report.
2. **Model tuning** — independently assess each scripted model's fit to
   current and emerging feeding patterns. Tune constants when warranted,
   constrained to that model's directory.
3. **Agent inference** — produce an independent feeding forecast from the
   export data and a persistent workspace. Excluded from the consensus
   blend, scored by the same retrospective as scripted models.

All three steps are skippable via CLI flags (see
[Quick Start](#quick-start)). Each runs on the review branch the
pipeline creates — the human merges or discards the branch after
reviewing.

**Edit the inference prompt:** Modify `feedcast/agents/prompt.md`. The
prompt uses `{{variable}}` placeholders that the pipeline substitutes at
runtime. The agent can also modify its own prompt across runs — the
branch workflow provides a review gate.

**Iterate on strategy:** The agent workspace (`feedcast/agents/`) persists
across runs. The agent can keep durable strategy notes, helper scripts,
or model code in the workspace.

**Use shared research if helpful:** Agents may inspect
`feedcast/research/` as optional reference material. Its findings can
inform an approach, but they are not requirements.

**Update the agent:** When the agent's behavior or instructions change,
add a new top entry to `feedcast/agents/CHANGELOG.md`.

## Working with Skills

Skills are reusable task instructions for agents — generic jobs like
"analyze these trends" or "tune this model." Some skills are invoked by
the pipeline automatically; others are designed for manual use in an
interactive agent session. They are distinct from the agent inference
workspace (`feedcast/agents/`), which is a persistent model that
produces its own forecast. Each skill lives in its own directory under
`skills/`:

| File | Purpose |
| ---- | ------- |
| `prompt.md` | Agent instructions with `{{variable}}` placeholders for runtime context. |
| `*.py`, `*.sh` | Optional helper scripts the agent can invoke during the task. |

Pipeline-integrated skills are read by `feedcast/pipeline.py`, which
substitutes context variables and passes the rendered prompt to the
agent CLI via [`feedcast/agent_runner.py`](feedcast/agent_runner.py).
Manual skills are read directly by an interactive agent session.

**Current skills:**

- `skills/trend_insights/` — feeding trend analysis for the report
- `skills/model_tuning/` — model assessment and optional constant tuning
- `skills/research_review/` — assess models against research hub findings (manual invocation)

**Add a skill:** Create a new directory under `skills/` with a
`prompt.md`. For pipeline-integrated skills, use `{{variable_name}}`
placeholders and wire the invocation into `feedcast/pipeline.py`. For
manual skills, write the prompt as self-contained instructions.

## Design Decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| Episode grouping | Shared rule, model-local handling | Deterministic boundary rule in `clustering.py`; evaluation and consensus collapse both sides; models decide independently how to use episodes |
| Model inputs | Raw activities, model-owned shaping | Each model receives `list[Activity]` and builds its own events (breastfeed merge, episode collapse) locally |
| Scripted models | Distinct conceptual frames | Template, instance-based ML, mechanistic, and gap-regression approaches for ensemble diversity |
| Model tuning | End-to-end canonical (under review) | Each model's constants are selected by canonical replay (same end-to-end objective). Stacked generalization — models on native objectives, blend on the canonical objective — is an [open investigation](feedcast/research/README.md) |
| Ensemble | Consensus uses scripted models only | Agents excluded until retrospectives demonstrate consistent value |
| Featured forecast | Consensus > static tiebreaker | Simple default; manually overridable via `FEATURED_DEFAULT` |
| Agent failure | Fail fast | No silent fallback |
| Branch per run | Isolated review branch | All mutations on `feedcast/YYYYMMDD-HHMMSS`; the working branch is never modified directly |
| Tuning provenance | Explicit tuning commit SHA | Captured immediately after tuning; not inferred from ambient worktree state |
| Agent steps | Independently skippable | `--skip-tuning`, `--skip-insights`, `--skip-agent-inference` (or `--no-agents` for all) |
| Agent model versions | Hardcoded in `agent_runner.py` | `_agent_command()` pins specific model IDs and effort flags for claude and codex; update there when model versions change |
| Parallel tuning | Up to 4 concurrent agents | One agent per scripted model; write scope constrained to `feedcast/models/<slug>/` by prompt instruction |
| Model registration | Explicit `MODELS` list | No auto-discovery; you see what runs by reading one list |
| Report tracking | `report/` and `tracker.json` committed | One workspace; latest report always accessible; tracker keeps the latest run per dataset rather than every retry |
| Exports | Untracked raw drops | Reproducibility via `tracker.json` dataset fingerprints |
| Report write | Atomic swap with rollback | If rendering fails, the prior report is preserved |

## Principles

- Feed timing is the success metric. Volume is secondary.
- Prefer simple approaches until complexity clearly earns its keep.
- Let new exports drive iteration. The goal is the next 24 hours.
- Simplicity wins unless the forecast improves.
