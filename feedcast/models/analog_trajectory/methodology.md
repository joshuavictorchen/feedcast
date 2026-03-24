# Analog Trajectory Retrieval

Instance-based forecasting that asks "when have we seen a state like
this before, and what happened next?" Instead of fitting a global
function, the model treats each historical feed event as a reference
state with a known 24-hour future trajectory.

At forecast time the model summarizes the current state as a feature
vector: recent gap and volume averages plus circular hour-of-day.
It finds the most similar historical states using normalized Euclidean
distance, weights them by a combination of proximity and recency
(3-day half-life), and produces the forecast by averaging their
actual future gap sequences. The predicted gaps are rolled forward
from the cutoff time to produce absolute feed times.

Volume predictions use per-step weighted averages from the same
neighbor trajectories. This lets volume reflect what actually happened
in analogous situations rather than relying on a global time-of-day
profile.

Uses bottle-only events (no breastfeed merge). The model needs at
least 10 historical states with complete 24-hour futures to produce
a forecast.
