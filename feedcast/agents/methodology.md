# Agent Inference

Five-bucket cadence model that forecasts feeding episodes by projecting
forward from recency-weighted gap medians estimated in narrow
time-of-day windows. The model collapses nearby bottle feeds into
feeding episodes using the shared clustering rule, then examines the
most recent 7 days of episode-level history.

For each consecutive pair of episodes, it computes the inter-episode gap
and tags it by the hour of the episode that started the gap. Gaps are
assigned to five buckets: evening (17:00-19:00), pre-sleep
(19:00-22:00), deep night (22:00-04:00), early morning (04:00-07:00),
and daytime (07:00-17:00). Each gap receives a recency weight with a
48-hour exponential half-life, and the weighted median is taken within
each bucket. When the predicted evening feed lands in the 20:00 hour,
the pre-sleep gap is refined with a narrower weighted median built from
historical gaps that also started in the 20:00 hour; the final
pre-sleep estimate blends 40% of that narrow estimate with 60% of the
broader bucket estimate.

Starting from the last observed episode, the model projects each next
feed by applying the bucket-appropriate gap for the predicted feed's
start time. Predicted volume is the recency-weighted median of recent
episode volumes, held at 3.5 oz for all feeds. Total feed count is
anchored to the recency-weighted mean of daily episode counts from
recent complete days, which yields an 8-feed 24-hour schedule for this
run.
