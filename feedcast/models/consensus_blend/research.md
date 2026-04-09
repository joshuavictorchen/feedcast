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
| Run date | 2026-04-09 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.consensus_blend.analysis` |
| Canonical headline | 74.8 |
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
selection step for those later knobs. The conflict grid stops at `150`
because the recency-weighted lower quartile of real episode gaps is
about `147` minutes on the current export; pushing the conflict window
far beyond that would suppress a large share of legitimate close
episodes by construction.

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
| Headline | 74.8 |
| Count | 96.4 |
| Timing | 58.5 |

All 24 windows scored (100% availability).

The selector constants are now:

| Parameter | Value |
|---|---|
| `ANCHOR_RADIUS_MINUTES` | `90` |
| `MAX_CANDIDATE_SPREAD_MINUTES` | `150` |
| `SELECTION_CONFLICT_WINDOW_MINUTES` | `135` |
| `SPREAD_PENALTY_PER_HOUR` | `5.0` |

The immediate comparison is against the prior production selector that
had already been refreshed on the same export:

| Metric | Prior (`120`, `150`, `135`, `0.25`) | Current (`90`, `150`, `135`, `5.0`) |
|---|---|---|
| Headline | 74.563 | 74.776 |
| Count | 96.666 | 96.393 |
| Timing | 57.986 | 58.469 |
| Availability | 24/24 | 24/24 |

This is a small but real improvement (`+0.213` headline) driven by
tighter timing (`+0.483`) with a modest count tradeoff (`-0.273`).

The selector surface is still shallow, but the current rerun changed two
important details:

- The best anchor radius is now `90`, not `120`. The base models,
  especially Analog Trajectory, are agreeing tightly enough that the
  selector benefits from a narrower search region.
- `SPREAD_PENALTY_PER_HOUR` is no longer completely flat at the top of
  the surface. The strongest tested penalty (`5.0`) wins at the top row
  once radius, spread, and conflict are fixed at the best geometry.

That does not mean utility weighting has become the dominant selector
lever. Geometry and conflict handling still explain most of the
variation across the grid. But the old claim that the penalty is a pure
no-op is no longer correct on the current export.

### Diagnostic findings

**Inter-episode gap context still pulls against the canonical winner:**
The recency-weighted median inter-episode gap is 172 minutes and the
lower quartile is about 147 minutes, but the minimum observed gap is 75
minutes. On local gap intuition alone, that argues for a relatively
conservative conflict window so close legitimate episodes can survive.
Canonical replay still prefers `135`, which means duplicate suppression
is currently more valuable than preserving every close episode pair.

**Model spread still justifies a non-trivial anchor radius:** Across the
five most recent diagnostic cutoffs, multi-model matches have spread
`P50=72`, `P75=86`, `P90=117`, `Max=143` minutes. The narrower `90`
minute anchor is viable only because the blend also filters by support
and spread; it is not evidence that the models now agree within a few
minutes.

**Candidate geometry still matters more than utility weighting overall:**
The sweep surface shows meaningful separation across spread caps and
conflict windows, while most penalty cells away from the very top still
tie. The current export does show a real top-row benefit from `5.0`,
but the main selector character is still set by support geometry and
conflict handling.

**The selector remains timing-limited even when count is strong:** The
canonical count score is strong across the surface, including the prior
selector. The main benefit from the new constants is timing. This
suggests the blend is already finding roughly the right number of
episodes; the remaining leverage is in choosing tighter candidate slots
and resolving near-duplicate explanations more cleanly.

## Conclusions

**Disposition: Change.** Updated `ANCHOR_RADIUS_MINUTES` from `120` to
`90` and `SPREAD_PENALTY_PER_HOUR` from `0.25` to `5.0`. Kept
`MAX_CANDIDATE_SPREAD_MINUTES=150` and
`SELECTION_CONFLICT_WINDOW_MINUTES=135`.

This is not a major design reversal. The selector architecture still
looks sound: immutable majority-supported candidates, single-use
evidence, and exact sequence selection. The update is a local retune
caused by the current upstream model behavior, not by a change in the
selector's core logic.

The more important conceptual result is that Consensus Blend remains
downstream-sensitive. Once Analog Trajectory tightened and moved to a
different regime, the best selector moved too. That is expected for an
ensemble selector, but it means consensus research should be treated as
dependent on the current base-model lineup rather than as a one-time
settled sweep.

## Open questions

### Model-local

- **Conflict-window tension:** The canonical winner still uses a
  conflict window much wider than the raw gap context would suggest. Is
  that a stable property of the selector, or a temporary compensation
  for current base-model timing patterns?
- **Penalty significance:** The current top row finally prefers a
  stronger spread penalty, but most of the surface still treats penalty
  as secondary. Is `5.0` a durable selector property, or just the first
  point high enough to break a shallow local tie?
- **Geometry vs. utility:** The latest sweep still says spread cap and
  conflict handling matter more than the utility weight. If that
  remains true across future exports, the selector may be better served
  by richer candidate construction or richer conflict logic rather than
  by more penalty tuning.

### Cross-cutting

- **Timing as shared bottleneck:** Count (`96.4`) is strong while timing
  (`58.5`) is the weaker component. This pattern persists across all
  five models — see `feedcast/research/README.md`.
- **Upstream-model sensitivity:** Consensus constants can move when the
  four base scripted models change. Whether selector retunes track
  upstream timing sharpness is an open question — see
  `feedcast/research/README.md` for cross-model patterns.
