# Consensus Blend Design Decisions

## Why majority vote?

The user requirement is explicit: if 3 of 4 models predict a feed
around 3pm and 1 predicts 4pm, the consensus should reflect the
majority, not split the difference. Simple majority (more than half)
is the threshold: 3-of-4, 2-of-3, or 2-of-2.

## Episode collapsing before candidate generation

Before any candidate search begins, each model's predictions are
collapsed into episodes using the shared cluster rule
(`feedcast/clustering.py`). If a model predicts a 3pm feed and a
3:50pm top-up (50-minute gap, within the 73-minute base rule), those
two predictions become one episode-level point at 3pm with summed
volume. This prevents cluster-internal predictions from anchoring
spurious candidate slots or inflating a model's apparent agreement
with other models.

The collapse is applied inside `_blend_by_sequence_selection()` to a
copy of each forecast — the caller's forecasts are not mutated. The
research sweep also collapses before generating candidates so that
sweep results match production behavior.

See `feedcast/research/feed_clustering/findings.md` for the boundary
rule derivation.

## How candidates are built

Each episode-level model prediction anchors a search: "which other
models predict something nearby?" For each anchor, the blend tries
every majority-sized group of models (all 3-of-4 combinations and the
full 4-of-4) and pulls each model's nearest prediction within a 2-hour
radius. If a group's predictions pass the spread cap (3 hours max), it
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
   105 minutes. The conflict window does not decide what counts as a
   real episode — that is the cluster rule's job (73-minute base in
   `feedcast/clustering.py`). The conflict window decides which
   competing candidate slots to keep when multiple slots target the
   same region. A wider window forces the selector to pick the
   better-supported candidate rather than fitting both, which
   produces more accurate timing. See `research_results.txt` for the
   parameter sweep evidence and `model.py` for the current constant.

## Why the 2-hour search radius?

Models often disagree by 100+ minutes about the same real episode
(see the spread percentiles in `research_results.txt`). A narrow
radius misses legitimate agreement. The wide radius pulls in outlier
predictions too, but the median timestamp naturally reflects the
majority position — one outlier barely moves a 3-point or 4-point
median.

## Current limitations

**Outliers are suppressed, not rejected.** A model predicting 4pm
when three others predict 3pm gets pulled into the candidate (it's
within the 2-hour radius). The median timestamp still lands at
~3pm, so the prediction is accurate, but the outlier model is
counted as a contributor. True rejection (excluding the outlier
entirely) would require a tighter radius, which hurts overall
accuracy by also excluding legitimate wide agreement.

## Research cutoff selection

The consensus research script evaluates the selector across multiple
retrospective cutoffs. It always includes the replay-equivalent
cutoff (latest activity time minus horizon) so the research sweep
evaluates the same window that the replay runner does. Remaining
cutoffs come from the last feed time of each recent complete day.
This ensures the research sweep captures the most recent model
behavior while also testing stability across older windows.

## Where to improve next

The constraint structure (single-use + conflict window) is tight
enough to dominate the selector outcome across a wide range of
utility weights. Radius and spread parameters have little effect;
the conflict window and spread penalty are the levers that
differentiate. See `research_results.txt` for the latest sweep.

Gains would come from changing how candidates are generated or how
conflicts are defined — for example, a scoring model where a tight
3-model agreement can beat a wide 4-model agreement, or conflict
windows that vary by time of day.
