# Agent inference

The agent inference workspace is the persistent home of Feedcast's LLM forecaster. On each run, a CLI agent (Claude or Codex) reads this directory, produces an independent forecast from the raw export, and can rewrite any file here when it wants to change approach. The workspace survives across runs, so prior agents' strategy notes, helper code, and tuning history accumulate in place.

The agent has full read access to the repository and may consult cross-cutting research or model code when deciding how to forecast. Its forecast is excluded from the consensus blend and scored by the same retrospective evaluation as every scripted model. See the top-level [`README.md`](../../README.md) for where this step fits in the pipeline.

Runtime instructions for the agent live in [`prompt.md`](prompt.md). That file is authoritative: it scopes the agent's freedom and boundaries. The workspace is the agent's; the rest of the repo is read-only reference material.

## What's currently in the workspace

  These are the files currently in the workspace and how they are used today. Future agents may reshape the approach or rewrite most of this workspace (and even this file). Under the current pipeline, `prompt.md` and `methodology.md` are expected inputs, and each run must produce `forecast.json`.

| File | Current use |
| ---- | ----------- |
| `model.py` | The current forecasting script. Prior agents have rewritten it when they had a better approach. |
| `prompt.md` | Runtime instructions the agent reads on each run. Uses `{{variable}}` placeholders substituted by the pipeline. The agent may edit this to change how future runs invoke it. |
| `strategy.md` | Durable notes from prior agents: approach, measured strengths and weaknesses, open questions. |
| `methodology.md` | Report-facing description. Content before the first `##` heading is rendered into the forecast report. |
| `design.md` | Design decisions behind the current implementation. |
| `CHANGELOG.md` | Prior agents' log of what changed and why. |
| `forecast.json` | The most recent forecast output. Overwritten on each run and read by the pipeline. |

## Current implementation

Empirical Cadence Projection: recency-weighted day-part gap medians with conditional survival for the first feed. See [`strategy.md`](strategy.md) for the approach notes and [`design.md`](design.md) for the rationale. A future agent may keep it, tune its constants, or replace it entirely.

## How the pipeline invokes the agent

The pipeline calls the agent via [`feedcast/agent_runner.py`](../agent_runner.py). See [`feedcast/pipeline.py`](../pipeline.py) for how the step is wired into a run.
