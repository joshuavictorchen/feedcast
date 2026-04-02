# Consensus Blend Research

> `design.md` documents why the model works the way it does.
> `methodology.md` is the report-facing description.
> This file is the evidence: current support and challenges for the
> model's design and constants.

## Overview

Consensus Blend does not learn a forecast from raw feeding history
directly. It turns the four scripted model forecasts into immutable
candidate feed slots, then chooses the best non-overlapping sequence.
The research questions are therefore different from the base models:

1. How well does the shipped selector perform under canonical
   multi-window evaluation?
2. Do the current selector constants remain the best choice under the
   canonical objective?
3. What do inter-episode gap context and inter-model spread say about
   why the selector succeeds or fails?
4. Which selector levers actually matter on the current export:
   candidate geometry, conflict handling, or utility weighting?

## Last run

| Field | Value |
|---|---|
| Run date | 2026-04-01 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.consensus_blend.analysis` |
| Canonical headline | 73.0 |
| Availability | 24/24 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("consensus_blend")`
through the shared replay infrastructure. This scores the full
production blend over the canonical 96-hour lookback, 36-hour half-life,
and episode-boundary cutoffs. Scoring uses bottle-only events, matching
the canonical evaluator, while the component models themselves still run
with their own local event-construction policies.

**Canonical tuning** sweeps the selector constants directly through
`evaluate_multi_window()` with custom `forecast_fn` closures. This is
the correct layer for Consensus Blend because the selector parameters
change candidate generation and sequence selection rather than simple
module-level constants in one base model. The current sweep evaluates:

- `ANCHOR_RADIUS_MINUTES`: `60`, `90`, `120`, `150`
- `MAX_CANDIDATE_SPREAD_MINUTES`: `90`, `120`, `150`, `180`
- `SELECTION_CONFLICT_WINDOW_MINUTES`: `75`, `90`, `105`, `120`, `135`, `150`
- `SPREAD_PENALTY_PER_HOUR`: `0.25`, `1.0`, `2.0`, `5.0`

Candidate generation is cached per `(radius, spread)` pair and reused
across conflict/penalty variants so the sweep only recomputes the exact
selection step for those later knobs. The current grid is wider than the
first local pass because the selector initially won at the geometry and
conflict boundaries. The conflict grid stops at `150` because the
recency-weighted lower quartile of real episode gaps is about
`147` minutes on the current export; pushing the conflict window far
beyond that would suppress a large share of legitimate close episodes
by construction.

### Model-specific diagnostics

**Inter-episode gap analysis** describes the recent spacing of real
feeding episodes. This is dataset context, not a tuning objective. It
helps interpret whether the selector should prefer conservative conflict
handling or admit closer episode pairs.

**Inter-model prediction spread** measures how widely the scripted
models disagree when they are all trying to predict the same real
episode. This is the most ensemble-specific diagnostic in the file: it
shows whether wide anchor radii are necessary and whether the spread cap
is acting as a useful filter or as a blunt rejection rule.

## Results

### Canonical findings

The current production selector scores:

| Metric | Score |
|---|---|
| Headline | 73.0 |
| Count | 95.4 |
| Timing | 56.2 |

All 24 windows scored (100% availability).

The selector constants were updated from
`radius=120`, `spread=180`, `conflict=105`, `penalty=0.25`
to `radius=120`, `spread=150`, `conflict=135`, `penalty=0.25`.
On the current export, the reproduced canonical comparison is:

| Metric | Pre-update | Current |
|---|---|---|
| Headline | 72.020 | 72.996 |
| Count | 95.176 | 95.434 |
| Timing | 54.994 | 56.176 |
| Availability | 24/24 | 24/24 |

This is a small but real improvement (`+0.976` headline) driven mainly
by tighter timing (`+1.182`) plus a smaller count gain (`+0.258`).

The selector surface is flat across most levers, but peaked sharply at
`conflict=135`:

- The best configuration uses an interior anchor radius (`120`) within
  the widened `60–150` grid. Narrower (`60`, `90`) and wider (`150`)
  anchors both score worse.
- The best configurations tighten the spread cap from `180` to `150`,
  but further tightening to `120` or `90` also scores worse. Candidate
  geometry still matters after candidate collapsing, but the best cap is
  now interior rather than a boundary artifact.
- The preferred conflict window moved upward as the grid widened and now
  lands at `135`, which is substantially wider than the cluster-floor
  intuition from the local gap analysis. That peak is narrow rather than
  shallow: at `radius=120`, `spread=150`, moving the conflict window 15
  minutes down to `120` costs `0.765` headline, while moving it 15
  minutes up to `150` costs `1.406`.
- The spread penalty is flat across the tested values at the top of the
  surface. Support and conflict handling dominate the ranking on this
  export.

The full `artifacts/research_results.txt` table now prints every sweep row at
three-decimal precision, so both the current production row and the old
production row remain visible in the artifact.

### Diagnostic findings

**Inter-episode gap context pulls against the canonical winner:** The
recency-weighted median inter-episode gap is 172 minutes and the lower
quartile is about 147 minutes, but the minimum observed gap is 75
minutes. On local gap intuition alone, that argues for a relatively
conservative conflict window so close legitimate episodes can survive.
Canonical replay still prefers `135`, which means duplicate suppression
is currently more valuable than preserving every close episode pair.
That tension is now explicit rather than hidden.

**Model spread justifies a wide anchor radius:** Across the five most
recent diagnostic cutoffs, multi-model matches have spread
`P50=64`, `P75=85`, `P90=115`, `Max=134` minutes. The wide anchor radius
is therefore still justified: many legitimate agreement regions would be
missed by a narrow anchor. The tighter spread cap then filters out
diffuse clusters rather than preventing them from being considered at
all.

**Candidate geometry matters more than utility weighting:** The sweep
surface shows meaningful separation across spread caps and conflict
windows, but none across the tested spread penalties. On the current
export, support and conflict constraints determine the selected
sequence; the utility weight is mostly a tie-breaker that does not move
the headline.

**The selector remains timing-limited even when count is strong:** The
canonical count score is strong across the surface, including the
pre-update selector. The main benefit from the new constants is timing.
This suggests the blend is already finding roughly the right number of
episodes; the remaining leverage is in choosing tighter candidate slots
and resolving near-duplicate explanations more cleanly.

## Conclusions

**Disposition: Change.** Updated
`MAX_CANDIDATE_SPREAD_MINUTES` from `180` to `150` and
`SELECTION_CONFLICT_WINDOW_MINUTES` from `105` to `135`. Kept
`ANCHOR_RADIUS_MINUTES=120` and `SPREAD_PENALTY_PER_HOUR=0.25`.

This is not a major design reversal. The selector architecture still
looks sound: wide anchors, immutable majority-supported candidates,
single-use evidence, and exact sequence selection. The update is a
local retune of candidate geometry and conflict handling. The refreshed
canonical sweep says the blend works better when it rejects diffuse
candidate clusters earlier and suppresses near-duplicate explanations
more aggressively.

The gain is still modest enough that the selector should be treated as a
shallow surface, not a settled optimum. But it is large enough to
warrant an update because availability is unchanged and the broader grid
removed the earlier geometry-boundary ambiguity. The improvement comes
from geometric choices, not from gaming availability or weight tuning,
and it survives a 24-window canonical evaluation.

The more important conceptual result is that the older 5-cutoff sweep
was not wrong so much as incomplete. Once the blend was re-evaluated on
the shared canonical multi-window objective, the best selector moved.
Consensus Blend is therefore sensitive not only to its own constants,
but also to the current behavior of the base scripted models it blends.

## Open questions

### Model-local

- **Conflict-window tension:** The canonical winner now uses a conflict
  window much wider than the raw gap context would suggest, and the
  local peak is narrow: at `radius=120`, `spread=150`, a 15-minute
  shift to `120` drops headline by `0.765`, while a 15-minute shift to
  `150` drops it by `1.406`. Is this a stable property of the blend, or
  a temporary compensation for the current base-model timing patterns?
- **Penalty flatness:** Why is `SPREAD_PENALTY_PER_HOUR` effectively a
  no-op across the tested range? This could mean the current candidate
  set is already well-separated, or it could mean the utility function
  is too weak to express meaningful preferences once the hard
  constraints are applied.
- **Geometry vs. utility:** The latest sweep says spread cap and
  conflict handling matter more than the utility weight. If that
  remains true across future exports, the selector may be better served
  by richer candidate construction or richer conflict logic rather than
  by more penalty tuning.

### Cross-cutting

- **Timing as shared bottleneck:** Count (95.4) is strong while timing
  (56.2) is the weaker component. This pattern persists across all five
  models — see `feedcast/research/README.md`.
- **Upstream-model sensitivity:** Consensus constants can move when the
  four base scripted models change. Whether selector retunes track
  upstream timing sharpness is an open question — see
  `feedcast/research/README.md` for cross-model patterns.
