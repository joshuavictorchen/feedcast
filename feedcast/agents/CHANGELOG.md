# Changelog

Tracks behavior-level changes to the agent inference model. Add newest entries first.

## Four-bucket day-part split | 2026-04-13

### Problem

The workspace baseline (`model.py`) splits inter-episode gaps into two
day-parts (overnight 19:00-07:00 and daytime 07:00-19:00) and takes
the recency-weighted median per bucket. Recent observations show
three structurally different gap regimes within overnight that a single
overnight median blends together: an evening transition into the first
sleep stretch, wake-feed-sleep intervals in deep night, and shorter
pre-dawn gaps. The prior run layered ad-hoc overnight sub-periods on
top of `model.py` but kept daytime on the baseline's two-bucket median,
so daytime and overnight were computed under different schemes.

### Research

Over the last 7 days, recency-weighted gap medians (48-hour half-life)
by clock-hour bucket of the gap-starting feed:

| Bucket        | Hours        | Weighted median gap |
|---------------|--------------|---------------------|
| Evening       | 19:00-22:00  | 3.77h               |
| Deep night    | 22:00-03:00  | 4.03h               |
| Early morning | 03:00-07:00  | 2.95h               |
| Daytime       | 07:00-19:00  | 2.31h               |

Projecting forward from the 2026-04-13T19:15:38 cutoff yields an
8-episode 24h schedule (evening 3.77h, deep night 4.03h, two early
morning 2.95h, four daytime 2.31h), which aligns with the
recency-weighted daily episode count of 7.7. The prior run's
retrospective against 2026-04-13 actuals scored headline 82.2 /
count 95.0 / timing 71.1, with 7 predicted vs. 8 actual episodes. The
fuller bucket scheme closes that one-episode undercount.

### Solution

Classify every inter-episode gap into one of four sub-periods by the
clock hour of the gap-starting feed, compute the recency-weighted
median per sub-period, and step the forecast forward from the cutoff
by applying the sub-period gap that matches each predicted feed's
start clock hour. Daytime is now computed under the same bucket-and-
weight scheme as the overnight sub-periods rather than inherited from
`model.py`'s two-bucket daytime median.

| Aspect               | Before                                        | After                                           |
|----------------------|-----------------------------------------------|-------------------------------------------------|
| Sub-period count     | 3 overnight + 1 daytime (daytime inherited)   | 4 unified (evening, deep night, early morning, daytime) |
| Daytime gap source   | `model.py` two-bucket daytime median          | Four-bucket scheme, same recency weighting as overnight |
| Projected 24h count  | 7 episodes                                    | 8 episodes                                      |

## Add explicit runtime budget and fast-path guidance | 2026-04-09

### Problem

Agent inference had a hard 10-minute subprocess timeout, but the prompt
did not say so. The agent also had no explicit conservative target or
deadline, which made slow repo-wide exploration too easy.

### Solution

Updated `prompt.md` to surface the hard timeout, a 5-minute target, and
absolute start/deadline timestamps. The prompt now also states that it
does not provide a live timer and explicitly prefers the fastest path to
a valid forecast: run the existing workspace model first when usable,
write `forecast.json` early, and treat deeper exploration as optional
when time remains.

## Initial forecasting model: Empirical Cadence Projection | 2026-04-09

### Problem

The agent workspace had no forecasting logic — only placeholder docs.
Agent inference was non-functional.

### Solution

Implemented `model.py`: a non-parametric forecasting script that
projects forward from recency-weighted inter-episode gap medians split
by day-part (overnight 19–07, daytime 07–19). Key features:
- 48h recency half-life (aggressive, tuned via multi-cutoff testing)
- Conditional survival estimate for the first predicted feed
- Count calibration against recent daily episode counts (30% threshold)
- CLI interface: `--export`, `--cutoff`, `--horizon` → writes `forecast.json`

Added `strategy.md` with approach documentation, performance baselines,
open questions, and guidance for future agents. Updated `methodology.md`
and `design.md` to reflect the actual implementation.

### Research

Tested against 3 available exports across 5 retrospective cutoff points.
Single retrospective: headline 67.8 (2nd, behind slot drift at 69.0;
best timing score of any model at 53.6). Multi-cutoff mean: 62.5
(4th; survival hazard leads at 71.1). Count accuracy (92.5) tied for
best; timing (43.9) is the main weakness, especially on evening cutoffs.

## Restructured to flat shared workspace | 2026-04-03

### Problem

The agent workspace was split into per-agent subdirectories (`claude/`,
`codex/`) with a shared prompt under `prompt/prompt.md` and a shell
dispatcher (`run.sh`). Only one agent runs per pipeline invocation, so
separate directories added complexity without isolation value. The
dispatcher duplicated invocation logic that now lives in
`feedcast/agent_runner.py`.

### Solution

Collapsed to a single flat workspace: `prompt.md`, `design.md`,
`methodology.md`, and `CHANGELOG.md` at the top level. `forecast.json`
is written here at runtime. The prompt now uses `{{var}}` placeholders
for runtime context substitution. Both agents share the workspace.
Pipeline integration is planned for Phase 4.
