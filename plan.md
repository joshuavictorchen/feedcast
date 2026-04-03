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
| 6 | **Provenance**: The tracker records the tuning commit (step 3), not the results commit. The tuning commit is the code state that generated the forecasts. The results commit just packages outputs and cannot be recorded because it doesn't exist yet when the tracker is written. |
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
| Tracker commit | Records the tuning commit (step 3) — the code state that generated the forecasts. The results commit packages outputs but is not itself provenance |
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
      → report/agent-insights.md

Step 2 · Model Assessment & Tuning              [agent ×4 parallel, skippable]
  └── For each scripted model:
        Agent + skills/model_tuning
        → may modify feedcast/models/<slug>/{model.py, CHANGELOG.md, ...}
        (writes scoped to model directory only)

Step 3 · Commit
  └── git commit --allow-empty (tuning changes + insights)

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
  ├── Render report (includes agent-insights + agent forecast)
  ├── Save tracker.json
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
invoke agents and by the standalone research review skill.

### Repo Layout (changes only)

```
scripts/
  run_forecast.py              UPDATED — new CLI args (--agent, --skip-tuning, etc.)
+ skills/                      NEW — agent skill definitions
+   trend_insights/
+     prompt.md
+   model_tuning/
+     prompt.md
+   research_review/           DEFERRED
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

### Phase 1 · Foundation

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

### Phase 2 · Agent Inference Restructuring

Can proceed in parallel with Phase 3.

**2.1 Flatten `feedcast/agents/`**
- Delete: `__init__.py`, `prompt/` directory, `claude/`, `codex/`
- Create: `prompt.md` (agent inference prompt), `design.md`, `methodology.md`
- Preserve or recreate `CHANGELOG.md`
- `forecast.json` written at runtime, deleted before each invocation

**2.2 `agents/prompt.md`** — initial agent inference prompt
- Task: produce a 24-hour feeding forecast from the CSV
- Required output: `forecast.json` with
  `{"feeds": [{"time": "ISO8601", "volume_oz": float}, ...]}`
- Update `methodology.md` if the approach changes (persistent living doc,
  like scripted models' methodology files — not regenerated each run)
- May reference `feedcast/research/`, model design docs
- May create/update `model.py`, notes, or other workspace artifacts
- Freeform: no prescribed approach

Context variables: `{{export_path}}`, `{{workspace_path}}`,
`{{cutoff_time}}`, `{{horizon_hours}}`

### Phase 3 · Skills

Can proceed in parallel with Phase 2.

**3.1 `skills/trend_insights/prompt.md`**

The agent reads feeding data, identifies recent trends, and writes a
concise summary.

Key prompt elements:
- Analyze the last 7–14 days of feeding history
- Report on: feed spacing trends (closer or further apart?), episode
  clustering changes, volume trends, day/night pattern shifts
- Any interesting or unique observations
- Output: 1–2 paragraphs, optional summary table
- Write to a staging path (pipeline captures content into memory after
  invocation; final `report/agent-insights.md` is published during the
  atomic report swap, not written to `report/` directly)
- Tone: concise, interesting, informative to a parent

Context variables: `{{export_path}}`, `{{baby_age_days}}`,
`{{cutoff_time}}`

**3.2 `skills/model_tuning/prompt.md`**

The agent assesses a single model's recent performance and decides whether
to tune its constants.

Key prompt elements:
- Read the model's `design.md`, `research.md`, `CHANGELOG.md`, `model.py`
- Review last retrospective scores (provided in context)
- Assess whether the baby's feeding patterns are shifting in ways the
  model's current constants don't reflect
- **Forward-looking framing**: anticipate where patterns are heading. The
  baby is growing — patterns shift week to week. Tuning is about adapting
  to emerging behavior, not minimizing historical error
- Available CLI tools:
  - Replay score: `.venv/bin/python scripts/run_replay.py <slug>`
  - Replay with overrides: `.venv/bin/python scripts/run_replay.py <slug> KEY=val`
  - Replay sweep: `.venv/bin/python scripts/run_replay.py <slug> KEY=v1,v2,v3`
  - Analysis: `.venv/bin/python -m feedcast.models.<slug>.analysis`
- **Write scope**: model directory only (`feedcast/models/<slug>/`).
  If tuning: update `model.py` constants, add `CHANGELOG.md` entry (what
  changed and why), update `research.md` if evidence changed.
  If declining: briefly note why no changes are needed
- Cross-cutting insights noted in the model's own `research.md` for the
  user to promote to `feedcast/research/` later

Context variables: `{{model_slug}}`, `{{model_dir}}`, `{{export_path}}`,
`{{last_retro_scores}}`, `{{research_hub_path}}`

### Phase 4 · Pipeline Orchestration

Depends on Phases 1–3.

**4.1 Rewrite `feedcast/pipeline.py`**

New `main()` signature:
```python
def main(
    export_path: str | None = None,
    agent: str = "claude",
    skip_tuning: bool = False,
    skip_insights: bool = False,
    skip_agent_inference: bool = False,
) -> None:
```

Sequence (references to pipeline flow above):
1. Pre-flight: git-clean check, resolve export, parse CSV → snapshot
2. `git checkout -b feedcast/{timestamp}` (all mutations on new branch from here)
3. Step 1: `invoke_agent(agent, skills/trend_insights, ...)` unless skipped
4. Step 2: `ThreadPoolExecutor` → `invoke_agent(agent, skills/model_tuning, ...)`
   × 4 scripted models, unless skipped
5. Step 3: `git add -A` → `git commit --allow-empty` (tuning + insights)
6. Step 4: `ThreadPoolExecutor` → run scripted models (existing `run_all_models`)
   + invoke agent inference (unless skipped), in parallel
7. Parse agent `forecast.json` via `validate_agent_forecast()` if produced
8. Consensus blend + featured selection (existing logic)
9. Retrospective scoring (existing logic)
10. Historical accuracy aggregation (existing logic)
11. Render report — pass `agent_insights` content + agent `Forecast` to renderer
12. Save tracker
13. `git add -A` → `git commit` (results)

Key reuse: `run_all_models()`, `select_featured_forecast()`, scoring,
retrospective, tracker, and report rendering logic all survive. The pipeline refactor is
about orchestration and sequencing, not reimplementing model execution or
scoring.

**4.2 Update `scripts/run_forecast.py`**
- Add `--agent {claude,codex}` (default: `claude`)
- Add `--skip-tuning`
- Add `--skip-insights`
- Add `--skip-agent-inference`
- Remove old `--skip-agents` from `feedcast/pipeline.py` (clean break; no external consumers)

**4.3 Update report template**
- `feedcast/templates/report.md.j2`: conditional `{% if agent_insights %}`
  section near top, before the forecast table
- `feedcast/report.py`: accept `agent_insights: str | None` from the pipeline
  and pass it to the template context. The pipeline reads the file content
  after the trend_insights agent writes it (or passes `None` if skipped).
  `report/agent-insights.md` is written as an output artifact alongside
  the report — the renderer never discovers it from disk

**4.4 Agent forecast integration**
- Agent forecast parsed into a `Forecast` object (same type as scripted models)
- Included in report's per-model methodology sections and spaghetti chart
- Excluded from consensus blend (enforced in `run_all_models` / blend logic)
- Tracked in `tracker.json` alongside scripted model predictions

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

### Phase 6 · Research Review Skill (deferred)

- `skills/research_review/prompt.md`: assess a model against latest research
  hub findings, propose changes if warranted, same forward-looking framing
- Runs in parallel across models (or a specified subset)
- Manual invocation mechanism TBD when this phase is built

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
