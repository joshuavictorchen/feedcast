# Replay

Replay is a local tuning tool for scripted models. It rewinds the current
export by 24 hours, reruns a model from that synthetic cutoff, and scores the
forecast against the now-known actuals — so you can test parameter changes
against real data before committing them.

Results are written to `.replay-results/` (gitignored).

## Usage

```bash
# Baseline score
.venv/bin/python scripts/run_replay.py slot_drift

# Score with a parameter override
.venv/bin/python scripts/run_replay.py slot_drift LOOKBACK_DAYS=5
```

## Tuning

Define candidate values in a YAML file and pass it as an argument:

```yaml
# sweep.yaml
LOOKBACK_DAYS: [5, 7, 9]
DRIFT_WEIGHT_HALF_LIFE_DAYS: [2.0, 3.0, 4.0]
MATCH_COST_THRESHOLD_HOURS: [1.5, 2.0, 2.5]
```

```bash
.venv/bin/python scripts/run_replay.py slot_drift sweep.yaml
```

Scalar values in YAML are treated as single overrides. List values define
sweep candidates.

Use `--json` when an agent needs the full artifact:

```bash
.venv/bin/python scripts/run_replay.py slot_drift sweep.yaml --json
```

For quick experiments, inline comma-separated values also work:

```bash
.venv/bin/python scripts/run_replay.py slot_drift LOOKBACK_DAYS=5,7,9
```

## How it works

The tool temporarily overrides module-level constants in the model's
`model.py` for each evaluation, then restores the originals. This works for
any parameter defined as a module constant that the model reads at runtime.
No sidecar files or metadata declarations are needed.

The baseline is inferred by reading the model module's current constant
values for the parameter names being tuned. Single value per param → score.
Any param with multiple values → sweep (cross-product evaluated, ranked by
headline score).

## Python API

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
