# Consensus Blend Changelog

## 2026-03-25 — Replace mutable backtracking with immutable exact selector

**Problem:** The backtracking selector still depended on candidate
rebuilds during search. That made correctness depend on search order
and kept the core algorithm harder to reason about than it needed to
be.

**Solution:** Replaced the rebuild path with immutable majority-subset
candidate generation plus an exact MILP set-packing selector. The new
runtime enforces single-use model points and temporal conflicts as hard
constraints instead of repairing candidates after selection.

## 2026-03-25 — Backtracking selector with single-use enforcement

**Problem:** Greedy selection was suboptimal and the per-point arrays
were misaligned with the sorted point_key, causing rebuilds to use
wrong timestamps.

**Solution:** Replaced the greedy selector with backtracking search
plus upper-bound pruning over forward-ordered subsequences. Fixed
point_key alignment by sorting chosen-points before building tuples and
restored wider radius (120 min) to give the selector more candidates.

## 2026-03-24 — Replace lockstep with majority sequence selector

**Problem:** The lockstep median-timestamp blend could create phantom
consensus and misalignment cascades when models emitted extra or
shifted feeds. The first flat clustering prototype fixed the
misalignment issue but over-predicted badly because it emitted every
local agreement region.

**Solution:** Production now builds majority-supported candidate feed
slots around each model prediction and selects a non-overlapping
sequence instead of walking the models in lockstep.

## 2026-03-24 — Extract into dedicated model directory

**Problem:** Consensus blend was defined inline in
`feedcast/models/__init__.py` without research, design docs, or the
standard model directory structure.

**Solution:** Moved into `feedcast/models/consensus_blend/` with
`model.py`, `methodology.md`, `design.md`, `research.py`, and
`research_results.txt`.
