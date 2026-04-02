# Retrospective Evaluation

Scores a forecast's bottle-feed timing accuracy against what actually
happened. The metric answers two questions separately, then combines
them into one headline score.

## Episode collapsing (cluster-aware scoring)

Before matching, both actuals and predictions are collapsed into
**feeding episodes** using the shared cluster rule. Close-together
feeds that represent a single feeding event (e.g., a bottle followed
by a top-up) are grouped into one episode. The episode's timestamp is
the first constituent's time; its volume is the sum.

This prevents models from being penalized for "missing" attachment
feeds that are not independent hunger events, and prevents models
from getting inflated credit for predicting them separately.

The cluster boundary rule: two consecutive feeds belong to the same
episode if the gap is ≤ 73 minutes, or ≤ 80 minutes when the later
feed is ≤ 1.50 oz. Chaining is transitive. Full derivation:
[`feedcast/research/feed_clustering/research.md`](../research/feed_clustering/research.md).

**Cross-cutoff clusters.** Actuals are grouped using pre-cutoff context
so that a post-cutoff attachment correctly joins its pre-cutoff anchor.
Episodes whose canonical timestamp precedes the cutoff are then excluded
from scoring. This means a post-cutoff attachment whose anchor is
pre-cutoff is excluded rather than scored as a phantom standalone feed.
This edge case is rare (requires a cluster spanning the exact cutoff
boundary) and may be revisited if retrospective data shows it matters.

## Count accuracy (weighted F1)

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
- **F1**: harmonic mean of precision and recall, so both directions
  hurt symmetrically for episodes at the same horizon position.

## Timing accuracy (weighted timing credit)

For the episodes that matched, how close were the timestamps?

Each matched pair receives a soft timing credit:

    timing_credit = 2^(-error_minutes / 30)

This gives 100% credit at 0 error, 50% at 30 minutes, 25% at 60
minutes, and so on — no cliff, just a smooth half-life curve. The
per-pair credits are averaged, weighted by the actual episode's horizon
weight, so tight timing on an early episode matters more than tight
timing on a late one.

## Headline score

The headline is the geometric mean of count and timing, scaled 0–100:

    headline = sqrt(count_score * timing_score) * 100

Geometric mean prevents one strong sub-score from masking a weak one.
A model that nails count but is sloppy on timing (or vice versa) cannot
hide behind the average.

## Episode matching (Hungarian assignment)

Matching is the most consequential design decision. We chose optimal
bipartite assignment (Hungarian algorithm) over alternatives:

- **Dynamic Time Warping / Needleman-Wunsch**: preserves temporal
  ordering, but episodes are unordered events on a timeline — the baby
  does not care about sequence, just "when is the next one." An ordering
  constraint can produce worse matches when episodes shift past each
  other.
- **Earth Mover's Distance**: elegant for distributions, but less
  interpretable per-episode. Harder to diagnose which predictions were
  good and which were bad.

The cost matrix is padded so that each episode can match a zero-cost
dummy partner instead of being forced into a bad real-world pairing.
This naturally handles different counts without a separate
unmatched-penalty constant. The assignment prioritizes early-horizon
matches when pairings conflict, because the final metric values those
episodes more highly.

## Horizon weighting

Both count and timing weight episodes by their distance from the prediction
time:

    horizon_weight = 2^(-hours_from_prediction / 24)

With a 24-hour half-life, the last episode in the horizon still counts half
as much as the first — a mild preference for near-term accuracy that
avoids ignoring the tail entirely. The half-life is configurable if
future tuning warrants a sharper or flatter curve.

## Partial horizons

When fewer than 24 hours have elapsed since the last prediction, the
scorer evaluates only the observed window. Predictions and actuals
beyond the window are excluded — not penalized and not credited. The
score is accompanied by a coverage ratio so the consumer knows how much
of the horizon was actually verified.

Historical averages weight each retrospective by its observed
evidence mass (the integral of the horizon weight over the observed
window, normalized by the full-horizon integral). This prevents thin
partial windows from inflating the historical mean.

## Parameters

| Parameter | Default | Rationale |
| --------- | ------- | --------- |
| Horizon weight half-life | 24 hours | Mild near-term preference without ignoring the tail |
| Timing credit half-life | 30 minutes | Strong preference for tight timing without a hard cutoff |
| Max match gap | 4 hours | Episodes are typically 2.5–5 hours apart; anything beyond 4 hours is noise |
| Headline combiner | Geometric mean | Both sub-scores must be decent for a good headline |
