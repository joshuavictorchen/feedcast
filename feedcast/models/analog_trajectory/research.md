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
| Run date | 2026-04-13 |
| Export | `exports/export_narababy_silas_20260413.csv` |
| Dataset | `sha256:1820a6f33b499f22c5adbfc8bbb0538fca2366fbf4661452b57fddd31a0a6d8d` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 69.4 |
| Availability | 25/25 windows (100%) |
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
| `LOOKBACK_HOURS` | `24` |
| `FEATURE_WEIGHTS` | `gap_hour [2, 2, 0.5, 0.5, 2, 2]` |
| `K_NEIGHBORS` | `5` |
| `RECENCY_HALF_LIFE_HOURS` | `240` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `gap` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 69.4 |
| Count | 94.1 |
| Timing | 51.8 |

All 25 windows scored (100% availability).

This retune on the new export (`20260413`) recovers +2.8 headline
points over the prior constants (tuned on `20260412`), which had
degraded from `70.2` to `66.6` on the new data. Three constants changed:
lookback (`9h` to `24h`), K (`3` to `5`), and alignment (`time_offset`
to `gap`). The most recent replay windows improved substantially
(Apr 12 19:15 went from headline 63.4 to 80.4, timing from 42.1 to
64.6).

The raw-vs-episode canonical margin narrowed to +1.2 headline points
(`69.4` vs `68.2`). Both best candidates now use gap_hour weighting
and gap alignment, but they differ on lookback (24h vs 18h),
K (5 vs 7), and recency (240h vs 72h).

Gap alignment regains the lead after a single-export time_offset
preference. All top 10 canonical candidates use gap alignment,
confirming the prior warning that the time_offset margin was narrow
and likely to flip.

The top of the canonical surface remains shallow. Several candidates
land between `68.5` and `69.4`. The broader conclusion is stronger than
the exact decimal ordering: gap_hour weighting with volume de-emphasis
dominates the top of the surface regardless of lookback and K details.

Count improved from 92.1 to 94.1. Timing improved from 49.0 to 51.8.

### Diagnostic findings

**Episode history is still locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.427h | 1.075h |
| `gap1_MAE` | 0.755h | 0.645h |
| `traj3_MAE` | 0.763h | 0.654h |

The diagnostic/canonical disagreement continues to extend to history mode.
Episode history produces cleaner retrieval locally, but canonical replay
favors raw history on the current export.

**Internal and canonical metrics diverge on nearly every axis:** The best
raw diagnostic configuration is (`raw`, `18h`, `means_only`, `k=7`,
`36h`, `median`, `time_offset`), while the shipped canonical
configuration is (`raw`, `24h`, `gap_hour`, `k=5`, `240h`, `median`,
`gap`). They agree on history mode (raw) and trajectory length (median),
but disagree on lookback, weighting, K, recency, and alignment.
Alignment is now a point of divergence again (diagnostic prefers
time_offset, canonical prefers gap).

**Feature distributions still show the episode advantage for retrieval:**
At the canonical-best 24-hour lookback, episode history produces larger
and tighter gap/volume signals than raw history:

- `last_gap`: `2.634 -> 3.005`
- `mean_gap`: `2.681 -> 3.044`
- `last_volume`: `3.297 -> 3.762`
- `mean_volume`: `3.314 -> 3.757`

These shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library. The cleaner state
space explains the diagnostic advantage, but canonical replay uses
bottle-only scoring events, so cluster-internal feeds are part of the
scoring target that the episode model cannot represent.

**Volume de-emphasis remains the dominant regime-level signal:** The top
4 canonical candidates all use gap_hour weighting (`[2, 2, 0.5, 0.5, 2,
2]`) or vol_deemphasis (`[1, 1, 0.5, 0.5, 1, 1]`). No top-10
candidate gives volume equal or dominant weight. This is the second
consecutive export where gap_hour weighting dominates, consistent with
the baby's feeding schedule consolidating and temporal regularity being
the strongest retrieval cue.

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
- `LOOKBACK_HOURS = 24`
- `FEATURE_WEIGHTS = gap_hour [2, 2, 0.5, 0.5, 2, 2]`
- `K_NEIGHBORS = 5`
- `RECENCY_HALF_LIFE_HOURS = 240`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The retune on the new export moved three constants: lookback (`9h` to
`24h`), K (`3` to `5`), and alignment (`time_offset` to `gap`).
Headline recovered from `66.6` to `69.4`, with count improving from
`92.1` to `94.1` and timing from `49.0` to `51.8`.

Four constants held across this export: gap_hour weighting, 240h
recency, raw history, and median trajectory length. The regime-level
signal continues to strengthen: gap_hour weighting dominates the top
of the canonical surface for the second consecutive export, and volume
de-emphasis is the strongest regime-level finding.

Gap alignment regains its historical lead after a single-export
time_offset preference. All top 10 canonical candidates use gap
alignment, resolving the prior export's ambiguity decisively.

The internal/canonical divergence widened on alignment (diagnostic
prefers time_offset, canonical prefers gap) and continues on lookback,
K, and recency. They agree on history mode (raw) and trajectory length
(median). The process is clean: a single full canonical sweep selects
all production constants.

## Open questions

### Model-local

- **RECENCY_HALF_LIFE_HOURS=240 is a boundary winner for the third
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
- **Gap/time_offset alignment continues to oscillate.** Gap alignment
  regained the lead after a single-export time_offset preference. This
  axis has now flipped twice in the tuning history. The margins remain
  narrow, suggesting the surface is genuinely flat on this dimension.
- **Top-up windows remain fragile.** Some of the weakest per-window
  scores still sit around short daytime follow-ups. The Apr 12 cluster
  feeding windows (16:42, 18:22) show the model struggling with
  short-gap events (0.39h actual vs 3.22h predicted).
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test near-
  miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `94.1`; timing is `51.8`.
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
