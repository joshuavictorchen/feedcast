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
