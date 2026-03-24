# Model Implementation Handoff

## Context

Feedcast predicts a newborn's bottle-feed schedule. We are rebuilding the
scripted model lineup. The first model (Slot Drift) is implemented and
reviewed. Three more models are planned. Three legacy models remain
temporarily for consensus diversity and will be hard-cut once the new
lineup has at least 3 models.

## Current state

- **Branch:** `refine`
- **Slot Drift:** implemented, reviewed, merged. Lives at
  `feedcast/models/slot_drift/` with `model.py`, `methodology.md`,
  `design.md`, `research.py`, and `research_results.txt`.
- **Legacy models** (still active, to be deleted later):
  `recent_cadence.py`, `phase_nowcast.py`, `gap_conditional.py`
- **Model notes:** `feedcast/models/notes.md` contains the full model
  lineup, domain observations, working theory, and cross-cutting
  considerations. Read this file first.

## Remaining models (in implementation order)

1. **Analog Trajectory Retrieval** — instance-based ML. Find similar
   historical states and reuse their future trajectories.
2. **Latent Hunger State** — mechanistic. Hidden hunger rises over time,
   feeding pushes it down, bigger feeds push harder.
3. **Survival / Hazard** — probabilistic. Hazard function over elapsed
   time since last feed, modulated by volume.

## Implementation pattern (follow Slot Drift exactly)

Each model gets its own subdirectory under `feedcast/models/`:

```
feedcast/models/<model_slug>/
  __init__.py          Exports MODEL_METHODOLOGY, MODEL_NAME, MODEL_SLUG, forecast_fn
  model.py             Implementation + tuning constants (model-specific, NOT in shared.py)
  methodology.md       Report-ready methodology text (top-level content only, loaded by load_methodology())
  design.md            Design decisions with rationale
  research.py          Repeatable data analysis script
  research_results.txt Saved output from research.py (committed)
```

Key conventions:
- `methodology.md` top-level text (before any `##` heading) becomes
  the report methodology via `load_methodology(__file__)`.
- `research.py` must use the same data path as the pipeline:
  `load_export_snapshot()` for export selection, `snapshot.latest_activity_time`
  for cutoff, model constants imported from `model.py` (not duplicated).
  Save output to `research_results.txt` with provenance (export path,
  dataset fingerprint, cutoff, lookback, run timestamp).
- Tuning constants belong in `model.py`, not in `shared.py`.
- `shared.py` is utilities only (weighting, normalization, volume profiles,
  `load_methodology`, `ForecastUnavailable`).
- Register the model in `feedcast/models/__init__.py`: add imports, a
  `ModelSpec` entry to the `MODELS` list, and a slot in
  `STATIC_FEATURED_TIEBREAKER`.

## Process that worked well

1. **Research first.** Analyze the actual feeding data to inform design
   decisions. Document findings. Run the research script and commit the
   output.
2. **Implement.** Write the model, wire it into the registry, run the
   full pipeline (`--skip-agents`) to verify.
3. **Document.** Write methodology.md (report text), design.md
   (decisions + rationale), update README if needed.
4. **Review.** Codex does a thorough review. Expect 2-3 review rounds.
   Common findings: doc/code mismatches, stale hardcoded counts in docs,
   research script not fully reproducing the model's data path.

## User preferences

- **Yolo mode** for implementation: make decisions autonomously, document
  non-obvious ones.
- Data-driven research with repeatable scripts.
- Bottle-only events (no breastfeed merge) unless the model has a clear
  reason to use it.
- No arbitrary snack/full-feed thresholds unless the model benefits.
- The user values conceptual diversity between models. Each model should
  frame the problem differently, not just vary the math.
- Models should handle cluster feeding naturally through their mechanics,
  not via special-casing.
- Outlier handling is model-specific, not shared infrastructure.

## Data summary (as of March 23, 2026)

- 81 bottle events over 9 days (March 15-23).
- DATA_FLOOR: March 15. Baby born February 27 (24 days old at cutoff).
- March 15-16 were chaotic (11-13 feeds, many snacks). March 17+ settled
  to 8-10 feeds/day.
- Key domain observation: larger feeds tend to precede longer gaps.
- Daily feed count is stable; timing shifts gradually.
- No clear day/night pattern yet.

## Files to read before starting

1. `feedcast/models/notes.md` — domain knowledge + model lineup
2. `feedcast/models/slot_drift/model.py` — reference implementation
3. `feedcast/models/slot_drift/design.md` — reference design decisions
4. `feedcast/models/__init__.py` — model registry pattern
5. `feedcast/data.py` — data types (FeedEvent, Forecast, ForecastPoint)
