# Changelog

Tracks behavior-level changes to the Slot Drift model. Add newest entries first.

## Wider lookback, faster drift for template stability | 2026-04-13

### Problem

The LOOKBACK=7 regime, tuned for an episode-count transition from 8 to 7,
degraded as episode counts rebounded. Headline dropped from 65.8 to 64.0 on
the 20260413 export, with the latest retrospective showing headline 57.9
(count 84.9, timing 39.5). The model predicted 6 episodes vs. 8 actual.

With LOOKBACK=7, episode counts [10, 7, 8, 9, 7, 7, 9] produced a median of
8 (up from 7 on the prior export), but only 1 of 7 days matched the median,
making the template seed fragile.

### Research

588-candidate canonical sweep via `tune_model()` on
`exports/export_narababy_silas_20260413.csv`, plus boundary checks extending
LOOKBACK to 12, 13, 15, 16, 21, and 28 and THRESHOLD to 3.5 and 4.0.

LOOKBACK landscape (DRIFT=2.5, THRESHOLD=3.0):

| LOOKBACK | Headline |
|----------|----------|
| 5        | 60.8     |
| 6        | 58.5     |
| 7        | 64.0     |
| 10       | 61.7     |
| 12       | 62.9     |
| 13       | 64.9     |
| 14       | 66.6     |
| 15       | 62.0     |
| 16       | 62.4     |
| 21       | 62.1     |
| 28       | 67.8     |

LOOKBACK=14 is a sharp interior peak (13→64.9, 14→66.6, 15→62.0). The
boundary artifact identified on the prior export (14→65.9, 21→66.5,
28→66.6 monotonically climbing) has resolved: LOOKBACK=21 now scores 62.1,
well below 14. LOOKBACK=28 (67.8) is isolated beyond a valley at 21.

DRIFT gradient at LOOKBACK=14, THRESHOLD=3.0:

| DRIFT | Headline |
|-------|----------|
| 2.0   | 66.0     |
| 2.5   | 66.6     |
| 3.0   | 66.3     |
| 5.0   | 66.0     |
| 7.0   | 65.5     |

DRIFT=2.5 is interior (2.0→66.0, 2.5→66.6, 3.0→66.3). THRESHOLD=3.0
is near-tied with 3.5 (66.6 vs. 66.8); 3.0 retained since the delta is
negligible and 3.5 is also interior (4.0→63.2).

| Constant | Before | After |
|---|---|---|
| `LOOKBACK_DAYS` | 7 | 14 |
| `DRIFT_WEIGHT_HALF_LIFE_DAYS` | 5.0 | 2.5 |
| `MATCH_COST_THRESHOLD_HOURS` | 3.0 | 3.0 |

| Metric | Before | After | Delta |
|---|---|---|---|
| Headline | 64.0 | 66.6 | +2.6 |
| Count | 87.1 | 88.8 | +1.7 |
| Timing | 47.3 | 50.3 | +3.0 |
| Availability | 25/25 | 25/25 | 0 |

### Solution

Wider lookback (14 days) stabilizes the episode template during a period of
high episode-count variability (7-10 range). With LOOKBACK=14, 7 of 14 days
match the median episode count of 7, providing a robust template seed. The
prior LOOKBACK=7 had only 1 matching day.

The 2.5-day drift half-life with 14-day lookback gives the oldest day ~2.7%
of yesterday's weight, focusing drift estimation on recent data while
allowing older data to contribute to template stability. This is a sharper
recency gradient than the prior 5.0-day half-life at LOOKBACK=7 (oldest
day ~44% weight), reflecting the model's need to track faster-moving timing
shifts within a wider template window.

Both count (+1.7) and timing (+3.0) improved. The LOOKBACK=14 regime was
selected because its prior boundary artifact (monotonically climbing through
21, 28) has resolved, making it a confirmed interior optimum.

## Wider lookback, looser threshold for episode-count transition | 2026-04-12

### Problem

The LOOKBACK=5 regime, tuned for a stable 8-episode pattern, degraded as
the baby's daily episode count shifted from 8 toward 7. Headline dropped
from 73.1 to 64.4 on the 20260412 export, with timing falling from 59.3
to 46.0. The most recent windows (April 11) showed timing scores of
30-36, indicating substantial misalignment between projected and actual
feed times.

### Research

588-candidate canonical sweep via `tune_model()` on
`exports/export_narababy_silas_20260412.csv`, plus boundary checks
extending LOOKBACK to 21 and 28 and THRESHOLD to 3.5 and 4.0.

Two competing regimes emerged:

LOOKBACK=7 regime (THRESHOLD=3.0):

| DRIFT | Headline | Count | Timing |
|-------|----------|-------|--------|
| 3.0   | 65.6     | 89.3  | 48.7   |
| 5.0   | 65.8     | 89.3  | 48.9   |
| 7.0   | 65.7     | 89.2  | 48.8   |

DRIFT plateau from 3.0 to 7.0 (range 0.2). THRESHOLD interior:
2.5→63.3, 3.0→65.8, 3.5→worse. LOOKBACK interior: 6→58.3, 7→65.8,
10→61.0.

LOOKBACK=14+ regime (THRESHOLD=2.5, DRIFT=2.5):

| LOOKBACK | Headline |
|----------|----------|
| 14       | 65.9     |
| 21       | 66.5     |
| 28       | 66.6     |

LOOKBACK=14 is a boundary artifact: performance continues climbing
through 21 and 28 without plateauing.

| Constant | Before | After |
|---|---|---|
| `LOOKBACK_DAYS` | 5 | 7 |
| `DRIFT_WEIGHT_HALF_LIFE_DAYS` | 7.0 | 5.0 |
| `MATCH_COST_THRESHOLD_HOURS` | 2.0 | 3.0 |

| Metric | Before | After | Delta |
|---|---|---|---|
| Headline | 64.4 | 65.8 | +1.4 |
| Count | 93.2 | 89.3 | -3.9 |
| Timing | 46.0 | 48.9 | +2.9 |
| Availability | 24/24 | 24/24 | 0 |

### Solution

Wider lookback (7 days) provides more history for template building as
the episode count transitions from 8 to 7. Episode counts over the
7-day window: 7, 10, 7, 8, 9, 7, 7 (median 7). Four of 7 days match
the median, providing a stable initial template seed (vs. 1 of 5 at the
prior LOOKBACK=5).

The 5.0-day drift half-life with 7-day lookback gives the oldest day
~44% of yesterday's weight, providing gentle recency weighting rather
than near-uniform averaging. Looser threshold (3.0h) admits more matches
during the transition, improving template refinement when episode counts
vary.

The LOOKBACK=7 regime was chosen over LOOKBACK=14+ because the longer
regime has no stable upper bound (performance continues climbing at 21,
28), indicating it is fitting stale history rather than capturing a
stable signal. LOOKBACK=7 is an interior optimum (6→58.3, 7→65.8,
10→61.0).

Timing improved +2.9, count traded down -3.9. The timing improvement
concentrates on the most recent windows (April 11 retrospective timing:
35.6→53.5, +17.9), where the pattern shift is most visible.

## Shorter lookback, near-uniform drift weighting | 2026-04-11

### Problem

The LOOKBACK=10 regime, tuned for a period of higher episode-count
variability, is no longer optimal. The competing LOOKBACK=5 regime
first identified on the 20260410(2) export (+0.9 headline) has
strengthened to +2.1 on the 20260411 export. Timing remains the
weaker component (55.2 vs count 91.8), and the LOOKBACK=5 regime
improves it by +4.1.

### Research

588-candidate canonical sweep via `tune_model()` on
`exports/export_narababy_silas_20260411.csv`, plus a 3-candidate
boundary check extending DRIFT to 8.0, 10.0, and 14.0 (resolving the
prior DRIFT=7.0 boundary concern).

DRIFT gradient at LOOKBACK=5, THRESHOLD=2.0:

| DRIFT | Headline |
|-------|----------|
| 3.0   | 72.2     |
| 4.0   | 72.6     |
| 5.0   | 72.9     |
| 7.0   | 73.1     |
| 8.0   | 73.1     |
| 10.0  | 73.2     |
| 14.0  | 73.0     |

The plateau spans DRIFT=7.0 through 14.0 (range 0.2). The peak at
DRIFT=10.0 confirms DRIFT=7.0 is not a boundary artifact.

THRESHOLD gradient at LOOKBACK=5, DRIFT=7.0:

| THRESHOLD | Headline |
|-----------|----------|
| 1.75      | <71.5    |
| 2.0       | 73.1     |
| 2.5       | 71.9     |
| 3.0       | 71.9     |

THRESHOLD=2.0 is an interior peak.

| Constant | Before | After |
|---|---|---|
| `LOOKBACK_DAYS` | 10 | 5 |
| `DRIFT_WEIGHT_HALF_LIFE_DAYS` | 0.80 | 7.0 |
| `MATCH_COST_THRESHOLD_HOURS` | 1.5 | 2.0 |

| Metric | Before | After | Delta |
|---|---|---|---|
| Headline | 71.0 | 73.1 | +2.1 |
| Count | 91.8 | 91.1 | -0.7 |
| Timing | 55.2 | 59.3 | +4.1 |
| Availability | 24/24 | 24/24 | 0 |

### Solution

Shorter lookback (5 days) focuses the template on the most recent
pattern. The 7.0-day drift half-life produces near-uniform weighting
over the 5-day window (oldest day receives ~61% of yesterday's weight),
effectively averaging slot positions rather than extrapolating linear
drift. This reflects the baby's feeding pattern stabilizing enough that
active drift tracking adds noise rather than signal. Looser threshold
(2.0h) admits more matches for template refinement. Timing improved +4.1
(the primary gain), count traded down -0.7.

The LOOKBACK=5 regime was identified as a competing alternative on the
prior export (+0.9) and has strengthened on this export (+2.1). The
prior concern about DRIFT=7.0 being a boundary value is resolved: an
extended sweep shows a flat plateau from 7.0 through 14.0 with a
gentle peak around 10.0. DRIFT=7.0 is on the ascending edge of that
plateau.

## Relaxed drift and threshold as pattern stabilizes | 2026-04-10

### Problem

The 0.25-day drift half-life and 1.0h match threshold, tuned for a
volatile period, degraded on the 20260410(2) export: headline dropped
from 69.3 to 65.7, with timing falling from 52.4 to 47.5. The baby's
feeding pattern is stabilizing, and the aggressive recency weighting was
over-reacting to day-to-day noise.

### Research

Targeted sweeps (84 + 24 + 54 + 9 candidates) plus a 588-candidate
canonical sweep via `tune_model()` on
`exports/export_narababy_silas_20260410(2).csv`.

| Constant | Before | After |
|---|---|---|
| `DRIFT_WEIGHT_HALF_LIFE_DAYS` | 0.25 | 0.80 |
| `MATCH_COST_THRESHOLD_HOURS` | 1.0 | 1.5 |
| `LOOKBACK_DAYS` | 10 | 10 |

| Metric | Before | After | Delta |
|---|---|---|---|
| Headline | 65.7 | 71.1 | +5.4 |
| Count | 91.7 | 90.2 | -1.5 |
| Timing | 47.5 | 56.4 | +8.9 |
| Availability | 25/25 | 25/25 | 0 |

DRIFT=0.80 is an interior peak (0.75→69.8, 0.80→71.1, 0.85→70.8).
THRESHOLD=1.5 is interior (1.25→67.7, 1.5→71.1, 1.75→63.6). A
competing regime at LOOKBACK=5, DRIFT=7.0, THRESHOLD=2.0 scored 72.0
(+0.9 above baseline) but DRIFT=7.0 is a boundary value with a
flattening gradient, and LOOKBACK=5 is more vulnerable to single-day
outliers during the current 7-10 episode-count variability.

### Solution

Longer drift half-life (0.80 days) gives about 42% weight to
yesterday's drift vs. ~6% at the prior 0.25 setting, smoothing out
day-to-day noise now that the baby's timing pattern is more consistent.
Looser threshold (1.5h) admits more matches for drift estimation without
letting noise dominate. Timing improved +8.9 (the primary gain), count
traded down -1.5. LOOKBACK=10 unchanged: episode-count variability
(7-10 range) still benefits from the wider stabilizing window.

## Wider lookback, tighter matching from canonical sweep | 2026-04-10

### Problem

The baby's daily episode count has become more variable (7–10 range vs.
prior 7–9). The 5-day lookback was too narrow to build a stable template
during this volatile period, and headline score degraded from 68.4 to
63.1 on the 20260410 export.

### Research

588-candidate canonical sweep via `tune_model()` on
`exports/export_narababy_silas_20260410.csv`, plus a 28-candidate
boundary check confirming all three winning values are interior optima
(not boundary artifacts).

| Constant | Before | After |
|---|---|---|
| `LOOKBACK_DAYS` | 5 | 10 |
| `DRIFT_WEIGHT_HALF_LIFE_DAYS` | 1.0 | 0.25 |
| `MATCH_COST_THRESHOLD_HOURS` | 1.5 | 1.0 |

| Metric | Before | After | Delta |
|---|---|---|---|
| Headline | 63.1 | 69.3 | +6.2 |
| Count | 83.2 | 92.2 | +9.0 |
| Timing | 48.4 | 52.4 | +4.0 |
| Availability | 26/26 | 26/26 | 0 |

### Solution

Longer lookback (10 days) stabilizes the template across a period of
higher episode-count variability — 10 days provides 5 days with the
median episode count vs. 1 day under the old 5-day window. Shorter
drift half-life (0.25 days) makes yesterday's drift dominate the
projection, matching the fast-shifting timing pattern. Tighter match
threshold (1.0h) rejects weak slot assignments that would pollute drift
estimation. Count improved +9.0 (the primary gap this round), timing
improved +4.0.

## Tighter constants from canonical sweep | 2026-03-29

### Problem

Timing score (40.4) was the primary bottleneck with the original
constants. The 3-day drift half-life and 7-day lookback included
stale history that diluted recent pattern signal.

### Research

128-candidate canonical sweep via `tune_model()` over
`DRIFT_WEIGHT_HALF_LIFE_DAYS` (8 values), `MATCH_COST_THRESHOLD_HOURS`
(4 values), and `LOOKBACK_DAYS` (4 values). Multi-window evaluation
(24 windows, 96h lookback, 36h half-life) on 20260327 export.

| Constant | Before | After |
|---|---|---|
| `DRIFT_WEIGHT_HALF_LIFE_DAYS` | 3.0 | 1.0 |
| `LOOKBACK_DAYS` | 7 | 5 |
| `MATCH_COST_THRESHOLD_HOURS` | 2.0 | 1.5 |

| Metric | Before | After | Delta |
|---|---|---|---|
| Headline | 59.2 | 68.4 | +9.2 |
| Count | 87.6 | 90.8 | +3.2 |
| Timing | 40.4 | 51.9 | +11.5 |
| Availability | 24/24 | 24/24 | 0 |

### Solution

All three constants push the model toward more recent, more focused
data: shorter drift half-life responds faster to recent timing shifts,
shorter lookback excludes stale days, tighter match threshold rejects
weak slot assignments. Timing improved +11.5 with no availability loss.

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
