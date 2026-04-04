# Agentic Pipeline Pivot

**Planning transcript**: [`.transcripts/9b10b8c5-c973-476b-afe7-5d158ed6940f.jsonl`](.transcripts/9b10b8c5-c973-476b-afe7-5d158ed6940f.jsonl)
— full conversation (Claude + Codex + user) that produced this plan, including
interview, design rationale, review findings, and simplification decisions.

## Context for Implementers

This plan was produced collaboratively by Claude and Codex with the user.
A fresh agent should internalize these points before starting work.

| # | Key Point |
| - | --------- |
| 1 | **Motivation**: The user's wife misses daily structure. Predicting feedings is the practical hook. The user also wants to experiment with agentic engineering — agents that maintain a repo's own models. |
| 2 | **Spirit**: Agents are collaborators, not autonomous systems. Every run creates a review branch; nothing merges without the human. "Agents propose, humans ship." |
| 3 | **Forward-looking tuning**: Model tuning is about anticipating where the baby's patterns are heading, not fitting historical data. The baby is growing fast — patterns shift week to week. Canonical replay is the production authority, but it is one signal, not the objective function for the agent's judgment. |
| 4 | **Simplicity**: The plan was deliberately simplified. No `run.sh` in agents/, no `prompt_hash()`, no bookkeeping abstractions. Simple `{{var}}` substitution for prompts (not Jinja2). `--allow-empty` commits instead of conditional logic. Don't re-add complexity. |
| 5 | **Write isolation**: Parallel tuning agents must write only to their own model directory (`feedcast/models/<slug>/`). Cross-cutting research updates go in the model's `research.md` and are promoted manually. This is a hard boundary, not a suggestion. |
| 6 | **Provenance**: The tracker records the tuning commit SHA, captured explicitly after the tuning commit via `git rev-parse HEAD`. Do not infer provenance from ambient worktree state later — the worktree will be dirty between the tuning and results commits. The tuning commit is the code state that generated the forecasts; the results commit packages outputs but is not itself provenance. |
| 7 | **Artifact flow**: `agent-insights.md` is ephemeral — the pipeline captures its content into memory after the agent writes it, then publishes it to `report/` during the atomic report swap. The renderer never reads it from disk. `methodology.md` in `agents/` is persistent and agent-maintained across runs. |
| 8 | **Existing code reuse**: `run_all_models()`, `select_featured_forecast()`, scoring, retrospective, tracker persistence, and report rendering all survive. The refactor is orchestration and sequencing, not reimplementation. Read the existing code before writing new code. |
| 9 | **Read README.md and its references first**: The README links to the research hub, evaluation methodology, replay docs, and model conventions. These are essential context for writing skill prompts and understanding what the pipeline does. |

## Overview

Transform Feedcast from a manually-tuned, script-orchestrated pipeline into
an agent-maintained system. CLI agents (Claude or Codex) analyze feeding
trends, assess and tune scripted models, and produce an independent forecast
— all within a single automated run. Each run creates an isolated branch
that the user reviews before merging. The repo proposes its own model
updates as new data arrives; the human decides what ships.

## Decisions

| Topic | Decision |
| ----- | -------- |
| Agent per run | Same agent (claude or codex) for all steps; CLI arg, default claude |
| Agent write access | Task-scoped; tuning agents write to their model directory only, inference agent writes to its workspace only |
| Parallel sessions | Acceptable (up to 4 concurrent for model tuning) |
| Error handling | Fail fast — any step failure aborts the run |
| Clean git | Required; refuse to run if working tree is dirty |
| Export selection | Unchanged (`--export-path` or latest in `exports/`) |
| Git workflow | New branch per run, left for manual review, not merged |
| Retrospective | Pipeline computes quantitative scores (unchanged); tuning agent does qualitative assessment |
| Model tuning | Optional, default on (`--skip-tuning` to disable) |
| Agent inference | Shared flat workspace in `agents/`, produces `forecast.json`, excluded from consensus blend |
| Trend insights | Output: `report/agent-insights.md`, rendered in report near top |
| Research review skill | Manual invocation, separate from pipeline, lowest priority |
| Replay tool | Unchanged; agents invoke it via CLI during model tuning |
| Tuning authority | Canonical replay is the production authority; native diagnostics are evidence, not the objective |
| Tuning write scope | Model-local only (`feedcast/models/<slug>/`); cross-cutting research updates are noted in model's `research.md` and promoted manually |
| Tracker compaction | Retained; same-dataset reruns overwrite the previous entry to prevent tracker bloat |
| Tracker commit | Records the tuning commit SHA, captured explicitly after the tuning commit — not inferred from ambient worktree state later. The tuning commit is the code state that generated the forecasts; the results commit packages outputs but is not itself provenance |
| Artifact lifecycles | `report/agent-insights.md` is ephemeral (regenerated each run); `feedcast/agents/methodology.md` is persistent (agent-maintained across runs) |

## Pipeline Flow

```
Pre-flight
  ├── Assert git working tree is clean
  ├── Resolve export (--export-path or latest in exports/)
  ├── Parse CSV → ExportSnapshot
  └── git checkout -b feedcast/YYYYMMDD-HHMMSS

Step 1 · Trend Insights                         [agent, skippable]
  └── Agent + skills/trend_insights
      → staging temp file → read into memory (published in Step 5)

Step 2 · Model Assessment & Tuning              [agent ×4 parallel, skippable]
  └── For each scripted model:
        Agent + skills/model_tuning
        → may modify feedcast/models/<slug>/{model.py, CHANGELOG.md, ...}
        (writes scoped to model directory only)

Step 3 · Tuning Commit
  ├── git commit --allow-empty (tuning changes only)
  └── Capture tuning commit SHA (provenance for tracker/report)

                    ┌──────────────────────────────┐
Step 4 · Execute    │         parallel              │
                    │  4a: Scripted models           │
                    │      → consensus blend         │
                    │      → featured selection       │
                    │                                │
                    │  4b: Agent inference  [skippable]│
                    │      Agent + agents/prompt.md  │
                    │      → agents/forecast.json    │
                    └──────────────────────────────┘

Step 5 · Finalize
  ├── Retrospective (score prior run's predictions vs. new actuals)
  ├── Historical accuracy aggregation
  ├── Render report (includes agent-insights from memory + agent forecast)
  │   └── Publishes report/agent-insights.md during atomic report swap
  ├── Save tracker.json (uses captured tuning commit SHA as provenance)
  └── git commit (results)
```

## Architecture

### Agent Inference Model vs. Skills

Skills (`skills/`) are generic reusable task instructions — "do this job."
The agent inference model (`feedcast/agents/`) is a persistent workspace with
its own prompt, design docs, and evolving artifacts — it is a model peer to
the scripted models, not a task.

The pipeline invokes both through the same agent runner, but they serve
different roles:

- `skills/trend_insights/prompt.md` — task: "analyze these feeding trends"
- `skills/model_tuning/prompt.md` — task: "assess and tune this model"
- `feedcast/agents/prompt.md` — model: "produce a feeding forecast"

### Skill Convention

Each skill is a directory under `skills/`:

| File | Purpose |
| ---- | ------- |
| `prompt.md` | Agent instructions. `{{variable}}` placeholders for runtime context. |
| `*.py`, `*.sh` | Optional helper scripts the agent can invoke during the task. |

The pipeline reads `prompt.md`, substitutes context variables, and passes
the rendered prompt to the agent CLI.

### Agent Runner

A single utility in `feedcast/agent_runner.py`:

```python
def invoke_agent(
    agent: str,              # "claude" or "codex"
    prompt_path: Path,       # path to prompt.md (skill or agents/)
    context: dict[str, str], # {{key}} → value substitutions
    timeout: int = 600,
) -> subprocess.CompletedProcess
```

Dispatches to `claude -p` or `codex -q` with the rendered prompt.
Raises on non-zero exit or timeout. Used by all pipeline steps that
invoke agents.

### Repo Layout (changes only)

```
scripts/
  run_forecast.py              UPDATED — new CLI args (--agent, --skip-tuning, etc.)
+ skills/                      NEW — agent skill definitions
+   trend_insights/
+     prompt.md
+   model_tuning/
+     prompt.md
+   research_review/
+     prompt.md
feedcast/
  pipeline.py                  REWRITTEN — new orchestration
+ agent_runner.py              NEW — agent CLI invocation + forecast validation
  report.py                    UPDATED — agent-insights.md integration
  templates/
    report.md.j2               UPDATED — agent-insights section near top
  agents/                      RESTRUCTURED → flat shared workspace
    prompt.md                  Agent inference prompt (the "model")
    design.md                  Design rationale
    methodology.md             Report-facing methodology text
    CHANGELOG.md               Behavior evolution
    forecast.json              Output (deleted before each invocation)
-   __init__.py                Logic relocates to agent_runner.py and pipeline.py
-   run.sh                     Removed (pipeline uses agent_runner.py; add back if standalone use needed)
-   prompt/prompt.md           Replaced by agents/prompt.md
-   claude/                    Removed — single shared workspace
-   codex/                     Removed — single shared workspace
```

Everything else unchanged: `models/`, `evaluation/`, `replay/`, `research/`,
`data.py`, `clustering.py`, `tracker.py`, `plots.py`.

## Implementation Phases

Build order reflects dependencies: foundation first, then parallel tracks
for workspace restructuring and skill authoring, then pipeline integration.

### Phase 1 · Foundation ✓ `163c068`

**1.1 Agent runner** — `feedcast/agent_runner.py`
- `invoke_agent()`: read prompt.md, substitute `{{context}}` vars, dispatch
  to `claude -p` or `codex -q`, raise on failure
- `validate_agent_forecast(path) -> list[ForecastPoint]`: parse and validate
  forecast.json (based on `_load_forecast_points()` in `agents/__init__.py`)

**1.2 Skills directory**
- Create `skills/` with `trend_insights/` and `model_tuning/` subdirectories
- Placeholder `prompt.md` files to validate the invocation path

**1.3 Pre-flight checks**
- Git-clean assertion: `git status --porcelain`, refuse if non-empty
- Existing export resolution logic stays as-is

**Also done**: `prompt_hash` plumbing removed from pipeline (tracker param
defaults to `{}`). Tests: `tests/test_agent_runner.py`, `tests/test_pipeline.py`.

### Phase 2 · Agent Inference Restructuring ✓ `39944fa`

Flattened `feedcast/agents/` to a single shared workspace: `prompt.md`
(with `{{var}}` placeholders), `design.md`, `methodology.md`,
`CHANGELOG.md`. Deleted `__init__.py`, `run.sh`, `prompt/`, `claude/`,
`codex/`. Removed `run_all_agents` import and `--skip-agents` from
`pipeline.py` (clean break). README correctness pass for deleted paths
and removed CLI flags.

### Phase 3 · Skills ✓ (uncommitted, staged with Phase 4)

Replaced placeholder prompts with full skill instructions:

- `skills/trend_insights/prompt.md`: 7–14 day feeding trend analysis,
  parent-facing output to `{{output_path}}` staging path.
  Context variables: `{{export_path}}`, `{{baby_age_days}}`,
  `{{cutoff_time}}`, `{{output_path}}`
- `skills/model_tuning/prompt.md`: four-step assess-and-tune workflow
  with forward-looking framing, replay CLI tools, hard write boundary
  to `{{model_dir}}` (tracked files only; gitignored replay artifacts
  are allowed).
  Context variables: `{{model_slug}}`, `{{model_dir}}`,
  `{{export_path}}`, `{{last_retro_scores}}`, `{{research_hub_path}}`

### Phase 4 · Pipeline Orchestration ✓

Depends on Phases 1–3 (all complete).

**Already done in earlier phases** (do not re-implement):
- `run_all_agents` import removed from `pipeline.py` (Phase 2)
- `--skip-agents` CLI flag removed from `pipeline.py` (Phase 2)
- `feedcast/agent_runner.py` exists with `invoke_agent()` and
  `validate_agent_forecast()` (Phase 1)
- Pre-flight git-clean check exists in `pipeline.py` (Phase 1)

**4.1 Rewrite `feedcast/pipeline.py`**

Split CLI from orchestration: `pipeline.py` exposes a Python API,
`scripts/run_forecast.py` owns argparse. Currently `main()` in
`pipeline.py` owns argparse and `run_forecast.py` is a thin wrapper
that calls `main()`. Refactor so `main()` takes explicit parameters:

```python
def main(
    export_path: Path | None = None,
    agent: str = "claude",
    skip_tuning: bool = False,
    skip_insights: bool = False,
    skip_agent_inference: bool = False,
) -> None:
```

Sequence (references to pipeline flow above):
1. Pre-flight: git-clean check, resolve export, parse CSV → snapshot
2. `git checkout -b feedcast/{timestamp}` (all mutations on new branch)
3. Trend insights (unless skipped):
   - Create a temp file for agent output
   - `invoke_agent(agent, skills/trend_insights, {... output_path=tmp ...})`
   - Read the temp file content into an `agent_insights: str` variable
   - This content is passed to the renderer later (not written to
     `report/` yet)
4. Model tuning (unless skipped):
   - `ThreadPoolExecutor` → `invoke_agent(agent, skills/model_tuning, ...)`
     × N scripted models (one per `ModelSpec` in `feedcast/models/`)
5. Tuning commit:
   - `git add -A` → `git commit --allow-empty` (tuning changes only)
   - **Capture this commit SHA** — it is the provenance commit recorded in
     tracker and report. Do not infer provenance from ambient git state
     later (the worktree will be dirty again after step 6 produces outputs)
6. Execute (parallel via `ThreadPoolExecutor`):
   - 6a: `run_all_models()` (existing, unchanged)
   - 6b: Agent inference (unless skipped):
     - Delete stale `feedcast/agents/forecast.json`
     - `invoke_agent(agent, feedcast/agents/prompt.md, {...})`
     - `validate_agent_forecast()` → `list[ForecastPoint]`
     - Read `feedcast/agents/methodology.md`
     - Construct `Forecast(name=..., slug=..., points=..., methodology=...)`
     - (This `Forecast` construction happens in the pipeline, not in
       `agent_runner.py` — the runner stays narrow)
7. Consensus blend + featured selection (existing logic, uses only
   scripted model forecasts — agent forecast is excluded)
8. Append agent `Forecast` to `all_forecasts` (after consensus, so the
   agent is in the report and tracker but not in the blend)
9. Retrospective scoring (existing logic)
10. Historical accuracy aggregation (existing logic)
11. Render report — pass `agent_insights` content + `all_forecasts`
    (including agent) to renderer. Also write `agent-insights.md` as a
    report artifact during the atomic swap
12. Save tracker — use the captured tuning commit SHA as provenance,
    not the current (dirty) worktree state
13. `git add -A` → `git commit` (results)

Key reuse: `run_all_models()`, `select_featured_forecast()`, scoring,
retrospective, tracker, and report rendering logic all survive. The
pipeline refactor is about orchestration and sequencing, not
reimplementing model execution or scoring.

**4.2 Update `scripts/run_forecast.py`**

Move argparse here. Parse CLI args, call `pipeline.main(...)`:
- `--export-path PATH` (existing, passed through)
- `--agent {claude,codex}` (default: `claude`)
- `--skip-tuning`
- `--skip-insights`
- `--skip-agent-inference`

**4.3 Update report rendering**

`feedcast/report.py`:
- `generate_report()` accepts `agent_insights: str | None` as a new
  parameter. Pass it into the template context.
- If `agent_insights` is not `None`, write `agent-insights.md` into the
  staging directory during `_render_report()` so it is published by the
  existing atomic swap. The renderer never reads it from disk.

`feedcast/templates/report.md.j2`:
- Add a conditional `{% if agent_insights %}` section near the top,
  after the charts and before the retrospective table.

**4.4 Agent forecast integration**
- Agent forecast is a `Forecast` object constructed by the pipeline
  (not by `agent_runner.py`)
- Included in report methodologies, spaghetti chart, and `tracker.json`
- Excluded from consensus blend: the blend operates on `base_forecasts`
  (output of `run_all_models`); the agent forecast is appended to
  `all_forecasts` after the blend is computed
- Methodology text comes from the persistent `feedcast/agents/methodology.md`

**4.5 Tracker provenance**

`build_run_entry()` currently infers `git_commit` and `git_dirty` from
the live worktree via subprocess. After the tuning commit and
before the results commit, the worktree is dirty with model
outputs, so the inferred state is misleading.

Fix: after the tuning commit, capture the SHA explicitly
(`git rev-parse HEAD`). Pass it to `build_run_entry()` — either as a
new optional parameter or by overwriting the `git_commit`/`git_dirty`
fields in the returned dict before saving. The report footer
(`_git_commit_display()`) uses the same tracker meta, so it will show
the correct provenance commit automatically.

### Phase 5 · README Rewrite

Lead with what makes this repo unique:
- An agent-maintained forecast repo that proposes its own model updates
- Explain the agentic pipeline: agents analyze trends, tune models, and
  forecast independently — each run creates a review branch
- The human reviews and merges; agents propose, they don't ship
- Show the pipeline flow diagram
- Document the skill convention and how to add skills
- Updated repo layout reflecting new structure
- Updated quick start with new CLI args
- Keep existing model and research documentation, reframed as components
  of the agent-maintained system

**Read the current README first.** The existing README is comprehensive
and well-structured. Most of it survives with targeted updates. Do not
start from scratch — edit what's there.

**Stale content to fix:**

- Intro paragraph: "using an ensemble of scripted forecasting models" is
  now incomplete. Agents are part of the pipeline.
- "LLM agent inference" paragraph under Forecast Sources: says "pipeline
  integration planned" — this is now complete. Rewrite to describe the
  implemented agent inference (what it does, that it's excluded from
  consensus, scored by retrospective). Also mention that agents now
  analyze trends and tune scripted models.
- Pipeline table: shows the old 8-step sequential flow. Replace with the
  new agentic flow (pre-flight → branch → insights → tuning → tuning
  commit → execute → blend → finalize → results commit). The ASCII
  diagram from the plan's Pipeline Flow section is a good starting point
  but should be adapted for README audience (less implementation detail,
  more "what happens and why").
- Quick Start: add `--agent`, `--skip-tuning`, `--skip-insights`,
  `--skip-agent-inference` flags. Note that `run_forecast.py` creates a
  review branch and commits automatically.
- Repo Layout: add `skills/` (with `trend_insights/` and
  `model_tuning/`), `feedcast/agent_runner.py`. Note
  `scripts/run_forecast.py` now owns argparse.
- Working with Agents: expand to cover the full pipeline integration
  (agents analyze trends, assess models, produce forecasts — all on a
  review branch). The current section only describes the workspace.
- Design Decisions: add rows for branch-per-run workflow, tuning commit
  provenance, agent steps skippable, and parallel tuning.

**New section needed:**

- "Working with Skills" — the skill convention (`skills/<name>/prompt.md`
  with `{{variable}}` placeholders), how to add a skill, reference to
  `feedcast/agent_runner.py` for invocation. Keep it short.

**Keep as-is (or light edits only):**

- Latest Forecast section and chart embed
- The Forecasting Challenge (feeds vs. episodes subsection)
- Event Construction
- Evaluation
- Replay And Tuning
- Working with Research
- Working with Models
- Principles

**Tone note:** The README currently mixes personal narrative (the intro
about the tired dad) with technical reference. Preserve both voices.
The personal context is part of what makes the repo distinctive.

### Phase 6 · Research Review Skill ✓

`skills/research_review/prompt.md`: assess scripted models against
cross-cutting research hub findings. Forward-looking framing consistent
with model tuning skill. Four-step workflow: read research hub, review
each model (parallel sub-agents recommended), flag cross-cutting
discrepancies to the user, summarize.

Manual invocation — designed for interactive Claude/Codex sessions, not
pipeline-integrated. The user tells the agent to follow the skill
prompt. Write scope: model directory only per model; cross-cutting
research modifications require human approval.

## Risks

| Risk | Mitigation |
| ---- | ---------- |
| Agent produces bad tuning | Branch-per-run isolates all changes; user reviews before merging |
| Agent forecast quality unknown | Excluded from consensus; retrospective tracks accuracy over time |
| Prompt engineering iteration needed | Skills are standalone `prompt.md` files — iterate freely |
| Claude/Codex CLI differences | Agent runner abstracts dispatch; test both early |
| Parallel tuning agents collide | Write scope hard-limited to `feedcast/models/<slug>/`; cross-cutting updates deferred to manual promotion |
| 4 parallel agent sessions are slow/expensive | User accepted; can `--skip-tuning` for fast runs |

## Open Questions

1. **Helper scripts in skills** — start without them. Add if prompt context
   becomes unwieldy (e.g., a script that summarizes model state into a
   compact briefing for the agent).

2. **Stale output cleanup** — delete only `forecast.json` before each agent
   inference invocation. `methodology.md` is a living document that the agent
   maintains across runs (like scripted models' `methodology.md` files) — it
   persists. Model tuning intermediates (replay results, etc.) are in
   `.replay-results/` which is gitignored — no cleanup needed.

3. **Agent prompt self-modification** — `agents/prompt.md` is the inference
   model's prompt. The agent could modify its own prompt across runs. Allow
   this: the branch workflow provides a review gate. If this causes drift,
   we can protect it later.

4. **Commit message content** — the two commits per run (tuning + results)
   should have informative messages. The pipeline can summarize what changed
   (which models were tuned, scores, etc.) or keep it simple. Start simple;
   enrich if useful.
