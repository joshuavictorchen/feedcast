# Consensus Blend Changelog

## 2026-03-25 — Backtracking selector with single-use enforcement

**Problem:** Greedy selection was suboptimal and the per-point
arrays were misaligned with the sorted point_key, causing rebuilds
to use wrong timestamps.

**Solution:** Replaced the greedy selector with backtracking search
plus upper-bound pruning over forward-ordered subsequences (~6ms
for 17 candidates). Not globally optimal — cannot discover
sequences where an earlier candidate becomes valid only after a
later candidate claims shared points — but covers the practical
cases and beats the lockstep baseline. Fixed point_key alignment
by sorting chosen-points before building tuples. Restored wider
radius (120 min) since the backtracking selector benefits from more
candidates. Conflict window 90 min (MIN_INTERVAL_HOURS).

## 2026-03-24 — Replace lockstep with majority sequence selector

**Problem:** The lockstep median-timestamp blend could create phantom
consensus and misalignment cascades when models emitted extra or
shifted feeds. The first flat clustering prototype fixed the
misalignment issue but over-predicted badly because it emitted every
local agreement region.

**Solution:** Production now builds majority-supported candidate feed
slots around each model prediction and selects the best
non-overlapping sequence with weighted interval scheduling.

## 2026-03-24 — Extract into dedicated model directory

**Problem:** Consensus blend was defined inline in
`feedcast/models/__init__.py` without research, design docs, or
the standard model directory structure.

**Solution:** Moved into `feedcast/models/consensus_blend/` with
`model.py`, `methodology.md`, `design.md`, `research.py`, and
`research_results.txt`. At that checkpoint, runtime behavior matched
the prior inline implementation while the new model directory and
research scaffolding were put in place.
