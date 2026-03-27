# Changelog

Tracks behavior-level changes to the Slot Drift model. Add newest entries first.

## Episode-level template building | 2026-03-26

### Problem

Raw feed history includes cluster-internal feeds (top-ups, continuations)
that inflate the daily count and create spurious template slots.

### Research

Updated `research.py` with an episode-level analysis section comparing
raw vs. episode template construction. Episode grouping removes
cluster-internal feeds, producing lower and more stable daily counts.
The median slot count dropped by one, and days with the most cluster
activity saw the largest reductions (up to 3 fewer). Episode-level
trial alignment was clean for most days, though one cluster-free day
with an unusually late feed lost a match because the smaller template
had no slot covering that time-of-day.

### Solution

Collapse raw history into feeding episodes (`episodes_as_events()`)
before template building. The slot count, template positions, drift
estimation, and filled-slot matching all operate on episode-level
events. Raw history is still used for the last-known-feed timestamp
in gap computation.

Replay gate: headline improved slightly (+0.28). Count F1 traded down
because one fewer episode matched, but timing improved enough to
compensate. Net positive on headline; change shipped.
