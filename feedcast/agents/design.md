# Agent Inference Design

## Approach: Code-First with LLM Strategy Layer

The agent maintains a Python forecasting script (`model.py`) in its
workspace. The script implements Empirical Cadence Projection — a
non-parametric approach that projects forward from recency-weighted gap
medians split by day-part. The LLM agent's role is the strategy layer:
running the script, reviewing retrospective results, and deciding when
and how to evolve the approach.

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
| `strategy.md` | Approach docs, performance data, constants rationale, open questions, and guidance for future agents. |
| `methodology.md` | Report-facing description (rendered into the forecast report). |
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
scratch. Every change is committed on an isolated review branch.

## Single Shared Workspace

Both Claude and Codex write to the same workspace directory. Only one
agent runs per pipeline invocation (selected via CLI arg), so there
are no concurrent write conflicts.
