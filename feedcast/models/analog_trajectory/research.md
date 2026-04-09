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
| Run date | 2026-04-09 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.models.analog_trajectory.analysis` |
| Canonical headline | 71.3 |
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

**Canonical tuning** last ran as a widened full 4704-candidate sweep via
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

This widened full rerun supersedes the earlier same-day targeted
lookback follow-up that had moved runtime to `18h`. The targeted probe
was a reasonable boundary check, but it only reopened one axis. The
full rerun is the clean shipping result because it lets lookback,
weighting, neighborhood size, and recency move together.

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

The widened full canonical sweep selects:

| Parameter | Value |
|---|---|
| `HISTORY_MODE` | `episode` |
| `LOOKBACK_HOURS` | `9` |
| `FEATURE_WEIGHTS` | `hour_emphasis [1, 1, 1, 1, 2, 2]` |
| `K_NEIGHBORS` | `7` |
| `RECENCY_HALF_LIFE_HOURS` | `120` |
| `TRAJECTORY_LENGTH_METHOD` | `median` |
| `ALIGNMENT` | `gap` |

The current production configuration scores:

| Metric | Score |
|---|---|
| Headline | 71.3 |
| Count | 93.0 |
| Timing | 55.4 |

All 24 windows scored (100% availability).

This widened rerun materially improves on the superseded same-export
episode regime that had shipped after the targeted lookback follow-up.
That targeted probe had found headline `70.19` at
(`episode`, `18h`, `recent_only`, `k=5`, `72h`, `median`, `gap`). Once
the full grid was reopened, the best regime moved again to the current
`71.28` winner. That is the more important result than the exact
decimal gain: the final winner is not just "shorter lookback"; it is a
more coherent package of shorter lookback, stronger hour-of-day
emphasis, larger neighborhood, and broader recency memory.

The reopened raw-vs-episode decision now has a wider margin than before.
The best raw-history candidate scores `69.2`, while the best
episode-history candidate scores `71.3`. Episode history wins by about
`2.1` headline points, with higher count (`93.0` vs `92.2`) and higher
timing (`55.4` vs `52.3`).

Gap alignment remains the best shipping choice. The best raw-history
candidate and the best episode-history candidate both use
`ALIGNMENT="gap"`. Time-offset alignment still trails nearby gap-based
configurations on the current export.

The top of the canonical surface is still shallow enough to warrant
modest language. Several nearby episode candidates land between `70.9`
and `71.3`. The winner is real, but the broader conclusion is stronger
than the exact decimal ordering: episode history plus focused local
matching beats raw history and blurrier retrieval.

### Diagnostic findings

**Episode history is locally cleaner:** The best diagnostic episode
configuration beats the best diagnostic raw configuration on every local
metric:

| Metric | Raw best | Episode best |
|---|---|---|
| `full_traj_MAE` | 1.696h | 1.126h |
| `gap1_MAE` | 0.785h | 0.659h |
| `traj3_MAE` | 0.802h | 0.621h |

This is not just a canonical replay preference. The underlying analog
retrieval problem is easier on episode history too.

**Internal and canonical metrics agree on the architecture, not the full
setting vector:** Both metrics prefer episode history and gap alignment.
They disagree on weighting, lookback, and neighborhood size. The best
episode diagnostic configuration is
(`episode`, `48h`, `means_only`, `k=5`, `36h`, `median`, `gap`), while
the current shipped canonical configuration is
(`episode`, `9h`, `hour_emphasis`, `k=7`, `120h`, `median`, `gap`).
That divergence matters. It means local trajectory reconstruction and
full 24-hour product quality align on architecture, but not on all
constant values.

**Feature distributions explain why episode history helps:** At the
canonical-best 9-hour lookback, episode history produces larger and
tighter gap/volume signals than raw history:

- `last_gap`: `2.547 -> 3.017`
- `mean_gap`: `2.644 -> 3.064`
- `last_volume`: `3.001 -> 3.555`
- `mean_volume`: `3.042 -> 3.547`

Those shifts are exactly what you would expect if cluster-internal
top-ups were being removed from the state library.

**The current winner is more hour-led than the prior regime:** The
widened full rerun moved away from `recent_only` and toward
`hour_emphasis`. That does not mean the model has become a clock-based
template. It means that on the current export, once state construction
is episode-level and the lookback is short, hour-of-day provides the
sharpest final separation among already-local analog candidates.

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

**Disposition: Change.** Analog Trajectory should now ship the current
evidence-backed winner:

- `HISTORY_MODE = "episode"`
- `LOOKBACK_HOURS = 9`
- `FEATURE_WEIGHTS = hour_emphasis [1, 1, 1, 1, 2, 2]`
- `K_NEIGHBORS = 7`
- `RECENCY_HALF_LIFE_HOURS = 120`
- `TRAJECTORY_LENGTH_METHOD = "median"`
- `ALIGNMENT = "gap"`

The important model-level conclusion is that the earlier same-day `18h`
follow-up was directionally useful but incomplete. Once the full grid
was reopened, the model improved again and settled on a different local
regime. That is a better outcome than "the targeted follow-up was
wrong": it shows the widened full sweep was actually worth running.

The stronger architectural conclusion holds. Episode history wins both
the local retrieval diagnostics and the canonical ship metric. Analog is
no longer a case where research says episode-level states are cleaner
but production must ship raw history.

The process conclusion is also cleaner now. Analog no longer needs a
proxy-gated two-stage tuning path. The project can afford the full
canonical sweep, and the internal `full_traj_MAE` sweep should stay in
the script only as diagnostic evidence.

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

- **Timing as shared bottleneck:** Count is `93.0`; timing is `55.4`.
  Timing drift remains the main quality constraint. This pattern
  persists across all five models — see `feedcast/research/README.md`.
