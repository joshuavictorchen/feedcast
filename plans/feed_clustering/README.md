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

**Constant migration:** Four constants in `data.py` are model concerns,
not data-parsing concerns:

| Constant | From | To | Reason |
|---|---|---|---|
| `SNACK_THRESHOLD_OZ` | `data.py:22` | `models/slot_drift/research.py` | Only used there |
| `MIN_INTERVAL_HOURS` | `data.py:23` | `models/consensus_blend/model.py` | Only used there |
| `MAX_INTERVAL_HOURS` | `data.py:24` | Remove | Dead code (unused) |
| `MIN_POINT_GAP_MINUTES` | `data.py:25` | `models/shared.py` | Forecast normalization |

**Forecast timestamp mutation:** `normalize_forecast_points()` in
`models/shared.py` currently nudges predicted timestamps forward to
enforce a 45-minute minimum gap. This silently rewrites model output.
Remove the time mutation — keep horizon clipping and sort order, but
stop adjusting timestamps. If a model emits non-monotonic times, fail
fast rather than silently fixing.

Note: this is a behavioral change for all models. The 45-minute gap
doesn't block current cluster examples (closest is 50 min apart), but
removing it makes model output honest and unblocks tighter clusters
if they appear in future data.

Separately: Consensus Blend has its own 90-minute conflict rule
(`consensus_blend/model.py`). That should be revisited after clustering
lands but is not part of Phase 0.

### Phase 1: Research — cluster labeling and episode-boundary rule

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

### Phase 2: Shared episode grouping function

1. Create `feedcast/clustering.py` with:
   - Cluster rule constants (from Phase 1 research).
   - `FeedEpisode` dataclass (canonical time, total volume, constituent
     feed count, constituent feeds list).
   - `group_into_episodes(events: list[FeedEvent]) -> list[FeedEpisode]`.
   - Also handles `list[ForecastPoint]` for collapsing predictions.
2. Unit tests for grouping: normal feeds, single-attachment clusters,
   multi-attachment clusters, edge cases (small anchor, back-to-back
   clusters, etc.).

### Phase 3: Evaluation integration

1. Update `score_forecast()` to apply episode grouping to both actuals
   and predictions before matching.
2. Update `feedcast/evaluation/methodology.md` to document cluster-aware
   scoring and reference the research article.
3. Verify replay inherits cluster-aware scoring via the same code path.
4. Hard cutover: no tracker versioning. Nuke `tracker.json` if needed.

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
