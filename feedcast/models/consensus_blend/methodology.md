# Consensus Blend

Majority-vote ensemble across the scripted base models. The blend
builds immutable candidate feed slots around each predicted point,
including majority-sized subsets of the available models, then solves
an exact set-packing problem to choose one non-overlapping feed
sequence.

Each candidate uses the median timestamp and median volume of its
contributing model predictions. The exact selector enforces two hard
rules: one model prediction can only support one consensus feed, and
two candidate feeds inside the conflict window cannot both survive.

This keeps the blend from reusing the same evidence twice while still
letting tight majority agreement compete directly against wider
all-model agreement.
