# Agent Inference

Empirical cadence projection with four-bucket day-part split. The model collapses raw bottle feeds into feeding episodes using the shared clustering rule (73-minute base gap, 80-minute extension for small top-ups), then examines the most recent 7 days of episode-level history with exponential recency weighting (48-hour half-life).

Inter-episode gaps are classified into four sub-periods by the clock hour of the feed that starts the gap: evening (19:00-22:00, weighted median 3.77h), deep night (22:00-03:00, weighted median 4.03h), early morning (03:00-07:00, weighted median 2.95h), and daytime (07:00-19:00, weighted median 2.31h). The forecast steps forward from the cutoff, applying the sub-period gap corresponding to each predicted feed's clock hour. This four-bucket split addresses the documented weakness of the two-bucket baseline, which blends structurally different overnight regimes into a single median: the evening-to-first-night transition, consistent deep-night wake intervals, and shorter pre-dawn gaps.

Feed count (8 episodes over 24 hours) aligns with the recency-weighted daily episode count of 7.7, an improvement over the two-bucket baseline which produced 7 episodes. Volume is a flat 4.0 oz per predicted episode, the recency-weighted median across recent episode volumes.
