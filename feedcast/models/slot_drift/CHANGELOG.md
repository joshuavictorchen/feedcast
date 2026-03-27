# Changelog

Tracks behavior-level changes to the Slot Drift model. Add newest entries first.

## Episode-level template building | 2026-03-26

### Problem

Raw feed history includes cluster-internal feeds (top-ups, continuations)
that inflate the daily count and create spurious template slots. The median
slot count was 9 with raw feeds; cluster-free episode count is 8.

### Solution

Collapse raw history into feeding episodes (`group_into_episodes()`) before
template building. The slot count, template positions, drift estimation, and
filled-slot matching all operate on episode-level events. Raw history is
still used for the last-known-feed timestamp in gap computation.

Replay gate: headline 53.74 (episode) vs. 53.46 (raw), +0.28. Count F1
trades down (80.98 vs. 91.77) but timing improves (35.67 vs. 31.14). Net
positive on headline.
