# Consensus Blend

Majority-vote ensemble across the scripted base models. Instead of
walking model forecasts in lockstep, the blend proposes candidate
feed slots around each predicted point by pulling in the nearest
prediction from every available model inside a shared time radius.

Only candidate slots backed by a simple majority of the available
models survive. Those candidates then compete in a weighted
interval scheduler that keeps the best non-overlapping sequence,
favoring higher model support and tighter timing agreement.

Each consensus feed uses the median timestamp and median volume of
its contributing model predictions. This prevents 2-vs-2 split
votes from becoming consensus and avoids emitting multiple nearby
"echo" feeds when several local candidate clusters are really
describing the same underlying feed.
