# Simplification Plan

## Goals

- Make the repo easy to navigate at first glance.
- Keep the primary outcome obvious: when the next feeds are.
- Use a single Python entrypoint under `scripts/`.
- Move reusable Python code into a `feedcast/` package.
- Keep agent workspaces and prompts easy to inspect and iterate on.
- Remove historical backtesting; rely on real retrospective results from prior runs.

## Decisions

- Keep repo-level `agents/` as the mutable agent area.
- Move Python library code under `feedcast/`.
- Use `scripts/run_forecast.py` as the thin CLI entrypoint.
- Delete `backtest.py` and all historical cutoff replay logic.
- Default the featured forecast to the consensus blend, with a static fallback list.
- Keep retrospective accuracy informational only; do not auto-switch the featured model.
- Save structured diagnostics as `report/diagnostics.yaml` because the user explicitly asked for YAML.
- Rename `summary.md` to `report.md`.

## Target Layout

```text
scripts/
  run_forecast.py
feedcast/
  __init__.py
  pipeline.py
  data.py
  tracker.py
  report.py
  plots.py
  agents.py
  templates/
    report.md.j2
  models/
    __init__.py
    shared.py
    recent_cadence.py
    phase_nowcast.py
    gap_conditional.py
agents/
  run.sh
  prompt/prompt.md
  claude/
  codex/
```

## Implementation Phases

1. Restructure code into `scripts/` and `feedcast/` without changing behavior.
2. Remove historical backtesting and simplify featured forecast selection.
3. Rework reporting:
   - write `report/report.md`
   - keep committed PNG charts
   - add `report/diagnostics.yaml`
   - remove inline diagnostics and HTML details blocks
4. Persist retrospective results into `tracker.json` and aggregate historical accuracy from stored retrospectives.
5. Make git metadata best-effort so the pipeline still runs outside a git checkout.
6. Add concise module-level docstrings/comments where the current files are not self-explanatory.
7. Verify scripted execution and report output before handing the code to Claude for review and docs work.

## Deliberate Non-Goals

- Do not add model auto-discovery or runtime plugin registration.
- Do not merge the scripted models.
- Do not auto-feature agents.
- Do not simplify away the atomic report swap.
