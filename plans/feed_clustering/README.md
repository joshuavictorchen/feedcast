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

1. Apply the cluster rule to each model's predictions before the blend
   votes (collapse cluster predictions into episode-predictions first).
2. Update consensus blend `design.md`.
3. Flag: the 90-minute conflict rule may need revisiting once episode
   semantics are in place.

### Phase 5: Model and agent awareness

1. Update research index (`feedcast/research/index.md`):
   - Add table entry for the feed clustering article.
   - Reverse the "cluster feeding should usually be handled inside model
     logic" guidance. New framing: the cluster DEFINITION is shared
     (research + evaluation), cluster HANDLING remains model-local.
2. Update each model's `design.md` to document how the model relates to
   clusters (even if the answer is "no changes needed").
3. Update agent prompt with a concise cluster rule description.

### Phase 6: Reports

1. Default report tables to collapsed (episode) view.
2. Raw event counts available as expandable diagnostics.
3. Include secondary diagnostics: raw predicted count, episode count,
   number of collapsed attachments (if low complexity).

### Phase 7: Documentation and cleanup

1. Update README.
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
