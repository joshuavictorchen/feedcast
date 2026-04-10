# Models

Scripted forecasting models. Each model lives in its own subdirectory with a standard set of files, a `CHANGELOG.md` tracking behavior changes, and a `research.md` documenting canonical evaluation evidence.

For the model lineup and hypothesis-level descriptions, see the top-level [`README.md`](../../README.md#model-lineup).

## Directory convention

| File | Purpose |
| ---- | ------- |
| `model.py` | Implementation. Exports `MODEL_NAME`, `MODEL_SLUG`, `MODEL_METHODOLOGY`, and a forecast function with signature `(activities, cutoff, horizon_hours) -> Forecast`. Each model receives raw `list[Activity]` and builds its own events locally (breastfeed merge policy, episode collapsing, cutoff filtering). Tuning constants live here. |
| `CHANGELOG.md` | Reverse-chronological behavior log. One-line summary with date, `Problem` / `Solution` sections, optional `Research` section. Update it whenever the model's behavior, assumptions, or tuning changes. |
| `methodology.md` | Report-facing text. Content before the first `##` heading is loaded by `load_methodology()` and rendered into the forecast report. |
| `design.md` | Design decisions and rationale. Documents why the model works the way it does. |
| `analysis.py` | Repeatable data analysis. Run with `.venv/bin/python -m feedcast.models.<slug>.analysis`. Shares the same export selection, core data parsing, and production constants as the model. |
| `research.md` | Evidence document. Current support and challenges for the model's design and constants. Standard template: overview, last canonical run box, methods (canonical and diagnostic), results (canonical and diagnostic), conclusions with disposition, labeled open questions (model-local and cross-cutting). |
| `artifacts/` | Committed outputs (`research_results.txt` and any other generated files) referenced by `research.md`. |

## Update an existing model

When a model's behavior, assumptions, or tuning changes:

1. Update `model.py` with the change.
2. Re-run `.venv/bin/python -m feedcast.models.<slug>.analysis` so committed artifacts match the shipped model.
3. Add a new top entry to `CHANGELOG.md` with Problem / Research / Solution and numeric deltas.
4. Update `research.md` so its conclusions, last-run metadata, and cited artifacts match the shipped model.
5. Update `design.md` if core assumptions have shifted.
6. Update `methodology.md` if the report-facing approach description has changed.

If a model change surfaces cross-cutting evidence (findings that affect other models or shared hypotheses), record it in that model's `research.md` under an appropriate section. Promotion into [`feedcast/research/`](../research/) is a deliberate manual step, kept separate from routine model tuning so shared conclusions do not drift on every run.

## Add a new model

1. Create a subdirectory `feedcast/models/<slug>/` with the files above. Use `slot_drift/` or `analog_trajectory/` as reference implementations.
2. Add a `ModelSpec` entry to the `MODELS` list in [`__init__.py`](__init__.py).
3. Populate `research.md` with initial canonical evaluation results.

## Remove a model

1. Delete its `ModelSpec` from the `MODELS` list in [`__init__.py`](__init__.py).
2. Optionally delete the directory.

## Tune parameters

Keep model-specific constants in the model file that uses them. Reserve `feedcast/models/shared.py` for utilities that multiple models use.

To replay a model across retrospective windows with its current constants:

```bash
.venv/bin/python scripts/run_replay.py <slug>
```

To run a parameter sweep:

```bash
# Inline overrides
.venv/bin/python scripts/run_replay.py <slug> LOOKBACK_DAYS=5,7,9

# Sweep file with candidate-parallel execution
.venv/bin/python scripts/run_replay.py <slug> sweep.yaml --parallel-candidates
```

See [`feedcast/replay/README.md`](../replay/README.md) for full usage and the Python API.

## Change the featured forecast

Set `FEATURED_DEFAULT` in [`__init__.py`](__init__.py) to any available model slug. The default is the consensus blend.
