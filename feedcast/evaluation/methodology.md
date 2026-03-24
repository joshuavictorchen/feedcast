# Retrospective Evaluation

Scores a forecast's bottle-feed timing accuracy against what actually
happened. The metric answers two questions separately, then combines
them into one headline score.

## Count accuracy (weighted F1)

Did the forecast predict the right number of feeds?

Predicted feeds are matched one-to-one against actual feeds using the
Hungarian algorithm (optimal bipartite assignment). Each feed is weighted
by its position in the horizon — earlier feeds count more — using
exponential decay with a 24-hour half-life. Pairs more than 4 hours
apart are blocked from matching (the guardrail), so a prediction
cannot claim credit for a feed it clearly was not aiming at.

The count score is the weighted F1 of matched vs total feeds:

- **Precision**: weighted fraction of predicted feeds that found a match.
  Penalizes over-prediction.
- **Recall**: weighted fraction of actual feeds that were matched.
  Penalizes under-prediction.
- **F1**: harmonic mean of precision and recall, so both directions
  hurt symmetrically for feeds at the same horizon position.

## Timing accuracy (weighted timing credit)

For the feeds that matched, how close were the timestamps?

Each matched pair receives a soft timing credit:

    timing_credit = 2^(-error_minutes / 30)

This gives 100% credit at 0 error, 50% at 30 minutes, 25% at 60
minutes, and so on — no cliff, just a smooth half-life curve. The
per-pair credits are averaged, weighted by the actual feed's horizon
weight, so tight timing on an early feed matters more than tight
timing on a late one.

## Headline score

The headline is the geometric mean of count and timing, scaled 0–100:

    headline = sqrt(count_score * timing_score) * 100

Geometric mean prevents one strong sub-score from masking a weak one.
A model that nails count but is sloppy on timing (or vice versa) cannot
hide behind the average.

## Feed matching (Hungarian assignment)

Matching is the most consequential design decision. We chose optimal
bipartite assignment (Hungarian algorithm) over alternatives:

- **Dynamic Time Warping / Needleman-Wunsch**: preserves temporal
  ordering, but feeds are unordered events on a timeline — the baby
  does not care about sequence, just "when is the next one." An ordering
  constraint can produce worse matches when feeds shift past each other.
- **Earth Mover's Distance**: elegant for distributions, but less
  interpretable per-feed. Harder to diagnose which predictions were
  good and which were bad.

The cost matrix is padded so that each feed can match a zero-cost dummy
partner instead of being forced into a bad real-world pairing. This
naturally handles different counts without a separate unmatched-penalty
constant. The assignment prioritizes early-horizon matches when pairings
conflict, because the final metric values those feeds more highly.

## Horizon weighting

Both count and timing weight feeds by their distance from the prediction
time:

    horizon_weight = 2^(-hours_from_prediction / 24)

With a 24-hour half-life, the last feed in the horizon still counts half
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
| Max match gap | 4 hours | Feeds are typically 2.5–5 hours apart; anything beyond 4 hours is noise |
| Headline combiner | Geometric mean | Both sub-scores must be decent for a good headline |
