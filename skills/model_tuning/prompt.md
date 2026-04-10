# Model Tuning

Model: {{model_slug}}
Model directory: `{{model_dir}}`
Export CSV: `{{export_path}}`
Research hub: `{{research_hub_path}}`

Last retrospective scores:
{{last_retro_scores}}

---

Assess the {{model_slug}} model's recent performance and decide whether
to tune its constants. You are looking at one scripted forecasting model
in a baby bottle-feed prediction pipeline.

## Runtime Budget

You are running under an external hard timeout.

- Hard timeout: {{hard_timeout_minutes}} minutes ({{hard_timeout_seconds}} seconds)
- Conservative target: finish within {{target_runtime_minutes}} minutes ({{target_runtime_seconds}} seconds)
- Started at: {{runtime_start_time}}
- Hard deadline: {{runtime_deadline}}

This prompt does not provide a live timer. If you need to check elapsed
wall-clock time, do it explicitly.

## Step 1: Understand the model

Read these files in the model directory (`{{model_dir}}`):

- `design.md` — why the model works the way it does
- `research.md` — current evidence, canonical evaluation results, open
  questions
- `CHANGELOG.md` — recent behavior changes
- `model.py` — implementation and current constants

Also read the research hub (`{{research_hub_path}}`) for cross-cutting
findings that may affect this model.

## Step 2: Assess recent performance

The retrospective scores above show how the model's last prediction
compared to what actually happened. Consider:

- Is the model tracking the baby's current feeding pattern well?
- Are there systematic errors (consistently early/late, wrong count)?
- Have the baby's patterns shifted since the model was last tuned?

**Forward-looking framing**: The baby is growing. Patterns shift week to
week. Your job is not to minimize historical error — it is to anticipate
where the baby's feeding patterns are heading and ensure the model's
constants reflect emerging behavior, not stale history. Canonical replay
is the production authority for evaluating changes, but trend direction
matters more than point estimates of past fit.

## Step 3: Decide whether to tune

If the model's constants are well-matched to current and emerging
patterns, say so briefly and stop. Not every model needs tuning every
run. Declining is a valid and often correct outcome.

If you see evidence that constants should change, proceed to step 4.

## Step 4: Tune (if appropriate)

You have these CLI tools available:

```bash
# Score with production constants
.venv/bin/python scripts/run_replay.py {{model_slug}}

# Score with overrides
.venv/bin/python scripts/run_replay.py {{model_slug}} KEY=value

# Sweep multiple values
.venv/bin/python scripts/run_replay.py {{model_slug}} KEY=v1,v2,v3

# Run the model's own analysis
.venv/bin/python -m feedcast.models.{{model_slug}}.analysis
```

Use replay to test candidate constant values against recent history.
Compare the headline score (geometric mean of weighted count F1 and
weighted timing score) across candidates. But remember: replay measures
historical fit. A constant change that scores slightly lower on replay
but better reflects an emerging trend may still be the right call. Use
replay as evidence, not as the sole decision-maker.

## Write scope

You may only modify tracked files inside `{{model_dir}}`. Do not touch
tracked files outside the model directory. Gitignored outputs from CLI
tools (e.g., `.replay-results/`) are fine — they are not committed.

If you tune constants:

- Update `model.py` with the new values
- Re-run `.venv/bin/python -m feedcast.models.{{model_slug}}.analysis`
  after the final constant values are in place so committed artifacts
  match the shipped model
- Add a `CHANGELOG.md` entry: what changed, why, and the evidence
  (replay scores, trend observations). Use the existing entry format.
- Update `research.md` so its conclusions, last-run metadata, and cited
  artifacts match the shipped model

If committed files under `artifacts/` changed, `research.md` must change
with them. Do not leave artifact-only diffs behind.

If you observe cross-cutting insights (findings that affect other models
or the research hub), note them in this model's `research.md` under an
appropriate section. The user will promote cross-cutting findings to
`feedcast/research/` manually.

Before you finish, run this consistency check and fix any failures:

```bash
.venv/bin/python -m feedcast.research.consistency {{model_dir}}
```

If you decline to tune, briefly note why in your response. No file
changes are needed.
