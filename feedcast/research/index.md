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
