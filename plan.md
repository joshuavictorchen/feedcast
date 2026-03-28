# Plan: Multi-Window Evaluation and Replay Overhaul

## Context and Session Log

**Before implementing any phase, read all transcripts below.** The design
discussion transcript contains motivation and trade-off analysis behind
every decision in this plan. Implementation transcripts (added as work
proceeds) capture the reasoning, edge cases, and adjustments discovered
during each phase. Reading them prevents re-litigating settled decisions
and surfaces context that the plan text alone does not capture.

| Phase | Date | Content | Transcript |
|---|---|---|---|
| Design | 2026-03-28 | Codebase orientation, fragmentation diagnosis, sliding-window design (lookback, decay, cutoff placement), model independence vs standardization, Codex review (6 findings resolved), availability-aware tuning ranking | `.transcripts/90469386-fc85-48ef-af2f-ab43f090b68c.jsonl` |
| Phase 1 Implementation | 2026-03-28 | Shared multi-window evaluation primitives, consensus_blend helper extraction, scorer-context decision (pass full bottle-event list to `score_forecast()`), unavailable-window semantics verification, Claude review convergence, fixed-step cutoff caveat noted for Phase 2 | `.transcripts/rollout-2026-03-28T16-24-48-019d361e-e909-7442-8801-897563198f41.jsonl` |

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
  from a cutoff point. Cutoff points are placed at feeding episode
  boundaries (the canonical timestamp of each `FeedEpisode`) within the
  lookback range. Individual feed events within an episode do not generate
  additional cutoffs — only the episode-level boundary matters. A fixed-step
  mode (configurable step size) is available as a fallback if episode-
  boundary mode is too expensive for sweeps.

- **Replay-equivalent cutoff always included:** The most recent cutoff
  (`latest_activity_time - 24h`) is always injected into the cutoff set,
  even if no episode boundary falls exactly at that time. This preserves
  backward compatibility with the prior single-window replay and ensures
  the most recent complete window is always evaluated.

- **Lookback range:** Default 96 hours. Configurable via `lookback_hours`.
  This is the boundary — no cutoffs are generated beyond it. The
  exponential decay within this range handles relevance weighting; the
  boundary prevents unbounded computation.

- **Recency weighting:** Exponential decay. Default half-life 36 hours.
  Weight formula: `2^(-age_hours / half_life_hours)` where `age_hours` is
  the distance from a cutoff to the most recent cutoff. The most recent
  cutoff always has weight 1.0.

- **Aggregate score:** Weighted mean of per-window headline scores.
  Per-window breakdowns are preserved in results for diagnostics.

- **Unavailable windows:** When a model cannot produce a forecast at a
  given cutoff (e.g., insufficient history), that window is **excluded**
  from the weighted aggregate — not counted as zero. The result reports
  both `window_count` (total attempted) and `scored_window_count` (those
  that produced a score) so availability is visible as a separate concern.
  Rationale: including unavailable windows as zero would penalize models
  that need more warmup history, conflating capability with availability.
  A model that scores well on 15 of 20 windows but cannot forecast from
  older cutoffs should be judged on those 15 windows, with its 75%
  availability noted alongside.

- **Tuning ranking and availability:** For `score_model()`, the raw
  weighted aggregate headline and `availability_ratio` are reported as-is.
  For `tune_model()`, candidates are ranked in two stages: first by
  `scored_window_count` descending (highest availability tier wins), then
  by weighted aggregate headline within that tier. This prevents a
  candidate from winning by scoring well on a small subset of windows
  while being unavailable on harder ones. If a parameter change reduces
  the model's ability to forecast from diverse cutoffs, that is treated
  as a disqualifying weakness, not hidden by a high headline on fewer
  windows.

- **Episode-boundary frequency bias:** Using episode boundaries as cutoff
  points means high-frequency feeding periods (e.g., cluster feeds)
  produce more cutoffs and therefore more aggregate weight than low-
  frequency periods. This is partially mitigated by using episode-level
  boundaries (collapsed from raw feeds) rather than individual feed
  events. The bias is intentional in the sense that periods with more
  feeding activity generate more evaluation scenarios — but implementers
  and researchers should be aware it exists. If a model performs poorly
  during high-frequency periods, that weakness will be amplified in the
  aggregate relative to a fixed-step evaluation.

### Tracker stays single-window

The tracker (`feedcast/tracker.py`) measures realized production accuracy:
"you predicted 24h, here is what happened." This is inherently one window
and should not adopt multi-window evaluation. Document this distinction
explicitly: tracker measures *realized accuracy*, replay/research measure
*estimated capability across scenarios*.

### Model independence vs standardization

Two layers:

1. **Mandatory canonical layer:** Every model's `research.py` must include
   a canonical evaluation section. For production-constant evaluation and
   constant-only parameter sweeps, research scripts should call replay's
   `score_model()` or `tune_model()` rather than reimplementing model
   execution. This ensures canonical results are produced by the same
   infrastructure the CLI uses and are directly comparable across models.
   For variant comparisons that go beyond constant overrides (e.g.,
   testing different code paths), research scripts may call
   `evaluate_multi_window()` directly with a custom forecast function.

2. **Optional internal layer:** Models may use whatever internal metrics
   help them understand their own mechanics (gap MAE, walk-forward
   simulation, alignment analysis, etc.). These are diagnostic tools, not
   tuning objectives.

### Analog trajectory: pragmatic exception for sweep cost

The canonical metric is authoritative for parameter selection. However,
analog_trajectory's 672-config grid search is too expensive to run
entirely through multi-window canonical scoring (potentially 15,000+
model runs). The recommended approach is a two-stage approximation:
use the internal `full_traj_mae` metric for the initial sweep to narrow
candidates, then validate the top N (e.g., top 10) via multi-window
canonical scoring. This is a pragmatic concession to compute cost, not
the ideal policy. If parallelization or fixed-step cutoffs make full
canonical sweeps tractable, prefer that instead. The plan should not be
read as endorsing proxy metrics in general — this exception is specific
to analog_trajectory's grid size.

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
                        # (no separate README.md exists; methodology.md
                        #  serves as the agent-usable methodology guide)

feedcast/replay/
    runner.py           # MODIFY - adopt multi-window evaluation
    results.py          # existing - extend result schema for per-window data
    README.md           # existing - rewrite as agent-usable guide

feedcast/models/<each>/
    research.py         # MODIFY - add canonical evaluation section

scripts/
    run_replay.py       # MODIFY - add CLI flags, update summary printers
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
    episodes: Sequence[FeedEpisode],
    latest_activity_time: datetime,
    lookback_hours: float = 96.0,
) -> list[datetime]:
    """Generate cutoff points at episode boundaries within the lookback range.

    Each cutoff is the canonical timestamp of a FeedEpisode, placed so that
    the 24h evaluation window following it falls entirely within observed
    data. Individual feed events within an episode do not produce separate
    cutoffs.

    The replay-equivalent cutoff (latest_activity_time - 24h) is always
    included, even if no episode boundary falls at that exact time.

    The oldest valid cutoff is latest_activity_time - lookback_hours. No
    cutoffs are generated beyond this boundary.

    Args:
        episodes: Pre-computed feeding episodes (from group_into_episodes).
        latest_activity_time: Upper bound of observed data.
        lookback_hours: Maximum lookback from latest_activity_time.

    Returns:
        Cutoffs sorted chronologically (oldest first).
    """

def generate_fixed_step_cutoffs(
    latest_activity_time: datetime,
    earliest_activity_time: datetime,
    lookback_hours: float = 96.0,
    step_hours: float = 12.0,
) -> list[datetime]:
    """Generate cutoffs at fixed intervals. Fallback for expensive sweeps.

    The replay-equivalent cutoff (latest_activity_time - 24h) is always
    included.
    """

def evaluate_multi_window(
    forecast_fn: Callable[[datetime], Forecast],
    scoring_events: Sequence[FeedEvent],
    cutoffs: Sequence[datetime],
    latest_activity_time: datetime,
    half_life_hours: float = 36.0,
    parallel: bool = False,
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
        parallel: If True, evaluate windows concurrently via ThreadPoolExecutor.

    Returns:
        MultiWindowResult with aggregate score and per-window breakdowns.
        Windows where the model is unavailable are excluded from the
        aggregate but included in per_window with status="unavailable".
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
    headline_score: float          # Weighted mean of scored windows' headlines
    count_score: float             # Weighted mean of scored windows' count scores
    timing_score: float            # Weighted mean of scored windows' timing scores
    window_count: int              # Total windows attempted
    scored_window_count: int       # Windows that produced a score
    availability_ratio: float      # scored_window_count / window_count
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
  respect lookback, always include replay-equivalent cutoff
- Unavailable windows: excluded from aggregate, counted in window_count
- Multi-window aggregation: weighted mean matches hand-calculated values
- Edge case: export with fewer than 24h of data raises clear error

### Phase 1 implementation notes (2026-03-28)

- `evaluate_multi_window()` passes the full bottle-event list to
  `score_forecast()` for every cutoff. The scorer owns window filtering and
  cross-cutoff episode grouping, so callers should not pre-filter actuals per
  window.
- Per-window `observed_until` is computed as
  `min(cutoff + 24h, latest_activity_time)`. Current Phase 1 cutoff generators
  still yield full 24-hour windows, but keeping the partial-horizon contract in
  the shared evaluator avoids baking in the wrong assumption for future callers.
- `generate_episode_boundary_cutoffs()` validates against the earliest episode
  timestamp, not the earliest raw activity timestamp. That is intentional:
  `windows.py` operates on precomputed `FeedEpisode` inputs. If replay later
  needs a raw-activity guard, it belongs in the caller, not in evaluation.
- Phase 2 should adapt replay by wrapping `_run_forecast()` in the
  `Callable[[datetime], Forecast]` closure that `evaluate_multi_window()`
  expects. `_run_forecast()` already catches `ForecastUnavailable` and
  normalizes it to `Forecast(available=False)`, and the evaluator already maps
  `available=False` to `status="unavailable"`. Do not add a second
  `ForecastUnavailable` catch inside `windows.py`.
- For override-based scoring and tuning, `override_constants(...)` must wrap
  the entire `evaluate_multi_window()` call for a candidate, not just closure
  construction. The `forecast_fn` closure ultimately calls `_run_forecast()`,
  which reads module-level constants at execution time; narrowing the `with`
  block would silently evaluate some windows under the wrong constants.
- `generate_fixed_step_cutoffs()` anchors its step grid at
  `max(earliest_activity_time, latest_activity_time - lookback_hours)`. When
  the dataset does not span the full lookback range, the fallback fixed-step
  grid shifts with data availability and can yield fewer windows than a
  boundary-anchored grid. This is acceptable, but Phase 2 should document it.
- `feedcast/models/consensus_blend/research.py` now routes all recency-weight
  calculations through shared `recency_weight()`, including the inter-episode
  gap analysis. Future weighting changes should stay centralized in
  `feedcast/evaluation/windows.py`.
- Focused verification after implementation:
  `.venv/bin/python -m pytest -q tests/test_windows.py tests/test_scoring.py tests/test_replay.py`
  → `28 passed`

## Phase 2: Replay Adopts Multi-Window

Modify `feedcast/replay/runner.py` to use multi-window evaluation.

### Changes to `score_model()`

Replace single-window `_latest_replay_window()` call with:
1. Build scoring events and episodes from activities
2. Generate cutoffs via `generate_episode_boundary_cutoffs()`
3. Build a `forecast_fn` closure that calls `_run_forecast()` for a given
   cutoff
4. Call `evaluate_multi_window()` to get aggregate + per-window results
5. Include both aggregate and per-window data in the result payload

The function signature gains optional parameters with defaults:
```python
def score_model(
    model_slug: str,
    *,
    overrides: dict[str, Any] | None = None,
    export_path: Path | None = None,
    output_dir: Path = DEFAULT_RESULTS_DIR,
    lookback_hours: float = 96.0,
    half_life_hours: float = 36.0,
    cutoff_mode: str = "episode",  # "episode" or "fixed"
    step_hours: float = 12.0,      # only used when cutoff_mode="fixed"
    parallel: bool = False,
) -> dict[str, Any]:
```

### Changes to `tune_model()`

Same multi-window adoption. Each candidate configuration is evaluated across
all windows. Candidates are ranked by highest availability tier first
(most `scored_window_count`), then by weighted aggregate headline within
that tier. See "Tuning ranking and availability" in Design Decisions.

### Changes to result schema

The `replay_window` field in result payloads becomes `replay_windows`:
```json
{
    "replay_windows": {
        "lookback_hours": 96.0,
        "half_life_hours": 36.0,
        "cutoff_mode": "episode",
        "window_count": 21,
        "scored_window_count": 21,
        "availability_ratio": 1.0,
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

### CLI updates (`scripts/run_replay.py`)

- Add CLI flags: `--lookback`, `--half-life`, `--cutoff-mode`,
  `--step-hours`, `--parallel`. Defaults match the function defaults.
- Update `_print_score_summary()` (currently at line 226): replace
  `payload["replay_window"]` reads with `payload["replay_windows"]`.
  Show aggregate scores and window count summary.
- Update `_print_tune_summary()` (currently at line 251): same schema
  change. Show aggregate scores, window count, and availability for
  baseline and best candidate.

### Parallelization

The `parallel` flag threads through from `score_model()` / `tune_model()`
to `evaluate_multi_window()` in `windows.py`.

### Tests

Update `tests/test_replay.py`:
- Existing tests should continue to pass (same behavior, multiple windows)
- Add test for multi-window result structure
- Add test for `lookback_hours` and `half_life_hours` parameter passthrough

## Phase 3: Research Scripts Adopt Canonical Scoring

Every model's `research.py` gains a canonical evaluation section.

### All models: canonical evaluation via replay

Each research script adds a section (called from `main()`) that calls
replay's `score_model()` with the model's slug and current production
constants. Research scripts must pass `export_path=snapshot.export_path`
explicitly so the canonical section evaluates the same dataset the script
already loaded, avoiding a TOCTOU race if a new export arrives mid-run.
This ensures the canonical result uses the same infrastructure as the CLI
and is directly comparable across models.

The section should be clearly labeled (e.g., "CANONICAL MULTI-WINDOW
EVALUATION") and appear prominently in the output, reporting:
- Aggregate headline, count, and timing scores
- Window count, scored window count, availability ratio
- Per-window breakdown (cutoff, score, weight)

### latent_hunger and survival_hazard: switch tuning metric

These two scripts currently select parameters by minimizing `gap1_mae`.
Change the parameter selection logic to use replay's `tune_model()`:

1. Define candidate parameter values as they do today, but pass them
   to `tune_model()` instead of the inline walk-forward evaluator.
   Pass `export_path=snapshot.export_path` explicitly (same dataset
   the script already loaded).
2. `tune_model()` handles multi-window canonical scoring and ranking
   (highest availability tier, then headline).
3. Report the best candidate from canonical scoring alongside the
   internal diagnostic results for comparison.
4. Keep the existing walk-forward / `gap1_mae` analysis as a diagnostic
   section that helps explain *why* a parameter set performs well or poorly.
5. Update findings.md and CHANGELOG.md to document the metric change.

The internal evaluation functions (`_evaluate_multiplicative()`,
`_evaluate_additive()`, `_walk_forward_weibull()`, etc.) remain in the
scripts as diagnostic tools. They are not deleted.

### analog_trajectory: two-stage canonical evaluation

Currently tunes on `full_traj_mae` via a 672-config grid search. Running
all 672 configs through multi-window canonical scoring is prohibitively
expensive. Use a two-stage approach:

1. Run the existing internal grid search using `full_traj_mae` as a fast
   proxy to rank all 672 configurations.
2. Take the top 10 candidates and validate each via replay's
   `score_model()` with appropriate overrides. Pass
   `export_path=snapshot.export_path` explicitly.
3. Report the canonical ranking of those top 10 as the authoritative
   result.

This is a pragmatic concession to compute cost (see "Analog trajectory:
pragmatic exception for sweep cost" in Design Decisions). The canonical
metric remains authoritative. If parallelization or fixed-step cutoffs
make full canonical sweeps tractable, prefer that instead.

Also report the production-constant canonical score via
`score_model(slug, export_path=snapshot.export_path)` (no overrides) so
the baseline is comparable.

### slot_drift: add canonical evaluation

Currently does no predictive evaluation — only alignment analysis. Add a
canonical evaluation section that calls replay's
`score_model("slot_drift", export_path=snapshot.export_path)`. The
alignment analysis remains as a diagnostic
section.

### consensus_blend: migrate to shared infrastructure

Replace the inline `_recency_weight()`, `_weighted_mean()`, and
`_pick_retrospective_cutoffs()` with imports from
`feedcast/evaluation/windows.py`. The research script's multi-cutoff logic
becomes a thin wrapper around the shared infrastructure.

For the selector parameter sweep, the research script calls
`evaluate_multi_window()` directly with a custom forecast function (since
it needs to vary selector internals, not just module-level constants).
This is the appropriate layer for variant comparisons that go beyond
constant overrides.

### Tests

No new test files for research scripts — they are analysis tools, not
library code. The shared infrastructure is tested in Phase 1. Verify each
script runs without error after modification:
```bash
python -m feedcast.models.<slug>.research
```

## Phase 4: Documentation

### feedcast/evaluation/methodology.md

Update to serve as an agent-usable methodology guide (no separate README.md
exists in this directory; methodology.md serves that role). Should cover:
- What `score_forecast()` measures and why (episode matching, horizon
  weighting, geometric mean)
- Multi-window evaluation: rationale, window generation modes, recency
  weighting math, episode-boundary frequency bias
- Unavailable window handling and availability reporting
- How to call the API for a canonical evaluation
- Distinction from tracker (multi-window estimates capability; tracker
  measures realized accuracy)

### feedcast/replay/README.md

Rewrite as an agent-usable guide for conducting research:
- What replay does (rewind, run, score across windows)
- How to use it for parameter tuning (score mode, tune mode)
- Default configuration and what each parameter controls
- How to interpret results (aggregate vs per-window, availability,
  what a good score looks like)
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
on windows.py). Phase 2 must complete before Phase 3 (research scripts call
replay's `score_model()` / `tune_model()`). Phase 4 can partially overlap
with Phase 3.

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
This is guaranteed when research scripts call `score_model()` directly for
their canonical section.
