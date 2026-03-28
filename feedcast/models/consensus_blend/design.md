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
   75 minutes. This sits just above the 73-minute base cluster rule
   and admits episode pairs at 75+ minutes (e.g., the 76-minute pair
   observed on 03/24) while still suppressing pairs that would be
   ambiguous with cluster boundaries. One confirmed non-cluster gap
   at 74.8 minutes is still suppressed — 75 is a conservative floor,
   not an exact match. Before episode collapsing, this was 90 minutes
   — a blunt proxy that also suppressed legitimate close episodes.
   Now that clusters are collapsed before candidate generation, the
   window only guards against duplicate candidate slots for the same
   real feed.

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

## Robustness to upstream model changes

When component models change their forecasting approach (e.g.,
switching from raw feed history to episode-level history), the
consensus selector does not necessarily need retuning. The selector
operates on episode-collapsed predictions regardless of how the
upstream model arrived at those predictions — the collapse step
normalizes the input before candidate generation.

The current selector constants were retained after component models
adopted episode-level history. A parameter sweep found that widening
the conflict window and increasing the spread penalty could marginally
improve headline scores, but the winning combination re-introduces
suppression of legitimate close episodes and shifts the selector from
support-primary to tightness-primary. Neither change alone moves the
score — the gain requires both, which contradicts the episode ontology
the conflict window was designed to respect. See `research_results.txt`
for the latest sweep output and `model.py` for the current shipped
constants.

## Where to improve next

The constraint structure (single-use + conflict window) is tight
enough to dominate the selector outcome across a wide range of
utility weights. Most parameter sweeps produce identical or
near-identical results, with gains only available from combinations
that conflict with the episode ontology.

Gains would come from changing how candidates are generated or how
conflicts are defined — for example, a scoring model where a tight
3-model agreement can beat a wide 4-model agreement, or conflict
windows that vary by time of day.
