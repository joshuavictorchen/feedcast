# Architecture

This document captures design decisions, invariants, and non-obvious
implementation details that are not derivable from reading the code alone.
For usage and workflow, see `README.md`.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scripted models | Recent Cadence, Phase Nowcast Hybrid, Gap-Conditional | Three distinct methodologies: interval baseline, recursive state-space, event-level regression |
| Ensemble | Consensus Blend over the 3 scripted models only | Agents are excluded from consensus until retrospectives show they help |
| LLM agents | Claude (Opus 4.6) + Codex (GPT-5.4) via CLI | No API keys; agents run as local CLI subprocesses with full repo access |
| Featured forecast | Consensus > best scripted by backtest > static tiebreaker | Agents never auto-featured |
| Agent failure | Fail fast; crashes the pipeline | Use `--skip-agents` if agent CLIs are unavailable |
| Model registration | Explicit lists in `models/__init__.py` and `agents/__init__.py` | No auto-discovery magic |
| Report tracking | `report/` tracked; `.report-archive/` gitignored | Only the latest report is committed |
| Exports | Untracked raw drops; reproducibility via `tracker.json` manifests | Each run records dataset fingerprint, source hash, and all predictions |

## Transactional Invariant

The report write and tracker update are a single logical transaction:

1. Render the new report into a temp directory.
2. Validate the staged output (summary.md and spaghetti plot exist).
3. If `report/` already exists, rename it to a sibling backup path.
4. Rename the staged directory into `report/`.
5. If step 4 fails, restore the backup back to `report/`.
6. Best-effort archive the backup into `.report-archive/<run_id>/`.
7. **Only after step 4 succeeds:** append the run entry to `tracker.json`.

If rendering or validation fails, `report/` is untouched. If the swap fails,
the previous report is restored before the exception propagates.

## Dataset Fingerprinting

The dataset identity (`dataset_id`) is a SHA-256 hash of raw CSV fields — not
interpreted volumes. This ensures the same raw export produces the same
fingerprint even if modeling assumptions (e.g., the breastfeed volume
heuristic) change.

Fields included: `Type`, `Start Date/time (Epoch)`, raw bottle volume columns
and units, raw breastfeed durations. A separate `source_hash` covers the exact
file bytes for file-level identity.

## Per-Model Event Caching

Scripted models do not all share the same event history.

- `Recent Cadence` uses `merge_window_minutes=None` (bottle-only events).
- `Phase Nowcast Hybrid` and `Gap-Conditional` use
  `merge_window_minutes=45` (breastfeed volume merged into the next bottle).

`build_event_cache()` in `models/__init__.py` builds each distinct event
representation once. Backtesting and consensus reuse the cache to avoid
redundant computation at every cutoff.

## Agent Contract

Both agents share one prompt (`agents/prompt/prompt.md`) and one dispatcher
(`agents/run.sh`). Python assembles the full prompt by prepending:

```
Export CSV to use: <resolved path>
Your workspace: <resolved path>
```

The agent has full read access to the repo. It must write two files to its
workspace before exiting:

- **`forecast.json`**: `{"feeds": [{"time": "ISO8601", "volume_oz": float}, ...]}`
  — chronologically ordered, times strictly increasing and after the latest
  recorded activity.
- **`methodology.md`**: what the agent did on this run, inserted directly
  into the report. Durable strategy notes belong in separate workspace files.

Before each run, the Python runner deletes any existing `forecast.json` and
`methodology.md` so a stale file from a prior run cannot mask a current
failure.

## Breastfeeding Heuristic

Breastfeeding is not a prediction target. Two scripted models use it as a
feature: estimated intake (0.5 oz per 30 min) is merged into the next bottle
when that bottle starts within 45 minutes. Timing is always scored against
bottle-feed start times, not breastfeeding times.

## Retrospective Completeness

When the current export does not fully cover 24 hours after the prior run's
cutoff, the retrospective reports a partial horizon. First-feed error is
always reported when at least one actual exists, but full-24h MAE is only
computed when the full horizon is observed.

## Data Floor

All activities before March 15, 2026 are discarded during CSV parsing
(`DATA_FLOOR` in `data.py`). This is a global constraint, not a per-model
lookback window — models may further narrow their training windows, but
nothing before this date is ever visible to any part of the pipeline.

## Git Metadata

`git_dirty` intentionally ignores untracked files (`--untracked-files=no`).
Raw export drops are untracked input, not code changes, so they should not
mark every run as dirty in the report footer.
