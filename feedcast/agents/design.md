# Agent Inference Design

## Baseline Workspace Design: Code-First with LLM Strategy Layer

The persistent workspace includes a Python forecasting script
(`model.py`) that implements Empirical Cadence Projection, a
non-parametric approach that projects forward from recency-weighted gap
medians split by day-part. The LLM agent is the strategy layer: it may
run the script, review retrospective results, tune it, replace it, or
use another method when the latest data supports that choice.

`methodology.md` is the report-facing description of the method actually
used for the latest run. `strategy.md` and `CHANGELOG.md` hold the
longer-lived notes about how the workspace is evolving over time.

**Why code-first:** The scoring system rewards timing precision (30-min
half-life). Algorithmic gap computation is more reliable for precise
timestamp placement than freeform LLM reasoning over timestamps.

**Why non-parametric:** The scripted models each assume a specific
distribution or structure (Weibull hazard, daily template, hunger
dynamics). The agent avoids these assumptions, using empirical medians
and recency weighting instead. This makes it more adaptive to pattern
changes but less precise in well-characterized regimes.

## Workspace Structure

| File | Purpose |
| ---- | ------- |
| `model.py` | Forecasting script. Run with `--export`, `--cutoff`, `--horizon`. Writes `forecast.json`. |
| `strategy.md` | Durable baseline approach notes, performance data, constants rationale, open questions, and guidance for future agents. |
| `methodology.md` | Report-facing description of the method used for the latest run. |
| `design.md` | This file. Design decisions and rationale. |
| `CHANGELOG.md` | Reverse-chronological behavior changes. |
| `forecast.json` | Generated at runtime. The pipeline's required output. |

## Key Design Decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| Algorithm | Empirical gap projection | Non-parametric; avoids distribution assumptions that may not hold for a fast-changing newborn |
| Day-part split | 2 buckets (overnight 19–07, daytime 07–19) | Captures the main behavioral difference; 3 buckets would be too sparse with current data |
| Recency weighting | 48h half-life | Aggressive; tested against 36h, 72h, 96h, 120h on multi-cutoff retrospective |
| First-feed estimate | Conditional survival | Filters to gaps longer than elapsed time, takes weighted median of remaining. Better than naive subtraction for evening cutoffs |
| Count calibration | 30% threshold | Gentle safety net; only fires for large mismatches. Preserves day/night ratio |
| Overnight boundary | 19:00 (not 20:00) | Captures pre-bed feeds at 7–8 PM. Tested: 19:00 improves count accuracy vs 20:00 |

## Relationship to Scripted Models

The agent is a model peer: same forecast schema, same evaluation. It
draws inspiration from the scripted models (day-part awareness from
survival hazard, episode collapsing from the shared pipeline) but uses
a distinct approach. Currently excluded from the consensus blend.

## Evolution Model

The workspace persists across runs. Future agents read `strategy.md`,
review recent retrospective scores, and decide whether to run `model.py`
as-is, tune constants, restructure the algorithm, or start from
scratch. Every change is committed on an isolated run branch.

## Single Shared Workspace

Both Claude and Codex write to the same workspace directory. Only one
agent runs per pipeline invocation (selected via CLI arg), so there
are no concurrent write conflicts.
