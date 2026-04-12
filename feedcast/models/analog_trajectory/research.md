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
| Export | `exports/export_narababy_silas_20260411(1).csv` |
| Dataset | `sha256:f71d7d136049e997e30fca06c93dd3f65cb1a46b7d37a2e41ed24b71fc9665d7` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 68.7 |
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
| `HISTORY_MODE` | `raw` |
| `LOOKBACK_HOURS` | `18` |
| `FEATURE_WEIGHTS` | `equal [1, 1, 1, 1, 1, 1]` |
| `K_NEIGHBORS` | `7` |
| `RECENCY_HALF_LIFE_HOURS` | `72` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `gap` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 68.7 |
| Count | 91.2 |
| Timing | 52.5 |

All 25 windows scored (100% availability).

This retune on the new export (`20260411(1)`) recovers +3.2 headline
points over the prior constants (tuned on `20260411`), which had
degraded from `69.7` to `65.5` on the new data. Five constants changed:
history mode (`episode` to `raw`), lookback (`12h` to `18h`), feature
weights (`means_only` to `equal`), K (`5` to `7`), and recency (`240h`
to `72h`). The most recent replay windows improved dramatically (Apr 10
18:33 went from headline 41.0 to 77.2, timing from 16.8 to 65.5).

The raw-vs-episode canonical margin flipped to raw by +0.5 headline
points (`68.7` vs `68.2`). This reverses the prior export where episode
led by +2.6. The best episode candidate uses a different regime from the
prior production config (`time_offset` alignment, `vol_deemphasis`
weighting, `k=3`, `hl=120h`), suggesting the episode surface shifted
substantially between exports.

Gap alignment remains the best shipping choice. The top 4 canonical
candidates all use the raw/18h/equal/k=7/hl=72h configuration with
gap or time_offset alignment; the gap variant leads.

The top of the canonical surface is still shallow. Several candidates
land between `68.0` and `68.7`. The broader conclusion is stronger than
the exact decimal ordering: raw history with equal weighting, moderate
lookback, and tighter recency beats other regimes on this export.

Count dropped from 94.9 to 91.2 in the retune. The raw model includes
cluster-internal feeds in its state library and trajectories, which
helps timing precision but slightly hurts count precision on windows
without cluster feeding.

### Diagnostic findings

**Episode history is still locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.433h | 1.070h |
| `gap1_MAE` | 0.760h | 0.634h |
| `traj3_MAE` | 0.767h | 0.643h |

The diagnostic/canonical disagreement now extends to history mode
itself. Episode history produces cleaner retrieval locally, but canonical
replay favors raw history on the current export. This is a deeper
disagreement than prior exports, where both metrics agreed on episode.

**Internal and canonical metrics diverge on most axes:** The best raw
diagnostic configuration is (`raw`, `24h`, `means_only`, `k=7`, `36h`,
`mean`, `gap`), while the shipped canonical configuration is (`raw`,
`18h`, `equal`, `k=7`, `72h`, `median`, `gap`). They agree on history
mode (raw), K (7), alignment (gap), but disagree on lookback, weighting,
recency, and trajectory length.

**Feature distributions still show the episode advantage for retrieval:**
At the canonical-best 18-hour lookback, episode history produces larger
and tighter gap/volume signals than raw history:

- `last_gap`: `2.641 -> 3.016`
- `mean_gap`: `2.718 -> 3.054`
- `last_volume`: `3.286 -> 3.753`
- `mean_volume`: `3.322 -> 3.737`

These shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library. The cleaner state
space explains the diagnostic advantage, but canonical replay uses
bottle-only scoring events, so cluster-internal feeds are part of the
scoring target that the episode model cannot represent.

**The current winner weights all features equally:** The retune moved
from `means_only` to `equal`, giving instantaneous values (last_gap,
last_volume) equal standing with rolling means. With raw history,
instantaneous values carry signal about recent cluster feeding patterns
(short gaps, small volumes) that means-only weighting suppresses.

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
- `LOOKBACK_HOURS = 18`
- `FEATURE_WEIGHTS = equal [1, 1, 1, 1, 1, 1]`
- `K_NEIGHBORS = 7`
- `RECENCY_HALF_LIFE_HOURS = 72`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The retune on the new export moved five constants: history mode
(`episode` to `raw`), lookback (`12h` to `18h`), feature weights
(`means_only` to `equal`), K (`5` to `7`), and recency (`240h` to
`72h`). Headline recovered from `65.5` to `68.7`, with timing improving
from `46.8` to `52.5`.

The architectural landscape shifted on this export. Raw history now
leads canonical replay by +0.5 headline points, reversing the +2.6
episode advantage on the prior export. Episode history still wins the
local retrieval diagnostics decisively (1.070h vs 1.433h), so the
diagnostic/canonical disagreement now extends to history mode. The most
likely explanation: recent cluster feeding produces short-gap scoring
events that episode collapse removes from the state library but that
canonical replay still scores against.

The internal/canonical divergence widened: they now agree only on K (7)
and alignment (gap), disagreeing on history mode, lookback, weighting,
recency, and trajectory length. The process is clean: a single full
canonical sweep selects all production constants.

## Open questions

### Model-local

- **Raw/episode history mode is unstable across exports.** The canonical
  winner flipped from episode (+2.6 on the prior export) to raw (+0.5
  on the current export). The margin is narrow in both directions and
  depends on how much recent cluster feeding is in the replay windows.
  This is the most consequential axis of constant churn since it
  changes the model's state representation.
- **Constant churn between exports.** The optimal constant combination
  has shifted on each of the last four exports (lookback oscillating
  12→9→24→12→18, weights moving through recent_only→hour_emphasis→
  gap_hour→means_only→equal, history mode flipping episode→raw). The
  shallow canonical surface means small data changes move the exact
  winner. This churn is expected given the shallow surface, but it also
  means point estimates of the best constants are fragile.
- **Top-up windows are still fragile.** Some of the weakest per-window
  scores still sit around short daytime follow-ups. The Apr 10 evening
  cluster feeds drove the largest timing errors under the prior
  production config.
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test near-
  miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `91.2`; timing is `52.5`.
  Timing drift remains the main quality constraint. This pattern
  persists across all five models — see `feedcast/research/README.md`.
- **Episode collapse vs. bottle-level scoring tension:** Episode history
  produces cleaner state representations but removes events that
  canonical replay scores against. This tension may affect other models
  that use episode history. The analog model's raw/episode flip
  highlights the tradeoff most sharply because it directly controls
  which events enter the state library and trajectory blending.
