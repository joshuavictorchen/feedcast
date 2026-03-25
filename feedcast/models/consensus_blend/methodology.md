# Consensus Blend

Combines the four scripted models into one forecast by finding where
a majority of models agree that a feed will happen.

For each predicted feed from any model, the blend looks at what the
other models predict nearby (within a 2-hour window) and asks: do
at least 3 of 4 models place a feed in this region? If so, that
region becomes a candidate consensus feed. Its predicted time is the
median of the contributing models' timestamps, and its volume is the
median of their volumes.

Many overlapping candidates can describe the same real feed, so the
blend picks the best non-overlapping set. Two rules prevent double-
counting: each individual model prediction can only support one
consensus feed, and two consensus feeds cannot be closer than 90
minutes apart. The final schedule is the highest-quality set of
feeds that satisfies both rules.

This approach means the consensus naturally favors feeds where
multiple models agree on timing, while isolated predictions that
only one or two models support are filtered out.
