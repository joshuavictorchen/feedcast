# Plan: Multi-Window Evaluation and Replay Overhaul

## Motivation

The project predicts the next 24 hours of baby feeds. Evaluation scores
predictions against actuals using `score_forecast()` (episode-matched,
horizon-weighted, geometric mean of count accuracy and timing credit).

Two problems:

1. **Replay uses a single 24h window.** Tuning on one window risks
   overfitting to recent outliers. A multi-window approach with recency
   weighting evaluates across multiple scenarios, producing more robust
   parameter recommendations.

2. **Research scripts are fragmented.** Three of four model research scripts
   implement their own inline evaluation (MAE-based metrics on individual
   gaps) rather than using the canonical `score_forecast()` metric. Models
   are tuned on a different metric than they are judged by. The fourth
   (slot_drift) does no predictive evaluation at all. Only
   consensus_blend/research.py calls `score_forecast()`, and it reimplements
   its own multi-cutoff logic rather than sharing infrastructure with replay.

## Design Decisions

These decisions were reached through discussion and should not be revisited
during implementation unless a technical blocker is found.

### Multi-window evaluation

- **Window generation:** Each evaluation window is a 24h horizon starting
  from a cutoff point. Cutoff points are placed at episode boundaries
  (the start time of each feeding episode) within the lookback range.
  A fixed-step mode (configurable step size) is available as a fallback
  if episode-boundary mode is too expensive for sweeps.

- **Lookback range:** Default 72 hours. Configurable via `lookback_hours`
  parameter.

- **Clamp mode:** Default **soft** (exponential decay handles relevance;
  `lookback_hours` is a practical search limit, not a weight cutoff).
  **Hard** mode available (strict zero beyond `lookback_hours`). Controlled
  via a `hard_clamp: bool = False` parameter.

- **Recency weighting:** Exponential decay. Default half-life 36 hours.
  Weight formula: `2^(-age_hours / half_life_hours)` where `age_hours` is
  the distance from a cutoff to the most recent cutoff. The most recent
  window always has weight 1.0.

- **Aggregate score:** Weighted mean of per-window headline scores. Per-window
  breakdowns are preserved in results for diagnostics.

### Tracker stays single-window

The tracker (`feedcast/tracker.py`) measures realized production accuracy:
"you predicted 24h, here is what happened." This is inherently one window
and should not adopt multi-window evaluation. Document this distinction
explicitly: tracker measures *realized accuracy*, replay/research measure
*estimated capability across scenarios*.

### Model independence vs standardization

Two layers:

1. **Mandatory canonical layer:** Every model's `research.py` must include
   a canonical evaluation section that scores the model via multi-window
   `score_forecast()`. This is how models are compared. Parameter selection
   should be driven by this metric.

2. **Optional internal layer:** Models may use whatever internal metrics
   help them understand their own mechanics (gap MAE, walk-forward
   simulation, alignment analysis, etc.). These are diagnostic tools, not
   tuning objectives.

### Parallelization

Optional `parallel: bool = False` flag on the multi-window evaluator.
Parallelizes across windows within a single candidate evaluation using
`concurrent.futures.ThreadPoolExecutor`. Safe because all windows share the
same model constants (no shared-state mutation). Cross-candidate parallelism
(for sweeps) is out of scope — it conflicts with `override_constants`
module-level mutation.

## File Layout

```
feedcast/evaluation/
    scoring.py          # existing - score_forecast() unchanged
    windows.py          # NEW - multi-window generation, weighting, aggregation
    methodology.md      # existing - update with multi-window rationale
    README.md           # existing - update as agent-usable methodology guide

feedcast/replay/
    runner.py           # MODIFY - adopt multi-window evaluation
    results.py          # existing - extend result schema for per-window data
    README.md           # existing - rewrite as agent-usable guide

feedcast/models/<each>/
    research.py         # MODIFY - add canonical evaluation section
```

## Phase 1: Shared Infrastructure

Create `feedcast/evaluation/windows.py` with the multi-window evaluation
primitives.

### Functions to implement

```python
def recency_weight(age_hours: float, half_life_hours: float) -> float:
    """Exponential decay weight. age=0 returns 1.0."""

def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    """Weighted arithmetic mean."""

def generate_episode_boundary_cutoffs(
    activities: Sequence[Activity],
    latest_activity_time: datetime,
    lookback_hours: float = 72.0,
    hard_clamp: bool = False,
) -> list[datetime]:
    """Generate cutoff points at episode boundaries within the lookback range.

    Each cutoff is the start time of a feeding episode, placed so that
    the 24h evaluation window following it falls entirely within observed
    data. The most recent valid cutoff is latest_activity_time - 24h
    (the current replay-equivalent window). The oldest valid cutoff is
    latest_activity_time - lookback_hours.

    Returns cutoffs sorted chronologically (oldest first).
    """

def generate_fixed_step_cutoffs(
    latest_activity_time: datetime,
    earliest_activity_time: datetime,
    lookback_hours: float = 72.0,
    step_hours: float = 12.0,
    hard_clamp: bool = False,
) -> list[datetime]:
    """Generate cutoffs at fixed intervals. Fallback for expensive sweeps."""

def evaluate_multi_window(
    forecast_fn: Callable[[datetime], Forecast],
    scoring_events: Sequence[FeedEvent],
    cutoffs: Sequence[datetime],
    latest_activity_time: datetime,
    half_life_hours: float = 36.0,
) -> MultiWindowResult:
    """Evaluate a model across multiple windows and return weighted aggregate.

    Args:
        forecast_fn: Callable that takes a cutoff datetime and returns a
            Forecast. The caller is responsible for binding model execution
            (replay binds via _run_forecast; research scripts bind their
            own logic).
        scoring_events: Bottle-only feed events for the full export
            (scorer filters to each window internally).
        cutoffs: Pre-generated cutoff points (from generate_*_cutoffs).
        latest_activity_time: Upper bound of observed data.
        half_life_hours: Recency decay half-life.

    Returns:
        MultiWindowResult with aggregate score and per-window breakdowns.
    """
```

### Data classes to define

```python
@dataclass
class WindowResult:
    cutoff: datetime
    observed_until: datetime
    weight: float
    score: ForecastScore | None  # None if model unavailable at this cutoff
    status: str  # "scored", "unavailable", "error"
    error_message: str | None

@dataclass
class MultiWindowResult:
    headline_score: float          # Weighted mean of per-window headlines
    count_score: float             # Weighted mean of per-window count scores
    timing_score: float            # Weighted mean of per-window timing scores
    window_count: int              # Number of windows evaluated
    scored_window_count: int       # Number that produced a score
    half_life_hours: float
    per_window: list[WindowResult] # Full per-window breakdown
```

### Extraction from consensus_blend/research.py

`_recency_weight()` (line 516) and `_weighted_mean()` (line 522) move to
`windows.py`. Update consensus_blend/research.py to import from the shared
module instead.

### Tests

Add `tests/test_windows.py`:
- Recency weight: known values (age=0 returns 1.0, age=half_life returns 0.5)
- Window generation: episode boundaries fall within expected range, are sorted,
  respect lookback and clamp settings
- Multi-window aggregation: weighted mean matches hand-calculated values
- Edge case: export with fewer than 24h of data raises clear error

## Phase 2: Replay Adopts Multi-Window

Modify `feedcast/replay/runner.py` to use multi-window evaluation.

### Changes to `score_model()`

Replace single-window `_latest_replay_window()` call with:
1. Generate cutoffs via `generate_episode_boundary_cutoffs()`
2. Build a `forecast_fn` closure that calls `_run_forecast()` for a given cutoff
3. Call `evaluate_multi_window()` to get aggregate + per-window results
4. Include both aggregate and per-window data in the result payload

The function signature gains optional parameters with defaults:
```python
def score_model(
    model_slug: str,
    *,
    overrides: dict[str, Any] | None = None,
    export_path: Path | None = None,
    output_dir: Path = DEFAULT_RESULTS_DIR,
    lookback_hours: float = 72.0,
    half_life_hours: float = 36.0,
    hard_clamp: bool = False,
    cutoff_mode: str = "episode",  # "episode" or "fixed"
    step_hours: float = 12.0,      # only used when cutoff_mode="fixed"
    parallel: bool = False,
) -> dict[str, Any]:
```

### Changes to `tune_model()`

Same multi-window adoption. Each candidate configuration is evaluated across
all windows. The ranking uses the weighted aggregate headline score.

### Changes to result schema

The `replay_window` field in result payloads becomes `replay_windows`:
```json
{
    "replay_windows": {
        "lookback_hours": 72.0,
        "half_life_hours": 36.0,
        "hard_clamp": false,
        "cutoff_mode": "episode",
        "window_count": 21,
        "scored_window_count": 21,
        "aggregate": { "headline": 72.3, "count": 78.1, "timing": 66.9 },
        "per_window": [ ... ]
    }
}
```

Update `results.py` accordingly. The `validation` field changes from
`"latest_24h_directional_replay_only"` to
`"multi_window_directional_replay"`.

### Remove `_latest_replay_window()`

This function is replaced by the window generation functions in
`evaluation/windows.py`. Delete it.

### Parallelization

Add optional thread-based parallelism to `evaluate_multi_window()` in
`windows.py`. When `parallel=True`, evaluate windows concurrently using
`ThreadPoolExecutor`. The `parallel` flag threads through from
`score_model()` and `tune_model()`.

### Tests

Update `tests/test_replay.py`:
- Existing tests should continue to pass (same behavior, multiple windows)
- Add test for multi-window result structure
- Add test for `lookback_hours` and `half_life_hours` parameter passthrough

### CLI

Update `scripts/run_replay.py` to accept optional `--lookback`, `--half-life`,
`--hard-clamp`, `--cutoff-mode`, `--step-hours`, and `--parallel` flags.
Defaults match the function defaults.

## Phase 3: Research Scripts Adopt Canonical Scoring

Every model's `research.py` gains a canonical evaluation section and imports
from the shared multi-window infrastructure.

### All models: add canonical evaluation section

Each research script adds a section (called from `main()`) that:
1. Generates multi-window cutoffs via `generate_episode_boundary_cutoffs()`
2. Runs the model at each cutoff with current production constants
3. Scores via `evaluate_multi_window()`
4. Reports the aggregate headline score and per-window breakdown

This section should be clearly labeled (e.g., "CANONICAL MULTI-WINDOW
EVALUATION") and appear prominently in the output.

### latent_hunger and survival_hazard: switch tuning metric

These two scripts currently select parameters by minimizing `gap1_mae`.
Change the parameter selection logic to:

1. For each candidate parameter set, run `evaluate_multi_window()` using the
   canonical `score_forecast()` metric (via the shared infrastructure).
2. Rank candidates by weighted aggregate headline score (maximize, not
   minimize — this is a score, not an error).
3. Keep the existing walk-forward / `gap1_mae` analysis as a diagnostic
   section that helps explain *why* a parameter set performs well or poorly.
4. Update findings.md and CHANGELOG.md to document the metric change.

The internal evaluation functions (`_evaluate_multiplicative()`,
`_evaluate_additive()`, `_walk_forward_weibull()`, etc.) remain in the
scripts as diagnostic tools. They are not deleted.

### analog_trajectory: add canonical evaluation

Currently tunes on `full_traj_mae` via a 672-config grid search. This is
a more defensible internal metric than `gap1_mae` (it considers the full
trajectory), but it still differs from the canonical metric.

Add a canonical evaluation section that scores the current production
constants and the best grid-search result via `evaluate_multi_window()`.
Report both metrics side by side so the relationship between `full_traj_mae`
and headline score is visible. If they consistently agree, the internal
metric is fine as a fast proxy. If they diverge, flag it.

The 672-config sweep is too expensive to run entirely through multi-window
evaluation (~15,000+ model runs with episode-boundary cutoffs). Options:
- Use the internal metric for the initial sweep, then validate the top N
  candidates (e.g., top 10) via multi-window canonical scoring.
- Use `cutoff_mode="fixed"` with a larger step size for the sweep.
- Accept the cost if parallelization makes it tractable.

Recommend the first option (internal sweep + canonical validation of top N)
as the default approach.

### slot_drift: add canonical evaluation

Currently does no predictive evaluation — only alignment analysis. Add a
canonical evaluation section that scores the model via
`evaluate_multi_window()` with current production constants. The alignment
analysis remains as a diagnostic section.

### consensus_blend: migrate to shared infrastructure

Replace the inline `_recency_weight()`, `_weighted_mean()`, and
`_pick_retrospective_cutoffs()` with imports from
`feedcast/evaluation/windows.py`. The research script's multi-cutoff logic
becomes a thin wrapper around the shared infrastructure.

### Tests

No new test files needed for research scripts — they are analysis tools,
not library code. The shared infrastructure is tested in Phase 1. Verify
each research script runs without error after modification:
```bash
python -m feedcast.models.<slug>.research
```

## Phase 4: Documentation

### feedcast/evaluation/README.md

Update to serve as an agent-usable methodology guide. Should cover:
- What `score_forecast()` measures and why (episode matching, horizon
  weighting, geometric mean)
- Multi-window evaluation: rationale, window generation modes, recency
  weighting math
- How to call the API for a canonical evaluation
- Distinction from tracker (multi-window estimates capability; tracker
  measures realized accuracy)

### feedcast/replay/README.md

Rewrite as an agent-usable guide for conducting research:
- What replay does (rewind, run, score across windows)
- How to use it for parameter tuning (score mode, tune mode)
- Default configuration and what each parameter controls
- How to interpret results (aggregate vs per-window, what a good score
  looks like)
- Relationship to evaluation (replay uses evaluation, not the other way
  around)

### feedcast/tracker.py documentation

Add a docstring or comment block explicitly stating:
- Tracker uses single-window evaluation: one prediction, one score
- This is intentional — it measures realized production accuracy
- Multi-window evaluation is for replay/research (estimated capability)

### Model research documentation

Each model's research output and findings.md should note:
- Which metric drives parameter selection (canonical headline score)
- What internal diagnostics are reported and why
- Date of last canonical evaluation run

## Implementation Notes

### Ordering

Phases are sequential. Phase 1 must complete before Phase 2 (replay depends
on windows.py). Phase 2 must complete before Phase 3 (research scripts
should use the replay infrastructure where appropriate). Phase 4 can
partially overlap with Phase 3.

### What NOT to do

- Do not modify `score_forecast()` in `scoring.py`. The single-window
  scorer is correct and unchanged.
- Do not modify the tracker to use multi-window evaluation.
- Do not delete internal diagnostic functions from research scripts (they
  have diagnostic value).
- Do not add parallelism for cross-candidate sweeps (module-level mutation
  conflict).
- Do not change model behavior (model.py files). Only research.py scripts
  and infrastructure are modified.
- Do not add dependencies beyond what is already in the project.

### Key invariant

`score_model("some_model")` with default parameters should produce a result
that is directly comparable to the canonical evaluation section in that
model's `research.py`. Both use the same windows, same weights, same scorer.
