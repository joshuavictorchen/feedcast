# Changelog

Tracks behavior-level changes to the Analog Trajectory model. Add newest entries first.

## Episode-level history evaluated, not shipped | 2026-03-26

### Problem

Cluster-internal feeds (top-ups, continuations) create spurious states
with short gaps and low volumes that pollute neighbor retrieval features
(`last_gap`, `mean_gap`, `last_volume`, `mean_volume`).

### Research

Updated `research.py` with an episode-level comparison section.
Episode grouping removed roughly one-sixth of raw events. The state
library shrank accordingly (fewer complete states). Feature
distributions improved: mean gaps rose and tightened (short
cluster-internal gaps removed), mean volumes rose and tightened
(episode sums replace individual feed volumes), and time-of-day
features were nearly unchanged. Fold-causal leave-one-out evaluation
showed substantial improvement in both first-gap and three-step
trajectory accuracy (~15% and ~12% lower MAE, respectively).

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
