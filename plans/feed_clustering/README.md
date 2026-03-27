# Feed Clustering — Plan

Living plan for the feed-clustering feature. Permanent code and docs will
land in `feedcast/research/feed_clustering/`, `feedcast/clustering.py`,
and the evaluation/model docs. This plan and session transcripts are
committed under `plans/feed_clustering/` for historical context.

## Problem

Small bottle feeds (often <= 1.5 oz) that occur close in time to a
larger feed are not independent hunger events. They are "attachments" —
top-ups or continuations of a single feeding episode. Treating them as
separate events:

- Inflates daily feed count (11 raw feeds on 3/24 vs ~8 real episodes).
- Creates artificially short inter-feed gaps that corrupt gap-based models.
- Contaminates the volume-gap relationship (a 3.0 oz feed "followed by"
  a 51-minute gap looks like a counterexample when it's actually one episode).
- Makes evaluation unfair: models that correctly predict 8 episodes get
  penalized for "missing" 3 attachment feeds.

### Motivating examples (3/24)

```
14:45  3.00 oz  ─┐
15:35  1.00 oz  ─┘ cluster: 50-min gap, 1.0 oz attachment

20:16  3.60 oz  ─┐
21:25  1.00 oz  ─┤ cluster: 69-min gap, then 50-min gap
22:15  1.50 oz  ─┘
```

### Historical small feeds (<= 1.5 oz) in the dataset

```
03/15  14:10  1.00 oz    03/16  14:58  0.50 oz    03/21  14:48  1.00 oz
03/15  20:15  0.50 oz    03/16  15:19  0.50 oz    03/22  13:06  1.00 oz
03/15  23:06  0.50 oz    03/16  16:53  1.50 oz    03/22  17:53  1.25 oz
03/16  11:18  1.00 oz    03/16  17:34  1.50 oz    03/24  15:35  1.00 oz
03/16  11:52  0.50 oz    03/16  21:13  1.00 oz    03/24  21:25  1.00 oz
03/16  14:58  0.50 oz    03/17  12:30  1.00 oz    03/24  22:15  1.50 oz
                         03/19  12:18  1.50 oz
```

Not all of these are cluster attachments. Some may be genuine small
independent feeds (e.g., after a long gap). The research process must
distinguish them.

## Decisions

### D1: Cluster definition is centralized; handling is model-local

The definition of "what is a cluster" lives in shared research and is
consumed by evaluation. Models receive raw events, are made AWARE of the
cluster rule, and decide independently how to handle clusters in their
logic.

### D2: Models may or may not predict clusters

Models predict feed events over the next 24 hours. Some of those may
correspond to cluster episodes. If a model's predictions form a cluster
pattern (by the evaluation rule), evaluation collapses them before
scoring. If a model predicts one feed per cluster, that works too.
Either way, evaluation handles it consistently.

Models are not required to predict cluster internal structure. They are
not penalized for predicting it either (because it gets collapsed).

### D3: Evaluation is cluster-aware

Evaluation applies the cluster rule to BOTH actuals and predictions
before scoring:
- Actual feeds are grouped into episodes using the cluster rule.
- Predicted feeds are also grouped using the same rule.
- Scoring matches episode-predictions against actual-episodes.

This ensures models aren't penalized for predicting attachment feeds and
aren't rewarded for missing them.

### D4: First feed is the canonical timestamp

A cluster is identified by its first feed's timestamp. "The cluster
starting at 14:45" = the episode whose first feed is at 14:45.

### D5: Volume handling — sum for simplicity

Episode volume = sum of constituent feed volumes. This preserves total
intake information for models that use volume (Latent Hunger). The
volume is a side effect, not a primary target.

### D6: Cluster = feed followed by nearby feeds

Directionality is forward: a feed starts a cluster, and subsequent feeds
within a time window are attachments. Attachments are almost always
smaller, but not necessarily so. The anchor is always the first feed
in the cluster. No minimum anchor volume — a small feed can anchor a
cluster.

### D7: Rule derived via episode-boundary detection

The research process finds a deterministic episode-boundary rule, not
just "attachment thresholds." For each consecutive pair of feeds, the
rule decides: "continues current episode" vs "starts new episode."
Volume may be one feature among others — the rule shape is not
presupposed. The exact rule comes from labeled data, not intuition.

The research is revisitable: as new data arrives, re-label, re-derive,
update the rule if needed. The process is documented for repeatability
across sessions.

### D8: Documentation lives in the research hub

- Research article: `feedcast/research/feed_clustering/` — phenomenon,
  evidence, derived rule, labeled data
- Evaluation methodology: references the research, explains how
  cluster-aware scoring works
- Research index: table entry for the article
- README: brief mention in the evaluation section
- No new top-level invariants doc

### D9: Scoring and reports default to collapsed view

Evaluation scores solely on collapsed (episode-level) signals. Reports
default to showing collapsed episode counts and timings. Raw event
counts available as expandable diagnostics. Per-model report tables
also show collapsed outputs by default.

Secondary raw-event diagnostics (raw predicted count, collapsed episode
count, number of collapsed attachments) are included if they don't add
significant complexity.

### D10: Hard cutover for tracker

No scorer versioning or backfill of historical retrospectives. When
cluster-aware scoring ships, `tracker.json` can be nuked and restarted.
Historical comparisons are not a concern.

### D11: Episode grouping lives in `feedcast/clustering.py`

Dedicated module, not in `data.py`. This keeps `data.py` focused on
CSV parsing and domain types. The clustering module contains:
- Cluster rule constants (derived from research)
- `FeedEpisode` dataclass
- `group_into_episodes()` function

### D12: Labels support ambiguity

The YAML labels file supports positive examples, excluded feeds, and
ambiguous cases. This prevents the research tool from silently treating
every unlabeled small feed as a negative example.

## Architecture

```
Raw CSV
  │
  ▼
load_activities()               # unchanged — raw bottle + breastfeed
  │
  ▼
build_feed_events()             # unchanged — bottle-centered events
  │
  ├──► Models receive raw FeedEvents
  │      (cluster-aware: they know the rule, handle as they choose)
  │
  ▼
group_into_episodes()           # NEW — feedcast/clustering.py
  │                             #   input: list[FeedEvent]
  │                             #   output: list[FeedEpisode]
  │
  ├──► Evaluation: collapse BOTH actuals and predictions, then score
  ├──► Replay: inherits via same scoring function
  ├──► Consensus Blend: collapse each model's predictions before voting
  └──► Reports: default to episode view, raw in diagnostics
```

Models continue to receive `list[FeedEvent]`. The episode grouping is a
scoring/reporting concern, not a data-loading concern. Models that want
to reason about episodes can call `group_into_episodes()` themselves.

## Implementation Plan

### Phase 0: Cleanup — migrate constants, remove forecast timestamp mutation

**Status: DONE**

**Constant migration:** Four constants removed from `data.py`:

| Constant | Action | Detail |
|---|---|---|
| `SNACK_THRESHOLD_OZ` | Moved to `models/slot_drift/research.py` | Only consumer; defined as local constant with comment |
| `MIN_INTERVAL_HOURS` | Moved to `models/consensus_blend/model.py` | Only consumer; added alongside other selector constants |
| `MAX_INTERVAL_HOURS` | Removed | Dead code (no consumer anywhere) |
| `MIN_POINT_GAP_MINUTES` | Removed entirely | Only consumer was the timestamp nudging logic, which was also removed |

**Forecast timestamp mutation:** Removed the 45-minute minimum gap
enforcement from `normalize_forecast_points()` in `models/shared.py`.
The function now filters to the horizon window, sorts by time, clips
volume to [0.1, 8.0], and recomputes gap_hours — but does NOT adjust
timestamps. Model output is preserved as-is.

`MIN_POINT_GAP_MINUTES` was removed entirely (not migrated) because
its only consumer was the nudging logic.

Implementation verified: 29 tests pass, all model imports resolve,
normalizer preserves close-together points correctly.

Note: Consensus Blend still has its own 90-minute conflict rule
(`SELECTION_CONFLICT_WINDOW_MINUTES` in `consensus_blend/model.py`).
That should be revisited after clustering lands (Phase 4).

### Phase 1: Research — cluster labeling and episode-boundary rule

**Status: DONE**

1. Create `feedcast/research/feed_clustering/` with the standard layout.
2. Create `labels.yaml` — walk through historical data in an interactive
   session with the user. User identifies clusters. Labels support
   positive (cluster), excluded, and ambiguous markers.
3. Build `analysis.py` that:
   - Loads the labels and the current export data.
   - Computes features for each consecutive-feed boundary (gap, volumes
     of both feeds, time of day, etc.).
   - Finds the tightest deterministic episode-boundary rule that
     separates cluster-internal boundaries from episode boundaries.
   - Outputs the derived rule and its accuracy on the labeled data.
4. Write `findings.md` documenting the phenomenon, the labeling process,
   and the derived rule. Written so fresh agents can repeat the process.

**Implementation notes:**

Labeled 96 boundaries from the 20260325 export (97 bottle feeds since
`DATA_FLOOR = 2026-03-15`): 17 `same_episode`, 79 `new_episode`,
0 `ambiguous`.

Chosen rule:

    same_episode if gap <= 73 minutes,
    or if gap <= 80 minutes and second feed <= 1.50 oz

On labeled data: fp=0, fn=0. The base gap of 73 minutes is the tightest
threshold with zero false collapses (a confirmed non-cluster at 74.8
minutes prevents any higher base). The extension to 80 minutes for
small second feeds captures one additional cluster at 77.5 min / 1.25 oz
while rejecting non-clusters in that gap range (74.8 min / 2.00 oz,
76.1 min / 3.00 oz). The 80-minute extension window provides headroom
beyond the tightest fit of 78 minutes.

Gap-only fallback (`gap <= 73`): fp=0, fn=1. Documented in findings.md
as the conservative alternative.

Research artifacts: `feedcast/research/feed_clustering/`
(`labels.yaml`, `analysis.py`, `findings.md`, `artifacts/`).

### Phase 2: Shared episode grouping function

**Status: DONE**

1. Create `feedcast/clustering.py` with:
   - Cluster rule constants (from Phase 1 research).
   - `FeedEpisode` dataclass (canonical time, total volume, constituent
     feed count, constituent feeds list).
   - `group_into_episodes(events: list[FeedEvent]) -> list[FeedEpisode]`.
   - Also handles `list[ForecastPoint]` for collapsing predictions.
2. Unit tests for grouping: normal feeds, single-attachment clusters,
   multi-attachment clusters, edge cases (small anchor, back-to-back
   clusters, etc.).

**Phase 2 implementation constraints (resolved before coding):**

- Models keep raw histories. Phase 2 defines the shared cluster rule and
  episode grouper; it does NOT change model inputs.
- Inputs must already be strictly chronological. The grouper validates
  this and fails fast on unsorted or non-increasing timestamps; it does
  not sort internally.
- Episode chaining is transitive across consecutive boundaries. If
  `A -> B` and `B -> C` both satisfy the rule, all three feeds belong to
  one episode even if `A -> C` exceeds the extension window.
- No minimum anchor volume. A small feed may start an episode.
- Episode timestamp = first constituent timestamp.
- Episode volume = sum of constituent `volume_oz` values only. The
  bottle-vs-breastfeed split on `FeedEvent` is ignored for clustering.
- Prefer the simplest API that supports both `FeedEvent` and
  `ForecastPoint` cleanly. Do not add extra abstraction unless a later
  phase actually needs it.

**Implementation notes:**

Created `feedcast/clustering.py` with one public function and one
dataclass. The module encodes the adopted Phase 1 rule as three
constants (`BASE_GAP_MINUTES = 73`, `EXTENSION_GAP_MINUTES = 80`,
`SECOND_FEED_MAX_OZ = 1.50`).

**`FeedEpisode`** (frozen dataclass): `time` (first constituent's
timestamp), `volume_oz` (sum), `feed_count`, and `constituents`
(typed tuple of the original `FeedEvent` or `ForecastPoint` objects).
No `gap_hours` field — consumers derive inter-episode gaps themselves.

**`group_into_episodes()`** accepts `Sequence[FeedEvent]` or
`Sequence[ForecastPoint]`. Both types share `.time` and `.volume_oz`
by coincidence of their existing design, so no protocol or wrapper
is needed. The function:
- Validates strictly increasing timestamps (`ValueError` on
  duplicates or out-of-order).
- Walks feeds linearly, comparing each feed to the *last constituent*
  of the current episode (not the anchor) for transitive chaining.
- Returns one `FeedEpisode` per group. A non-clustered feed becomes
  a single-constituent episode.

**How downstream phases use this:**
- Phase 3 (evaluation): call `group_into_episodes()` on both actuals
  and predictions before Hungarian matching.
- Phase 4 (consensus): collapse each model's predictions into episodes
  before voting.
- Phase 5 (models): models may optionally import and call the function
  on their history to reason about episodes. No model is required to.
- Phase 6 (reports): default to episode counts/timings in tables.

**Tests:** 18 tests in `tests/test_clustering.py` covering empty input,
singletons, simple and multi-attachment clusters, both arms of the
boundary rule (base gap and extension), boundary inclusivity, small
anchors, back-to-back clusters, transitive chaining beyond the
extension window, unsorted and duplicate timestamp rejection,
constituent preservation, and ForecastPoint inputs. Full suite: 47
tests pass (18 new + 29 existing).

### Phase 3: Evaluation integration

**Status: DONE**

1. Update `score_forecast()` to apply episode grouping to both actuals
   and predictions before matching.
2. Update `feedcast/evaluation/methodology.md` to document cluster-aware
   scoring and reference the research article.
3. Verify replay inherits cluster-aware scoring via the same code path.
4. Hard cutover: no tracker versioning. Nuke `tracker.json` if needed.

**Phase 3 implementation constraints (resolved before coding):**

- Clustering goes inside `score_forecast()`, not at call sites. Replay,
  tracker, and consensus-blend research inherit automatically.
- One episode = one weight, based on canonical timestamp. No
  constituent-count scaling.
- Episode matching uses first-feed timestamp (D4).
- Do not collapse event caches separately — only the scorer collapses.
- Do not auto-delete `tracker.json` in code. Delete manually when the
  scorer semantics change.
- Rename `predicted_count`, `actual_count`, `matched_count` on
  `ForecastScore` to episode-based names. These flow through tracker,
  report, and replay — all references must be updated.
- Raw feed count diagnostics deferred to Phase 6 (reports). Phase 3
  ships episode-only scoring.
- Cross-cutoff actual cluster policy: group actuals using pre-cutoff
  context so that post-cutoff attachment feeds correctly attach to
  their pre-cutoff anchors, then exclude episodes whose canonical
  timestamp precedes the cutoff. This means a post-cutoff attachment
  whose anchor is pre-cutoff is excluded from scoring rather than
  scored as a phantom standalone. Document this behavior in the
  evaluation methodology. Revisit if retrospective data shows the
  edge case matters in practice.

**Implementation notes:**

Updated `score_forecast()` in `feedcast/evaluation/scoring.py` to
collapse both actuals and predictions into episodes before matching.
The collapse is internal to the scorer — all call sites (tracker,
replay, consensus-blend research) inherit automatically.

**Scoring flow after Phase 3:**
1. Group ALL actuals (including pre-cutoff) into episodes using
   `group_into_episodes()`.
2. Filter: keep episodes whose canonical time is in
   `(prediction_time, evaluation_end]`. Cross-cutoff episodes excluded.
3. Window predictions to scoring window, then group into episodes.
4. Compute horizon weights, match episodes via Hungarian assignment.
5. Return episode-level counts and scores.

**Field rename (hard cutover):** `ForecastScore` fields renamed from
`predicted_count`/`actual_count`/`matched_count` to
`predicted_episode_count`/`actual_episode_count`/`matched_episode_count`.
All downstream references updated: `RetrospectiveResult` in tracker.py,
serialization in tracker.py and report.py, replay runner output,
consensus-blend research, and `scripts/run_replay.py` display.

**Methodology:** `feedcast/evaluation/methodology.md` updated with a
new "Episode collapsing" section documenting cluster-aware scoring,
the cross-cutoff exclusion policy, and a reference to the research
article. All "feed" language updated to "episode" throughout.

**Report template:** `feedcast/templates/report.md.j2` updated to
describe retrospective comparison in episode terms. Column header
changed to "Episodes (Pred/Actual/Matched)".

**Hard cutover:** `tracker.json` reset to empty `{"runs": []}`.
`report/diagnostics.yaml` deleted (contained stale `*_feed_count`
keys). `report/report.md` and chart PNGs are stale snapshots from
the prior pipeline run but are NOT deleted — they are rendered
artifacts that will regenerate on the next `run_forecast.py`
invocation. Deleting them would break README links for no functional
gain. The report's data is correct for when it was generated; only
the terminology will update on next render.

**Tests:** 3 new tests in `tests/test_scoring.py` (actual cluster
collapse, predicted cluster collapse, cross-cutoff exclusion).
Full suite: 50 tests pass (3 new + 47 existing).

**Replay verification:** Replay inherits cluster-aware scoring via
the same `score_forecast()` code path. No separate changes needed.

### Phase 4: Consensus blend update

**Status: DONE**

1. Collapse each model's predictions into episodes before candidate
   generation. Convert episodes to representative ForecastPoints
   (canonical timestamp, summed volume) and pass those to the existing
   candidate generator and MILP selector.
2. Update the research script sweep to collapse before generating
   candidates, matching production behavior.
3. Update consensus blend `design.md` and `methodology.md`.
4. Evaluate the 90-minute conflict window under episode semantics via
   the existing research sweep.

**Phase 4 decision points (resolved):**

- **DP1: 90-minute conflict window.** Chose (b): ran the research sweep
  with collapse in place. All conflict window values (75/90/105) produce
  identical scores, but direct comparison of selected candidates shows
  75 allows more realistic close-episode predictions on at least one
  cutoff (03/24: 76-minute episode pair selected at 75, suppressed at
  90). Lowered to 75 minutes — just above the 73-minute base cluster
  rule. One confirmed non-cluster gap at 74.8 minutes is still
  suppressed; 75 is a conservative floor, not an exact match.
- **DP2: Research script scope.** Chose (a): sweep updated to collapse
  before generating candidates, matching production.
- **DP3: Documentation scope.** Chose (b): `design.md` and
  `methodology.md` both updated.
- **DP4: Diagnostics scope.** Chose (b): deferred to Phase 6.
- **DP5: Existing diagnostics key semantics.** Chose (a):
  `component_forecast_counts` still reports raw per-model point counts.
  The collapse happens inside `_blend_by_sequence_selection()` on a copy;
  diagnostics in `run_consensus_blend()` read from the original.

**Phase 4 implementation constraints (resolved by agents):**

- Collapse happens inside `_blend_by_sequence_selection()`, before
  `generate_candidate_clusters()`. Each model's ForecastPoints are
  grouped into episodes via `group_into_episodes()` and converted back
  to ForecastPoints (canonical timestamp, summed volume, inter-episode
  gap). A new dict of collapsed forecasts is passed to candidate
  generation; the caller's forecasts are not mutated.
- The existing candidate generator, MILP selector, and output conversion
  operate unchanged on collapsed inputs.
- `gap_hours` on collapsed ForecastPoints is computed as inter-episode
  gap but is not load-bearing: candidate generation and selection do not
  use it, and `_candidates_to_forecast_points()` recomputes it for
  output.
- Consensus output is episode-level. If multiple models predict cluster
  internal structure, those predictions collapse before voting. This is
  consistent with D2 (models are not penalized for predicting clusters)
  and Phase 3 evaluation semantics.
- Testing confirms the integration: (a) collapse happens and changes
  blend output when cluster predictions are present; (b) without
  cluster predictions, output is identical. Clustering logic coverage is
  Phase 2's responsibility.

**Implementation notes:**

Added `_collapse_to_episode_points()` and `_collapse_forecast_dict()`
helpers to `consensus_blend/model.py`. The blend calls
`_collapse_forecast_dict(component_forecasts)` at the top of
`_blend_by_sequence_selection()` before any candidate generation. The
research script's `_sweep_selector_parameters()` imports and uses the
same `_collapse_forecast_dict()` when building per-cutoff data, so
sweep results match production behavior.

**Sweep results (20260325 export, 4 cutoffs):** All parameter
combinations (radius 90/120, spread 150/180, conflict 75/90/105,
penalty 0.25–5.0) produce identical scores: 64.0 headline, 85.6 count,
48.0 timing. However, direct comparison of selected candidates shows
conflict=75 admits a 76-minute episode pair on the 03/24 cutoff that
conflict=90 suppresses. Lowered `SELECTION_CONFLICT_WINDOW_MINUTES`
from 90 to 75 — a more principled floor just above the 73-minute base
cluster rule. One confirmed non-cluster gap at 74.8 minutes is still
suppressed; 75 is a conservative floor, not an exact match.

**Documentation:** `design.md` updated with a new "Episode collapsing
before candidate generation" section. `methodology.md` updated to
describe pre-voting episode collapsing. `CHANGELOG.md` updated with
the behavior change.

**Tests:** 3 new tests in `tests/test_consensus_blend.py`:
`test_collapse_merges_cluster_predictions_into_one_episode` (unit test
for the collapse helper),
`test_cluster_predictions_produce_same_consensus_as_clean` (integration
test confirming cluster predictions produce the same consensus as clean
single-episode predictions), and
`test_conflict_window_admits_76_minute_episode_pair` (regression test
locking in the lowered 75-minute conflict window). Full suite: 53 tests
pass (3 new + 50 existing).

### Phase 5: Model and agent cluster awareness

**Status: IN PROGRESS**

Phase 5 makes every model and agent cluster-aware. This includes
implementation changes to models — not just documentation. Each model
gets its own sub-phase covering research, design, methodology, and
implementation. Shared context updates come first.

**Resolved decisions:**

- **Research index scope.** Research index gets the table entry, reversed
  cross-cutting bullet, and updated hypotheses. The feed-vs-episode
  distinction must be clear to fresh agents in three places: research
  index, top-level README, and agent prompt. Evaluation methodology
  already covers this (Phase 3 added an "Episode collapsing" section)
  and does not need further changes in 5a.
- **Model scope.** Phase 5 is NOT documentation-only. Each of the four
  non-consensus models gets a full update cycle: research, design,
  methodology, implementation. Consensus gets a revisit sub-phase at the
  end.
- **Agent prompt.** The prompt gets an episode-first recommendation and
  points to the source files for the rule (`feedcast/clustering.py`,
  `feedcast/research/feed_clustering/`), rather than hardcoding threshold
  values that could go stale.

**Resolved assumptions:**

- Each model sub-phase is self-contained: run research, decide on
  changes, implement, update docs. Decision points within a model
  sub-phase are resolved during implementation based on research data.
- Each model sub-phase ends with a `CHANGELOG.md` entry using the
  canonical `Problem` / `Research` / `Solution` sections. If replay
  fails and no runtime change ships, the `Solution` section records the
  not-shipped decision.
- `methodology.md` is updated when the model's report-facing
  description changes.
- Consensus blend was already updated in Phase 4. Sub-phase 5f revisits
  it to check whether upstream model changes affect consensus behavior —
  not to redo Phase 4 work.

**Ship/no-ship guardrail:** Model-local research scripts have different
metrics (template analysis for Slot Drift, trajectory MAE for Analog,
gap-based proxies for Latent Hunger and Survival Hazard). These generate
hypotheses about whether episode-level inputs help. Runtime changes only
ship if replay scoring (`scripts/run_replay.py <slug>`) shows the
headline score improves or holds. If replay degrades, document the
finding in `design.md` and do not ship the implementation change.

**Sub-phase ordering:** The four model sub-phases (5b–5e) are
independent and can be tackled in any order. Listed order is not
binding. Expected cluster impact by model, highest first:

1. **Latent Hunger** — growth rate fitted directly from contaminated
   (volume, gap) pairs.
2. **Survival Hazard** — Weibull distributions fitted to contaminated
   inter-feed gaps.
3. **Analog Trajectory** — core neighbor-search features (`last_gap`,
   `mean_gap`, `last_volume`, `mean_volume`) include cluster noise.
4. **Slot Drift** — most resilient (timing-only, Hungarian matching
   tolerates extras), but raw cluster feeds still inflate daily count
   and can affect template construction.

#### Sub-phase 5a: Shared context

**Status: DONE**

Note: `feedcast/evaluation/methodology.md` already documents episode
collapsing (added in Phase 3). No further evaluation doc changes needed.

1. Update `feedcast/research/index.md`:
   - Add table entry for the feed clustering article.
   - Reverse the cross-cutting bullet: cluster DEFINITION is shared
     (research + evaluation + consensus blend), cluster HANDLING is
     model-local.
   - Update "Current Hypotheses" bullet on daily feed count stability
     to distinguish episode count from raw feed count.
   - Add open question: does episode-level analysis change the
     volume-gap relationship findings?
2. Update top-level `README.md`:
   - Add a clear definition of feeds vs. episodes early in the document.
   - Note that evaluation scores at the episode level.
3. Update `feedcast/agents/prompt/prompt.md`:
   - Add a section on feeding episodes and the cluster rule.
   - Point to `feedcast/clustering.py` and
     `feedcast/research/feed_clustering/` for the rule definition.
   - Recommend episode-level forecasting: "Optimize for feeding
     episodes; cluster internal structure is optional and will be
     collapsed before scoring."

**Implementation notes:**

Updated three files:

- `feedcast/research/index.md`: Added feed clustering table entry with
  summary conclusion and link to findings. Reversed cross-cutting bullet
  from "should be model-local" to "definition is shared, handling is
  model-local." Updated hypothesis and open question from "feed count"
  to "episode count." Added new open question about episode-level
  volume-gap relationship.
- `README.md`: Added "Feeds vs. Episodes" subsection under "The
  Forecasting Challenge" defining episodes, pointing to the research,
  and noting evaluation collapses both sides. Updated "Evaluation"
  section language from "feeds" to "episodes."
- `feedcast/agents/prompt/prompt.md`: Added "Feeding Episodes" section
  between the intro and "Freedom." Points to `clustering.py` and
  research for the rule (no hardcoded thresholds). Recommends
  episode-level forecasting; cluster internal structure is optional.

All 53 tests pass. No Python runtime changes. The agent prompt update
steers agents toward episode-level forecasting, which is a behavioral
change for LLM agents but not for scripted models or the pipeline.

#### Sub-phase 5b: Slot Drift

**Status: DONE**

Slot Drift builds a daily template from raw feed times. Cluster feeds
can inflate the daily count and create spurious template slots.

1. Run `research.py` with episode-level history. Compare template slot
   count and positions to raw-input results.
2. Decide whether to group history into episodes before template
   construction. If so, implement.
3. Update `design.md` with cluster relationship and any design changes.
4. Update `methodology.md` if report-facing description changes.
5. Update `CHANGELOG.md` with `Problem` / `Research` / `Solution`
   sections documenting the sub-phase outcome.
6. Run tests and replay verification.

**Implementation notes:**

Updated `research.py` to add an episode-level analysis section that
compares raw vs. episode daily counts, template positions, and trial
alignment. Key finding: median slot count drops from 9 to 8 with
episode-level history.

Implemented episode-level template building in `model.py` using the
shared `episodes_as_events()` helper. The episode-level history is used
for template building, matching, drift estimation, and today's
filled-slot identification. Raw history is still passed to
`_build_forecast_points()` for last-known-feed-time gap computation.

**Replay gate (20260325 export, 03/24→03/25 window):**

| Metric | Baseline (raw) | Episode-level | Delta |
|--------|----------------|---------------|-------|
| Headline | 53.46 | 53.74 | +0.28 |
| Count F1 | 91.77 | 80.98 | -10.79 |
| Timing | 31.14 | 35.67 | +4.52 |
| Matched | 8/9 | 7/9 | -1 |

Headline improved. Trade-off: one fewer matched episode, but the
matched episodes have better timing. The episode-level template (8
slots) is a more accurate representation of the daily feeding
pattern.

Diagnostics keys renamed: `daily_feed_counts` → `daily_episode_counts`,
`total_feeds` → `total_episodes` to match the new episode-level
semantics and avoid ontology drift.

**Tests:** 4 new tests in `tests/test_slot_drift.py`:
`test_cluster_feeds_collapse_into_one_event` (unit test for
`episodes_as_events()`), `test_independent_feeds_stay_separate`
(non-cluster feeds preserved), `test_cluster_day_does_not_inflate_slot_count`
(integration test confirming cluster top-ups don't inflate median slot
count), `test_diagnostics_use_episode_keys` (diagnostics use
episode-level naming). Full suite: 57 tests pass (4 new + 53
existing). `design.md`, `methodology.md`, `CHANGELOG.md` updated.

#### Sub-phase 5c: Analog Trajectory

**Status: DONE (no runtime change)**

Analog Trajectory uses `last_gap`, `mean_gap`, `last_volume`, and
`mean_volume` as core features for neighbor search. Cluster-internal
gaps and volumes pollute these features.

1. Run `research.py` with episode-level history. Compare feature
   distributions and neighbor quality to raw-input results.
2. Decide whether to compute features from episode-level history. If
   so, implement.
3. Update `design.md` with cluster relationship and any design changes.
4. Update `methodology.md` if report-facing description changes.
5. Update `CHANGELOG.md` with `Problem` / `Research` / `Solution`
   sections documenting the sub-phase outcome.
6. Run tests and replay verification.

**Implementation notes:**

Updated `research.py` with an episode-level comparison section that
builds an episode state library, compares feature distributions, and
runs fold-causal evaluation alongside the raw sweep. Episode-level
features are cleaner (gaps longer and tighter, volumes higher and
less noisy) and neighbor retrieval accuracy improved substantially
in research metrics.

Prototyped episode-level history for replay evaluation, then reverted
it after the replay gate failed. **Replay headline dropped** (66.65
vs. 68.22 baseline, -1.57). The episode model predicted fewer episodes
than the baseline (7 vs. 9 actual) because episode-level trajectories
contain fewer events, making the median trajectory length shorter and
producing fewer forecast points. Count F1 drop (91.16 vs. 100.0)
outweighed timing improvement (48.73 vs. 46.55).

**Decision: not shipped.** Model change reverted. Raw feed history
preserved. The episode-level comparison is kept in `research.py` for
future reference.

Additionally, extracted the private Slot Drift helper to
`feedcast/clustering.py` as the shared public function
`episodes_as_events()`. Slot Drift updated to import from the shared
location.

`design.md` updated with "Cluster relationship" section documenting
the research finding and explaining why the model tolerates cluster
noise. `CHANGELOG.md` updated with research findings and the
not-shipped decision. No `methodology.md` change needed (no runtime
behavior change).

57 tests pass (no new tests needed — no runtime change shipped).

#### Sub-phase 5d: Latent Hunger

**Status: DONE**

Latent Hunger fits a hunger growth rate from (volume, gap) pairs.
Cluster-internal pairs are noise: a short gap after any volume biases
the growth rate estimate upward (short gap in denominator → large
implied rate), causing the model to predict shorter gaps than the real
inter-episode rhythm.

1. Run `research.py` with episode-level history. Compare growth rate
   estimates and forecast quality to raw-input results.
2. Decide whether to fit growth rate from episode-level
   (total_volume, inter-episode_gap) pairs. If so, implement.
3. Update `design.md` with cluster relationship and any design changes.
4. Update `methodology.md` if report-facing description changes.
5. Update `CHANGELOG.md` with `Problem` / `Research` / `Solution`
   sections documenting the sub-phase outcome.
6. Run tests and replay verification.

**Implementation notes:**

Updated `research.py` with an episode-level comparison section that
converts events to episodes, compares volume-gap statistics, and runs
the multiplicative grid search on episode-level data. Key finding:
all walk-forward metrics improve substantially (gap1_MAE −20%,
fcount_MAE −19%). The optimal satiety rate shifted from 0.800 (raw)
to 0.257 (episode).

Implemented episode-level history in `model.py` using the shared
`episodes_as_events()` helper. Raw feeds are collapsed into episodes
before growth-rate estimation, simulation volume computation, and
current hunger state tracking. Also re-tuned two parameters:

- **SATIETY_RATE 0.386 → 0.257** — re-fitted on episode-level data.
  The lower rate fits the real volume-gap relationship without cluster
  inflation. The surface is shallow (0.012h gap1_MAE difference from
  the old value) but consistently confirmed by replay cross-sweep.
- **RECENCY_HALF_LIFE_HOURS 48 → 168** — with cluster noise removed,
  the growth rate estimate benefits from broader averaging across the
  full lookback window. Control experiment confirmed the interaction:
  raw data degrades at longer half-lives while episode data improves.
  168h = LOOKBACK_DAYS × 24, giving 50% weight at the lookback
  boundary.

Diagnostics keys renamed: `recent_events_in_window` →
`recent_episodes_in_window`, `fit_events_used` → `fit_episodes_used`.

**Replay gate (20260325 export, 03/24→03/25 window):**

| Metric | Baseline (raw) | Episode-level | Delta |
|--------|----------------|---------------|-------|
| Headline | 73.351 | 78.471 | +5.120 |
| Count F1 | 94.242 | 100.0 | +5.758 |
| Timing | 57.091 | 61.576 | +4.485 |
| Episodes | 10/9/9 | 9/9/9 | perfect |

**Tests:** 4 new tests in `tests/test_latent_hunger.py`:
`test_cluster_pairs_excluded_from_growth_rate` (cluster-internal gaps
absent from fit details after episode conversion),
`test_episode_volume_used_for_hunger_reset` (episode summed volume
produces deeper reset), `test_diagnostics_use_episode_keys`
(diagnostics use episode naming), `test_satiety_rate_is_episode_tuned`
(SATIETY_RATE is 0.257). Full suite: 61 tests pass (4 new + 57
existing). `design.md`, `methodology.md`, `CHANGELOG.md` updated.

#### Sub-phase 5e: Survival Hazard

Survival Hazard fits Weibull distributions to inter-feed gaps per
day-part. Cluster-internal gaps create a bimodal artifact that doesn't
reflect real hunger dynamics.

1. Run `research.py` with episode-level history. Compare Weibull
   shape/scale estimates to raw-input results.
2. Decide whether to fit distributions to inter-episode gaps. If so,
   implement.
3. Update `design.md` with cluster relationship and any design changes.
4. Update `methodology.md` if report-facing description changes.
5. Update `CHANGELOG.md` with `Problem` / `Research` / `Solution`
   sections documenting the sub-phase outcome.
6. Run tests and replay verification.

#### Sub-phase 5f: Consensus Blend revisit

Consensus already collapses predictions into episodes before voting
(Phase 4). This sub-phase checks whether upstream model changes from
5b–5e affect consensus behavior.

1. Run consensus research sweep with updated model outputs.
2. Compare results to Phase 4 baseline.
3. If parameters need adjustment, update and document.
4. If no changes needed, note in this plan section and move on.

### Phase 6: Reports

1. Default report tables to collapsed (episode) view.
2. Raw event counts available as expandable diagnostics.
3. Include secondary diagnostics: raw predicted count, episode count,
   number of collapsed attachments (if low complexity).

### Phase 7: Documentation and cleanup

1. Final README cleanup (feed-vs-episode framing added in 5a; this is
   residual polish only).
2. Final update to this plan file with completion notes.

## Plans Directory Convention

All implementation plans live under `plans/` at the repo root. Each plan
gets its own subdirectory:

```
plans/
  README.md                         # table of contents: folder, one-liner, date
  feed_clustering/
    README.md                       # this file — the living plan
    transcripts/                    # session transcripts (.jsonl copies)
```

- `plans/README.md` — one entry per plan: folder name, concise summary,
  implementation date.
- `plans/<name>/README.md` — the living plan document.
- `plans/<name>/transcripts/` — copies of session .jsonl files for full
  context on what was implemented and how.

## Notes

- The existing `SNACK_THRESHOLD_OZ = 1.5` is Slot Drift's snack filter
  for research. It may happen to align with the derived cluster rule,
  but they serve different purposes. Slot Drift keeps its own constant
  after migration.
- The 45-minute forecast normalization enforcement predates cluster
  semantics. Removing it is a cleanup, not a clustering feature.
- The consensus blend's 90-minute conflict rule is conceptually separate
  from the 45-minute normalizer. Both warrant revisiting after clustering
  but on different timelines.
- `tracker.json` history is expendable. Hard cutover when scoring
  semantics change.
