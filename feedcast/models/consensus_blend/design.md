# Consensus Blend Design Decisions

## Why majority vote?

The user requirement is explicit: if most models predict a feed around
3pm and one predicts 4pm, the consensus should reflect the majority,
not split the difference. The threshold is a strict majority of the
available models, not an average over all predictions.

## Episode collapsing before candidate generation

Before any candidate search begins, each model's predictions are
collapsed into episodes using the shared cluster rule
(`feedcast/clustering.py`). If a model predicts a 3pm feed and a
3:50pm top-up (within the cluster boundary rule), those
two predictions become one episode-level point at 3pm with summed
volume. This prevents cluster-internal predictions from anchoring
spurious candidate slots or inflating a model's apparent agreement
with other models.

The collapse is applied inside `_blend_by_sequence_selection()` to a
copy of each forecast — the caller's forecasts are not mutated. The
research sweep also collapses before generating candidates so that
sweep results match production behavior.

See `feedcast/research/feed_clustering/research.md` for the boundary
rule derivation.

## How candidates are built

Each episode-level model prediction anchors a search: "which other
models predict something nearby?" For each anchor, the blend tries
every strict-majority-sized group of models, plus the full-model
group, and pulls each model's nearest prediction within a search
radius (see `model.py`). If a group's predictions pass the configured
spread cap, it becomes a candidate.

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

2. **Minimum spacing:** Two consensus feeds cannot be closer than the
   conflict window (see `model.py`). The conflict window does not
   decide what counts as a real episode — that is the cluster rule's
   job (`feedcast/clustering.py`). The conflict window decides which
   competing candidate slots to keep when multiple slots target the
   same region. The current canonical sweep favors a wider spacing rule
   than raw gap context alone would suggest, which means duplicate
   suppression is currently more valuable than preserving every close
   episode pair. See `research.md` for the current evidence and
   `model.py` for the shipped constant.

## Why a bounded search radius?

Models often disagree by significant margins about the same real
episode (see the spread percentiles in `artifacts/research_results.txt`). A
narrow radius misses legitimate agreement. A wide radius pulls in
outlier predictions too, but the median timestamp naturally reflects
the majority position — one outlier barely moves the median of a
multi-model agreement set.

## Current limitations

**Outliers are suppressed, not rejected.** A model predicting 4pm
when three others predict 3pm gets pulled into the candidate (it's
within the search radius). The median timestamp still lands at
~3pm, so the prediction is accurate, but the outlier model is
counted as a contributor. True rejection (excluding the outlier
entirely) would require a tighter radius, which hurts overall
accuracy by also excluding legitimate wide agreement.

## Research cutoff selection

The consensus research script evaluates the selector across multiple
retrospective cutoffs. It always includes the replay-equivalent
cutoff (latest activity time minus horizon) so the research sweep
evaluates the same window that the replay runner does. The remaining
cutoffs come from recent episode boundaries, not arbitrary wall-clock
steps, so the research sweep stays aligned with the scorer's ontology
and samples the recent regime more densely during active periods.

## Where to improve next

The selector surface is now shaped mostly by candidate geometry and
conflict handling rather than utility weighting. The latest sweep shows
that tighter spread caps and a wider conflict window can move the
headline, while the tested spread penalties sit on a broad local
plateau. See `research.md` for the current evidence.

Gains would come from changing how candidates are generated or how
conflicts are defined — for example, a scoring model where a tight
majority agreement can beat a wide near-unanimous agreement, or conflict
windows that vary by time of day.
