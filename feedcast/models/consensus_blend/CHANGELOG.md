# Changelog

Tracks behavior-level changes to the Consensus Blend model. Add newest entries first.

## Replace lockstep blend with immutable majority-subset MILP selector | 2026-03-25

### Problem

The consensus blend was defined inline in `feedcast/models/__init__.py`
and used a lockstep median-timestamp walk that had three structural
issues: misalignment cascades (one skipped outlier shifted all
downstream pairings), phantom consensus (the median of a 2-vs-2 split
produced a time no model believed in), and equal treatment of 2-of-4
minority splits as "consensus."

### Solution

Extracted the consensus blend into its own model directory with the
standard file set. Replaced the lockstep algorithm with a three-stage
pipeline:

1. **Immutable candidate generation.** Every model prediction is an
   anchor. For each anchor, the blend enumerates every majority-sized
   model subset (3-of-4 and 4-of-4 with four models) and builds a
   candidate from each subset's nearest predictions within a shared
   radius. Candidates are deduplicated by their exact set of
   contributing model points and are never mutated after creation.

2. **Exact set-packing selection.** A MILP solver (scipy `milp`) picks
   the highest-utility non-overlapping sequence subject to two hard
   constraints: each model prediction is used at most once, and
   candidates closer than the conflict window (90 min) cannot both
   survive.

3. **Scorer-based research.** The research script evaluates the
   production selector on retrospective cutoffs using the real
   `score_forecast()` function with recency weighting, replacing the
   earlier proxy-based cluster statistics.

The majority floor (simple majority of available models) rejects
2-of-4 splits by construction. The single-use constraint prevents
one model's prediction from counting as evidence for multiple
consensus feeds. The immutable candidate design eliminates rebuild
bugs and search-order dependence that affected earlier iterations.
