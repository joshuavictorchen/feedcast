# Changelog

Tracks behavior-level changes to the Consensus Blend model. Add newest entries first.

## Retune selector after analog refresh | 2026-04-09

### Problem

Consensus Blend is downstream of the four scripted base models. After
Analog Trajectory moved to a stronger episode-level regime, the current
selector constants were no longer obviously the best fit for the new
candidate geometry.

### Research

Ran the refreshed selector sweep on
`exports/export_narababy_silas_20260327.csv` after the analog retune.

The prior production selector:

- `ANCHOR_RADIUS_MINUTES=120`
- `MAX_CANDIDATE_SPREAD_MINUTES=150`
- `SELECTION_CONFLICT_WINDOW_MINUTES=135`
- `SPREAD_PENALTY_PER_HOUR=0.25`

scored headline `74.563`, count `96.666`, timing `57.986`.

The new canonical winner:

- `ANCHOR_RADIUS_MINUTES=90`
- `MAX_CANDIDATE_SPREAD_MINUTES=150`
- `SELECTION_CONFLICT_WINDOW_MINUTES=135`
- `SPREAD_PENALTY_PER_HOUR=5.0`

scored headline `74.776`, count `96.393`, timing `58.469`.

This is a modest but real gain (`+0.213` headline) driven by timing
(`+0.483`) with a small count tradeoff (`-0.273`). The narrower anchor
and stronger penalty indicate that the refreshed ensemble now benefits
from a tighter agreement region and a more meaningful cost for diffuse
support.

### Solution

Lowered `ANCHOR_RADIUS_MINUTES` from `120` to `90` and raised
`SPREAD_PENALTY_PER_HOUR` from `0.25` to `5.0`. Kept
`MAX_CANDIDATE_SPREAD_MINUTES=150` and
`SELECTION_CONFLICT_WINDOW_MINUTES=135`.

## Broaden selector sweep, tighten spread cap, and widen conflict window | 2026-04-01

### Problem

The shipped selector constants (`spread=180`, `conflict=105`) were
chosen under an older five-cutoff research pass. After the base scripted
models were retuned on the March 27 export, that selector no longer
matched the best canonical multi-window configuration. The old setting
was still solid, but it left a small amount of timing accuracy on the
table.

### Research

Ran the refreshed consensus research script on
`export_narababy_silas_20260327.csv` using the canonical 24-window
multi-window objective (96h lookback, 36h half-life, episode-boundary
cutoffs). An initial nearby sweep still hit the geometry/conflict
boundaries, so the authoritative selector sweep was widened to:

- `ANCHOR_RADIUS_MINUTES`: `60`, `90`, `120`, `150`
- `MAX_CANDIDATE_SPREAD_MINUTES`: `90`, `120`, `150`, `180`
- `SELECTION_CONFLICT_WINDOW_MINUTES`: `75`, `90`, `105`, `120`, `135`, `150`
- `SPREAD_PENALTY_PER_HOUR`: `0.25`, `1.0`, `2.0`, `5.0`

The conflict grid stops at `150` because the recency-weighted lower
quartile of real episode gaps is about `147` minutes on this export;
going much wider would suppress a large share of legitimate close
episodes by construction.

All 384 configurations scored all 24 windows. The shipped constants
scored headline `72.020`, count `95.176`, timing `54.994`. The best
configuration scored headline `72.996`, count `95.434`, timing
`56.176` at `radius=120`, `spread=150`, `conflict=135`. The gain came
mainly from tighter timing (+1.182) plus a smaller count gain (+0.258).
`SPREAD_PENALTY_PER_HOUR` was flat across the tested values at the top
of the surface, so the utility weight was not a meaningful lever on this
export.

### Solution

Lowered `MAX_CANDIDATE_SPREAD_MINUTES` from `180` to `150` and
raised `SELECTION_CONFLICT_WINDOW_MINUTES` from `105` to `135`. Kept
`ANCHOR_RADIUS_MINUTES=120` and `SPREAD_PENALTY_PER_HOUR=0.25`.

## Raise conflict window from 75 to 105, fix research method | 2026-03-27

### Problem

The consensus research sweep had two issues: (1) end-of-day cutoffs
that systematically excluded the latest complete 24h window, causing
the sweep to miss model improvements visible in replay; (2) gap
analysis and model agreement diagnostics used raw feed counts instead
of episodes, reporting cluster-internal gaps and inflated counts
inconsistent with the episode-level ontology used by the scorer and
production blend.

### Research

Fixed the research script in three ways:
- Cutoff selection now always includes the replay-equivalent cutoff
  (latest activity time minus horizon), excluding per-day cutoffs at
  or after it to prevent weight distortion.
- Gap analysis uses collapsed episodes (cluster-internal gaps removed,
  episode counts replace raw feed counts).
- Model agreement matches collapsed predictions against actual
  episodes, consistent with what the production blend sees.

With the latest window included, all conflict=105 combos outperform
all conflict=75/90 combos under that narrower five-cutoff research
pass. That conclusion was later superseded by the broader 24-window
canonical sweep on 2026-04-01, which re-opened the selector search and
shifted the preferred conflict window again.

### Solution

Raised `SELECTION_CONFLICT_WINDOW_MINUTES` from 75 to 105. Replay
headline improved from 78.5 to 85.5, driven entirely by timing
(+12.0) with identical episode match counts. Research sweep
production score improved to 68.9 (recency-weighted, 5-cutoff).
Spread penalty kept at 0.25 (no selector character change).

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
