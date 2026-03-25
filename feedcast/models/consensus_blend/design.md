# Consensus Blend Design Decisions

## Why majority vote?

The user requirement is explicit: if 3 of 4 models predict a feed
around 3pm and 1 predicts 4pm, the consensus should reflect the
majority, not split the difference. Simple majority (more than half)
is the threshold: 3-of-4, 2-of-3, or 2-of-2.

## How candidates are built

Each model prediction anchors a search: "which other models predict
something nearby?" For each anchor, the blend tries every majority-
sized group of models (all 3-of-4 combinations and the full 4-of-4)
and pulls each model's nearest prediction within a 2-hour radius.
If a group's predictions pass the spread cap (3 hours max), it
becomes a candidate.

Candidates are fixed once created — they are never modified during
selection. This avoids bugs where the selection order changes what
a candidate looks like.

## How the best schedule is chosen

Multiple candidates can describe the same real feed (anchored from
different model predictions but pulling in the same evidence). An
optimizer (scipy MILP) picks the highest-scoring non-overlapping
set subject to two hard rules:

1. **No double-counting:** Each model prediction can support at most
   one consensus feed. If model A's 3pm prediction is used for one
   consensus feed, it cannot also be counted as evidence for another.

2. **Minimum spacing:** Two consensus feeds cannot be closer than
   90 minutes (the physiological minimum between real feeds).

## Why the 2-hour search radius?

Research shows models often disagree by 100+ minutes about the same
real feed (median spread = 102 min). A narrow radius misses
legitimate agreement. The wide radius pulls in outlier predictions
too, but the median timestamp naturally reflects the majority
position — one outlier barely moves a 3-point or 4-point median.

## Current limitations

**Utility ranking doesn't matter much.** Candidates are scored by
model support with a small bonus for tighter agreement, but the
hard constraints (single-use + spacing) are tight enough to
determine the answer on their own. Parameter sweeps across a wide
range of scoring weights all produced identical results.

**Outliers are suppressed, not rejected.** A model predicting 4pm
when three others predict 3pm gets pulled into the candidate (it's
within the 2-hour radius). The median timestamp still lands at
~3pm, so the prediction is accurate, but the outlier model is
counted as a contributor. True rejection (excluding the outlier
entirely) would require a tighter radius, which hurts overall
accuracy by also excluding legitimate wide agreement.

## Where to improve next

The score ceiling for this constraint setup is around 69.8 (on the
current 5-cutoff retrospective). Gains would come from changing how
candidates are generated or how conflicts are defined — for example,
a scoring model where a tight 3-model agreement can beat a wide
4-model agreement, or conflict windows that vary by time of day.
