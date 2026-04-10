# Skills

Skills are reusable task instructions for CLI agents (Claude or Codex). They describe generic jobs like "analyze these trends" or "tune this model." The agent inference workspace in [`feedcast/agents/`](../feedcast/agents/) is a separate concept: a persistent forecasting model that produces its own forecast.

Some skills are invoked by the pipeline automatically. Others are designed for manual use in an interactive agent session.

## Current skills

| Skill | Purpose | Invocation |
| ----- | ------- | ---------- |
| [`trend_insights/`](trend_insights/) | Analyze a 7-day baseline, then zoom in on the newest data to write a parent-facing summary for the forecast report. | Pipeline (per run) |
| [`model_tuning/`](model_tuning/) | Assess one scripted model's fit to current patterns and tune its constants when warranted. | Pipeline (one agent per base model, parallel) |
| [`research_review/`](research_review/) | Review scripted models against the latest findings in the research hub and propose changes where evidence warrants. | Manual |

## Directory convention

Each skill lives in its own directory under `skills/`:

| File | Purpose |
| ---- | ------- |
| `prompt.md` | Agent instructions with `{{variable}}` placeholders for runtime context. |
| `*.py`, `*.sh` | Optional helper scripts the agent can invoke during the task. |

Pipeline-integrated skills are read by [`feedcast/pipeline.py`](../feedcast/pipeline.py), which substitutes context variables and passes the rendered prompt to the agent CLI via [`feedcast/agent_runner.py`](../feedcast/agent_runner.py). Manual skills are read directly by an interactive agent session.

## Add a new skill

1. Create a new directory under `skills/`.
2. Write `prompt.md` with the agent task instructions.
3. For pipeline-integrated skills, use `{{variable_name}}` placeholders and wire the invocation into `feedcast/pipeline.py`.
4. For manual skills, write self-contained instructions that a human can pass to an interactive agent session.
