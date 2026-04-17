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
| Run date | 2026-04-16 |
| Export | `exports/export_narababy_silas_20260416.csv` |
| Dataset | `sha256:383bff93af3fbf40ff86f1eccecd6d2fefd9a4b7d5093eb1b37174f552ac6e74` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 70.5 |
| Availability | 26/26 windows (100%) |
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
On the current export, every analog candidate scored all 26 windows, so
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
| `FEATURE_WEIGHTS` | `gap_emphasis [2, 2, 1, 1, 1, 1]` |
| `K_NEIGHBORS` | `7` |
| `RECENCY_HALF_LIFE_HOURS` | `36` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `gap` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 70.5 |
| Count | 92.9 |
| Timing | 54.1 |

All 26 windows scored (100% availability).

This retune on the new export (`20260416`) recovers +3.9 headline
points over the prior constants (tuned on `20260413`), which had
degraded from `69.4` to `66.6` on the new data. Four constants changed:
lookback (`24h` to `9h`), weights (`gap_hour` to `gap_emphasis`), K (`5`
to `7`), and recency (`240h` to `36h`). Per-window degradation on the
prior config was diffuse rather than concentrated in any one cluster,
consistent with a regime-level shift rather than a local failure.

The raw-vs-episode canonical margin widened to +1.8 headline points
(`70.5` vs `68.7`). The two best candidates disagree on almost every
axis: raw prefers `lb=9h`, `gap_emphasis`, `k=7`, `hl=36h`, `gap`
alignment, while episode prefers `lb=18h`, `means_only`, `k=3`,
`hl=120h`, `time_offset` alignment. They agree only on trajectory
length method.

Gap alignment retains its lead over time_offset by a thin margin
(`70.5` vs `70.3`). Rows 3, 4, 6, 7 of the top 10 use time_offset, so
this axis is effectively tied on the current export.

The top of the canonical surface is coherent: all top 10 candidates
use raw history and `k=7`, and 8 of 10 use `lb=9h` with gap_emphasis
weighting. Recency clusters at 36h or 72h across the top; none of the
top 10 use the previously-shipped 240h value. The regime-level signal
is a shift from broad averaging toward tight, recent-pattern retrieval.

`RECENCY_HALF_LIFE_HOURS=36` is a boundary winner of the production
grid `[36, 72, 120, 240]`, but a targeted same-axis check at
`[12, 18, 24, 36]` confirms 36h is an interior optimum:

| hl | Headline |
|----|----------|
| 12 | 66.03 |
| 18 | 67.89 |
| 24 | 68.84 |
| 36 | 70.45 |
| 72 | 69.59 |

This flips the "240h boundary winner for three consecutive exports" open
question: the optimum has now moved to the opposite end of the grid and
is locally interior. The recency axis is driven by how stable the
baby's current regime is relative to older history, not by a grid
artifact.

Count is roughly unchanged (92.9 vs 94.1). Timing improved from 51.8 to
54.1, recovering the timing gap that drove the headline degradation.

### Diagnostic findings

**Episode history is still locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.411h | 1.078h |
| `gap1_MAE` | 0.742h | 0.645h |
| `traj3_MAE` | 0.758h | 0.654h |

The diagnostic/canonical disagreement continues to extend to history mode.
Episode history produces cleaner retrieval locally, but canonical replay
favors raw history on the current export.

**Internal and canonical metrics diverge on nearly every axis:** The best
raw diagnostic configuration is (`raw`, `18h`, `means_only`, `k=7`,
`36h`, `median`, `time_offset`), while the shipped canonical
configuration is (`raw`, `9h`, `gap_emphasis`, `k=7`, `36h`, `median`,
`gap`). They agree on history mode (raw), K (7), recency (36h), and
trajectory length (median), but disagree on lookback, weighting, and
alignment. Recency is now a point of agreement — the diagnostic has
preferred `36h` for several exports while canonical was stuck at `240h`,
and the two converged this export as canonical moved down.

**Feature distributions still show the episode advantage for retrieval:**
At the canonical-best 9-hour lookback, episode history produces larger
and tighter gap/volume signals than raw history:

- `last_gap`: `2.640 -> 2.995`
- `mean_gap`: `2.740 -> 3.042`
- `last_volume`: `3.328 -> 3.775`
- `mean_volume`: `3.385 -> 3.771`

These shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library. The cleaner state
space explains the diagnostic advantage, but canonical replay uses
bottle-only scoring events, so cluster-internal feeds are part of the
scoring target that the episode model cannot represent.

**Gap-feature emphasis replaces volume de-emphasis at the top:** The
prior export's top of the surface was dominated by gap_hour and
vol_deemphasis (both de-emphasize volume). The current top 10 instead
clusters on gap_emphasis (`[2, 2, 1, 1, 1, 1]`), which keeps volume at
baseline and does not elevate hour features. This is a regime-level
shift: gap features continue to dominate retrieval, but the de-emphasis
of volume and the elevation of time-of-day features both disappear.
Volume has become informative enough to carry baseline weight, and
time-of-day is no longer a dominant separator as the daytime cadence
tightens to a narrow 2.5-3.1h band.

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
- `FEATURE_WEIGHTS = gap_emphasis [2, 2, 1, 1, 1, 1]`
- `K_NEIGHBORS = 7`
- `RECENCY_HALF_LIFE_HOURS = 36`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The retune on the new export moved four constants: lookback (`24h` to
`9h`), weights (`gap_hour` to `gap_emphasis`), K (`5` to `7`), and
recency (`240h` to `36h`). Headline recovered from `66.6` to `70.5`,
with count roughly unchanged (`94.1` to `92.9`) and timing improving
from `51.8` to `54.1`. The improvement concentrated on timing, which
matches the degradation pattern on the prior config.

Three constants held across this export: raw history, median trajectory
length, and gap alignment. Every other production constant moved. The
broader regime-level signal is a shift toward faster adaptation:
shorter lookback, more neighbors, and a sharply tighter recency
half-life all bias retrieval toward recent states and away from older
history.

The recency half-life flip (`240h` to `36h`) is the most consequential
change. The low-boundary concern from the prior export (third
consecutive 240h winner) is now resolved in the opposite direction —
the optimum moved to the grid's low end and a targeted check confirms
it is interior. The canonical and diagnostic surfaces agree on recency
for the first time in several exports. This supports the project-level
working assumption that tracking emerging behavior beats averaging over
longer history when the baby's pattern is shifting.

Gap alignment retains a thin lead over time_offset (`70.5` vs `70.3`).
The axis is effectively tied on the current export and is expected to
remain volatile.

The internal/canonical divergence narrowed on recency (now agree on
36h) but remains on lookback, weighting, and alignment. The process is
clean: a single full canonical sweep selects all production constants,
followed by a targeted same-axis boundary check on recency.

## Open questions

### Model-local

- **Recency half-life flipped from the high to the low boundary.** The
  240h winner held for three consecutive exports, then the current
  export selected 36h — the opposite end of the grid. A targeted check
  at [12, 18, 24, 36] confirms 36h is interior, so the grid is locally
  adequate. The larger question is whether the recency axis will
  continue to swing wildly between exports as the baby's regime shifts,
  or whether tight recency is the new stable choice. If a future export
  again selects 240h, reconsider whether a smoother recency-adaptation
  strategy is needed in place of export-by-export retuning.
- **Constant churn between exports continues.** The optimal constant
  combination has now shifted on every one of the last six exports.
  Four constants changed this export (lookback, weights, K, recency).
  The shallow canonical surface means small data changes move the
  exact winner. The regime-level signal this export (raw history, k=7,
  gap-feature emphasis) is coherent but different from the prior
  export's signal (gap_hour weighting dominant). Regime shifts across
  exports make it hard to call any individual constant "stable" beyond
  `HISTORY_MODE=raw`, `TRAJECTORY_LENGTH_METHOD=median`, and
  `ALIGNMENT=gap` (which is itself marginal).
- **Gap/time_offset alignment remains effectively tied.** Top candidates
  include both alignments within 0.2 headline points. This axis is flat
  enough that either choice is defensible; gap is shipped because it
  wins the headline today by the narrowest margin.
- **Top-up/cluster windows remain fragile.** The last-10-states
  neighbor-quality table under the new config shows the Apr 15 cluster
  (16:29, 18:24, 19:32) producing the largest trajectory errors
  (up to 3.3h on a single gap). Short-gap cluster feeds continue to
  challenge neighbor retrieval because the historical library has few
  matching states.
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test
  near-miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `92.9`; timing is `54.1`.
  Timing remains the binding constraint. The gap narrowed this export
  (timing +2.3, count -1.2 vs prior), but count-vs-timing asymmetry
  still dominates the headline. This pattern persists across all five
  models (see `feedcast/research/README.md`).
- **Episode collapse vs. bottle-level scoring tension:** Episode history
  produces cleaner state representations but removes events that
  canonical replay scores against. This tension may affect other models
  that use episode history. The analog model's raw history preference
  highlights the tradeoff most sharply because it directly controls
  which events enter the state library and trajectory blending.
- **Short recency and short lookback may favor emerging-behavior
  tracking across models.** The analog model's retune concentrated
  neighbor weight on the most recent ~1.5 days and collapsed rolling
  features to ~3 recent feeds, recovering +3.9 headline points. If
  other base models (slot_drift, latent_hunger, survival_hazard) also
  have parameters controlling recency, they may benefit from a similar
  tightening given the same underlying data shift. Worth checking
  whether their canonical sweeps show the same directional signal.
- **Volume de-emphasis no longer dominates the top of the surface.**
  The prior export's top candidates all de-emphasized volume; this
  export's top 10 instead use gap_emphasis (volume at baseline). If
  other models encoded volume de-emphasis as a long-term finding, that
  choice may be worth revisiting on the current export.
