# Consensus Blend Changelog

## 2026-03-24 — Replace lockstep with majority sequence selector

**Problem:** The lockstep median-timestamp blend could create phantom
consensus and misalignment cascades when models emitted extra or
shifted feeds. The first flat clustering prototype fixed the
misalignment issue but over-predicted badly because it emitted every
local agreement region.

**Solution:** Production now builds majority-supported candidate feed
slots around each model prediction and selects the best
non-overlapping sequence with weighted interval scheduling. The old
lockstep walk remains in `model.py` as the research baseline.

## 2026-03-24 — Extract into dedicated model directory

**Problem:** Consensus blend was defined inline in
`feedcast/models/__init__.py` without research, design docs, or
the standard model directory structure.

**Solution:** Moved into `feedcast/models/consensus_blend/` with
`model.py`, `methodology.md`, `design.md`, `research.py`, and
`research_results.txt`.  Production behavior is unchanged — the
lockstep median-timestamp algorithm produces identical output.
Added scorer-based research infrastructure comparing the lockstep
blend against a pool-then-cluster candidate generator.  The
candidate generator shows better timing but worse count accuracy;
a sequence-aware selector is planned as the next step.
