# Slot Drift

Daily template model that identifies recurring feed slots and tracks
their drift over time. Instead of predicting individual gaps, it asks:
"what does a typical day look like, and how is it shifting?"

The model groups recent history into calendar days, determines a
canonical slot count (median daily feed count over the lookback
window), and builds a template of slot positions by taking the median
hour-of-day for each ordinal position across days that match the
canonical count. Each day's feeds are then aligned to the template
using the Hungarian algorithm with circular time-of-day distance.
Feeds that exceed a cost threshold (2 hours) are left unmatched,
which naturally handles cluster feeds and extras without special-casing.

After alignment, each slot has a position history across days. A
recency-weighted linear regression (half-life 3 days) estimates the
current position and drift rate for each slot. The forecast projects
today's unfilled slots and tomorrow's full schedule, with one
additional day of drift applied to tomorrow's positions. Volume is a
recency-weighted per-slot average.

Uses bottle-only events (no breastfeed merge). Breastfeeding volume
is estimated, not measured, and Slot Drift is primarily a timing model.

## Design Decisions

**Slot count of 8:** Derived from the median daily feed count across
recent complete days. The research confirmed this: complete days from
March 17-22, 2026 had counts of 8, 8, 9, 8, 10, 9 (median 8). The
earlier days (March 15-16) had inflated counts (11, 13) due to many
sub-1oz feeds during the initial adjustment period. Full-feed counts
(>= 1.5 oz) across ALL days were 7-9 with median exactly 8,
confirming the slot count independently. The slot count is not fixed:
it is recomputed from recent history on each run, so it adapts as the
baby's pattern evolves.

**Absolute clock time (not relative to first feed):** Slots are
anchored to time-of-day, not to the gap from the previous feed. This
matches the "structured schedule" framing and allows drift tracking
per slot position.

**Hungarian matching with cost threshold:** The Hungarian algorithm
(scipy.optimize.linear_sum_assignment) finds the globally optimal
assignment of feeds to slots, minimizing total time-of-day distance.
The 2-hour cost threshold rejects assignments where a feed is too far
from any slot. In the research, this naturally left cluster feeds
unmatched (e.g., March 21's four feeds in 3.4 hours produced two
unmatched extras) while correctly assigning all regular feeds.

**Circular time-of-day distance:** Feeds near midnight (e.g., 23:46)
need to match slots near midnight (e.g., 00:30). The circular
distance function handles the 24-hour wrap correctly. In practice
this matters for the "after midnight" slot which can occur on either
side of midnight depending on the day.

**Recency-weighted linear drift:** Per-slot drift is fit via weighted
linear regression with a 3-day half-life. This means recent days
dominate the trend estimate. Linear drift is sufficient for now;
curvature (second derivative) would require more data to estimate
reliably. If the drift is changing, the recency weighting naturally
adapts by down-weighting older observations.

**Template refinement:** The initial template is built from days with
exactly the canonical count, then refined by matching all days and
recomputing each slot's center from the matched positions. This
single refinement pass corrects any bias from the initial template
selection.

**Today's filled slots:** Before forecasting, the model matches the
current (incomplete) day's feeds against the template to identify
which slots are already filled. Only unfilled slots are forecast for
the remainder of today.

## Research

Data from export_narababy_silas_20260323.csv (81 bottle events,
March 15-23, 2026).

### Daily feed counts

| Date | Total | Full (>= 1.5oz) | Snack-sized |
| ---- | ----- | ---------------- | ----------- |
| Mar 15 | 11 | 8 | 3 |
| Mar 16 | 13 | 8 | 5 |
| Mar 17 | 8 | 7 | 1 |
| Mar 18 | 8 | 8 | 0 |
| Mar 19 | 9 | 9 | 0 |
| Mar 20 | 8 | 8 | 0 |
| Mar 21 | 10 | 9 | 1 |
| Mar 22 | 9 | 7 | 2 |

March 15-16 had many small feeds during the early adjustment period.
March 17 onward settled to 8-10 total feeds (median 8).

### Template from 8-feed days

Using the three days with exactly 8 feeds (Mar 17, 18, 20), the
median per-position template:

| Slot | Template time | Typical gap |
| ---- | ------------- | ----------- |
| 1 | 00:30 | (start of cycle) |
| 2 | 04:02 | 3.5h |
| 3 | 07:20 | 3.3h |
| 4 | 09:51 | 2.5h |
| 5 | 12:37 | 2.8h |
| 6 | 15:58 | 3.3h |
| 7 | 18:31 | 2.5h |
| 8 | 21:10 | 2.6h |

Roughly 3-hour intervals throughout the day.

### Trial alignment results

Hungarian matching with a 2-hour cost threshold across all recent
days:
- 8-feed days (Mar 17, 18, 20): all feeds matched, max cost 1.89h
- 9-feed days (Mar 19, 22): 8 matched, 1 unmatched extra each
- 10-feed day (Mar 21): 8 matched, 2 unmatched (13:18 and 14:48,
  the cluster feeds between slots 5 and 6)

The matching correctly identified cluster feeds as extras without any
special-casing.
