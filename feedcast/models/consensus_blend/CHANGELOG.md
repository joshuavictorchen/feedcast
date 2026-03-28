# Changelog

Tracks behavior-level changes to the Consensus Blend model. Add newest entries first.

## No selector change after upstream model updates | 2026-03-27

### Problem

Three of four component models (Slot Drift, Latent Hunger, Survival
Hazard) switched to episode-level history, changing their forecast
outputs. Need to verify the consensus selector parameters are still
appropriate.

### Research

Re-ran the consensus research sweep (20260325 export). Production
headline unchanged. Count component improved (upstream models predict
the right number of episodes more often); timing slightly degraded;
the two offset. The parameter sweep's prior full degeneracy partially
broke: a wider conflict window combined with a much higher spread
penalty scores marginally better, but this requires both changes —
neither alone moves the score. The winning combination re-introduces
suppression of legitimate close episodes and shifts the selector
from support-primary to tightness-primary. Inter-model spread
tightened. Replay confirmed no regression.

### Solution

No parameter change. The marginal gain contradicts the episode
ontology (suppresses real episode pairs that the current conflict
window was specifically set to preserve). The count improvement comes
from upstream model changes, not the selector. Production constants
retained.

## Lower conflict window from 90 to 75 minutes | 2026-03-26

### Problem

The 90-minute conflict window was set before episode collapsing existed.
It served two roles: preventing duplicate candidate slots and suppressing
cluster-internal feeds. With clusters now collapsed before candidate
generation, the second role is redundant, and the 90-minute floor
suppresses legitimate close episodes (the tightest confirmed non-cluster
gap in the labeled data is 74.8 minutes).

### Solution

Lowered `SELECTION_CONFLICT_WINDOW_MINUTES` from 90 to 75. This sits
just above the 73-minute base cluster rule and admits episode pairs at
75+ minutes while still suppressing pairs ambiguous with cluster
boundaries. One confirmed non-cluster gap at 74.8 minutes is still
suppressed — 75 is a conservative floor, not an exact match. The
research sweep shows all conflict values (75/90/105) produce identical
scores on current data, but direct comparison of selected candidates
reveals that 75 allows more realistic close-episode predictions on at
least one retrospective cutoff (03/24: 76-minute episode pair selected
at 75, suppressed at 90).

## Collapse model predictions into episodes before candidate generation | 2026-03-26

### Problem

Models can predict cluster-internal feeds (e.g., a 3pm feed followed by
a 3:50pm top-up). Without collapsing, each cluster-internal point
anchored separate candidate searches, polluting the candidate pool and
potentially inflating a model's apparent agreement with other models.

### Solution

Each model's predictions are now collapsed into episodes using the
shared cluster rule (`feedcast/clustering.py`) before candidate
generation. A model predicting a 3pm feed and a 3:50pm top-up now
contributes one episode-level point at 3pm with summed volume. The
existing candidate generator and MILP selector operate unchanged on
collapsed inputs.

The research sweep also collapses before generating candidates, so
sweep results match production behavior. Post-collapse sweep shows
all conflict window values (75/90/105) produce identical scores —
the 90-minute conflict window is retained as-is.

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
   candidates closer than the conflict window cannot both survive.

3. **Scorer-based research.** The research script evaluates the
   production selector on retrospective cutoffs using the real
   `score_forecast()` function with recency weighting, replacing the
   earlier proxy-based cluster statistics.

The majority floor (simple majority of available models) rejects
2-of-4 splits by construction. The single-use constraint prevents
one model's prediction from counting as evidence for multiple
consensus feeds. The immutable candidate design eliminates rebuild
bugs and search-order dependence that affected earlier iterations.
