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
| Phase 2 Implementation | 2026-03-28 | Replay adopts multi-window evaluation. Codex review caught best-can-regress bug, missing top-level tune `replay_windows`, and baseline leaking into candidates list — all resolved. 31 tests pass. | `.transcripts/9c218a97-a6db-4ace-a7d3-0d67af4fb47a.jsonl` |
| Phase 3 Implementation | 2026-03-28 | All five research scripts gain canonical multi-window evaluation. latent_hunger/survival_hazard gain canonical tuning. analog_trajectory gains two-stage validation and ALIGNMENT constant. consensus_blend migrates to shared infrastructure (Codex caught weighting divergence and availability bug). Plan restructured to Phases 4–6. 79 tests pass. | `.transcripts/bcc43b44-38db-44e7-8b68-513c05b7aac8.jsonl` |
| Phase 4–5 Planning | 2026-03-29 | Merged old Phases 4+5 into per-model end-to-end sub-phases. Defined research.md template (canonical/diagnostic split, last-run staleness box), document relationship contract, advisory tuning pipeline. Codex review resolved: commit instruction conflict, readiness marker, consensus_blend dual-purpose framing. Old Phase 6 renamed to Phase 5 with system contract section. | `.transcripts/14689751-83e9-4d29-be6f-9c8b90bc90b7.jsonl` |
| Phase 4.0–4.1 Implementation | 2026-03-29 | slot_drift research refresh and constant tuning. 128-candidate sweep updated DRIFT 3.0→1.0, LOOKBACK 7→5, THRESHOLD 2.0→1.5 (+9.2 headline). Codex caught overclaim about tuning surface and stale disposition guidance — resolved. Plan threshold normalized to "any improvement." | `.transcripts/e61917e0-0184-48b8-b7f9-29807ea6140a.jsonl` |
| Phase 4.2 Implementation | 2026-03-31 | latent_hunger research refresh and constant tuning. 12-candidate SATIETY_RATE sweep updated 0.257→0.05 (+0.550 headline). Codex caught stale artifacts (research_results.txt generated before constant change) and overstated volume insensitivity (post-feed hunger framing vs correct satiety effect 3.7x ratio). Third review caught stale "adopted" label on diagnostic section. All resolved. 79 tests pass. | `.transcripts/b7826d26-7e2a-457c-b4d1-15a691aba5ce.jsonl` |
| Phase 4.3 Implementation | 2026-03-31 | survival_hazard research refresh and constant tuning. Initial 40-candidate canonical sweep hit the grid boundary, so the sweep was widened to a 154-candidate mixed-resolution grid; production shapes updated 6.54/3.04→4.75/1.75 (+6.981 headline, 24/24 availability). Follow-up discussion clarified descriptive episode-level MLE vs canonical replay tuning, added windowed-MLE and component-ablation follow-ups, and renamed the final-summary label to "Episode-level MLE (descriptive fit)". 79 tests pass. | `.transcripts/rollout-2026-03-31T22-25-05-019d46db-d72c-7c81-9bff-1af02fc6638b.jsonl` |
| Phase 4.3.5–4.4 Implementation | 2026-04-01 | Implemented replay candidate parallelism via process isolation, passing the analog benchmark gate, then completed analog_trajectory retuning under a full canonical sweep. Found and fixed the LOOKBACK_HOURS default-argument override bug, added HISTORY_MODE to the canonical search space, updated analog production constants to the corrected winner (episode history, recent_only, k=5, 72h half-life), completed Claude review convergence, and did a repo-wide docs cleanup to remove phase-framed or quickly stale numbers from design/methodology files. 84 tests pass. | `.transcripts/rollout-2026-04-01T00-04-38-019d4736-f7f2-77b3-b0c8-69db397bf39d.jsonl` |
| Phase 4.5 Implementation | 2026-04-01 | consensus_blend selector sweep and retune. Initial 48-config sweep hit grid boundaries on all three geometric parameters; widened to 384 configs (4 radii × 4 spread caps × 6 conflict windows × 4 penalties). Winner moved to radius=120, spread=150, conflict=135 — all interior. Production updated 72.020→72.996 headline, 24/24 availability. Claude review caught boundary hit, artifact truncation, CHANGELOG reversal narrative, and sharp conflict peak at 135 (±15 min costs 0.765–1.406). Codex fixed all: full table at 3-decimal precision, supersession note, quantified peak in research.md, boundary-region test added. 85 tests pass. | `.transcripts/7dae7abe-764c-448b-b6aa-f31be9c5afec.jsonl` |
| Phase 4.6 + Phase 5 (partial) | 2026-04-01–02 | Cross-model synthesis: promoted timing-bottleneck and internal-vs-canonical-divergence to index.md, added episode-level history convergence. Sharpened metric-divergence framing (questions the models, not the methodology). Research hub playbook: centralized research convention in index.md, evolution tracking via CHANGELOG.md, staleness boxes, unified naming (findings.md→research.md, research.py→analysis.py, research_results.txt→artifacts/), standardized section headers across all seven research.md files. Multiple Codex review rounds resolved convention consistency. Phase 5 remaining: evaluation/methodology.md, replay/README.md, tracker.py docstring, README event-construction split. 85 tests pass. | `.transcripts/692ce868-a943-4e20-b40c-75bbf48917c8.jsonl` |

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

### Analog trajectory: exception retired

Sub-phase 4.4 retired the old analog-specific proxy exception. Analog
now uses:

- two 1344-config `full_traj_mae` sweeps as diagnostic evidence only
  (`raw` and `episode` history)
- one 2688-config canonical replay sweep as the shipping gate

The canonical metric is authoritative for parameter selection.
`full_traj_mae` remains useful for understanding retrieval quality, but
it no longer acts as a shortlist gate for shipping decisions.

### Parallelization

Optional `parallel: bool = False` flag on the multi-window evaluator.
Parallelizes across windows within a single candidate evaluation using
`concurrent.futures.ThreadPoolExecutor`. Safe because all windows share the
same model constants (no shared-state mutation).

Cross-candidate parallelism is valid only through **process isolation**.
The replay harness currently mutates module-level constants via
`override_constants()`, so concurrent candidate evaluation in a shared
process would be incorrect. The clean path is process-local mutation:
`tune_model()` submits one candidate per worker in a
`concurrent.futures.ProcessPoolExecutor`, each worker initializes its
own snapshot / scoring context once, and candidate evaluation happens in
that worker process only. This preserves `model.py` as the production
source of truth and avoids a broader refactor of every model API.

When candidate-parallel replay is enabled, worker-local window
parallelism should default to off to avoid nested parallel oversubscription.

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
    analysis.py         # MODIFY - add canonical evaluation section

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

### Phase 2 implementation notes (2026-03-28)

- Deleted `_latest_replay_window()`, `_evaluate_model()`, and
  `_serialize_window()` from `runner.py`. All three are fully replaced by
  the shared infrastructure in `evaluation/windows.py`.
- Cutoff generation uses `group_into_episodes(scoring_events)` where
  `scoring_events = build_feed_events(..., merge_window_minutes=None)`.
  This matches the scorer's bottle-only episode view, as Codex flagged
  during pre-implementation alignment.
- The `forecast_fn` closure wraps `_run_forecast()`, which already handles
  `ForecastUnavailable` → `Forecast(available=False)`. No second catch was
  added in `runner.py`, per Phase 1 implementation notes.
- `override_constants()` wraps the entire `evaluate_multi_window()` call
  per candidate, not just closure construction (the closure reads
  module-level constants at execution time).
- The broad `except Exception` around candidate evaluation in `tune_model()`
  was removed. `evaluate_multi_window()` records per-window errors and
  unavailability internally. Catching around the whole call would discard
  diagnostics.
- **Baseline competes for best:** Baseline is evaluated alongside sweep
  candidates via the same `_rank_key` comparator
  `(-scored_window_count, -headline_score, str(params))`, but it does not
  appear in the serialized `candidates` list — it is reported separately
  as `baseline`. This prevents both the "best regresses vs baseline" bug
  and the "baseline duplicated in candidates" bug caught during review.
- **Split improvement deltas:** `improvement_vs_baseline` was replaced with
  `availability_delta` (int, `scored_window_count` difference) and
  `headline_delta` (float). Both are present so the artifact is honest
  about lexicographic ranking. When baseline wins, both are 0.
- **Tune payload has top-level `replay_windows`:** Contains the shared
  config (lookback, half_life, cutoff_mode, step_hours if fixed,
  window_count) via `_serialize_multi_window_config()`. Per-candidate
  aggregates and per-window detail are nested under each candidate entry.
  Score payload uses the full `_serialize_multi_window()` at the top level.
- `step_hours` is persisted in the artifact only when
  `cutoff_mode="fixed"`, so the artifact is reproducible from its own
  metadata.
- `results.py` required no changes — it writes whatever payload dict it
  receives. The schema change is entirely in `runner.py` payload
  construction.
- Focused verification:
  `.venv/bin/python -m pytest -q tests/test_windows.py tests/test_scoring.py tests/test_replay.py`
  → `31 passed`

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

### analog_trajectory: full canonical evaluation

Sub-phase 4.4 replaces the old two-stage analog path.

1. Run the raw and episode `full_traj_mae` sweeps as diagnostic
   evidence.
2. Run one full canonical replay sweep across all production-relevant
   constants, including `HISTORY_MODE`.
3. Report the canonical winner as the authoritative result.

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
python -m feedcast.models.<slug>.analysis
```

### Phase 3 implementation notes (2026-03-28)

- All five research scripts now include a "CANONICAL MULTI-WINDOW
  EVALUATION" section that calls `score_model()` with
  `export_path=snapshot.export_path`, ensuring the same dataset the
  script already loaded is used (TOCTOU guard). Canonical results use
  default replay parameters (lookback=96h, half_life=36h) for
  cross-model comparability.
- **slot_drift:** Added canonical evaluation section. No tuning section
  because the model has no constant-only parameter sweep — its template
  is derived from data alignment, not tunable constants.
- **latent_hunger:** Added canonical evaluation and canonical tuning.
  `tune_model()` sweeps `SATIETY_RATE` (12 candidates, 0.05–0.8).
  Growth rate is runtime-estimated and not overridable via constant
  overrides. Existing walk-forward diagnostics (gap1/gap3/fcount MAE,
  additive vs multiplicative, circadian) preserved.
- **survival_hazard:** Added canonical evaluation and canonical tuning.
  `tune_model()` jointly sweeps `OVERNIGHT_SHAPE` (8 values, 4.0–8.0)
  and `DAYTIME_SHAPE` (5 values, 2.0–4.0) = 40 candidates. Scale is
  runtime-estimated. Existing diagnostics preserved.
- **analog_trajectory:** Added canonical evaluation and two-stage
  validation. Top 10 configs from the 1344-config internal sweep are
  validated via `score_model()` with overrides. `ALIGNMENT` is now a
  module-level constant, so canonical validation can compare both `gap`
  and `time_offset` variants instead of hard-coding `gap` only.
  Canonical validation table shows both internal metric
  (`full_traj_mae`) and canonical headline side-by-side.
- **consensus_blend:** Migrated to shared infrastructure.
  `_pick_retrospective_cutoffs()` replaced by
  `generate_episode_boundary_cutoffs()` (bottle-only episodes, matching
  the scorer's view). `_report_production_scores()` replaced by
  `score_model("consensus_blend", ...)`. `_sweep_selector_parameters()`
  rewritten to use `evaluate_multi_window()` with a custom `forecast_fn`
  closure per sweep configuration. Pre-caching of model outputs and
  candidate clusters preserved for efficiency. `_weighted_row_mean()`
  deleted (no longer needed).
- Scoring events in the consensus_blend sweep now use bottle-only events
  (consistent with the canonical scorer), not breastfeed-merged events as
  before. This is a correctness fix: the old approach scored against
  merged events while `score_model()` and replay always use bottle-only.
- The consensus_blend sweep and canonical evaluation now use the same
  replay defaults (lookback=96h, half_life=36h) so the sweep optimizes
  the same objective the canonical section reports. The old 4-day
  (96h) recency half-life is retained only for the inter-episode gap
  analysis diagnostic, which is unrelated to canonical scoring.
- The sweep's `forecast_fn` now returns `available=False` when the
  selector produces no points, matching production `run_consensus_blend`
  semantics. Without this, `evaluate_multi_window` would score empty
  forecasts instead of excluding them, inflating availability ratios.
- Sweep rows are ranked by availability tier first (most scored windows),
  then by headline score — the same policy `tune_model` uses. This
  prevents a config from winning by being unavailable on hard windows.
- Diagnostic sections in consensus_blend (`_analyze_inter_episode_gaps`,
  `_analyze_model_agreement`) use a 5-cutoff subset of the shared
  cutoffs to avoid running all models at every episode-boundary window.
- The plan specified updating `findings.md` and `CHANGELOG.md` for
  latent_hunger and survival_hazard. No model-level `findings.md` files
  exist (research findings live in `feedcast/research/`). CHANGELOGs
  were updated for both models.
- Post-Phase-3 cleanup: `analog_trajectory` now validates `ALIGNMENT`
  explicitly and fails fast on invalid values instead of silently
  behaving like `gap`. This does not change intended production output;
  it only closes a bad-override footgun.
- No intended production forecast behavior was changed by the Phase 3
  research work. All 79 tests pass.
- Focused verification:
  `.venv/bin/python -m pytest -q` → `79 passed`

## Phase 4: Per-Model Research Refresh and Documentation

*Fleshed out 2026-03-29 with user and Codex peer review. Ready to
execute.*

Phases 1–3 built canonical multi-window evaluation into all five
research scripts. Phase 4 handles each model end-to-end: run its
research script, analyze the results, write a `research.md` guide,
and decide whether production constants should change.

**Current state:** All five `research_results.txt` files predate Phase 3
and lack canonical evaluation sections. Each script must be re-run
before its documentation can reference concrete results.

### Document relationships

Each model directory has three documents that serve different audiences:

| Document | Purpose |
|---|---|
| `design.md` | **The why.** Design decisions and rationale — why the model works the way it does. |
| `methodology.md` | **The report-facing how.** Text rendered into the forecast report for end-user consumption. |
| `research.md` | **The evidence.** Current support and challenges for the model's design and constants. |

Research should inform design and methodology, but the three documents
are not redundant. Each `research.md` should open with a brief note
establishing this relationship so readers know where to look for what.

### Research-tuning-production pipeline

This pipeline is advisory at the infrastructure level but Phase 4
sub-phases may apply constant changes directly when evidence warrants:

1. **Research scripts produce evidence.** Canonical scores, parameter
   recommendations, diagnostic analysis. The scripts themselves do not
   modify production behavior — `analysis.py` writes to
   `artifacts/research_results.txt`, not `model.py`.
2. **Tuning constants live in `model.py`.** Each model's tunable
   constants are module-level variables. `tune_model()` evaluates
   candidates using temporary overrides (`override_constants`) that
   are restored after evaluation. Nothing writes back to `model.py`
   automatically.
3. **Replay is for evidence, not automation.** `score_model()` and
   `tune_model()` provide canonical evaluation. The output is a ranked
   list with scores. The decision to change a constant is made by the
   agent or human reviewing the evidence in each sub-phase.

This distinction should be clear in every `research.md` Conclusions
section: the research pipeline produces recommendations; constant
changes are intentional decisions with documented evidence.

### Document template

Each `research.md` follows this structure:

| Section | Content |
|---|---|
| **Header** | One-line document-relationship note: `design.md` is the why, `methodology.md` is the report-facing how, `research.md` is the evidence. |
| **Overview** | What question(s) this research answers. Why the model needs its own research beyond canonical scoring. |
| **Last run** | Metadata box (see format below). Hard staleness signals so readers know when results are outdated. |
| **Methods** | Two structural subsections: **Canonical evaluation and tuning** first (shared replay infrastructure, cross-model comparable), then **Model-specific diagnostics** (internal metrics, model mechanics). Within each subsection, follow script section order. |
| **Results** | Lead with **Canonical findings** (do current production constants win?), then **Diagnostic findings**. Summarize — do not mirror raw output. Reference `research_results.txt` for full tables. |
| **Conclusions** | What results mean for current constants and design. Frame as recommendations. Note which findings informed production parameters and which are informational. |
| **Open questions** | Labeled as **Model-local** or **Cross-cutting**. Cross-cutting questions include enough local context to be useful but point to `feedcast/research/index.md` for shared discussion rather than duplicating analysis across five files. |

**Last run format:**

```markdown
| Field | Value |
|---|---|
| Run date | YYYY-MM-DD |
| Export | `exports/export_narababy_silas_YYYYMMDD.csv` |
| Dataset | `sha256:...` |
| Command | `.venv/bin/python -m feedcast.models.<slug>.analysis` |
| Canonical headline | XX.X |
| Availability | N/N windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.
```

The dataset fingerprint makes staleness mechanically detectable: if
the export has changed, the fingerprint won't match. The absolute run
date and export path provide a second signal.

### Per-model sub-phase structure

Each model sub-phase (4.1–4.5) follows the same steps:

1. **Run** the research script:
   `.venv/bin/python -m feedcast.models.<slug>.research`
2. **Verify** the output includes canonical sections
   (`CANONICAL MULTI-WINDOW EVALUATION` header present in output).
3. **Analyze** the results against the model-specific questions listed
   in that sub-phase.
4. **Write** `research.md` following the document template above.
5. **Decide** on constants — end with an explicit disposition:
   - **Keep:** Current production constants are optimal. Document the
     evidence supporting them in Conclusions.
   - **Change:** Update `model.py` and add a `CHANGELOG.md` entry with
     the canonical evidence that motivated the change. Any improvement
     in canonical headline score warrants a constant update.
   - **Unresolved:** Results are ambiguous (e.g., availability drops
     offset a headline gain). Document the uncertainty in Open questions.

### Ordering constraints

- Sub-phase 4.0 must complete before any model sub-phase begins.
- Sub-phases 4.1–4.3 are independent and can run in any order.
- **Sub-phase 4.3.5 must run before 4.4.** Analog trajectory is the
  first consumer of candidate-parallel replay.
- **Sub-phase 4.5 (consensus_blend) must run after 4.1–4.4.** Its
  research script calls `run_all_models()`, which executes every model
  with current production constants. If earlier sub-phases change
  constants, consensus_blend should see those changes.
- Sub-phase 4.6 must wait until all model sub-phases are done.

### Expected runtime reference

Per-model cost breakdown (with current export, ~20+ canonical windows):
- `slot_drift`: one `score_model()` run — fast
- `latent_hunger`: one `score_model()` + 12-candidate `tune_model()`
- `survival_hazard`: one `score_model()` + 40-candidate `tune_model()`
- `analog_trajectory`: two 1344-config diagnostic sweeps plus one
  2688-config canonical sweep; candidate-parallel replay keeps it
  practical but it still dominates wall-clock time
- `consensus_blend`: one `score_model()` + 48 selector sweep configs
  over cached model outputs

### Sub-phase 4.0: Shared setup

1. Verify the latest export is available in `exports/`.
2. Run `.venv/bin/python -m pytest -q` to confirm all tests pass
   (baseline check before any changes).
3. Read and internalize the document template, document relationships,
   and research-tuning-production pipeline sections above.

**Deliverable:** Confirmed test baseline. No script execution or file
creation in this sub-phase.

### Sub-phase 4.0 implementation notes (2026-03-29)

- Latest export: `export_narababy_silas_20260327.csv`. 79 tests pass.
- Disposition guidance updated: any improvement in canonical headline
  warrants a constant update (replaces "material and defensible"
  threshold).
- Workflow: Claude performs, Codex reviews, sub-phase by sub-phase.

### Sub-phase 4.1: slot_drift

**Questions to answer:**
- What is the canonical headline score with production constants?
- Is availability 100%?
- Does the alignment analysis still support the current template
  slot count and positions?

**Per-model context:** Alignment analysis only (no parameter sweep).
Canonical eval is the only predictive scoring. `research.md` should
explain what the template alignment analysis tells you (whether the
daily feeding pattern is stable enough for a fixed-slot template) and
what it doesn't (whether the template parameters are optimal — there
is no constant-only sweep to test this).

**Disposition guidance:** No canonical tuning sweep was defined for
slot_drift. Constants are technically overridable but were assessed
via alignment analysis. Change constants if alignment diagnostics or
a future sweep show any improvement.

**Deliverable:** Fresh `research_results.txt`,
`feedcast/models/slot_drift/research.md`, and any `model.py` /
`CHANGELOG.md` updates if warranted.

### Sub-phase 4.1 implementation notes (2026-03-29)

- Canonical sweep: 128 candidates (`DRIFT_WEIGHT_HALF_LIFE_DAYS` ×
  `MATCH_COST_THRESHOLD_HOURS` × `LOOKBACK_DAYS`). All 128 had 24/24
  availability.
- Disposition: **Change.** Updated three constants:
  `DRIFT_WEIGHT_HALF_LIFE_DAYS` 3.0→1.0, `LOOKBACK_DAYS` 7→5,
  `MATCH_COST_THRESHOLD_HOURS` 2.0→1.5. Headline improved 59.2→68.4
  (+9.2), primarily from timing 40.4→51.9 (+11.5). Count also
  improved 87.6→90.8 (+3.2). No availability loss.
- Updated constants confirmed as sweep winner: re-running with new
  production values shows baseline = best candidate.
- Tuning section added to `research.py` so future runs include the
  128-candidate sweep. Design.md and methodology.md updated with new
  constant values.
- `research.md` written with canonical/diagnostic split, last-run
  staleness box, and document-relationship header. No phase references
  in persistent docs.
- Focused verification: `.venv/bin/python -m pytest -q` → 79 passed.

### Sub-phase 4.2: latent_hunger

**Questions to answer:**
- What is the canonical headline score with production constants?
- Does `tune_model()` select the current `SATIETY_RATE` or a different
  value? If different, is there any headline improvement?
- Do the internal walk-forward diagnostics (gap1/gap3/fcount MAE)
  agree with canonical ranking direction?
- Is availability 100%?

**Per-model context:** Internal walk-forward diagnostics
(gap1/gap3/fcount MAE), additive vs multiplicative comparison,
circadian analysis, episode comparison. Canonical tune sweeps
`SATIETY_RATE`. Growth rate is runtime-estimated and not overridable
via constant overrides. `research.md` should map each diagnostic
section to the design question it answers (e.g., "Section 4 compares
additive vs multiplicative satiety — this is the evidence for the
multiplicative design choice in `design.md`").

**Disposition guidance:** If `tune_model()` selects a different
`SATIETY_RATE` with any headline improvement, update `model.py`.

**Deliverable:** Fresh `research_results.txt`,
`feedcast/models/latent_hunger/research.md`, and any `model.py` /
`CHANGELOG.md` updates if warranted.

### Sub-phase 4.2 implementation notes (2026-03-31)

- Canonical evaluation (pre-update, sr=0.257): headline=66.3,
  count=92.6, timing=47.8. 24/24 windows (100% availability).
- Canonical tuning: 12-candidate `SATIETY_RATE` sweep (0.05–0.8). Best
  candidate sr=0.05, headline=66.9 (+0.550 vs baseline sr=0.257).
  Improvement driven by count (+1.4), timing nearly unchanged (+0.1).
  All candidates had 24/24 availability. Tuning surface is shallow —
  top 5 span only 0.5 headline points.
- Disposition: **Change.** `SATIETY_RATE` updated 0.257→0.05.
  Post-update canonical evaluation confirms headline=66.9 with
  baseline=best (research script re-run after constant change).
- **Internal vs. canonical disagreement:** Episode-level walk-forward
  grid search finds best sr=0.645, while canonical scoring finds best
  sr=0.05. The metrics disagree on direction. The model retains
  meaningful volume sensitivity at sr=0.05 (satiety effect scales 3.7x
  from 1oz to 4oz), but the lower rate produces more uniform gap
  predictions that improve canonical episode-count matching. Internal
  gap-MAE rewards stronger per-feed differentiation; canonical headline
  rewards consistent 24h trajectory quality.
- `research.py` final summary updated to reflect canonical tuning
  origin (replaced stale "re-tuned on episode-level data" narrative).
  Research script re-run after both constant change and summary update
  so `research_results.txt` matches current production state.
- `research.md` written with canonical/diagnostic split, last-run
  staleness box, and document-relationship header. Follows slot_drift
  template structure. Distinguishes pre-update sweep evidence from
  current production canonical score.
- Methodology.md not updated — describes the multiplicative mechanism
  generally without citing the specific rate value. The mechanism is
  unchanged; only the constant changed.
- Updated `test_latent_hunger.py` — `SatietyRateTests` asserts the new
  production value (0.05). Test name and docstring updated to reflect
  canonical tuning origin.
- Focused verification: `.venv/bin/python -m pytest -q` → 79 passed.

### Sub-phase 4.3: survival_hazard

**Questions to answer:**
- What is the canonical headline score with production constants?
- Does `tune_model()` select the current `OVERNIGHT_SHAPE` and
  `DAYTIME_SHAPE` or different values? If different, is there any
  headline improvement?
- Do the internal Weibull fitting diagnostics agree with canonical
  ranking direction?
- Is availability 100%?

**Per-model context:** Weibull fitting, day-part analysis, discrete
hazard comparison, volume covariate testing, episode analysis. Canonical
tune sweeps both shape parameters jointly. The original plan assumed an
8 overnight × 5 daytime grid (40 candidates), but the executed 4.3 work
expanded this after the initial canonical winner hit the lowest-tested
corner. Scale is runtime-estimated. `research.md` should clarify which
sections are historical exploration (e.g., discrete hazard comparison
was an early design alternative) vs current validation (e.g., day-part
Weibull fits confirm the overnight/daytime split).

**Disposition guidance:** Same criteria as latent_hunger — any headline
improvement warrants an update. Update both shape constants together
if the evidence supports it.

**Deliverable:** Fresh `research_results.txt`,
`feedcast/models/survival_hazard/research.md`, and any `model.py` /
`CHANGELOG.md` updates if warranted.

### Sub-phase 4.3 implementation notes (2026-03-31)

- Activated `parallel=True` in `feedcast.models.survival_hazard.research`
  for canonical `score_model()` and `tune_model()` calls. Full research
  run time on `export_narababy_silas_20260327.csv`: `real 5.66s`.
- Canonical evaluation with pre-update production constants
  (`OVERNIGHT_SHAPE=6.54`, `DAYTIME_SHAPE=3.04`) scored
  headline=`65.672`, count=`92.810`, timing=`47.417`, availability
  `24/24`.
- The original 40-candidate canonical grid bottomed out at the lowest
  tested corner (`OVERNIGHT_SHAPE=4.0`, `DAYTIME_SHAPE=2.0`,
  headline=`70.417`). Rather than document a boundary winner, the sweep
  was widened to a mixed-resolution 154-candidate grid.
- Disposition: **Change.** Expanded canonical sweep selected
  `OVERNIGHT_SHAPE=4.75`, `DAYTIME_SHAPE=1.75`, improving headline to
  `72.653` (`+6.981`). Count improved `92.810→94.347` (`+1.537`);
  timing improved `47.417→56.572` (`+9.155`). Availability remained
  `24/24`.
- Post-update research script re-run confirms baseline=`best` on the
  widened grid. `research_results.txt` is now in the final production
  state (`72.653`, `24/24`).
- Internal diagnostics and canonical replay disagree materially on
  shape direction. Raw day-part fits are `4.4417 / 2.7987`; episode-
  level MLE fits are `7.2296 / 3.4225`; canonical replay selects the
  softer production pair `4.75 / 1.75`. The day-part split is still
  supported; direct distribution fit is not authoritative for
  production constants.
- Episode-level history remains strongly supported (`121` raw bottle
  events collapse to `103` episodes). Volume overlay remains rejected:
  episode-level LR test is significant (`6.025`), but every positive
  beta worsens walk-forward accuracy. Breastfeed merge still causes `0`
  boundary differences, so bottle-only input policy stands.
- Wrote `feedcast/models/survival_hazard/research.md`, updated
  `model.py`, `design.md`, `CHANGELOG.md`, and
  `tests/test_survival_hazard.py`. `methodology.md` was left unchanged
  because the high-level mechanism is unchanged and it intentionally
  avoids frozen parameter values.
- Focused verification: `.venv/bin/python -m pytest -q` → `79 passed`.

### Sub-phase 4.3.5: Replay Candidate Isolation and Parallel Sweeps

*Fleshed out 2026-04-01 with Codex and Claude peer review. Ready to
execute before Sub-phase 4.4.*

The goal of this prereq is to make cross-candidate replay sweeps safe
and fast enough to test whether analog_trajectory still needs the
`full_traj_mae` shortlist proxy.

**Architecture choice:** keep `model.py` as the production source of
truth for constants. Do **not** externalize model constants into YAML.
That would duplicate configuration, add drift risk, and still require a
runtime injection mechanism. Use process-local module mutation instead.

**Implementation path:**

1. Refactor `feedcast/replay/runner.py` so serial and parallel tuning
   share one candidate-evaluation helper that returns
   `(params, MultiWindowResult)`.
2. Add process-isolated candidate evaluation inside `runner.py` using
   `ProcessPoolExecutor`.
   Prefer an explicit multiprocessing context rather than relying on
   platform defaults.
3. Each worker initializes its own replay context once:
   `load_export_snapshot(export_path=...)`, bottle-only scoring events,
   and generated cutoffs.
4. Each worker task evaluates exactly one candidate by wrapping
   `override_constants()` around `evaluate_multi_window()`. Module-level
   mutation is therefore process-local and safe.
5. Parent process remains responsible for baseline evaluation, ranking,
   artifact assembly, and serial-vs-parallel parity checks.

**API changes:**

- Add `parallel_candidates: bool = False` to `tune_model()`.
- Add `candidate_workers: int | None = None` to `tune_model()`.
- Keep existing window-level `parallel` flag, but when
  `parallel_candidates=True`, worker-local window parallelism should
  default to `False` unless explicitly benchmarked otherwise.

**Correctness constraints:**

- Parallel and serial tune runs must produce identical `baseline`,
  `best`, `candidates`, ranking, and top-level `replay_windows`.
- Baseline must still compete for `best` without leaking into
  `candidates`.
- Artifact schema must not change.
- No model code should need to read external config files for this work.

**Verification:**

- Add replay parity tests: serial vs candidate-parallel on a small sweep
  return identical results.
- Re-run existing replay tests to ensure schema stability.
- Validate parity on at least one previously completed real sweep
  (slot_drift or latent_hunger) before trusting the analog benchmark.

**Benchmark gate for Sub-phase 4.4:**

- Run a full analog canonical sweep on the current export with
  candidate-parallel replay.
- If the full analog canonical sweep completes in **15 minutes or
  less** wall-clock, Sub-phase 4.4 uses the full canonical sweep as the
  shipping gate. Sub-phase 4.4 later expands that authoritative sweep
  to 2688 candidates by making `HISTORY_MODE` tunable.
- If it exceeds 15 minutes, Sub-phase 4.4 falls back to a broadened
  two-stage approach: internal `full_traj_mae` ranking for shortlist
  generation plus canonical validation that explicitly includes the best
  `time_offset` config even if it is outside the top 10 by proxy.

**Deliverable:** parallel-safe replay tuning for candidate sweeps,
parity tests, and an analog benchmark result that determines Sub-phase
4.4's execution path.

### Sub-phase 4.3.5 implementation notes (2026-04-01)

- Implemented candidate-parallel replay tuning in
  `feedcast/replay/runner.py` using `ProcessPoolExecutor` with an
  explicit `spawn` multiprocessing context. Worker functions live in
  `runner.py`; no separate worker module or YAML config layer was
  added.
- Added `parallel_candidates` and `candidate_workers` to `tune_model()`
  and CLI flags `--parallel-candidates` / `--candidate-workers` in
  `scripts/run_replay.py`.
- Worker processes initialize their own export snapshot, bottle-only
  scoring events, and replay cutoffs once, then evaluate one candidate
  at a time with process-local `override_constants()`.
- Worker-local window parallelism is disabled in candidate-parallel mode
  to avoid nested oversubscription. Serial and candidate-parallel paths
  share the same candidate-evaluation helper.
- Added replay parity coverage:
  serial vs candidate-parallel tuning now compare equal in
  `tests/test_replay.py`, and invalid `candidate_workers` values raise
  a clear error.
- Real-sweep parity check passed on `slot_drift` with the current export
  (`DRIFT_WEIGHT_HALF_LIFE_DAYS=[1.0, 3.0]`,
  `LOOKBACK_DAYS=[5]`, `MATCH_COST_THRESHOLD_HOURS=[1.5]`):
  serial and candidate-parallel tune payloads matched exactly
  (excluding artifact path).
- **Benchmark gate passed.** Full analog canonical sweep on
  `export_narababy_silas_20260327.csv` with candidate-parallel replay:
  `1344` candidates evaluated in `14.484s` (`0.241m`). Sub-phase 4.4
  later discovered and fixed a `LOOKBACK_HOURS` override bug in
  analog_trajectory, so this benchmark remains authoritative for runtime
  only, not for parameter selection.
- **Sub-phase 4.4 path is now decided:** use the full canonical
  sweep as the shipping gate on the current export. Sub-phase 4.4 later
  expands that sweep to 2688 candidates when `HISTORY_MODE` becomes
  part of the canonical search space.
- Focused verification:
  `.venv/bin/python -m pytest -q` → `81 passed`.

### Sub-phase 4.4: analog_trajectory

**Must run after Sub-phase 4.3.5.**

**Current state:** Completed on `export_narababy_silas_20260327.csv`.

**Outcome:**
- Full-canonical replay is now the analog shipping gate.
- `HISTORY_MODE` became a real production parameter, expanding the
  canonical sweep from `1344` to `2688` candidates.
- The earlier raw-vs-episode rejection was overturned under the
  canonical metric.
- `ALIGNMENT="gap"` remains correct.
- Availability is 100%.

**Per-model context:** Large internal grid sweep (the most expensive
section), feature statistics, neighbor diagnostics, episode comparison.

**Sub-phase 4.4 implementation notes (2026-04-01):**

- Added `HISTORY_MODE` to `feedcast/models/analog_trajectory/model.py`
  so raw vs episode state history could be evaluated by the same
  canonical tuning machinery as every other analog constant.
- Fixed a correctness bug in `analog_trajectory`: `_state_features()`
  had captured `LOOKBACK_HOURS` as a default argument, so replay
  overrides were not actually changing lookback. The recorded sweep is
  post-fix and authoritative.
- Rewrote `feedcast/models/analog_trajectory/research.py` around:
  - two 1344-config diagnostic `full_traj_mae` sweeps (`raw`,
    `episode`)
  - one 2688-config canonical replay sweep across `HISTORY_MODE`,
    `LOOKBACK_HOURS`, `FEATURE_WEIGHTS`, `K_NEIGHBORS`,
    `RECENCY_HALF_LIFE_HOURS`, `TRAJECTORY_LENGTH_METHOD`, and
    `ALIGNMENT`
- Canonical baseline before the 4.4 update (old production config:
  `raw`, `72h`, `hour_emphasis`, `k=7`, `36h`, `median`, `gap`) scored
  headline `63.540`, count `88.186`, timing `46.107`, availability
  `24/24`.
- Final shipped config:
  `episode`, `12h`, `recent_only`, `k=5`, `72h`, `median`, `gap`.
  Canonical headline `69.899`, count `93.800`, timing `52.800`,
  availability `24/24`.
- Corrected raw-history canonical best still reached headline `69.2`,
  but episode history won at `69.9`, so the earlier raw-history policy
  was reopened and replaced.
- Updated `model.py`, `design.md`, `methodology.md`, `research.md`,
  `research_results.txt`, and `CHANGELOG.md`.
- Added `tests/test_analog_trajectory.py` to cover history-mode
  behavior, the lookback-override regression, and the shipped constants.

### Sub-phase 4.5: consensus_blend

**Must run after sub-phases 4.1–4.4** so that any constant changes
from earlier models are reflected in the `run_all_models()` calls.

**Questions to answer:**
- What is the canonical headline score with production constants?
- Do the selector-sweep winners agree with the current production
  selector constants under the canonical objective?
- Does the sweep surface any configuration with higher availability
  (more scored windows)?
- Is availability 100%?

**Per-model context:** Inter-episode gap analysis, model agreement
analysis, selector parameter sweep via `evaluate_multi_window()` with
custom `forecast_fn` closures. `research.md` should clarify which
sections are ensemble-specific diagnostics (inter-model spread) vs
dataset context (inter-episode gaps). The selector sweep serves both
purposes: it validates the current production constants and may
surface a better configuration. `research.md` should frame it as
design validation first — if the sweep also reveals a clearly
superior alternative, that is a finding worth acting on, not just a
confirmation exercise.

**Disposition guidance:** If the selector sweep identifies a
configuration with any headline improvement and equal or better
availability, update constants. If the production constants are
confirmed, document the validation in Conclusions.

**Deliverable:** Fresh `research_results.txt`,
`feedcast/models/consensus_blend/research.md`, and any `model.py` /
`CHANGELOG.md` updates if warranted.

### Sub-phase 4.5 implementation notes (2026-04-01)

- Canonical evaluation with the pre-update production selector
  (`radius=120`, `spread=180`, `conflict=105`, `penalty=0.25`) scored
  headline=`72.020`, count=`95.176`, timing=`54.994`, availability
  `24/24`.
- The canonical selector sweep evaluated 48 configurations
  (`2 radii × 2 spread caps × 3 conflict windows × 4 penalties`) on
  the same 24 replay windows. That initial pass still hit geometry and
  conflict boundaries, so the authoritative selector sweep was widened
  to 384 configurations (`4 radii × 4 spread caps × 6 conflict windows × 4 penalties`).
  Every configuration had `24/24` availability, so the decision reduced
  to headline score.
- Disposition: **Change.** The best canonical configuration was
  `radius=120`, `spread=150`, `conflict=135`, with headline=`72.996`,
  count=`95.434`, timing=`56.176`, availability `24/24`. This is a
  modest but real improvement (`+0.976` headline) driven mostly by
  tighter timing (`+1.182`) plus a smaller count gain (`+0.258`).
- `SPREAD_PENALTY_PER_HOUR` was flat across the tested values at the
  top of the surface, so production kept `0.25` rather than changing a
  selector weight without evidence of benefit.
- The widened grid resolves the earlier radius/spread boundary problem:
  `radius=120` and `spread=150` are now interior winners. Conflict
  needed widening beyond the original 105-minute cap; the final grid
  extends to 150 minutes because the recency-weighted lower quartile of
  real episode gaps is about 147 minutes on the current export.
- Updated `model.py`, `CHANGELOG.md`, `design.md`, and created
  `feedcast/models/consensus_blend/research.md`. Re-ran the research
  script after the constant change so `research_results.txt` ends in the
  final production state.
- Updated `tests/test_consensus_blend.py` to assert that two
  majority-supported episodes 136 minutes apart both survive, matching
  the shipped 135-minute conflict window.

### Sub-phase 4.6: Cross-model synthesis and cleanup

After all five model sub-phases are complete:

1. **Cross-model comparison:** Compare canonical headline scores across
   all five models. Note any surprises — a model that scores much
   better or worse than expected, availability differences, or cases
   where canonical and internal metrics disagree.

2. **Consistency check:** Verify all five `research.md` files follow
   the agreed structure template (header, overview, last canonical run,
   methods with canonical/diagnostic split, results with
   canonical/diagnostic split, conclusions, labeled open questions).
   Verify all "Last run" boxes reference the same export and
   dataset fingerprint.

3. **Cross-cutting question dedup:** Review the Open Questions sections
   across all five `research.md` files. If the same cross-cutting
   question appears in multiple models, promote it to
   `feedcast/research/index.md` and replace the duplicates with
   pointers.

4. **Shared research update:** If any canonical run or constant change
   alters a cross-model conclusion (e.g., episode clustering
   effectiveness, volume-gap relationship strength), update the
   relevant article in `feedcast/research/` and
   `feedcast/research/index.md`.

5. **Disposition summary:** Produce a brief summary of all five model
   dispositions (keep / change / unresolved) and present it to the
   user. Do not commit — the user will decide when to commit and may
   want to review changes first.

### Sub-phase 4.6 implementation notes (2026-04-01)

- **Cross-model comparison:** All five models evaluated on the same
  export (`20260327`, fingerprint `118402...`), all with 24/24
  availability. Headlines: Consensus Blend 73.0, Survival Hazard 72.7,
  Analog Trajectory 69.9, Slot Drift 68.4, Latent Hunger 66.9. Count
  scores are uniformly strong (90.8–95.4); timing scores are uniformly
  weak (47.9–56.6). The consensus blend adds only ~0.3 headline over
  the best individual model. No scoring anomalies or availability
  differences.
- **Consistency check passed.** All five `research.md` files follow the
  agreed template. All reference the same export and dataset fingerprint.
  Analog Trajectory was missing `Model-local` / `Cross-cutting`
  sub-headings under Open Questions — fixed.
- **Cross-cutting question dedup:** Two shared patterns promoted to
  `feedcast/research/index.md`:
  (1) Timing as shared bottleneck — count >> timing across all five
  models, with enough context to start a dedicated research article.
  (2) Internal vs canonical metric divergence — at least three models
  show local diagnostics and canonical replay preferring different
  constants. Added as open questions in index.md with model-specific
  evidence cited. All five model `research.md` cross-cutting sections
  replaced with concise pointers to index.md. Consensus Blend gained a
  new timing pointer that had been implicit in its diagnostic findings
  but absent from its open questions.
- **Shared research update:** Added episode-level history convergence to
  `Cross-Cutting Considerations` in index.md. All four scripted models
  independently produce better canonical scores with episode-collapsed
  history — noted as a cross-cutting observation, not a research article
  (the evidence is established).
- **Disposition summary:** All five models changed constants. Full
  details in the implementation notes for sub-phases 4.1–4.5.

## Phase 5: Documentation

### System contract

Phase 5 documents the relationships between the system's components
as a coherent contract. The individual file updates below implement
this contract in their respective locations. The contract:

- **`model.py` holds production constants.** Each model's tunable
  parameters are module-level variables in its `model.py`. This is the
  single source of truth for what the model does in production.
- **Tunable constants must be read at call time.** Replay overrides
  only affect code paths that read module-level constants during
  forecast execution. Do not capture tunable constants in Python
  default arguments, import-time caches, or other long-lived derived
  state that bypasses the current module value.
- **`analysis.py` and `research.md` generate and interpret evidence.**
  Analysis scripts call into replay infrastructure to produce canonical
  scores and parameter recommendations. `research.md` documents the
  findings. Neither modifies production behavior directly.
- **Replay and `tune_model()` evaluate alternatives but do not apply
  them.** `override_constants()` is temporary and restored after
  evaluation. The output is a ranked list — applying a change to
  `model.py` is a separate intentional step.
- **Tracker measures realized production accuracy.** One prediction,
  one score, single-window. This is distinct from replay/research,
  which estimate capability across multiple scenarios.
- **Evaluation defines the canonical scoring method.** `score_forecast()`
  in `scoring.py` is the single-window scorer. `evaluate_multi_window()`
  in `windows.py` aggregates across windows. Both are reused by replay
  and research — no component reimplements scoring.

### feedcast/evaluation/methodology.md

Update to serve as an agent-usable methodology guide (no separate
README.md exists in this directory; methodology.md serves that role).
Should cover:
- What `score_forecast()` measures and why (episode matching, horizon
  weighting, geometric mean)
- What event stream evaluation operates on by default (currently
  bottle-only actual events) and why that is distinct from model-local
  input construction
- Multi-window evaluation: rationale, window generation modes, recency
  weighting math, episode-boundary frequency bias
- Unavailable window handling and availability reporting
- How to call the API for a canonical evaluation
- Distinction from tracker (multi-window estimates capability; tracker
  measures realized accuracy)

### feedcast/replay/README.md

Rewrite as an agent-usable guide for conducting research:
- What replay does (rewind, run, score across windows)
- What input policy replay uses for canonical evaluation and how that
  may differ from a model's local event-building policy
- How to use it for parameter tuning (score mode, tune mode)
- Default configuration and what each parameter controls
- Operational note for candidate-parallel tuning: replay uses process
  isolation with `spawn`, so prefer normal file-backed entrypoints
  (`scripts/run_replay.py`, model `analysis.py`) over ad hoc stdin
  snippets when running cross-candidate sweeps
- How to interpret results (aggregate vs per-window, availability,
  what a good score looks like)
- Relationship to evaluation (replay uses evaluation, not the other
  way around)
- The research-tuning-production pipeline: research is advisory,
  `tune_model()` evaluates but does not apply, constants live in
  `model.py`, the decision to change is made by a human or agent

### feedcast/research/index.md — research hub playbook ✅

Complete. The research index is a centralized playbook with a unified
research convention (one directory convention, one `research.md`
template, one workflow), evolution tracking via `CHANGELOG.md`, shared
evaluation infrastructure reference, and standardized naming
(`research.md`, `analysis.py`, `artifacts/`). See implementation notes
below for the full history.

### README.md (partially complete)

Done:
- Convention tables aligned with unified research naming
- `research.md` added to model directory convention and repo layout
- Reading order updated to include `research.md`

Remaining:
- Make the event-construction split explicit: which parts of the system
  use canonical evaluation inputs versus model-local inputs, when
  bottle-only versus breastfeed-merged events are used, why those
  policies differ across layers
- Note the advisory nature of the research-tuning pipeline in the
  "Working with Models" section

### feedcast/evaluation/methodology.md

Update to serve as an agent-usable methodology guide (no separate
README.md exists in this directory; methodology.md serves that role).
Should cover:
- What `score_forecast()` measures and why (episode matching, horizon
  weighting, geometric mean)
- What event stream evaluation operates on by default (currently
  bottle-only actual events) and why that is distinct from model-local
  input construction
- Multi-window evaluation: rationale, window generation modes, recency
  weighting math, episode-boundary frequency bias
- Unavailable window handling and availability reporting
- How to call the API for a canonical evaluation
- Distinction from tracker (multi-window estimates capability; tracker
  measures realized accuracy)

### feedcast/replay/README.md

Rewrite as an agent-usable guide for conducting research:
- What replay does (rewind, run, score across windows)
- What input policy replay uses for canonical evaluation and how that
  may differ from a model's local event-building policy
- How to use it for parameter tuning (score mode, tune mode)
- Default configuration and what each parameter controls
- Operational note for candidate-parallel tuning: replay uses process
  isolation with `spawn`, so prefer normal file-backed entrypoints
  (`scripts/run_replay.py`, model `analysis.py`) over ad hoc stdin
  snippets when running cross-candidate sweeps
- How to interpret results (aggregate vs per-window, availability,
  what a good score looks like)
- Relationship to evaluation (replay uses evaluation, not the other
  way around)
- The research-tuning-production pipeline: research is advisory,
  `tune_model()` evaluates but does not apply, constants live in
  `model.py`, the decision to change is made by a human or agent

### feedcast/tracker.py documentation

Add a docstring or comment block explicitly stating:
- Tracker uses single-window evaluation: one prediction, one score
- This is intentional — it measures realized production accuracy
- Multi-window evaluation is for replay/research (estimated capability)

### Model research documentation verification ✅

Complete. All five `research.md` files verified during Phase 4.6
(same export, same fingerprint, consistent template, cross-cutting
questions promoted to `index.md`).

### Phase 5 implementation notes

**2026-04-01 — Research hub playbook.** Scope expansion: research hub
mechanics were originally planned for a subsequent effort but fit
naturally into Phase 5. Index.md rewritten as centralized playbook.
Evolution tracking via CHANGELOG.md added. Staleness boxes added to
both cross-cutting articles. Initial CHANGELOGs created. README
convention tables updated.

**2026-04-02 — Unified research convention.** After Codex review and
user feedback: naming standardized (`findings.md` → `research.md`,
`research.py` → `analysis.py`, `research_results.txt` → `artifacts/`).
Template unified — one set of section headers (`Last run`, `Overview`,
`Methods`, `Results`, `Conclusions`, `Open questions`, `Artifacts`)
works for both cross-cutting and model research. Workflow refined
(assess motivation first, CHANGELOG at end). All stale references
updated across model docs, source code comments, evaluation docs,
README, and analysis scripts. Multiple Codex review rounds resolved
convention consistency issues (CHANGELOG semantics, playbook scope,
model.py comments, README reading order, template overstatement).
85 tests pass throughout.

Cross-model conclusion to carry forward:
- Episode-level local history is now the production choice for
  slot_drift, latent_hunger, survival_hazard, and analog_trajectory.
  Analog had previously been the outlier. Phase 5 should reflect that
  this is now a consistent local-model pattern, while also noting that
  internal diagnostics can still diverge from canonical replay on the
  final tuned constant values.

## Implementation Notes

### Current state

Phases 1–4 are complete. Phase 5 is partially complete: the research
hub playbook and unified research convention are done (including
naming standardization and all Codex review rounds). The remaining
Phase 5 items are pure documentation tasks — no code changes, no
ordering dependencies between them:

- `feedcast/evaluation/methodology.md` rewrite
- `feedcast/replay/README.md` rewrite
- `feedcast/tracker.py` docstring
- README: event-construction split, advisory tuning pipeline note

### What NOT to do

- Do not modify `score_forecast()` in `scoring.py`. The single-window
  scorer is correct and unchanged.
- Do not modify the tracker to use multi-window evaluation.
- Do not delete internal diagnostic functions from research scripts
  (they have diagnostic value).
- Do not solve cross-candidate replay parallelism by moving production
  constants into YAML or any other second source of truth outside
  `model.py`.
- Do not add cross-candidate parallelism through shared-process module
  mutation. Use process isolation inside replay.
- Do not change model constants without canonical evidence. Any
  improvement in canonical headline score warrants a constant update.
  Add a `CHANGELOG.md` entry explaining the evidence.
- Do not add dependencies beyond what is already in the project.

### Key invariant

`score_model("some_model")` with default parameters should produce a
result that is directly comparable to the canonical evaluation section
in that model's `research.py`. Both use the same windows, same weights,
same scorer. This is guaranteed when research scripts call
`score_model()` directly for their canonical section.
