# Changelog

Tracks behavior-level changes to the Slot Drift model. Add newest entries first.

## Episode-level template building | 2026-03-26

### Problem

Raw feed history includes cluster-internal feeds (top-ups, continuations)
that inflate the daily count and create spurious template slots.

### Research

Updated `research.py` to compare raw vs. episode template construction.
The question: does grouping raw feeds into episodes before building
the daily template produce a slot count closer to the true number of
independent feeding episodes?

| Metric | Raw | Episode | Better? |
|--------|-----|---------|---------|
| Median daily count | 9 | 8 | Episode — 8 matches the true daily episode count from labeled data |
| Daily count std | 1.07 | 0.69 | Episode — more stable across days |
| Trial alignment (unmatched/day) | 0–4 | 0–3 | Comparable; one cluster-free day with an unusually late feed lost a match in the smaller template |

Replay (ship gate, 20260325 export, 03/24→03/25 window):

| Metric | Raw | Episode | Better? |
|--------|-----|---------|---------|
| Headline | 53.46 | 53.74 | Episode (+0.28) |
| Count F1 | 91.77 | 80.98 | Raw (one fewer matched episode) |
| Timing | 31.14 | 35.67 | Episode (matched episodes positioned more accurately) |

### Solution

Collapse raw history into feeding episodes (`episodes_as_events()`)
before template building. The slot count, template positions, drift
estimation, and filled-slot matching all operate on episode-level
events. Raw history is still used for the last-known-feed timestamp
in gap computation.

Replay gate: headline improved slightly (+0.28). Count F1 traded down
because one fewer episode matched, but timing improved enough to
compensate. Net positive on headline; change shipped.
