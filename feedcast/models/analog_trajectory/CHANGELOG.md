# Changelog

Tracks behavior-level changes to the Analog Trajectory model. Add newest entries first.

## Episode-level history evaluated, not shipped | 2026-03-26

### Problem

Cluster-internal feeds (top-ups, continuations) create spurious states
with short gaps and low volumes that pollute neighbor retrieval features
(`last_gap`, `mean_gap`, `last_volume`, `mean_volume`).

### Research

Updated `research.py` to compare raw vs. episode state libraries and
feature quality. The question: does grouping raw feeds into episodes
before building states produce cleaner similarity features and better
neighbor retrieval?

| Metric | Raw | Episode | Better? |
|--------|-----|---------|---------|
| Events | 97 | 80 | Episode — 17 cluster-internal feeds removed |
| Complete states | 84 | 70 | Raw has more, but episode states are cleaner |
| Mean gap (last_gap) | 2.50h | 2.99h | Episode — no artificial sub-hour cluster gaps |
| Gap std | 1.01 | 0.78 | Episode — tighter, less noisy |
| Mean volume (last_volume) | 2.87oz | 3.44oz | Episode — sums reflect real intake |
| Volume std | 1.10 | 0.78 | Episode — tighter |
| gap1 MAE (fold-causal) | 0.770h | 0.656h | Episode — 15% more accurate |
| traj3 MAE (fold-causal) | 0.764h | 0.670h | Episode — 12% more accurate |

Replay (ship gate, 20260325 export, 03/24→03/25 window):

| Metric | Raw | Episode | Better? |
|--------|-----|---------|---------|
| Headline | 68.22 | 66.65 | Raw (-1.57) |
| Count F1 | 100.0 | 91.16 | Raw (episode predicted 7, actual was 9) |
| Timing | 46.55 | 48.73 | Episode (matched episodes more accurate) |

### Solution

Despite strong research metrics, **replay headline dropped**. The
episode model under-predicted episode count because episode-level
trajectories are shorter (fewer events per trajectory), so blended
forecasts produce fewer predictions. The count score drop outweighed
the timing improvement.

**Decision: not shipped.** Model continues to use raw feed history.
The episode-level comparison is preserved in `research.py`. A
possible future fix: decouple trajectory length estimation from
per-neighbor event count (e.g., derive from episode-level daily
counts rather than median neighbor trajectory length).
