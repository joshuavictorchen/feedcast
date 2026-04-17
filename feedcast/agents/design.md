# Agent Inference Design

## Workspace Design: Code-First with LLM Strategy Layer

The persistent workspace includes a Python forecasting script
(`model.py`) implementing four-bucket cadence projection, a
non-parametric approach that projects forward from recency-weighted
gap medians across four clock-hour sub-periods. The LLM agent is
the strategy layer: it may run the script, review retrospective
results, tune constants, rewrite the script, or adopt another
method when the latest data supports that choice.

`methodology.md` is the report-facing description of the method
actually used for the latest run. `strategy.md` and `CHANGELOG.md`
hold the longer-lived notes about how the workspace is evolving.

**Why code-first:** The scoring system rewards timing precision
(30-min half-life). Algorithmic gap computation is more reliable
for precise timestamp placement than freeform LLM reasoning over
timestamps.

**Why non-parametric:** The scripted models each assume a specific
distribution or structure (Weibull hazard, daily template, hunger
dynamics). The agent avoids these assumptions, using empirical
medians with recency weighting instead. This improves adaptivity
to pattern changes at the cost of precision in well-characterized
regimes.

## Workspace Structure

| File | Purpose |
| ---- | ------- |
| `model.py` | Canonical forecast implementation. Run with `--export`, `--cutoff`, `--horizon`. Writes `forecast.json`. Other `.py` files for research or helpers are allowed alongside it. |
| `prompt.md` | Runtime instructions the agent reads on each run. |
| `strategy.md` | Durable notes: tuning rationale, strengths and weaknesses, open questions, guidance for future agents. |
| `methodology.md` | Report-facing description of the method used for the latest run. |
| `design.md` | This file. Design decisions and rationale. |
| `CHANGELOG.md` | Reverse-chronological behavior changes. |
| `forecast.json` | Generated at runtime. The pipeline's required output. |

## Key Design Decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| Algorithm | Empirical gap projection | Non-parametric; avoids distribution assumptions that may not hold for a fast-changing newborn |
| Day-part split | 4 buckets: evening (19-22), deep night (22-03), early morning (03-07), daytime (07-19) | Each sub-period exhibits distinct gap characteristics that a coarser split blends together |
| Gap tag | Clock hour of the feed that *starts* the gap | Determines which sub-period the gap belongs to, consistent whether the gap crosses a boundary or not |
| Recency weighting | 48h half-life | Aggressive 2-day decay; data from 5+ days ago gets <20% weight |
| Bucket occupancy floor | 3 gaps minimum | Buckets below threshold fall back to the overall recency-weighted median |
| Minimum gap | 1.0h | Prevents degenerate cascading of very short predicted gaps |
| Projection anchor | Cutoff time | Forecast steps forward from the cutoff using the sub-period gap that matches each predicted feed's start hour |
| Volume | Recency-weighted median episode volume | Flat per-episode volume across the forecast |

## Relationship to Scripted Models

The agent is a model peer: same forecast schema, same evaluation.
It draws inspiration from the scripted models (day-part awareness
from survival hazard, episode collapsing from the shared pipeline)
but uses a distinct approach. Currently excluded from the consensus
blend.

## Evolution Model

The workspace persists across runs. Future agents read
`strategy.md`, review recent retrospective scores in `tracker.json`,
and decide whether to run `model.py` as-is, tune constants,
restructure the algorithm, or start from scratch. Every change is
committed on an isolated run branch.

## Single Shared Workspace

Both Claude and Codex write to the same workspace directory. Only
one agent runs per pipeline invocation (selected via CLI arg), so
there are no concurrent write conflicts.
