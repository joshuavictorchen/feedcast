# Agent Inference

Slot-anchored cadence model that forecasts feeding episodes by combining
time-of-day slot medians with gap-level verification against a five-bucket
inter-episode gap profile.

The model collapses nearby bottle feeds into feeding episodes using the
shared clustering rule, then examines the most recent 7 days of
episode-level history. It assigns each episode to one of eight daily
slots based on its time of day (mid-morning, lunch, afternoon, evening,
pre-bed, first wake, deep night, morning wake). Within each slot, the
recency-weighted median clock time (48-hour exponential half-life) gives
the typical time that feed occurs.

These slot medians anchor the forecast to the baby's daily rhythm rather
than cascading gaps forward from the last episode. Each predicted feed
lands near the historical median for its slot, so a timing error in one
feed does not propagate to subsequent feeds.

Gap-level verification uses a five-bucket profile (daytime 07:00-17:00,
evening 17:00-19:00, pre-sleep 19:00-22:00, deep night 22:00-04:00,
early morning 04:00-07:00) computed from recency-weighted inter-episode
gaps. The forecast is checked to ensure each gap between consecutive
predictions falls within the plausible range for its time-of-day bucket.

Feed count is anchored to the recency-weighted mean of daily episode
counts from recent complete days (7.7 for this run, rounded to 8).
Whether a pre-bed feed is included depends on its recent frequency: it
appeared on 4 of the last 5 complete evenings, so it is included.
Predicted volume is 3.5 oz per episode, the modal volume across recent
episodes.
