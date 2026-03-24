# Analog Trajectory Retrieval

Instance-based forecasting that asks "when have we seen a state like
this before, and what happened next?" Instead of fitting a global
function, the model treats each historical feed event as a reference
state with a known 24-hour future trajectory.

At forecast time the model summarizes the current state as a six-
dimensional feature vector: last gap and last volume (instantaneous),
rolling mean gap and rolling mean volume (computed over a 72-hour
lookback window), and circular hour-of-day (sin/cos encoding). It
finds the most similar historical states using weighted Euclidean
distance with per-feature weights that emphasize hour-of-day over
gap and volume. Neighbors are weighted by a combination of proximity
and recency (36-hour half-life), and the forecast is produced by
averaging their actual future gap sequences. The predicted gaps are
rolled forward from the cutoff time to produce absolute feed times.

Volume predictions use per-step weighted averages from the same
neighbor trajectories. This lets volume reflect what actually happened
in analogous situations rather than relying on a global time-of-day
profile.

Uses bottle-only events (no breastfeed merge). The model needs at
least 10 historical states whose trajectories extend at least 20
hours past the state time (with at least 3 future events) to
produce a forecast.
