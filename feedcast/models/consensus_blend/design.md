# Consensus Blend Design Decisions

## Anchor-based candidate slots

The production algorithm no longer walks forecasts in lockstep.
Instead, every model prediction is treated as a possible anchor for
one real feed. Around that anchor, the selector pulls the nearest
prediction from each available model inside a shared radius and
forms one candidate slot.

This lets the blend recover majority agreement even when the models
disagree by more than a narrow clustering threshold. The old flat
clustering approach split some real feeds into multiple local
clusters; the anchor view keeps one candidate tied to one proposed
feed explanation.

## Simple-majority support floor

The user requirement here is explicit: consensus means simple
majority of the available models. With four available models, a
2-of-4 split is not consensus. With three available models, 2-of-3
is enough. This is enforced when candidate slots are generated, not
as an afterthought during aggregation.

This rule is stricter than the old blend and stricter than the first
pool-then-cluster prototype. It removes minority-supported echo
feeds by construction.

## Non-overlapping sequence selection

Candidate slots are not emitted directly. Several nearby candidates
can describe the same real feed, especially when every model point
is allowed to anchor its own slot. Weighted interval scheduling
forces those candidates to compete. The selected schedule is the
highest-utility non-overlapping sequence instead of the union of all
local agreements.

Support drives utility. Spread is a secondary penalty, so tighter
majority candidates win when support is equal. The current selector
does not impose a hard count budget because retrospective research
showed that soft or hard count caps reduced the headline score more
than they helped.

## Wide anchor radius and spread cap

Recent research showed model disagreement for the same real feed is
often wider than one hour. The production selector therefore uses a
two-hour anchor radius to recover majority support across that
spread. A separate spread cap rejects candidates that become too
diffuse to defend as one feed.

This is a deliberate tradeoff. A narrower radius or spread cap
reduced over-prediction, but it also pushed the blend back below the
lockstep baseline on retrospective headline score.

## 75-minute conflict window

The selector treats candidates closer than 75 minutes as competing
explanations for the same feed. That number is lower than
`MIN_INTERVAL_HOURS` because the recent data includes real short-gap
feeds around 72-75 minutes. Using a stricter 90-minute conflict
window collapsed too many valid near-term feeds and lost score.

## Legacy lockstep baseline

The old lockstep median-timestamp walk stays in `model.py` as a
research baseline. It is no longer production, but it remains useful
for regression comparisons and future tuning.

## Known limitations

The production selector still leans toward timing accuracy over
strict count control. In the recent retrospective sweep, the
highest-scoring majority selector still predicted more feeds than the
old lockstep blend on several cutoffs. That tradeoff was accepted
because the user prioritized maximum accuracy and timing-first
behavior over preserving the old count profile.
