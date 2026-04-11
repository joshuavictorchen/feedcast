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
| Run date | 2026-04-11 |
| Export | `exports/export_narababy_silas_20260411.csv` |
| Dataset | `sha256:138b5d3ad7d106444951acc6c56154bcd1ae94184f58a566f83c032ad41ef5ec` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 69.7 |
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
episode history at the canonical-best lookback (`24h`). This is the
simplest way to see how episode collapse changes the state space before
any tuning metric is applied.

## Results

### Canonical findings

The full canonical sweep selects:

| Parameter | Value |
|---|---|
| `HISTORY_MODE` | `episode` |
| `LOOKBACK_HOURS` | `12` |
| `FEATURE_WEIGHTS` | `means_only [0.5, 2, 0.5, 2, 1, 1]` |
| `K_NEIGHBORS` | `5` |
| `RECENCY_HALF_LIFE_HOURS` | `240` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `gap` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 69.7 |
| Count | 95.1 |
| Timing | 51.7 |

All 24 windows scored (100% availability).

This retune on the new export (`20260411`) recovers +2.1 headline points
over the prior constants (tuned on `20260410`), which had degraded from
`73.5` to `67.6` on the new data. Four constants changed: shorter
lookback (`24h` → `12h`), means-only weighting (`gap_hour` →
`means_only`), fewer neighbors (`7` → `5`), and broader recency (`120h`
→ `240h`). The most recent replay windows improved substantially (Apr 10
09:45 went from headline 46.3 to 67.9).

The raw-vs-episode margin is `+2.6` headline points (`69.7` vs `67.1`).
Episode history wins on both count (`95.1` vs `92.4`) and timing (`51.7`
vs `49.3`).

Gap alignment remains the best shipping choice. The top 4 canonical
candidates all use the means_only/12h/k=5/240h configuration with
either gap or time_offset alignment; the gap variant leads.

The top of the canonical surface is still shallow. Several nearby
episode candidates land between `68.6` and `69.7`. The broader
conclusion is stronger than the exact decimal ordering: episode history
with means-only emphasis at a 12-hour lookback and broad recency beats
other regimes on this export.

`RECENCY_HALF_LIFE_HOURS=240` is a boundary winner in the current grid
[36, 72, 120, 240]. Future sweeps should check whether higher values
improve further, though 240h (10 days) is already broad for a baby whose
patterns shift week to week.

### Diagnostic findings

**Episode history is locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.417h | 1.063h |
| `gap1_MAE` | 0.758h | 0.652h |
| `traj3_MAE` | 0.758h | 0.649h |

This is not just a canonical replay preference. The underlying analog
retrieval problem is easier on episode history too.

**Internal and canonical metrics now agree on weighting and K but still
diverge on lookback and recency:** Both metrics prefer episode history,
gap alignment, and means_only weighting with k=5. They disagree on
lookback and recency. The best episode diagnostic configuration is
(`episode`, `48h`, `means_only`, `k=5`, `36h`, `mean`, `gap`), while
the shipped canonical configuration is
(`episode`, `12h`, `means_only`, `k=5`, `240h`, `median`, `gap`).
This is narrower disagreement than the prior export, where canonical and
diagnostic also differed on weighting and K.

**Feature distributions explain why episode history helps:** At the
canonical-best 12-hour lookback, episode history produces larger and
tighter gap/volume signals than raw history:

- `last_gap`: `2.653 -> 3.010`
- `mean_gap`: `2.739 -> 3.063`
- `last_volume`: `3.291 -> 3.734`
- `mean_volume`: `3.338 -> 3.734`

Those shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library.

**The current winner emphasizes rolling means over instantaneous
values:** The retune moved from `gap_hour` to `means_only`, which
upweights mean_gap and mean_volume while deemphasizing last_gap,
last_volume, and hour features. This suggests the baby's mean feeding
rhythm is a more stable discriminator than any single recent gap or
time-of-day feature on the current export.

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

- `HISTORY_MODE = "episode"`
- `LOOKBACK_HOURS = 12`
- `FEATURE_WEIGHTS = means_only [0.5, 2, 0.5, 2, 1, 1]`
- `K_NEIGHBORS = 5`
- `RECENCY_HALF_LIFE_HOURS = 240`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The retune on the new export moved four constants: lookback (`24h` →
`12h`), feature weights (`gap_hour` → `means_only`), K (`7` → `5`),
and recency (`120h` → `240h`). Headline recovered from `67.6` to
`69.7`, with timing improving from `48.9` to `51.7`.

The architectural conclusions remain stable. Episode history wins both
the local retrieval diagnostics and the canonical ship metric, with a
`+2.6` headline margin on this export. The internal/canonical
divergence narrowed: both now agree on means_only weighting and k=5,
differing only on lookback and recency. The process is clean: a single
full canonical sweep selects all production constants.

## Open questions

### Model-local

- **Recency half-life is a boundary winner.** `RECENCY_HALF_LIFE_HOURS
  =240` is the highest value in the current grid [36, 72, 120, 240].
  Future sweeps should extend the grid (e.g., add 360, 480) to
  determine whether the optimum is interior or continues beyond 240h.
- **Constant churn between exports.** The optimal constant combination
  has shifted on each of the last three exports (lookback oscillating
  12→9→24→12, weights moving through recent_only→hour_emphasis→
  gap_hour→means_only). The shallow canonical surface means small data
  changes move the exact winner. This churn is expected given the
  shallow surface, but it also means point estimates of the best
  constants are fragile.
- **Top-up windows are still fragile.** Some of the weakest per-window
  scores still sit around short daytime follow-ups. The local neighbor
  diagnostics show the same pattern.
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test near-
  miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `95.1`; timing is `51.7`.
  Timing drift remains the main quality constraint. This pattern
  persists across all five models — see `feedcast/research/README.md`.
