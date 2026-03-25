# Consensus Blend Design Decisions

## Immutable majority-subset candidates

Every model prediction is treated as a possible anchor for one real
feed. Around each anchor, the blend enumerates every simple-majority
subset of the available models and asks each model in that subset for
its nearest prediction inside a shared radius.

This is the key structural decision. The old selector mutated
candidates during search by rebuilding them after shared points were
claimed. That made correctness depend on search order. The current
selector makes every candidate immutable up front, so selection only
has to decide which fixed candidates survive.

## Simple-majority support floor

Consensus means simple majority of the available models:

- 4 models available: support floor = 3
- 3 models available: support floor = 2
- 2 models available: support floor = 2

This rejects 2-of-4 split votes while still allowing consensus to work
when one model is unavailable.

## Exact set-packing selector

Selected consensus feeds are chosen with a mixed-integer linear
program. There is one binary decision per candidate slot.

The selector enforces two hard constraints:

1. Each underlying model prediction (`slug:index`) can be used at most once.
2. Two candidates closer than the conflict window cannot both survive.

That gives the blend a clean invariant: no model point is ever reused
to support multiple consensus feeds.

## Wide anchor radius, explicit spread cap

The production radius is 120 minutes. Recent research shows the median
inter-model spread for the same real feed is about 102 minutes, so a
narrow clustering threshold would drop too much legitimate agreement.

Because the radius is intentionally wide, the model also enforces a
hard candidate spread cap of 180 minutes. That keeps obviously diffuse
slots out of the optimizer.

## Utility favors support first, tightness second

Candidate utility is:

`support * 10 - spread_penalty_per_hour * spread_hours`

Support is still the main signal. Spread mostly breaks ties between
similarly-supported slots. This means the current production utility is
still somewhat suppressive rather than strictly outlier-rejecting: a
wide 4-model candidate can beat a tight 3-model candidate. That is a
deliberate simplification for now, not a claim that the utility is
finished.

## Direction over perfection

The exact selector is a cleaner long-term structure even though the
current utility is not fully tuned. It removes rebuild bugs, order
dependence, and post-selection repair logic. Future tuning should
happen by adjusting candidate utility and constraints, not by bringing
mutation back into the selector.
