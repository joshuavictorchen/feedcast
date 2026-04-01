# Slot Drift Design Decisions

## Episode-level history

The model collapses raw feed history into episodes before building
the daily template. This removes cluster-internal feeds (top-ups and
continuations) that would otherwise inflate the daily count and create
spurious template slots. The episode rule is defined in
`feedcast/clustering.py` (see `feedcast/research/feed_clustering/`
for derivation). Episode-level history gives a more stable slot count
(median 8 vs. 9 with raw feeds on the 20260325 export) and slightly
better headline replay score (+0.28) due to improved timing accuracy.

## Slot count

The canonical slot count is the median daily episode count across
recent complete days in the lookback window (default 5 days). It is
not fixed: it is recomputed from recent history on each run, so it
adapts as the baby's pattern evolves.

## Absolute clock time

Slots are anchored to time-of-day, not to the gap from the previous
feed. This matches the "structured schedule" framing and allows drift
tracking per slot position.

## Hungarian matching with cost threshold

The Hungarian algorithm (scipy.optimize.linear_sum_assignment) finds
the globally optimal assignment of episodes to slots, minimizing total
time-of-day distance. The 1.5-hour cost threshold rejects assignments
where an episode is too far from any slot. Episodes that exceed the
threshold are left unmatched.

## Circular time-of-day distance

Feeds near midnight (e.g., 23:46) need to match slots near midnight
(e.g., 00:30). The circular distance function handles the 24-hour
wrap correctly.

## Recency-weighted linear drift

Per-slot drift is fit via weighted linear regression with a 1-day
half-life. Recent days dominate the trend estimate. Linear drift is
sufficient for now; curvature (second derivative) would require more
data to estimate reliably.

## Template refinement

The initial template is built from days with exactly the canonical
count, then refined by matching all days and recomputing each slot's
center from the matched positions. One refinement pass corrects bias
from the initial template selection.

## Today's filled slots

Before forecasting, the model matches the current (incomplete) day's
feeds against projected slot positions (not the raw template) so that
drift doesn't cause misclassification. Only unfilled slots are
forecast for the remainder of today.

## Breastfeed handling

Builds bottle-only events locally (no breastfeed merge). Breastfeeding
volume is estimated and noisy. Slot Drift is a timing model; adding
uncertain breastfeed volume would not improve slot alignment.
