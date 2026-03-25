# Consensus Blend Design Decisions

## Lockstep median-timestamp walk

The production algorithm walks models in lockstep, consuming one
point per model per step.  The median timestamp anchors each step,
making it robust to a single model drifting earlier or later.
Mean volume is used because the models produce similar ranges and
outlier volume is less harmful than outlier timing.

## +/- 90 minute match window

The window must be wide enough to capture inter-model disagreement
about the same feed (research shows P50 spread ~102 minutes) while
narrow enough to avoid merging distinct feeds (recent minimum
inter-feed gap ~72 minutes on the tightest days).  90 minutes is a
pragmatic compromise; the lockstep walk's sequential consumption
provides additional separation that flat clustering does not.

## Leading outlier discard

When one model's next point falls before the cluster window, it is
a "leading outlier" — likely an extra snack prediction or early
drift.  Discarding it and re-anchoring prevents the outlier from
pulling the median and avoids misaligning all downstream pairings.

## Minimum 2-model agreement

A single model predicting a feed is not consensus.  At least two
models must place a point in the cluster window for a consensus
point to be emitted.

## Known limitations

**Misalignment cascades.** When one model has an extra point, the
lockstep walk discards it, but this shifts that model's index
relative to the others.  Downstream clusters may pair the wrong
points.

**Phantom consensus.** If two models predict 14:00 and two predict
16:00, the median is 15:00 — a time no model actually believes in.
The 90-minute window usually prevents this, but borderline splits
can still produce compromise times.

**Equal weighting.** All models contribute equally regardless of
historical accuracy.  A model that consistently scores poorly has
the same influence as the best performer.

## Planned replacement: pool-then-cluster with sequence selection

A candidate replacement pools all model predictions into agreement
clusters (agglomerative complete-linkage), scores each cluster by
support and tightness, then selects the best non-conflicting
sequence using weighted interval scheduling.  This resolves the
cascade and phantom problems by decoupling clustering from
sequence formation.

Research (see research.py) confirmed the candidate generator
improves timing accuracy (+2.7 weighted timing score) but
over-predicts feed count (-8.4 weighted count score) because flat
clustering lacks sequence awareness.  The replacement will only
be promoted once it beats the lockstep blend on retrospective
headline score.  The sequence selector is the missing piece.
