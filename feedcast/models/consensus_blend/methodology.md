# Consensus Blend

Median-timestamp ensemble across the scripted base models. It does
not align forecasts by feed index, because different models may
emit different numbers of future feeds. Instead, on each step it
takes the next unconsumed point from every available model,
computes the median timestamp as an anchor, and forms a cluster
from points within +/- 90 minutes of that anchor.

Points that fall earlier than the cluster window are discarded as
leading outliers. If fewer than two models fall into the current
cluster, the earliest candidate is discarded and the procedure
retries. Once a cluster contains at least two models, the
consensus point uses the median timestamp and mean volume across
that cluster, with its gap measured from the previous consensus
point. The process repeats until fewer than two models have
points left. This lets the blend stay robust when one model
predicts an extra snack feed or drifts earlier/later than the
others.
