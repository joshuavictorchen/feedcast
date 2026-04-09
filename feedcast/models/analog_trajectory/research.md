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
| Run date | 2026-04-01 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 69.9 |
| Availability | 24/24 windows (100%) |
| Full output | [`artifacts/research_results.txt`](artifacts/research_results.txt) |

> **Research integrity note:** the final recorded sweep is post-fix for a
> bug in `_state_features()` where `LOOKBACK_HOURS` had been captured as
> a default argument. Replay overrides now change lookback correctly.

> **Staleness check:** if the current export differs from the one listed
> here, re-run the command above to refresh results.

## Methods

### Canonical evaluation and tuning

**Canonical evaluation** calls `score_model("analog_trajectory")`
through the shared replay infrastructure. This produces the same
multi-window aggregate used elsewhere in the project: 96-hour replay
lookback, 36-hour window half-life, and episode-boundary cutoffs.

**Canonical tuning** runs a full 2688-candidate sweep via `tune_model()`
with candidate-parallel replay. The sweep includes every
production-relevant constant:

- `HISTORY_MODE`: `raw`, `episode`
- `LOOKBACK_HOURS`: `12`, `24`, `48`, `72`
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

The model still runs two local `full_traj_MAE` sweeps:

- one 1344-config sweep on raw bottle history
- one 1344-config sweep on episode-collapsed history

These are fold-causal retrieval/blending diagnostics. They explain what
kind of state representation and neighbor behavior is locally clean, but
they do not choose shipped constants.

### Raw vs. episode comparison

The research script also compares feature distributions across raw and
episode history at the canonical-best lookback. This is the simplest way
to see how episode collapse changes the state space before any tuning
metric is applied.

## Results

### Canonical findings

The old production configuration
(`raw`, `72h`, `hour_emphasis`, `k=7`, `36h`, `median`, `gap`) scored:

| Metric | Score |
|---|---|
| Headline | 63.54 |
| Count | 88.19 |
| Timing | 46.11 |

The corrected full canonical sweep updates production to
(`episode`, `12h`, `recent_only`, `k=5`, `72h`, `median`, `gap`), which
scores:

| Metric | Score |
|---|---|
| Headline | 69.90 |
| Count | 93.80 |
| Timing | 52.80 |

All 24 windows scored in both cases. The improvement is not marginal:
headline `+6.36`, count `+5.61`, timing `+6.69`, with no availability
loss.

The reopened raw-vs-episode decision now has a clean canonical answer.
The best raw-history candidate scores `69.2`; the best episode-history
candidate scores `69.9`. Episode history wins by about `0.7` headline
points while also leading on count and timing.

Gap alignment remains the best shipping choice. The best corrected raw
candidate and the best corrected episode candidate both use
`ALIGNMENT="gap"`. Time-offset alignment no longer has a case for
shipping on the current export.

The top of the canonical surface is fairly shallow once history is
episode-level. Several nearby episode candidates land between `69.6` and
`69.9`, which suggests the model is reasonably robust within the current
design family even though the shipped winner is distinct.

### Diagnostic findings

**Episode history is locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| full_traj_MAE | 1.696h | 1.126h |
| gap1_MAE | 0.785h | 0.659h |
| traj3_MAE | 0.802h | 0.621h |

This is not just a canonical replay preference. The underlying analog
retrieval problem is easier on episode history too.

**Internal and canonical metrics agree on the big design choices:** Both
metrics prefer episode history and gap alignment.

**Internal and canonical metrics disagree on some knob settings:** The
best episode diagnostic configuration is
(`episode`, `48h`, `means_only`, `k=5`, `36h`, `median`, `gap`), while
the best canonical configuration is
(`episode`, `12h`, `recent_only`, `k=5`, `72h`, `median`, `gap`).
That divergence matters. It means local trajectory reconstruction and
full 24-hour product quality are aligned on architecture, but not on all
constant values.

**Feature distributions explain why episode history helps:** At the
canonical 12-hour lookback, episode history produces larger and tighter
gap/volume signals than raw history:

- `last_gap`: `2.547 -> 3.017`
- `mean_gap`: `2.652 -> 3.080`
- `last_volume`: `3.001 -> 3.555`
- `mean_volume`: `3.047 -> 3.555`

Those shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library.

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
  regime (`raw`, `12h`, `recent_only`, `k=5`, `72h`, `median`, `gap`).
- **Forecast conformance:** the public forecaster reproduces the
  planted future for a new occurrence of the archetype under that same
  regime.
- **Canonical diagnostic:** replay on a targeted grid rewards the
  focused retrieval regime over deliberately blurrier alternatives
  (larger `k`, longer lookback, means-emphasis). This layer uses
  deterministic within-archetype jitter so feature weighting, neighbor
  count, and trajectory blending are exercised on non-trivial distances
  rather than exact duplicates.

These results confirm that **the implementation behaves correctly on
clean hypothesis-conforming data**. They do not validate the production
episode configuration: the fixture uses raw history and omits clustered
top-ups to isolate analog retrieval from episode collapse. All
synthetic gaps stay above the clustering-extension boundary, so
`HISTORY_MODE="episode"` is a no-op on this DGP — the canonical
diagnostic and the raw-history specification tests exercise the same
underlying event sequence.

The canonical diagnostic also reinforces a finding from the real-data
sweep: the analog canonical surface is shallow. Small fixture changes
move the exact top-ranked candidate while preserving the broader
ordering that focused retrieval beats blurrier retrieval. Regime-level
assertions are therefore more defensible than exact-best-candidate
assertions.

## Conclusions

**Disposition: Change.** Analog Trajectory should ship the full-canonical
winner:

- `HISTORY_MODE = "episode"`
- `LOOKBACK_HOURS = 12`
- `FEATURE_WEIGHTS = recent_only [2, 0.5, 2, 0.5, 1, 1]`
- `K_NEIGHBORS = 5`
- `RECENCY_HALF_LIFE_HOURS = 72`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The important model-level conclusion is that the earlier raw-history
rejection is no longer defensible once the question is asked under the
correct objective and the lookback override bug is fixed. Episode
history now wins both the local retrieval diagnostics and the canonical
ship metric.

The important process-level conclusion is that analog no longer needs a
proxy-gated two-stage tuning path. The project can afford the full
canonical sweep, and the internal `full_traj_MAE` sweep should stay in
the script only as diagnostic evidence.

The synthetic validation adds a narrower conclusion: when the analog
story is made true in a controlled DGP, the current implementation does
retrieve the right neighbors and produce the right future. That reduces
the chance that the remaining disagreement between internal diagnostics,
canonical replay, and real-world behavior is caused by a basic
implementation bug in retrieval or blending.

## Open questions

### Model-local

- **Top-up windows are still fragile.** Some of the weakest per-window
  scores still sit around short daytime follow-ups. The local neighbor
  diagnostics show the same pattern.
- **The top episode surface is shallow.** Several nearby candidates are
  within a few tenths of headline score. If future exports shift, the
  exact weight/half-life combination may move while the higher-level
  design stays the same.
- **How robust is the model once archetypes overlap or drift?** The
  simulation suite validates clean recurrence, not ambiguous or
  contaminated states. The next synthetic extensions should test near-
  miss archetypes, gradual archetype evolution, and top-up
  contamination.

### Cross-cutting

- **Timing as shared bottleneck:** Count is `93.8`; timing is `52.8`.
  Timing drift remains the main quality constraint. This pattern persists
  across all five models — see `feedcast/research/README.md`.
