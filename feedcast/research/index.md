# Feedcast Research

This is the shared research hub for the Feedcast project. It tracks
cross-cutting findings, hypotheses, open questions, and the conventions
for conducting research. Start here before working on any model or
research article.

Research is advisory, not binding. Models and agents may use these
findings when helpful, or ignore them when a different approach is
better supported.

## Research Articles

| Concept | Conclusion | Last updated | Where |
| ------- | ---------- | ------------ | ----- |
| Feed volume vs. subsequent gap | Supported: larger feeds → longer gaps, but the effect is modest | 2026-03-24 | [`volume_gap_relationship/`](volume_gap_relationship/) |
| Feed clustering (episodes) | 73-min base / 80-min small-feed extension, zero errors on 96 boundaries | 2026-03-26 | [`feed_clustering/`](feed_clustering/) |

## Conducting Research

Cross-cutting research articles (in `feedcast/research/`) and model-
specific research (in each model directory under `feedcast/models/`)
share the same file names, document structure, and workflow. The content
within each section varies — model research has canonical evaluation
sections, cross-cutting articles have bespoke methods — but the section
headers and overall flow are identical.

### Directory convention

| File | Purpose |
| ---- | ------- |
| `research.md` | Current conclusions. Written from first principles — may reference `CHANGELOG.md` entries for evolution context where that adds value. |
| `analysis.py` | Repeatable analysis. Cross-cutting: `.venv/bin/python -m feedcast.research.<name>.analysis`. Model: `.venv/bin/python -m feedcast.models.<slug>.analysis`. |
| `artifacts/` | Committed outputs (tables, charts, CSVs, `research_results.txt`) referenced by `research.md`. |
| `CHANGELOG.md` | Reverse-chronological evolution log. Cross-cutting articles log hypothesis, method, and conclusion changes. Model CHANGELOGs log behavior changes (constants, logic) — see the README for model CHANGELOG conventions. |

### `research.md` template

| Section | Content |
| ------- | ------- |
| `# Title` | Article title |
| `## Last run` | Staleness box: date, export path, dataset fingerprint, re-run command. Include additional fields as relevant (e.g., canonical headline, availability for model research). |
| `## Overview` | What this research investigates and why. Can be a hypothesis statement, numbered research questions, or a framing paragraph. |
| `## Methods` | How the investigation was conducted. Organize into subsections as the content requires (e.g., canonical + diagnostic for model research, bespoke sections for cross-cutting). |
| `## Results` | What the analysis found. Structure mirrors Methods. |
| `## Conclusions` | What the results mean. End with a clear outcome: a disposition (Keep / Change / Unresolved) for model research, or a verdict (Supported / Not supported / Inconclusive) for cross-cutting research. |
| `## Open questions` | What remains unknown. Sub-sections as appropriate (e.g., Model-local + Cross-cutting). |
| `## Artifacts` | Links to outputs in `artifacts/`. May be omitted when artifacts are referenced inline. |

See the existing articles for reference implementations:
- Cross-cutting: [`volume_gap_relationship/`](volume_gap_relationship/),
  [`feed_clustering/`](feed_clustering/)
- Model: any model's `research.md` under `feedcast/models/`

### Workflow

1. **Assess motivation.** What is spurring this research? Has the
   hypothesis, methodology, or data changed? If the analysis script or
   approach needs updating, do so before running.
2. **Run the analysis script.**
   - Cross-cutting: `.venv/bin/python -m feedcast.research.<name>.analysis`
   - Model: `.venv/bin/python -m feedcast.models.<slug>.analysis`
3. **Decide.**
   - Model: disposition — **Keep** (current constants are best),
     **Change** (update `model.py`), or **Unresolved** (ambiguous).
   - Cross-cutting: verdict — **Supported**, **Not supported**, or
     **Inconclusive**.
4. **Update `research.md`.** Write from first principles. May reference
   `CHANGELOG.md` entries for evolution context where it adds value.
5. **Update `CHANGELOG.md`.**
   - Cross-cutting articles: log changes to hypotheses, methods, or
     conclusions using **Prior conclusion**, **New conclusion**, and
     **What changed**. For initial analyses, record the conclusion and
     export.
   - Model research: log behavior changes (constants, logic) using the
     model CHANGELOG convention (one-line summary with Problem/Solution
     sections). See the README for the format.
6. **Update shared docs.**
   - Cross-cutting: update the Research Articles table above (conclusion
     summary and last-updated date).
   - Model: update this file only if the change affects a cross-model
     conclusion in the sections below.
   - Either: update any affected model or cross-cutting docs if the
     change alters shared hypotheses or assumptions.

**When to create a new cross-cutting article:** When an observation is
repeated across multiple models or agents and backed by evidence. The
Open Questions section below tracks candidates.

### Shared evaluation infrastructure

Model research uses shared evaluation infrastructure so canonical
results are directly comparable across models. Cross-cutting articles
use bespoke analysis methods — the infrastructure below is not required
for cross-cutting research but is available when useful.

**Key entry points:**

| Function | Location | Purpose |
| -------- | -------- | ------- |
| `score_forecast()` | `feedcast/evaluation/scoring.py` | Single-window scorer. Episode-matched, horizon-weighted, geometric mean of count F1 and timing credit. |
| `evaluate_multi_window()` | `feedcast/evaluation/windows.py` | Multi-window aggregation with recency weighting. |
| `score_model()` | `feedcast/replay/runner.py` | Evaluate a model at production constants across multiple windows. |
| `tune_model()` | `feedcast/replay/runner.py` | Sweep constant overrides and rank candidates by canonical score. |

**Canonical defaults:**

| Parameter | Default | Purpose |
| --------- | ------- | ------- |
| Lookback | 96 hours | How far back to generate replay windows |
| Half-life | 36 hours | Recency decay for window weighting |
| Cutoff mode | Episode boundary | Cutoffs placed at feeding episode boundaries |
| Scoring events | Bottle-only | Actual events scored against predictions |

**What "canonical" means:** Canonical evaluation uses bottle-only
scoring events, the shared replay infrastructure, and production
constants (or explicit overrides for tuning). Canonical results are the
authoritative basis for production constant decisions. Internal
diagnostics (gap MAE, MLE fits, trajectory error) inform understanding
of model mechanics but do not override canonical results.

See [`feedcast/replay/README.md`](../replay/README.md) for CLI usage
and tuning examples. See
[`feedcast/evaluation/methodology.md`](../evaluation/methodology.md)
for scoring design rationale.

## Working Framing

One useful framing is that the forecast problem may be less about predicting
one gap in isolation and more about identifying a mostly stable daily structure
and how it drifts. That is not a settled fact. It is a working theory that
helps explain why the model lineup includes daily-template, instance-based,
mechanistic, and hazard-style views instead of several versions of the same
gap regressor.

**Trend direction is critical.** The baby is growing fast — feeding patterns
shift week to week as gaps lengthen, volumes increase, and overnight behavior
consolidates. A model that tracks where the pattern is heading right now is
more useful than one that averages over all history. Recent trend direction is
likely the most actionable signal in the data after raw feeding cadence itself.
This is a strong hypothesis, not yet validated by shared research; it should be
an early candidate for a dedicated article. Acceleration (is the trend speeding
up or leveling off?) may also matter, but second-derivative estimates are noisy
with limited data and should be treated cautiously until more history
accumulates.

## Unobserved Variables

Important drivers of feed timing are not present in the export data:

- Sleep state and wake windows
- Growth spurts and developmental changes
- Fussiness and comfort feeding
- True breastfeeding volume, which is logged only through an estimate

These missing variables are a hard limit on what any model can explain. Shared
research should help separate real signal from intuition, but it cannot make
the data richer than it is.

## Current Hypotheses

- Daily episode count may stay fairly stable even as timing shifts.
  (Raw feed count and episode count are distinct — a single episode can
  contain multiple close-together feeds. See `feed_clustering/`.)
- The schedule may drift gradually over time rather than jump between unrelated
  states.
- Breastfeeding volume may be too noisy to help timing-first models unless
  logging habits change.
- Hard snack/full thresholds may be too brittle unless research shows they add
  value.

## Cross-Cutting Considerations

- **Episode definition is shared.** A deterministic rule in
  `feedcast/clustering.py`, derived from labeled data (see
  `feed_clustering/`). Evaluation and consensus blend collapse feeds
  into episodes using this rule. Models receive raw events and decide
  independently how to handle episodes in their own logic.

- **Episode-collapsed history outperforms raw feed history across all
  four base feed-history models:**
  [Slot Drift](../models/slot_drift/research.md),
  [Latent Hunger](../models/latent_hunger/research.md),
  [Survival Hazard](../models/survival_hazard/research.md), and
  [Analog Trajectory](../models/analog_trajectory/research.md).
  - Cluster-internal feeds (short top-ups within a feeding episode) add
    noise to gap distributions, state representations, and template
    alignment.
  - The improvement is consistent across template, mechanistic,
    instance-based, and hazard architectures.

- **Outlier handling is model-specific.** The same event can be noise
  for one model and signal for another.

- **Promote repeated observations into research articles** rather than
  leaving them as undocumented intuition.

## Open Questions

- How stable is daily episode count once more complete days accumulate?

- Does recent trend direction or acceleration improve forecasts more
  than raw recent cadence?

- Are time-of-day features capturing real structure or fitting noise
  given the small dataset?

- How much variance is explained by observed cadence and volume versus
  unobserved external factors?

- When does breastfeeding volume become strong enough to matter for
  shared research rather than model-local sensitivity checks?

- Should the day/night regime split be promoted from model research into
  a standalone cross-cutting article?

- Does the volume-gap relationship change when measured at the episode
  level (summed volume, inter-episode gap) rather than at the raw feed
  level?

- **Is timing accuracy fundamentally harder than count accuracy on this
  dataset?** The current canonical evaluations all show a wide
  count-vs-timing gap, spanning:
  - Template ([Slot Drift](../models/slot_drift/research.md)):
    count 90.8, timing 51.9
  - Mechanistic ([Latent Hunger](../models/latent_hunger/research.md)):
    count 94.0, timing 47.9
  - Hazard ([Survival Hazard](../models/survival_hazard/research.md)):
    count 94.3, timing 56.6
  - Instance-based
    ([Analog Trajectory](../models/analog_trajectory/research.md)):
    count 93.8, timing 52.8
  - Ensemble
    ([Consensus Blend](../models/consensus_blend/research.md)):
    count 95.4, timing 56.2
  - Candidate explanations: irreducible variability from unobserved
    variables (sleep, growth spurts), concentration of timing error in
    specific window types (cluster-feed periods, overnight transitions),
    or a structural property of the evaluation metric.
  - A dedicated article could quantify whether timing variance
    concentrates in specific windows or is uniformly distributed, and
    whether the gap narrows on later exports as the baby's schedule
    consolidates.

- **Do internal diagnostics and canonical replay disagree
  systematically on optimal constants?** At least three models show
  this:
  - [Latent Hunger](../models/latent_hunger/research.md): gap-MAE
    prefers sr≈0.6, canonical prefers 0.05
  - [Survival Hazard](../models/survival_hazard/research.md):
    episode-level MLE prefers shapes 7.2/3.4, canonical prefers
    4.75/1.75
  - [Analog Trajectory](../models/analog_trajectory/research.md):
    trajectory-MAE prefers different lookback and weighting than
    canonical
  - The project rule that canonical replay is authoritative for
    production constants is settled. The divergence itself is
    informative: it measures how much each production forecaster's
    mechanics (chained predictions, conditional logic, runtime
    estimation) distort the relationship between the data-generating
    distribution and shipped forecast quality.
  - The important follow-up question is whether the divergence indicates
    that some models succeed for reasons other than their stated design
    hypothesis. If canonical-best constants neutralize a model's
    distinguishing feature (e.g., Latent Hunger at sr=0.05 barely uses
    volume sensitivity), a simpler model without that feature may
    perform comparably — meaning the hypothesis is correct about the
    data but not earning its keep in the production forecaster.
  - This is a question about the models, not the scoring methodology:
    internal diagnostics measure one-step-ahead local accuracy or
    distributional fit, while canonical replay measures full-day forecast
    quality under the production pipeline. These are genuinely different
    objectives, and divergence between them is expected. Recalibrating
    the metric to match internal diagnostics would fit the metric to the
    models rather than the models to the objective.
  - Tracking the size and direction of this gap across exports would
    reveal whether it is structural (inherent to each model's
    architecture) or transient (a property of the current data window).
