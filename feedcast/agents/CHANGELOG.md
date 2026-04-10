# Changelog

Tracks behavior-level changes to the agent inference model. Add newest entries first.

## Initial forecasting model: Empirical Cadence Projection | 2026-04-09

### Problem

The agent workspace had no forecasting logic — only placeholder docs.
Agent inference was non-functional.

### Solution

Implemented `model.py`: a non-parametric forecasting script that
projects forward from recency-weighted inter-episode gap medians split
by day-part (overnight 19–07, daytime 07–19). Key features:
- 48h recency half-life (aggressive, tuned via multi-cutoff testing)
- Conditional survival estimate for the first predicted feed
- Count calibration against recent daily episode counts (30% threshold)
- CLI interface: `--export`, `--cutoff`, `--horizon` → writes `forecast.json`

Added `strategy.md` with approach documentation, performance baselines,
open questions, and guidance for future agents. Updated `methodology.md`
and `design.md` to reflect the actual implementation.

### Research

Tested against 3 available exports across 5 retrospective cutoff points.
Single retrospective: headline 67.8 (2nd, behind slot drift at 69.0;
best timing score of any model at 53.6). Multi-cutoff mean: 62.5
(4th; survival hazard leads at 71.1). Count accuracy (92.5) tied for
best; timing (43.9) is the main weakness, especially on evening cutoffs.

## Restructured to flat shared workspace | 2026-04-03

### Problem

The agent workspace was split into per-agent subdirectories (`claude/`,
`codex/`) with a shared prompt under `prompt/prompt.md` and a shell
dispatcher (`run.sh`). Only one agent runs per pipeline invocation, so
separate directories added complexity without isolation value. The
dispatcher duplicated invocation logic that now lives in
`feedcast/agent_runner.py`.

### Solution

Collapsed to a single flat workspace: `prompt.md`, `design.md`,
`methodology.md`, and `CHANGELOG.md` at the top level. `forecast.json`
is written here at runtime. The prompt now uses `{{var}}` placeholders
for runtime context substitution. Both agents share the workspace.
Pipeline integration is planned for Phase 4.
