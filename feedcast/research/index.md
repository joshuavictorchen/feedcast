# Feedcast Research

This directory holds cross-cutting research that may inform any model or
agent. Models and agents may use these findings when they help, or ignore
them when a different approach is better supported.

Each research article lives in its own folder and should include:

- `findings.md` for the concise write-up
- `analysis.py` for the rerunnable analysis
- `artifacts/` for committed outputs used to support the write-up

Re-run research when new exports arrive or when behavior appears to have
changed.

## Research Articles

| Concept | Summary conclusion | Where |
| ------- | ------------------ | ----- |
| Feed volume vs. subsequent gap | Supported on the current dataset: larger feeds are usually followed by longer gaps, but the effect is modest and should be treated as one signal among several. | [`volume_gap_relationship/findings.md`](volume_gap_relationship/findings.md) |
| Feed clustering (episodes) | Consecutive bottle feeds within 73 minutes (or 80 minutes if the later feed is ≤ 1.50 oz) belong to the same feeding episode. Derived from hand-labeled boundary data with zero errors on 96 boundaries. The shared rule lives in `feedcast/clustering.py`. | [`feed_clustering/findings.md`](feed_clustering/findings.md) |

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

- The episode (cluster) definition is shared: a deterministic rule in
  `feedcast/clustering.py`, derived from labeled data (see
  `feed_clustering/`). Evaluation and consensus blend collapse feeds into
  episodes using this rule. Models receive raw events and decide
  independently how to handle episodes in their own logic.
- Model-local evidence across all four base feed-history models supports
  episode-collapsed history over raw feed history:
  [Slot Drift](../models/slot_drift/research.md),
  [Latent Hunger](../models/latent_hunger/research.md),
  [Survival Hazard](../models/survival_hazard/research.md), and
  [Analog Trajectory](../models/analog_trajectory/research.md).
  Cluster-internal feeds (short top-ups within a feeding episode) add
  noise to gap distributions, state representations, and template
  alignment across template, mechanistic, instance-based, and hazard
  architectures.
- Outlier handling is model-specific: the same event can be noise for one model
  and signal for another.
- Promote repeated, evidence-backed observations into research articles instead
  of leaving them as undocumented intuition.

## Open Questions

- How stable is daily episode count once more complete days accumulate?
- Does recent trend direction or acceleration improve forecasts more than raw
  recent cadence?
- Are time-of-day features capturing real structure or fitting noise given the
  small dataset?
- How much variance is explained by observed cadence and volume versus
  unobserved external factors?
- When does breastfeeding volume become strong enough to matter for shared
  research rather than model-local sensitivity checks?
- Should the day/night regime split be promoted from model research into a
  standalone cross-cutting article?
- Does the volume-gap relationship change when measured at the episode level
  (summed volume, inter-episode gap) rather than at the raw feed level?
- Is timing accuracy fundamentally harder than count accuracy on this
  dataset? The current canonical evaluations all show a wide count-vs-
  timing gap, spanning template
  ([Slot Drift](../models/slot_drift/research.md)),
  mechanistic ([Latent Hunger](../models/latent_hunger/research.md)),
  hazard ([Survival Hazard](../models/survival_hazard/research.md)),
  instance-based ([Analog Trajectory](../models/analog_trajectory/research.md)),
  and ensemble ([Consensus Blend](../models/consensus_blend/research.md))
  architectures. Candidate explanations include irreducible variability
  from unobserved variables (sleep, growth spurts), concentration of
  timing error in specific window types (cluster-feed periods, overnight
  transitions), or a structural property of the evaluation metric. A
  dedicated article could quantify whether timing variance concentrates in
  specific windows or is uniformly distributed, and whether the gap
  narrows on later exports as the baby's schedule consolidates.
- Do internal diagnostics and canonical replay disagree systematically on
  optimal constants? At least three models show this:
  [Latent Hunger](../models/latent_hunger/research.md)
  (gap-MAE prefers sr≈0.6, canonical prefers 0.05),
  [Survival Hazard](../models/survival_hazard/research.md)
  (episode-level MLE prefers shapes 7.2/3.4, canonical prefers
  4.75/1.75), and
  [Analog Trajectory](../models/analog_trajectory/research.md)
  (trajectory-MAE prefers different lookback and weighting than
  canonical). The project rule that canonical replay is authoritative for
  production constants is settled. The divergence itself is informative:
  it measures how much each production forecaster's mechanics (chained
  predictions, conditional logic, runtime estimation) distort the
  relationship between the data-generating distribution and shipped
  forecast quality. The important follow-up question is whether the
  divergence indicates that some models succeed for reasons other than
  their stated design hypothesis. If canonical-best constants neutralize
  a model's distinguishing feature (e.g., Latent Hunger at sr=0.05
  barely uses volume sensitivity), a simpler model without that feature
  may perform comparably — meaning the hypothesis is correct about the
  data but not earning its keep in the production forecaster. Note that
  this is a question about the models, not the scoring methodology:
  internal diagnostics measure one-step-ahead local accuracy or
  distributional fit, while canonical replay measures full-day forecast
  quality under the production pipeline. These are genuinely different
  objectives, and divergence between them is expected. Recalibrating the
  metric to match internal diagnostics would fit the metric to the models
  rather than the models to the objective. Tracking the size and direction
  of this gap across exports would reveal whether it is structural
  (inherent to each model's architecture) or transient (a property of the
  current data window).
