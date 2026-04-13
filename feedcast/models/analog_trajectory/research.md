# Analog Trajectory Research

> `design.md` documents why the model works the way it does.
> `methodology.md` is the report-facing description.
> This file is the evidence: current support and challenges for the
> model's design and constants.

## Overview

Analog Trajectory forecasts by retrieving historical states with similar
feeding patterns and blending what happened next. The key research
questions are:

1. Which configuration wins under the canonical replay objective?
2. Does canonical replay still prefer raw history, or should the model
   build states from episode-collapsed history?
3. Does canonical replay agree with the local retrieval/blending
   diagnostic (`full_traj_MAE`) on the important design choices?
4. Does gap alignment still beat time-offset alignment on the current
   export?

## Last run

| Field | Value |
|---|---|
| Run date | 2026-04-12 |
| Export | `exports/export_narababy_silas_20260412.csv` |
| Dataset | `sha256:1fc8695c14bda5dabdbdf2c554024159f9efbc5e853e5ed449ed1c4f7156f481` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 70.2 |
| Availability | 24/24 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Staleness check:** if the current export differs from the one listed
> here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("analog_trajectory")`
through the shared replay infrastructure. This produces the same
multi-window aggregate used elsewhere in the project: 96-hour replay
lookback, 36-hour window half-life, and episode-boundary cutoffs.

**Canonical tuning** last ran as a full 4704-candidate sweep via
`tune_model()` with candidate-parallel replay. The sweep covers every
production-relevant constant:

- `HISTORY_MODE`: `raw`, `episode`
- `LOOKBACK_HOURS`: `6`, `9`, `12`, `18`, `24`, `48`, `72`
- `FEATURE_WEIGHTS`: `equal`, `gap_emphasis`, `hour_emphasis`,
  `vol_deemphasis`, `gap_hour`, `recent_only`, `means_only`
- `K_NEIGHBORS`: `3`, `5`, `7`
- `RECENCY_HALF_LIFE_HOURS`: `36`, `72`, `120`, `240`
- `TRAJECTORY_LENGTH_METHOD`: `median`, `mean`
- `ALIGNMENT`: `gap`, `time_offset`

Candidates are ranked by availability tier first, then headline score.
On the current export, every analog candidate scored all 24 windows, so
headline score decides the ranking.

### Objective comparison contract

Canonical and internal diagnostics answer different questions.
Canonical evaluation uses the shared replay stack: bottle-only scoring
events, episode-boundary cutoffs over the most recent 96 hours, and the
24-hour headline scorer. The local sweeps optimize fold-causal
retrieval/blending diagnostics such as `full_traj_MAE`, `gap1_MAE`, and
`traj3_MAE` on raw or episode history. Because the canonical surface is
shallow, use the internal/canonical comparison mainly for regime-level
agreement and disagreement, not for strong exact-best-candidate claims.

### Diagnostic sweeps

The last recorded analysis ran two local `full_traj_MAE` sweeps:

- one 2352-config sweep on raw bottle history
- one 2352-config sweep on episode-collapsed history

These are fold-causal retrieval/blending diagnostics. They explain what
kind of state representation and neighbor behavior is locally clean, but
they do not choose shipped constants.

### Raw vs. episode comparison

The research script also compares feature distributions across raw and
episode history at the canonical-best lookback (`9h`). This is the
simplest way to see how episode collapse changes the state space before
any tuning metric is applied.

## Results

### Canonical findings

The full canonical sweep selects:

| Parameter | Value |
|---|---|
| `HISTORY_MODE` | `raw` |
| `LOOKBACK_HOURS` | `9` |
| `FEATURE_WEIGHTS` | `gap_hour [2, 2, 0.5, 0.5, 2, 2]` |
| `K_NEIGHBORS` | `3` |
| `RECENCY_HALF_LIFE_HOURS` | `240` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `time_offset` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 70.2 |
| Count | 91.6 |
| Timing | 54.7 |

All 24 windows scored (100% availability).

This retune on the new export (`20260412`) recovers +4.5 headline
points over the prior constants (tuned on `20260411(1)`), which had
degraded from `68.7` to `65.7` on the new data. Five constants changed:
lookback (`18h` to `9h`), feature weights (`equal` to `gap_hour`),
K (`7` to `3`), recency (`72h` to `240h`), and alignment (`gap` to
`time_offset`). The most recent replay windows improved substantially
(Apr 11 19:35 went from headline 54.7 to 68.3, timing from 35.1 to
50.8).

The raw-vs-episode canonical margin widened to +2.3 headline points
(`70.2` vs `67.9`). Both best candidates now use gap_hour weighting
and time_offset alignment, but they differ on lookback (9h vs 12h),
K (3 vs 3), and recency (240h vs 120h).

Time_offset alignment leads gap for the first time on any export (+0.4
headline points for the raw-history winner). The gap variant with the
same core constants (raw/9h/gap_hour/k=3/hl=240h) scores 69.8, so the
margin is narrow.

The top of the canonical surface remains shallow. Several candidates
land between `69.4` and `70.2`. The broader conclusion is stronger than
the exact decimal ordering: gap_hour weighting with volume de-emphasis
dominates the top of the surface regardless of alignment and lookback
details.

Count improved slightly from 90.4 to 91.6. Timing improved from 48.4
to 54.7, the largest single-export timing gain in the model's tuning
history.

### Diagnostic findings

**Episode history is still locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.418h | 1.070h |
| `gap1_MAE` | 0.753h | 0.640h |
| `traj3_MAE` | 0.759h | 0.653h |

The diagnostic/canonical disagreement continues to extend to history mode.
Episode history produces cleaner retrieval locally, but canonical replay
favors raw history on the current export.

**Internal and canonical metrics diverge on nearly every axis:** The best
raw diagnostic configuration is (`raw`, `18h`, `means_only`, `k=7`,
`36h`, `median`, `time_offset`), while the shipped canonical
configuration is (`raw`, `9h`, `gap_hour`, `k=3`, `240h`, `median`,
`time_offset`). They agree on history mode (raw), trajectory length
(median), and alignment (time_offset, a new point of agreement), but
disagree on lookback, weighting, K, and recency.

**Feature distributions still show the episode advantage for retrieval:**
At the canonical-best 9-hour lookback, episode history produces larger
and tighter gap/volume signals than raw history:

- `last_gap`: `2.648 -> 3.023`
- `mean_gap`: `2.741 -> 3.068`
- `last_volume`: `3.293 -> 3.759`
- `mean_volume`: `3.347 -> 3.758`

These shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library. The cleaner state
space explains the diagnostic advantage, but canonical replay uses
bottle-only scoring events, so cluster-internal feeds are part of the
scoring target that the episode model cannot represent.

**Volume de-emphasis is the dominant regime-level signal:** The top 6
canonical candidates all use gap_hour weighting (`[2, 2, 0.5, 0.5, 2,
2]`), and the remaining top-10 entries use vol_deemphasis (`[1, 1, 0.5,
0.5, 1, 1]`) or hour_emphasis (`[1, 1, 1, 1, 2, 2]`). No top-10
candidate gives volume equal or dominant weight. This is a shift from the
prior export where equal weighting won. The baby's feeding schedule
may be consolidating, making temporal regularity a stronger retrieval cue
than feed size.

### Simulation-study findings

`tests/simulation/test_analog_trajectory.py` validates the Analog
Trajectory implementation against a synthetic DGP where the analog
hypothesis is exactly true. The DGP is a clean bottle-only
alternating-archetype schedule: two distinct state archetypes repeat
across 14 days, each with a characteristic subsequent trajectory.

Both archetypes anchor at the same hour (`08:00`). This forces
retrieval to separate states using recent gap/volume structure rather
than `sin_hour`/`cos_hour`. If the archetypes differed by time-of-day,
the test would mostly validate the hour features instead of the
intended recurrence signal.

The suite validates three properties on that conforming DGP:

- **Retrieval correctness:** the nearest-neighbor search recovers
  same-archetype historical anchors exactly under a focused retrieval
  regime.
- **Forecast conformance:** the public forecaster reproduces the
  planted future for a new occurrence of the archetype under that same
  regime.
- **Canonical diagnostic:** replay on a targeted grid rewards focused
  retrieval over deliberately blurrier alternatives.

These results confirm that **the implementation behaves correctly on
clean hypothesis-conforming data**. They do not validate the production
episode configuration end to end: the fixture uses raw history and
omits clustered top-ups to isolate analog retrieval from episode
collapse. All synthetic gaps stay above the clustering-extension
boundary, so `HISTORY_MODE="episode"` is effectively a no-op on this
DGP.

The canonical diagnostic also reinforces a finding from the real-data
sweep: the analog canonical surface is shallow. Small fixture changes
move the exact top-ranked candidate while preserving the broader
ordering that focused retrieval beats blurrier retrieval. Regime-level
assertions are therefore more defensible than exact-best-candidate
assertions.

## Conclusions

**Disposition: Change.** Analog Trajectory ships updated constants:

- `HISTORY_MODE = "raw"`
- `LOOKBACK_HOURS = 9`
- `FEATURE_WEIGHTS = gap_hour [2, 2, 0.5, 0.5, 2, 2]`
- `K_NEIGHBORS = 3`
- `RECENCY_HALF_LIFE_HOURS = 240`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "time_offset"`

The retune on the new export moved five constants: lookback (`18h` to
`9h`), feature weights (`equal` to `gap_hour`), K (`7` to `3`), recency
(`72h` to `240h`), and alignment (`gap` to `time_offset`). Headline
recovered from `65.7` to `70.2`, with timing improving from `48.4` to
`54.7`.

The regime-level signal on this export is clearer than prior exports:
gap_hour weighting dominates the top of the canonical surface regardless
of other constant choices. Volume de-emphasis is the strongest
regime-level finding, consistent with the baby's schedule consolidating
and temporal regularity becoming the primary retrieval signal.

Time_offset alignment leads gap for the first time (+0.4 headline
points). The margin is narrow and time_offset has been inferior on every
prior export, so this axis may flip again.

The internal/canonical divergence narrowed slightly on alignment (both
now prefer time_offset) but widened on lookback, K, and recency. They
agree on history mode (raw), trajectory length (median), and alignment
(time_offset). The process is clean: a single full canonical sweep
selects all production constants.

## Open questions

### Model-local

- **RECENCY_HALF_LIFE_HOURS=240 is a boundary winner for the second
  consecutive export.** The recency grid is [36, 72, 120, 240]. 240h
  (~10 days) is already broad for a growing baby. Extending the grid
  (e.g., to 360h, 480h) could reveal whether the optimum is interior or
  still climbing. However, going broader than ~10 days risks matching
  against stale patterns from a meaningfully different feeding regime.
- **Constant churn between exports continues.** The optimal constant
  combination has shifted on each of the last five exports. The shallow
  canonical surface means small data changes move the exact winner. The
  regime-level signal (volume de-emphasis, raw history) is more stable
  than the point estimates.
- **Time_offset alignment flipped for the first time.** The margin is
  narrow (+0.4 headline) and gap has been preferred on every prior
  export. This could represent a real regime change (more regular daily
  structure favoring absolute positioning) or sampling noise on the
  shallow surface.
- **Top-up windows remain fragile.** Some of the weakest per-window
  scores still sit around short daytime follow-ups. The Apr 11 evening
  windows drove the largest timing errors under the prior production
  config.
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test near-
  miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `91.6`; timing is `54.7`.
  Timing drift remains the main quality constraint. This pattern
  persists across all five models (see `feedcast/research/README.md`).
- **Episode collapse vs. bottle-level scoring tension:** Episode history
  produces cleaner state representations but removes events that
  canonical replay scores against. This tension may affect other models
  that use episode history. The analog model's raw history preference
  highlights the tradeoff most sharply because it directly controls
  which events enter the state library and trajectory blending.
- **Volume de-emphasis as a cross-model signal:** The top of the
  canonical surface uniformly de-emphasizes volume. If this reflects a
  real shift in the baby's feeding patterns (more regular timing, more
  variable volume), other models that weight volume heavily may also
  benefit from reduced volume sensitivity.
