# Evaluation Methodology

Feedcast evaluates forecast quality at two levels: **single-window
scoring** measures accuracy over one prediction horizon, and
**multi-window evaluation** aggregates scores across many retrospective
windows to estimate a model's overall capability.

Single-window scoring is the atomic building block. The tracker uses
it once per run to measure realized production accuracy. Multi-window
evaluation calls it repeatedly across different cutoff points, weights
the results by recency, and produces an aggregate — this is what replay
and research scripts use to evaluate and tune models.

## Event inputs

Evaluation operates on **bottle-only feed events** — the output of
`build_feed_events(activities, merge_window_minutes=None)`. Breastfeed
volume is excluded from the scoring stream because breastfeed volume
estimates are too noisy to anchor timing accuracy measurements against.

This is distinct from model-local event construction. Each model builds
its own input events and may choose to merge nearby breastfeed volume
into bottle feeds (via a non-`None` merge window). That choice affects
what the model sees as input, but the actual events it is scored against
are always bottle-only. The separation is intentional: models are free to
use whatever input representation helps them forecast, but they are all
judged against the same ground truth.

Both actuals and predictions are collapsed into **feeding episodes**
before matching (see below). The episode boundary rule is shared and
deterministic — defined in `feedcast/clustering.py`, derived from
labeled data in
[`feedcast/research/feed_clustering/`](../research/feed_clustering/).

## Single-window scoring

`score_forecast()` in `scoring.py` scores one forecast over one observed
window. It answers two questions separately, then combines them.

### Episode collapsing

Before matching, both actuals and predictions are collapsed into
feeding episodes using the shared cluster rule. Close-together feeds
that form a single feeding event (e.g., a bottle followed by a top-up)
are grouped into one episode. The episode's timestamp is the first
constituent's time; its volume is the sum.

This prevents models from being penalized for "missing" attachment
feeds that are not independent hunger events, and prevents models from
getting inflated credit for predicting them separately.

The cluster boundary rule: two consecutive feeds belong to the same
episode if the gap is <=73 minutes, or <=80 minutes when the later
feed is <=1.50 oz. Chaining is transitive.

**Cross-cutoff clusters.** Actuals are grouped using pre-cutoff context
so that a post-cutoff attachment correctly joins its pre-cutoff anchor.
Episodes whose canonical timestamp precedes the cutoff are then excluded
from scoring. This means a post-cutoff attachment whose anchor is
pre-cutoff is excluded rather than scored as a phantom standalone feed.

### Count accuracy (weighted F1)

Did the forecast predict the right number of episodes?

Predicted episodes are matched one-to-one against actual episodes using
the Hungarian algorithm (optimal bipartite assignment). Each episode is
weighted by its position in the horizon — earlier episodes count more —
using exponential decay with a 24-hour half-life. Pairs more than 4
hours apart are blocked from matching (the guardrail), so a prediction
cannot claim credit for an episode it clearly was not aiming at.

The count score is the weighted F1 of matched vs total episodes:

- **Precision**: weighted fraction of predicted episodes that found a
  match. Penalizes over-prediction.
- **Recall**: weighted fraction of actual episodes that were matched.
  Penalizes under-prediction.
- **F1**: harmonic mean of precision and recall.

### Timing accuracy (weighted timing credit)

For the episodes that matched, how close were the timestamps?

Each matched pair receives a soft timing credit:

    timing_credit = 2^(-error_minutes / 30)

This gives 100% credit at 0 error, 50% at 30 minutes, 25% at 60
minutes — no cliff, just a smooth half-life curve. The per-pair credits
are averaged, weighted by the actual episode's horizon weight, so tight
timing on an early episode matters more than tight timing on a late one.

### Headline score

The headline is the geometric mean of count and timing, scaled 0-100:

    headline = sqrt(count_score * timing_score) * 100

Geometric mean prevents one strong sub-score from masking a weak one.
A model that nails count but is sloppy on timing (or vice versa) cannot
hide behind the average.

### Episode matching (Hungarian assignment)

Matching uses optimal bipartite assignment (Hungarian algorithm). The
cost matrix is padded so that each episode can match a zero-cost dummy
partner instead of being forced into a bad real-world pairing. This
naturally handles different counts without a separate unmatched-penalty
constant. The assignment prioritizes early-horizon matches when pairings
conflict, because the final metric values those episodes more highly.

Alternatives considered:

- **Dynamic Time Warping / Needleman-Wunsch**: preserves temporal
  ordering, but episodes are unordered events on a timeline — an
  ordering constraint can produce worse matches when episodes shift
  past each other.
- **Earth Mover's Distance**: elegant for distributions, but less
  interpretable per-episode.

### Horizon weighting

Both count and timing weight episodes by their distance from the
prediction time:

    horizon_weight = 2^(-hours_from_prediction / 24)

With a 24-hour half-life, the last episode in the horizon still counts
half as much as the first — a mild preference for near-term accuracy.

### Partial horizons

When fewer than 24 hours have elapsed since the last prediction, the
scorer evaluates only the observed window. Predictions and actuals
beyond the window are excluded — not penalized and not credited. The
score is accompanied by a coverage ratio so the consumer knows how much
of the horizon was actually verified.

### Parameters (current scoring assumptions)

These constants are part of the current evaluation design. They are
explicit assumptions chosen for operational usefulness, not empirically
settled truths. The scorer has to start somewhere; future sensitivity
work may justify changing these values or the cutoff policy.

| Parameter | Default | Rationale |
| --------- | ------- | --------- |
| Horizon weight half-life | 24 hours | Mild near-term preference without ignoring the tail |
| Timing credit half-life | 30 minutes | Strong preference for tight timing without a hard cutoff |
| Max match gap | 4 hours | Episodes are typically 2.5-5 hours apart; anything beyond 4 hours is noise |
| Headline combiner | Geometric mean | Both sub-scores must be decent for a good headline |

## Multi-window evaluation

`evaluate_multi_window()` in `windows.py` runs `score_forecast()`
across many retrospective cutoff points and aggregates the results with
recency weighting. This estimates how well a model forecasts across
diverse scenarios rather than measuring one-shot accuracy.

### Rationale

A single 24-hour replay window can overfit to recent outliers. A
multi-window approach evaluates from multiple cutoff points within
observed history, weighting recent windows more heavily. This produces
more robust parameter recommendations and a more honest picture of
model capability.

### Window generation

Each evaluation window is a 24-hour horizon starting from a cutoff
point. Two cutoff generation modes are available:

**Episode-boundary mode** (default): Cutoffs are placed at feeding
episode boundaries within the lookback range. This means high-frequency
feeding periods produce more cutoffs and therefore more aggregate
weight — the model is tested more heavily where feeding activity is
densest. The bias is intentional and partially mitigated by using
episode-level boundaries (collapsed from raw feeds) rather than
individual feed events.

```python
from feedcast.evaluation.windows import generate_episode_boundary_cutoffs

cutoffs = generate_episode_boundary_cutoffs(
    episodes=episodes,          # pre-computed FeedEpisode list
    latest_activity_time=latest, # upper bound of observed data
    lookback_hours=96.0,         # how far back to place cutoffs
)
```

**Fixed-step mode**: Cutoffs are placed at regular intervals. Useful as
a fallback when episode-boundary mode is too expensive for large sweeps.

```python
from feedcast.evaluation.windows import generate_fixed_step_cutoffs

cutoffs = generate_fixed_step_cutoffs(
    latest_activity_time=latest,
    earliest_activity_time=earliest,
    lookback_hours=96.0,
    step_hours=12.0,
)
```

Both modes always include the **replay-equivalent cutoff**
(`latest_activity_time - 24h`), preserving backward compatibility with
the most recent complete window. No cutoffs are generated beyond the
lookback boundary.

### Recency weighting

Each window's score is weighted by its recency relative to the latest
cutoff:

    weight = 2^(-age_hours / half_life_hours)

where `age_hours` is the distance from the cutoff to the most recent
cutoff. The most recent cutoff always has weight 1.0. With the default
36-hour half-life, a window 36 hours older than the latest has half the
influence, a window 72 hours older has one quarter, and so on.

### Aggregate score

The headline aggregate is the weighted mean of per-window headline
scores, using the recency weights. Per-window count and timing
breakdowns are preserved for diagnostics.

### Unavailable windows

When a model cannot produce a forecast at a given cutoff (e.g.,
insufficient warmup history), that window is **excluded** from the
weighted aggregate — not counted as zero. The result reports both
`window_count` (total attempted) and `scored_window_count` (those that
produced a score) so availability is visible as a separate concern.

A model that scores well on 15 of 20 windows but cannot forecast from
older cutoffs is judged on those 15 windows, with its 75% availability
noted alongside. For tuning, candidates are ranked by availability tier
first (most scored windows), then by headline — a candidate cannot win
by scoring well on a small subset while being unavailable on harder
windows.

### API usage

Canonical evaluation for a model with production constants:

```python
from feedcast.replay import score_model

result = score_model("slot_drift")
# result["replay_windows"]["aggregate"]["headline"]
```

Canonical evaluation with parameter overrides:

```python
result = score_model("slot_drift", overrides={"LOOKBACK_DAYS": 5})
```

Parameter sweep across candidates:

```python
from feedcast.replay import tune_model

result = tune_model(
    "slot_drift",
    candidates_by_name={
        "LOOKBACK_DAYS": [5, 7, 9],
        "DRIFT_WEIGHT_HALF_LIFE_DAYS": [1.0, 2.0, 3.0],
    },
)
# result["best"]["params"], result["best"]["replay_windows"]["aggregate"]["headline"]
```

For custom forecast functions (e.g., consensus blend selector sweeps),
call `evaluate_multi_window()` directly:

```python
from feedcast.evaluation.windows import evaluate_multi_window

result = evaluate_multi_window(
    forecast_fn=my_forecast_function,  # Callable[[datetime], Forecast]
    scoring_events=bottle_events,
    cutoffs=cutoffs,
    latest_activity_time=latest,
)
```

See [`feedcast/replay/README.md`](../replay/README.md) for full CLI
usage and tuning workflow.

### Default parameters

| Parameter | Default | Purpose |
| --------- | ------- | ------- |
| Lookback | 96 hours | How far back to generate replay windows |
| Half-life | 36 hours | Recency decay for window weighting |
| Cutoff mode | Episode boundary | Cutoffs placed at feeding episode boundaries |
| Step hours | 12 hours | Step size for fixed-step mode only |
| Parallel | Off | Thread-level parallelism across windows within one evaluation |

## Tracker vs. replay evaluation

The tracker (`feedcast/tracker.py`) and replay
(`feedcast/replay/runner.py`) both use `score_forecast()`, but they
measure different things:

| | Tracker | Replay / Research |
| - | ------- | ----------------- |
| **What it measures** | Realized production accuracy | Estimated capability across scenarios |
| **Windows** | Single: one prediction vs. what actually happened | Multiple: many retrospective cutoffs within observed history |
| **When it runs** | Each pipeline run, comparing the prior prediction to the new export | On demand, for model evaluation and parameter tuning |
| **Recency weighting** | N/A (one window) | Exponential decay across windows |
| **Purpose** | "How did we do?" | "How well does the model forecast in general?" |

The tracker should not adopt multi-window evaluation. Its job is to
record the realized outcome of one production prediction — that is
inherently a single window.
