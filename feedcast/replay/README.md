# Replay

Replay is a local development tool for evaluating and tuning scripted models.
It rewinds the current export by exactly 24 hours, reruns a model from that
synthetic cutoff, and scores the forecast against the now-known actual bottle
feeds. Results are written to `.replay-results/` (gitignored).

This is a directional tool for recent-pattern fitting, not robust out-of-sample
validation. It complements model-local research, not replaces it.

## Usage

```bash
# Score one model against the latest observed 24 hours
.venv/bin/python scripts/run_replay.py score --model slot_drift

# Score with parameter overrides
.venv/bin/python scripts/run_replay.py score \
  --model slot_drift \
  --param LOOKBACK_DAYS=5

# Score the consensus blend
.venv/bin/python scripts/run_replay.py score --model consensus_blend

# Machine-readable JSON for agents or automation
.venv/bin/python scripts/run_replay.py score --model slot_drift --json
```

## Tuning

`tune` evaluates the cross-product of candidate parameter values against the
latest 24 hours and ranks results by headline score. Each `--param` flag adds
a candidate value. Repeat the same key with different values to sweep it.

```bash
# Sweep LOOKBACK_DAYS across three values
.venv/bin/python scripts/run_replay.py tune \
  --model slot_drift \
  --param LOOKBACK_DAYS=5 \
  --param LOOKBACK_DAYS=7 \
  --param LOOKBACK_DAYS=9

# Multi-parameter sweep (cross-product: 3 × 3 = 9 evaluations)
.venv/bin/python scripts/run_replay.py tune \
  --model slot_drift \
  --param LOOKBACK_DAYS=5 \
  --param LOOKBACK_DAYS=7 \
  --param LOOKBACK_DAYS=9 \
  --param MATCH_COST_THRESHOLD_HOURS=1.5 \
  --param MATCH_COST_THRESHOLD_HOURS=2.0 \
  --param MATCH_COST_THRESHOLD_HOURS=2.5

# JSON output for agents
.venv/bin/python scripts/run_replay.py tune \
  --model slot_drift \
  --param LOOKBACK_DAYS=5 \
  --param LOOKBACK_DAYS=7 \
  --json
```

Parameter values are parsed as int, float, JSON (for lists/dicts), or string.
For complex values, quote the JSON:

```bash
--param 'FEATURE_WEIGHTS=[1,1,1,1,2,2]'
```

## How it works

The harness temporarily overrides module-level constants in the model's
`model.py` for each evaluation, then restores the originals. This means
tuning works for any parameter defined as a module-level constant that the
model reads at runtime. No sidecar files or metadata declarations are needed.

The baseline is inferred by reading the model module's current constant
values for the parameter names being tuned.

## Python API

Agents and research scripts can import the replay harness directly:

```python
from feedcast.replay import score_model, tune_model

# Score with current constants
result = score_model("slot_drift")

# Score with overrides
result = score_model("slot_drift", overrides={"LOOKBACK_DAYS": 5})

# Sweep
result = tune_model(
    "slot_drift",
    candidates_by_name={
        "LOOKBACK_DAYS": [5, 7, 9],
        "DRIFT_WEIGHT_HALF_LIFE_DAYS": [2.0, 3.0, 4.0],
    },
)
```

## Where results go

Every run prints the saved artifact path:

```
Saved:    .replay-results/20260324-180501-slot_drift-tune.json
```

Results are JSON files in `.replay-results/`, gitignored by default. Promote
findings manually into CHANGELOG.md, design.md, or shared research when a
conclusion matters.
