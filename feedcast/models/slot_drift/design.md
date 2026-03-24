# Slot Drift Design Decisions

## Slot count

The canonical slot count is the median daily feed count across recent
complete days in the lookback window (default 7 days). It is not fixed:
it is recomputed from recent history on each run, so it adapts as the
baby's pattern evolves.

The initial research (see research.py) showed that once the early
chaotic days (March 15-16) drop out of the lookback window, the
median converges to 8. While those days are still in-window, the
median is 9 due to March 16's inflated count (13 feeds, many
snack-sized). This is expected and correct behavior: the model uses
whatever data is in the window. The slot count will naturally settle
as the window moves forward.

## Absolute clock time

Slots are anchored to time-of-day, not to the gap from the previous
feed. This matches the "structured schedule" framing and allows drift
tracking per slot position.

## Hungarian matching with cost threshold

The Hungarian algorithm (scipy.optimize.linear_sum_assignment) finds
the globally optimal assignment of feeds to slots, minimizing total
time-of-day distance. The 2-hour cost threshold rejects assignments
where a feed is too far from any slot. In the research, this naturally
left cluster feeds unmatched (e.g., March 21's four feeds in 3.4
hours produced two unmatched extras) while correctly assigning all
regular feeds.

## Circular time-of-day distance

Feeds near midnight (e.g., 23:46) need to match slots near midnight
(e.g., 00:30). The circular distance function handles the 24-hour
wrap correctly.

## Recency-weighted linear drift

Per-slot drift is fit via weighted linear regression with a 3-day
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

Uses bottle-only events (merge_window_minutes=None). Breastfeeding
volume is estimated and noisy. Slot Drift is a timing model; adding
uncertain breastfeed volume would not improve slot alignment.
