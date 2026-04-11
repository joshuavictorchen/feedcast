# Replay

Replay is the model evaluation and tuning tool for Feedcast. It rewinds
the current export to multiple retrospective cutoff points, reruns a
model at each cutoff, scores each forecast against the now-known actuals,
and aggregates the results with recency weighting.

Use replay to answer: "How well does this model forecast across recent
history?" and "Would different constants improve it?"

Results are written to `.replay-results/` (gitignored).

## What replay does

1. Loads the latest (or specified) export.
2. Generates retrospective cutoff points within a lookback window.
3. At each cutoff, runs the model as if the cutoff were the current time.
4. Scores each forecast against the actuals now visible in the export.
5. Aggregates per-window scores with exponential recency weighting.

Replay builds on the shared evaluation infrastructure in
`feedcast/evaluation/`. Single-window scoring is handled by
`score_forecast()` in `scoring.py`; multi-window aggregation is handled
by `evaluate_multi_window()` in `windows.py`. Replay adds model
execution, parameter override machinery, and artifact persistence on
top. See
[`feedcast/evaluation/README.md`](../evaluation/README.md)
for the full scoring design.

## Input policy

Replay uses **bottle-only feed events** for scoring
(`build_feed_events(activities, merge_window_minutes=None)`). This is
the canonical evaluation input — the same ground truth stream the
tracker and model research scripts score against. (Cross-cutting
research articles may use different event streams depending on
their analysis needs.)

Individual models may build their own input events differently. Some
merge nearby breastfeed volume into bottle feeds to inform their
predictions. That choice affects what the model sees as input but does
not affect what it is scored against. Replay always scores against
bottle-only actuals.

## CLI usage

```bash
# Score with production constants (defaults: episode cutoffs, 96h lookback)
.venv/bin/python scripts/run_replay.py slot_drift

# Score with parameter overrides
.venv/bin/python scripts/run_replay.py slot_drift LOOKBACK_DAYS=5

# Score with custom lookback and fixed-step cutoffs
.venv/bin/python scripts/run_replay.py slot_drift --lookback 48 --cutoff-mode fixed

# JSON output (for agents or programmatic consumption)
.venv/bin/python scripts/run_replay.py slot_drift --json
```

## Tuning

Define candidate values in a YAML file and pass it as an argument.
Replay evaluates the full cross-product and ranks candidates.

```yaml
# sweep.yaml
LOOKBACK_DAYS: [5, 7, 9]
DRIFT_WEIGHT_HALF_LIFE_DAYS: [1.0, 2.0, 3.0]
MATCH_COST_THRESHOLD_HOURS: [1.5, 2.0, 2.5]
```

```bash
.venv/bin/python scripts/run_replay.py slot_drift sweep.yaml
```

Scalar YAML values are treated as single overrides (score mode). List
values define sweep candidates (tune mode).

For quick experiments, inline comma-separated values also work:

```bash
.venv/bin/python scripts/run_replay.py slot_drift LOOKBACK_DAYS=5,7,9
```

Array-valued constants can be passed as JSON:

```bash
.venv/bin/python scripts/run_replay.py slot_drift 'FEATURE_WEIGHTS=[1,1,2,2]'
```

### Candidate-parallel tuning

For large sweeps, use process isolation to evaluate candidates
concurrently:

```bash
.venv/bin/python scripts/run_replay.py slot_drift sweep.yaml \
    --parallel-candidates --candidate-workers 4
```

Each worker process initializes its own export snapshot and model state.
Module-level constant overrides are process-local, so concurrent
candidates do not interfere with each other. Worker-local window
parallelism is disabled in this mode to avoid nested oversubscription.

Candidate-parallel tuning uses `ProcessPoolExecutor` with the `spawn`
multiprocessing context. This means the worker entrypoint must be
importable — prefer running sweeps from file-backed entrypoints
(`scripts/run_replay.py`, model `analysis.py`) over ad hoc stdin
snippets.

## Python API

```python
from feedcast.replay import score_model, tune_model

# Score with current production constants
result = score_model("slot_drift")

# Score with overrides
result = score_model("slot_drift", overrides={"LOOKBACK_DAYS": 5})

# Tune: sweep candidates, ranked by canonical score
result = tune_model(
    "slot_drift",
    candidates_by_name={
        "LOOKBACK_DAYS": [5, 7, 9],
        "DRIFT_WEIGHT_HALF_LIFE_DAYS": [1.0, 2.0, 3.0],
    },
)

# Tune with candidate parallelism
result = tune_model(
    "slot_drift",
    candidates_by_name={...},
    parallel_candidates=True,
    candidate_workers=4,
)
```

## Parameters

| Parameter | CLI flag | Default | Purpose |
| --------- | -------- | ------- | ------- |
| `lookback_hours` | `--lookback` | 96 | How far back to generate cutoff points (hours) |
| `half_life_hours` | `--half-life` | 36 | Recency decay half-life for window weighting (hours) |
| `cutoff_mode` | `--cutoff-mode` | `episode` | `episode` places cutoffs at feeding episode boundaries; `fixed` uses regular intervals |
| `step_hours` | `--step-hours` | 12 | Step size for fixed-interval cutoffs (hours); ignored in episode mode |
| `parallel` | `--parallel` | off | Thread-level parallelism across windows within one candidate |
| `parallel_candidates` | `--parallel-candidates` | off | Process-level parallelism across candidates (tune mode) |
| `candidate_workers` | `--candidate-workers` | auto | Worker process count for candidate parallelism |
| `export_path` | `--export-path` | latest | Explicit export CSV path |
| `output_dir` | `--output-dir` | `.replay-results/` | Where JSON artifacts are written |

## Interpreting results

### Score mode

A score result contains:

- **Aggregate scores**: Headline (geometric mean of count and timing),
  count score, and timing score — all weighted means across scored
  windows.
- **Window count**: Total windows attempted vs. scored. If a model
  cannot forecast from an older cutoff (insufficient history), that
  window is excluded from the aggregate but counted for availability.
- **Per-window breakdown**: Each window's cutoff, score, weight, and
  status (scored / unavailable / error).

### Tune mode

A tune result adds:

- **Baseline**: The current production constants evaluated across all
  windows.
- **Best candidate**: The highest-ranked candidate by the tuning
  comparator.
- **Candidates list**: All evaluated candidates with their scores.
- **Deltas**: `headline_delta` and `availability_delta` between best
  and baseline. When baseline wins, both are 0.

### Ranking

Candidates are ranked in two stages:

1. **Availability tier** (descending `scored_window_count`): A candidate
   that scores well on 20 windows beats one that scores slightly better
   on 18 windows. Reducing availability is a disqualifying weakness.
2. **Headline score** (descending) within the same availability tier.

The baseline competes under the same comparator but is reported
separately — it does not appear in the candidates list.

### What a good score looks like

Scores are 0-100. Current production models score in the 65-75 headline
range. Count scores are uniformly strong (90-95); timing scores are the
bottleneck (48-57). A headline improvement of +1 or more is meaningful.
Perfect scores are not expected — unobserved variables (sleep state,
growth spurts) set a ceiling on what any model can explain from feeding
history alone.

## Where results go

Every run prints the saved artifact path:

```
Saved:    .replay-results/20260327-180501-slot_drift-tune.json
```

Results are JSON files in `.replay-results/`, gitignored by default.
Promote findings into `CHANGELOG.md`, `research.md`, or the research
hub when a conclusion matters.

## The research-tuning-production pipeline

Replay is for evidence, not automation. The pipeline:

1. **`analysis.py` produces evidence.** Model research scripts call
   `score_model()` and `tune_model()` to generate canonical scores and
   parameter recommendations. The scripts themselves do not modify
   production behavior — they write to `artifacts/`, not to `model.py`.
2. **`tune_model()` evaluates alternatives but does not apply them.**
   `override_constants()` is temporary and restored after evaluation.
   The output is a ranked list.
3. **Constants live in `model.py`.** Each model's tunable parameters
   are module-level variables. This is the single source of truth for
   production behavior.
4. **The decision to change a constant is intentional.** A human or
   agent reviews the evidence and updates `model.py` with a
   `CHANGELOG.md` entry explaining what changed and why.

Research is advisory. A better canonical score warrants a constant
update, but the update itself is a deliberate step, not an automated
side effect.
