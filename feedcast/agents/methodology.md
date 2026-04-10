# Agent Inference

Slot-aware cadence model that forecasts feeding episodes from recent
episode-level gap patterns. Instead of one broad daytime gap and one
broad overnight gap, it estimates typical gaps for feeds at similar
hours of day so the schedule can treat evening follow-ups, first-night
stretches, morning resets, and afternoon cadence differently.

The model first collapses nearby bottle feeds into feeding episodes
using the shared clustering rule. It then looks back over recent
history and measures the gap after each episode, tagging each gap by the
hour of day of the episode that started it. Gap estimates are weighted
toward newer observations with a 48-hour recency half-life so the
forecast follows the latest cadence rather than a longer-run average.

For the first predicted feed, the model conditions on how much time has
already elapsed since the last observed episode and chooses a remaining
wait that is consistent with comparable recent gaps. Later predicted
feeds step forward using the hour-appropriate gap estimate for each new
predicted feed time. When recent evenings support two different
patterns, such as a late-evening top-up versus a direct jump to
midnight, the model follows the branch with the stronger recent support.

Predicted volume is held near the recent central tendency at 3.5 oz.
Overall feed count stays close to recent daily episode counts so the
schedule remains plausible over the full 24-hour horizon.
