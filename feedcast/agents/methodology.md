# Agent Inference

Four-bucket cadence projection. The model collapses raw bottle feeds
into feeding episodes using the shared clustering rule (73-minute base
gap, 80-minute extension for small top-ups), then examines the most
recent 7 days of episode-level history with exponential recency
weighting (48-hour half-life).

Each inter-episode gap is tagged by the clock hour of the feed that
starts the gap and assigned to one of four sub-periods: evening
(19:00-22:00), deep night (22:00-03:00), early morning (03:00-07:00),
and daytime (07:00-19:00). Within each sub-period, the recency-weighted
median of observed gaps yields a characteristic gap duration. For this
run: evening 3.82h, deep night 3.74h, early morning 2.64h, daytime
2.59h.

Starting from the cutoff, the forecast steps forward by applying the
sub-period gap that matches the clock hour of each predicted feed's
start. Feed count (8 episodes over 24 hours) aligns with the
recency-weighted daily episode count of 8.1. Volume is a flat 4.0 oz
per predicted episode, the recency-weighted median across recent
episode volumes.
