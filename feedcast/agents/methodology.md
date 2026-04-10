# Agent Inference

Slot-aware Cadence Projection. The agent builds on the Empirical Cadence
Projection model but replaces the 2-bucket overnight/daytime split with
hourly-slot-conditional gap estimates. Gaps are computed from feeds at
similar times of day (e.g., "after a 20:xx feed" or "after a 03:xx feed")
and weighted by a 48-hour recency half-life. This captures structure that
a broad overnight median misses: evening-to-late-evening gaps (~2.8h),
late-evening-to-deep-night gaps (~4.1h), and deep-night-to-morning gaps
(~3.4-3.6h) are each estimated separately.

The evening transition uses pattern detection: some nights include a
late-evening feed (~23:xx), others skip to midnight. The agent predicts
whichever pattern has the highest recency-weighted probability. Afternoon
gaps (after ~12:xx) are estimated separately from morning gaps, as
they tend to run longer (~3.2h vs ~2.5h).

Volume is set at a flat 3.5 oz based on the recency-weighted mean of
recent feeds. Feed count targets ~8 episodes per 24 hours, consistent
with recent daily episode counts.
