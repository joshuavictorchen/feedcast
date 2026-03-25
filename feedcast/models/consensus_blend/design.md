# Consensus Blend Design Decisions

## Anchor-based candidate slots

Every model prediction is treated as a possible anchor for one real
feed. Around that anchor, the selector pulls the nearest prediction
from each available model inside a shared radius and forms one
candidate slot.

This lets the blend recover majority agreement even when the models
disagree by more than a narrow clustering threshold. The anchor view
keeps one candidate tied to one proposed feed explanation.

## Simple-majority support floor

Consensus means simple majority of the available models. With four
available models, a 2-of-4 split is not consensus. With three
available models, 2-of-3 is enough. This is enforced when candidate
slots are generated.

## Backtracking sequence selection with single-use enforcement

Candidate slots are selected via backtracking search with
upper-bound pruning over forward-ordered subsequences. For the
typical problem size (~17 candidates) this runs in milliseconds.

The search is not globally optimal: it processes candidates in
time order and cannot discover sequences where an earlier
candidate becomes valid only after a later candidate claims shared
points. This edge case requires a specific point-sharing pattern
and did not affect retrospective scores on the current data.

Two constraints are enforced jointly during the search:

1. **Temporal non-overlap:** candidates closer than the conflict
   window are competing explanations for the same feed.
2. **Single-use model points:** each model prediction is claimed by
   at most one selected consensus feed. If a candidate's points
   are partly claimed, it is rebuilt from unclaimed evidence only
   (recomputed median timestamp, volume, support, spread). If the
   rebuilt support drops below majority, that branch is pruned.

Single-use enforcement is the key correctness property. Without it,
one model's prediction counts as evidence for multiple nearby
consensus feeds, inflating support counts and producing more feeds
than the models actually warrant.

## Wide anchor radius

The production radius is 120 minutes. Research shows inter-model
spread for the same real feed is P50=102 minutes, so a wide radius
is needed to recover majority agreement across that disagreement.

With the exhaustive selector, a wider radius generates more
candidates for the optimizer to choose from, which improves the
final sequence. This is the opposite of the greedy heuristic
(where wider radius created more point-sharing and worse greedy
decisions). The retrospective sweep confirmed radius=120 scores
above all tighter alternatives.

Outliers within the radius (e.g., one model 60 minutes from the
majority) are median-suppressed: the consensus timestamp reflects
the majority position. The outlier model's prediction is consumed
by single-use enforcement, so it cannot also anchor a separate
phantom feed.

## 90-minute conflict window

The selector treats candidates closer than 90 minutes as competing
explanations for the same feed. This aligns with
`MIN_INTERVAL_HOURS`, the physiological floor for distinct feeds.

## Utility function

Support drives utility (`support * 10`). Spread is a secondary
penalty (`0.25 per hour`), so tighter majority candidates win when
support is equal. The spread penalty is intentionally small — a
4-model candidate with wide spread is still preferred over a
3-model candidate with tight spread, because more models agreeing
is stronger evidence.
