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
| Run date | 2026-04-10 |
| Export | `exports/export_narababy_silas_20260410.csv` |
| Dataset | `sha256:8dc1ea2650b0779b6a342b90aa918bc5bd2d5412bfbef25a2df4a8e1bada504e` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 73.5 |
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
episode history at the canonical-best lookback (`24h`). This is the
simplest way to see how episode collapse changes the state space before
any tuning metric is applied.

## Results

### Canonical findings

The full canonical sweep selects:

| Parameter | Value |
|---|---|
| `HISTORY_MODE` | `episode` |
| `LOOKBACK_HOURS` | `24` |
| `FEATURE_WEIGHTS` | `gap_hour [2, 2, 0.5, 0.5, 2, 2]` |
| `K_NEIGHBORS` | `7` |
| `RECENCY_HALF_LIFE_HOURS` | `120` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `gap` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 73.5 |
| Count | 97.1 |
| Timing | 56.5 |

All 26 windows scored (100% availability).

This retune on the new export (`20260410`) materially improves over the
prior constants (tuned on `20260327`), which had degraded to headline
`65.4` on the new data. The two changes — longer lookback (`9h` → `24h`)
and gap+hour emphasis (`hour_emphasis` → `gap_hour`) — are consistent
with the baby's schedule consolidating: longer, more regular gaps make
gap cadence and time-of-day sharper retrieval cues, while volume has
grown noisier as a discriminator.

The raw-vs-episode margin widened to `+4.1` headline points (`73.5` vs
`69.4`), up from `+2.1` on the prior export. Episode history wins on
both count (`97.1` vs `92.0`) and timing (`56.5` vs `53.1`).

Gap alignment remains the best shipping choice. The top 6 canonical
candidates all use `gap` alignment; the best `time_offset` candidate
trails at `72.8`.

The top of the canonical surface is still shallow. Several nearby
episode candidates land between `72.3` and `73.5`. The broader
conclusion is stronger than the exact decimal ordering: episode history
with gap+hour emphasis at a 24-hour lookback beats other regimes
consistently.

### Diagnostic findings

**Episode history is locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.419h | 1.067h |
| `gap1_MAE` | 0.742h | 0.649h |
| `traj3_MAE` | 0.741h | 0.647h |

This is not just a canonical replay preference. The underlying analog
retrieval problem is easier on episode history too.

**Internal and canonical metrics agree on the architecture, not the full
setting vector:** Both metrics prefer episode history and gap alignment.
They disagree on weighting, lookback, and recency. The best episode
diagnostic configuration is
(`episode`, `48h`, `means_only`, `k=5`, `36h`, `mean`, `gap`), while
the shipped canonical configuration is
(`episode`, `24h`, `gap_hour`, `k=7`, `120h`, `median`, `gap`).
That divergence matters. It means local trajectory reconstruction and
full 24-hour product quality align on architecture, but not on all
constant values.

**Feature distributions explain why episode history helps:** At the
canonical-best 24-hour lookback, episode history produces larger and
tighter gap/volume signals than raw history:

- `last_gap`: `2.640 -> 3.010`
- `mean_gap`: `2.668 -> 3.037`
- `last_volume`: `3.279 -> 3.738`
- `mean_volume`: `3.298 -> 3.745`

Those shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library.

**The current winner emphasizes gap and hour while deemphasizing
volume:** The retune moved from `hour_emphasis` to `gap_hour`, which
upweights both gap features and hour-of-day while halving the influence
of volume. This is consistent with the baby's schedule consolidating:
gap cadence and time-of-day are sharper discriminators among analogs
than volume, which has grown more variable.

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

**Disposition: Keep.** Analog Trajectory ships the current
evidence-backed constants:

- `HISTORY_MODE = "episode"`
- `LOOKBACK_HOURS = 24`
- `FEATURE_WEIGHTS = gap_hour [2, 2, 0.5, 0.5, 2, 2]`
- `K_NEIGHBORS = 7`
- `RECENCY_HALF_LIFE_HOURS = 120`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The retune on the new export moved two constants — lookback (`9h` →
`24h`) and feature weights (`hour_emphasis` → `gap_hour`) — recovering
headline from `65.4` to `73.5`. Count improved from `93.4` to `97.1`
and timing from `46.8` to `56.5`.

The architectural conclusions are strengthening. Episode history wins
both the local retrieval diagnostics and the canonical ship metric,
with the margin widening from `+2.1` on the prior export to `+4.1` on
the current one. The process is clean: a single full canonical sweep
selects all production constants.

## Open questions

### Model-local

- **Top-up windows are still fragile.** Some of the weakest per-window
  scores still sit around short daytime follow-ups. The local neighbor
  diagnostics show the same pattern.
- **The top episode surface is still shallow.** Several nearby episode
  candidates are within a few tenths of headline score. If future
  exports shift, the exact weight/half-life combination may move while
  the higher-level design stays the same.
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test near-
  miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `97.1`; timing is `56.5`.
  Timing drift remains the main quality constraint. This pattern
  persists across all five models — see `feedcast/research/README.md`.
