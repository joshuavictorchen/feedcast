# Feedcast

Feedcast is an experiment in agentic engineering: a forecasting repo that maintains its own models. It predicts the next 24 hours of bottle feeds from Nara Baby app exports. On each run, a CLI agent (Claude or Codex) analyzes recent feeding patterns, evaluates every scripted model against replay-backed evidence, and tunes constants when warranted. The same run also produces an independent LLM forecast and scores the prior run's predictions against what actually happened.

*Built by a sleep-deprived dad with Claude and Codex between bottle feedings. My wife missed the daily structure we had before Silas was born. I threw together a bottle-feed predictor from our Nara Baby exports as a quick AI agent demo to cheer her up, and it grew into what you see here. Coordinated via [claodex](https://github.com/joshuavictorchen/claodex).*

## Latest forecast

![Featured Forecast](report/schedule.png)

*Latest committed featured forecast.*

Each run refreshes the full report at [`report/report.md`](report/report.md) with per-model predictions, retrospective accuracy, agent trend insights, and diagnostics.

*Feedcast is a single-subject experimental system on a small, shifting dataset. It is not a validated forecasting product or decision-support tool.*

## What makes this different

Feedcast is a self-improving forecasting system: each run tunes the models that produce the next forecast.

- **The models tune themselves.** On each run, a CLI agent (Claude or Codex) assesses every base scripted model's fit to current feeding patterns and rewrites its constants when the evidence supports it. Tuning decisions are backed by replay sweeps over recent history, and every change lands as a committed `CHANGELOG.md` entry with numeric deltas. See any per-model `CHANGELOG.md` for the live tuning history.
- **The repo scores itself.** Each run compares the previous run's predictions to what actually happened in the new export. A growing history lives in `tracker.json` and the report's retrospective accuracy table.
- **Documentation is dual-use.** Research articles, model design notes, and skill prompts are the same instructions the CLI agents read at runtime. Writing for human readers and writing for agents converge.
- **Hypothesis diversity by design.** Four base scripted models encode distinct hypotheses about what drives feeding patterns: template drift, instance-based retrieval, mechanistic hunger, and survival hazards. A consensus blend ensembles them into a fifth deterministic forecast. A sixth forecast comes from a CLI agent running independently.

## Pipeline

```
Pre-flight
  ├── Verify clean git state
  ├── Resolve export (--export-path or latest in exports/)
  └── Create run branch  →  feedcast/YYYYMMDD-HHMMSS

Trend Insights                                   [agent · skippable]
  └── Analyze 7 to 14 days of feeding patterns
      → summary held in memory for the report

Model Tuning                                     [agent ×4 parallel · skippable]
  └── One agent per base model assesses recent
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

Each run produces two commits on the run branch. The tuning commit captures the model code state that produced the forecasts; its SHA is the provenance recorded in `tracker.json`. The results commit packages the report, charts, and tracker update.

> Committing `report/`, `tracker.json`, and tuning diffs into the main repo is unorthodox. Feedcast does it so the commit history itself becomes the telemetry: every run leaves a branch recording what the data looked like, what the models decided, what was tuned, and how the previous forecast scored. The repo becomes an experiment log. For a single-subject learning space for agentic engineering, this beats standing up a database.

## Substantial subsystems

The forecasting machinery is built from four substantial subsystems, each with its own documentation and each a small project in its own right.

- **Research hub** ([`feedcast/research/`](feedcast/research/)). Cross-cutting research articles, current hypotheses, shared directory conventions, and the research workflow. Current articles cover the volume-gap relationship, feed clustering (the episode boundary rule), and a simulation study on canonical tuning.
- **Replay and tuning** ([`feedcast/replay/`](feedcast/replay/)). Multi-window replay rewinds the export to retrospective cutoffs, reruns a model at each, scores the forecasts against now-known actuals, and ranks candidate constants by canonical headline. Supports parameter sweeps, candidate-parallel execution, and a Python API.
- **Evaluation scoring** ([`feedcast/evaluation/methodology.md`](feedcast/evaluation/methodology.md)). Hungarian-matched count F1 and a soft timing credit combined as a geometric-mean headline. Episode-level ground truth, horizon-weighted, partial-horizon aware. The same scorer backs the production tracker and the replay tool.
- **Simulation study** ([`feedcast/research/simulation_study/`](feedcast/research/simulation_study/)). Synthetic data-generating processes verify that canonical tuning is not distorted by the production pipeline. On real data, three of four models show that their internal diagnostics and canonical replay disagree, which reveals how closely each model's hypothesis matches actual feeding patterns.

## Model lineup

A feeding episode is a cluster of close-together feeds treated as a single hunger event. The repo scores at the episode level, so a bottle plus a top-up ten minutes later counts once.

| Model | Hypothesis | Technique |
| ----- | ---------- | --------- |
| [Slot Drift](feedcast/models/slot_drift/) | Days have recurring time-of-day slots that drift gradually. | Hungarian bipartite matching on circular time-of-day distance; per-slot recency-weighted linear drift. |
| [Analog Trajectory](feedcast/models/analog_trajectory/) | Similar past states produce similar futures. | k-nearest-neighbor retrieval over state features (gaps, volumes); weighted mean of neighbor continuations. |
| [Latent Hunger](feedcast/models/latent_hunger/) | Hunger is a hidden process that rises with time and resets with each feed. | Hidden scalar state with multiplicative satiety reset proportional to volume; growth rate fit at runtime; forward simulation. |
| [Survival Hazard](feedcast/models/survival_hazard/) | The next feed is a timed event whose hazard rises with elapsed time, with distinct day and night regimes. | Day-part Weibull hazard fit by maximum likelihood; conditional survival for the runtime feed. |
| [Consensus Blend](feedcast/models/consensus_blend/) | A majority vote across diverse hypotheses beats any single model. | Exact majority-vote sequence selector via mixed-integer linear programming, with episode collapsing before candidate generation. |
| [Agent Inference](feedcast/agents/) | Freeform LLM reasoning may capture patterns that scripted models miss. | CLI agent with a persistent workspace that it can rewrite between runs. Current implementation is Empirical Cadence Projection: recency-weighted day-part gap medians with conditional survival for the first feed. |

Each scripted model folder contains its implementation, a `design.md`, a `methodology.md`, a `research.md` with canonical evaluation results, and a `CHANGELOG.md` with every behavior change. The agent inference workspace uses a different document set: `model.py`, `prompt.md` (runtime instructions), `strategy.md` (durable approach notes), `methodology.md`, `design.md`, and `CHANGELOG.md`.

## Design choices

- **Agent-maintained constants with provenance.** Each base scripted model's constants live in its `model.py` and are tuned by a dedicated agent on each run with replay-backed evidence. Every tuning action is a `CHANGELOG.md` entry with Problem / Research / Solution. The tuning commit SHA is carried into `tracker.json` so any forecast can be traced to the exact model code that produced it.
- **Every run is isolated.** The pipeline creates a per-run branch `feedcast/YYYYMMDD-HHMMSS` and performs all mutations there: tuning diffs, report renders, and tracker updates. The working branch is never written to by the pipeline.
- **Shared evaluation standard.** Every model is scored against the same bottle-only feeding episodes using Hungarian-matched count F1 and a soft timing credit, combined as a geometric-mean headline. Models can shape their own input events; the scoring ground truth is fixed.
- **Timing is the objective.** Forecasts are scored primarily on when feeds happen. Count accuracy (how many feeds in the next 24 hours) is achievable on this dataset; timing accuracy (when exactly) is the hard problem and drives most of the tuning decisions. Volume is a secondary output.
- **Parallel tuning with per-model ownership.** Up to four model-tuning agents run concurrently, one per base model. Each prompt assigns one model directory and instructs the agent to modify only files inside it. The pipeline stages all tuning edits into a single commit on the run branch, so any out-of-scope change is visible in the diff.
- **Fail fast on agent error.** Agent failures stop the run. There is no silent fallback.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The full pipeline requires a configured `claude` or `codex` CLI and a clean git working tree. For scripted-only runs, pass `--no-agents`.

1. Drop a Nara export CSV into `exports/`.
2. Run `.venv/bin/python scripts/run_forecast.py`.
3. Review the new run branch and the updated report at `report/report.md`.

A full run takes a few minutes, most of it the agents thinking.

```bash
# Specific export
.venv/bin/python scripts/run_forecast.py --export-path exports/export_narababy_silas_YYYYMMDD.csv

# Use Codex instead of Claude
.venv/bin/python scripts/run_forecast.py --agent codex

# Scripted models only, no agents
.venv/bin/python scripts/run_forecast.py --no-agents

# Skip individual agent steps
.venv/bin/python scripts/run_forecast.py --skip-tuning
.venv/bin/python scripts/run_forecast.py --skip-insights
.venv/bin/python scripts/run_forecast.py --skip-agent-inference
```

Each run updates these artifacts on the run branch:

- `report/report.md`: forecast report
- `report/agent-insights.md`: agent trend analysis (when insights enabled)
- `report/schedule.png`: featured schedule chart
- `report/spaghetti.png`: all-model trajectory chart
- `report/diagnostics.yaml`: structured diagnostics
- `tracker.json`: stored predictions and retrospective history

## Where to look

```
feedcast/
  models/          four base scripted forecasters plus the consensus blend
  agents/          LLM agent inference workspace
  research/        cross-cutting research articles and research hub
  evaluation/      scoring methodology and scorer
  replay/          multi-window replay and tuning tool
  pipeline.py      end-to-end orchestration
scripts/           CLI entrypoints
skills/            reusable agent task prompts (trend insights, model tuning, research review)
report/            latest forecast (committed)
tracker.json       run history with predictions and retrospectives
exports/           raw Nara CSV drops (untracked)
```

Contributor workflow for models lives in [`feedcast/models/README.md`](feedcast/models/README.md), agent inference in [`feedcast/agents/README.md`](feedcast/agents/README.md), and skills in [`skills/README.md`](skills/README.md).

## The pattern beyond baby feeds

Feedcast's architecture is domain-agnostic: a scripted model lineup with diverse hypotheses, a shared evaluation standard, an agent loop that assesses and tunes with evidence-backed commits, and a persistent record of every run. Substitute any periodic time-series drop with a measurable forecast objective and the scaffold transfers. Baby feeds are a good testbed because the dataset is small, shifting, and single-subject, and the forecast horizon is short enough to score against the next data drop.

## Glossary

| Term | Meaning |
| ---- | ------- |
| Agent inference | An independent forecast produced by a CLI agent (Claude or Codex) from the workspace in `feedcast/agents/`. Excluded from the consensus blend. |
| Canonical evaluation | Shared scoring protocol: bottle-only feeding episodes, Hungarian matching, horizon-weighted count F1 and timing credit. The shipping gate for production constant decisions. |
| Feed | A single logged bottle or breast feed event with timestamp, type, and volume. |
| Feeding episode | A cluster of close-together feeds treated as one hunger event. Scoring operates at the episode level. The boundary rule is derived from hand-labeled data in [`feedcast/research/feed_clustering/`](feedcast/research/feed_clustering/). |
| Headline score | Geometric mean of weighted count F1 and weighted timing credit, scaled 0 to 100. The primary quality metric. |
| Replay | Rewinding the export to retrospective cutoffs, rerunning a model at each, and aggregating scores with recency weighting. The core evidence tool for tuning. |
| Run branch | A per-run branch named `feedcast/YYYYMMDD-HHMMSS` where tuning and results commits land. |
| Scripted model | A deterministic forecasting model under `feedcast/models/`. The scripted lineup comprises four base models (slot_drift, analog_trajectory, latent_hunger, survival_hazard) plus the consensus blend. |
| Skill | A reusable task prompt under `skills/` (trend insights, model tuning, research review). |
| Tracker | `tracker.json`: the persistent record of runs, predictions, and retrospective scores. |
| Tuning | Changing a scripted model's constants in `model.py` based on replay evidence. Done by an agent during the pipeline or by a human. |
| Tuning commit | The commit that records tuning changes. Its SHA is the provenance recorded in `tracker.json`. |
