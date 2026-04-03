# Changelog

Tracks behavior-level changes to the agent inference model. Add newest entries first.

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
