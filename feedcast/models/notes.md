# Model Notes

Brainstorm notes for domain knowledge, model design, and open questions
about feeding pattern prediction. Nothing here is an implementation
requirement. These are observations, theories, and ideas that may
inform model design. Models are free to ignore anything in this file.

Update this file as observations change, models are implemented, and
new insights emerge.

## Domain Observations

- Larger feeds tend to be followed by longer gaps.
- The number of daily feeds is relatively stable, even as timing shifts.
- No clear day/night feeding pattern yet (as of ~24 days old).
- The schedule appears stable but shifting gradually over time.
- Breastfeeding volume is estimated (0.5 oz/30 min), not measured.
  It may be mostly noise. A model may choose to ignore it or
  down-weight it heavily.
- The 1.5 oz snack threshold is arbitrary. A model may choose to
  avoid pigeonholing feeds into snack/full categories unless that
  split clearly helps prediction.

## Working Theory

There are a mostly-set number of distinct feeds each day. The timing
of those feeds can shift forward or back, and there may be a trend to
those shifts that can be extrapolated. The core prediction problem may
be less about modeling individual gaps and more about identifying the
daily template and how it drifts.

## Unobserved Variables

Factors that likely influence feeding timing but are not in the data:

- Sleep state and wake windows
- Growth spurts and developmental leaps
- Fussiness and comfort feeding
- Breastfeeding volume (logged but estimated, not measured)

These are real drivers. For now, the only available signal is prior
feed times and volumes.

## Open Questions

- Is breastfeeding volume signal or noise?
- Are time-of-day features (sin/cos hour) capturing real structure,
  or fitting noise given limited usable data?
- How much variance is explained by prior feeding cadence versus
  external factors we cannot observe?

## Model Lineup

The suggested models below are brainstorm candidates, not implementation
requirements. Each is intended to be distinct in how it frames the
prediction problem, not just in the math used to produce another
next-gap estimate.

| Model | Frame | Key distinction | Status |
| ----- | ----- | --------------- | ------ |
| Slot Drift | Daily template | Predicts the whole day in slot space | Implemented |
| Analog Trajectory Retrieval | Instance-based ML | Reuses futures from similar historical states | Implemented |
| Latent Hunger State | Mechanistic hidden state | Models an internal driver rather than surface patterns | Implemented |
| Survival / Hazard Model | Probabilistic event process | Predicts feeding probability over time | Implemented |
| Consensus Blend | Ensemble | Combines forecasts from distinct model families | Implemented |

If multiple models agree despite these different frames, the agreement
is more meaningful than if we keep building variations of the same
gap regressor.

### Slot Drift

**Frame:** Daily template with slot-level drift.

Assume each day has a mostly stable set of feed slots. Align recent
days to a canonical template, then track how each slot moves over
time. The alignment step can use minimum-cost matching on time-of-day
distance so a day with seven feeds can still be compared to a day
with eight. Once slots are aligned, fit a simple trend per slot and
project the next day. Volume is the recent weighted average for that
slot.

This directly matches the working theory. A gap model has no notion of
"the late-evening feed"; this model does. Main risk: slot assignment
gets harder when the daily feed count changes.

Dependencies: numpy (already present).

### Analog Trajectory Retrieval

**Frame:** Instance-based machine learning from similar historical
states.

Treat each historical cutoff as a training example. Build a feature
vector that summarizes the current state (last few gaps, last few
volumes, feeds so far today, time since start of day). The label is
the actual next 24-hour trajectory from that cutoff. At forecast time,
find the most similar historical states and produce a forecast by
aligning and averaging their future trajectories.

Well-suited to small data. Does not fit a global function. Asks "when
we have looked like this before, what happened next?" Main risk: too
few similar historical states early on. Distance metric and feature
scale choices matter.

Dependencies: numpy (already present).

### Survival / Hazard Model

**Frame:** Feeding as an event process whose probability rises with
time since the last feed.

Model the probability of the next feed as a hazard function over
elapsed time. The simplest version uses a Weibull family; a more
flexible version uses a discrete-time hazard. Recent feed volume and
cadence shift the hazard curve so larger feeds make near-term feeding
less likely. Can produce uncertainty windows in addition to point
estimates.

Captures a real structural fact: right after a feed, another feed is
less likely, and the chance rises as time passes. Main risk: a single
hazard family may average over regimes that should stay separate.

Dependencies: scipy for continuous fitting; numpy only for
discrete-time.

### Latent Hunger State

**Frame:** Mechanistic hidden-state model.

Treat hunger as a hidden state that rises over time and is pushed
down by feeding. A larger feed lowers the hidden hunger state more,
so it takes longer to reach the next feed threshold. Fit a small set
of parameters: hunger growth rate, volume-to-satiety effect, recovery
after a feed, and possibly a slow drift term. Forecast by simulating
forward and emitting a feed each time the threshold is crossed.

Encodes the most believable causal relationship: bigger feeds usually
buy more time. Avoids arbitrary snack thresholds and does not need
hour-of-day features. Main risk: hidden state makes parameter
estimation underdetermined with limited data.

Dependencies: numpy (already present).

## Cross-Cutting Considerations

**Trend extrapolation (not just direction, but acceleration):**
Identifying whether a trend is accelerating or leveling off matters
more than just knowing the current direction. Slot Drift can extend
per-slot trends to track curvature. Latent Hunger State can absorb
this via a drifting hunger growth rate. With limited data,
2nd-derivative estimates are noisy, but they become viable as history
accumulates.

**Cluster feeding:**
Babies sometimes break their normal cadence with multiple smaller
feeds in quick succession. This is not something to codify as a
special case. The right test is whether each model handles it
naturally:
- Survival / Hazard and Latent Hunger State handle it well: a small
  feed barely resets the hazard or hunger state, so another feed
  follows quickly.
- Analog Trajectory Retrieval benefits if cluster episodes exist in
  the training set; they surface when the current state looks similar.
- Slot Drift is weakest here: cluster feeds don't map cleanly to
  daily slots and may cause noisy alignment.
- Agents are likely better positioned to reason about cluster feeding
  as a concept.

**Outlier awareness:**
Not all data points should carry equal weight. Some events are
genuinely unusual (a disrupted feed, a missed log entry, an
unusually early wake). What counts as an outlier depends on the
model's frame: a cluster feed is noise to Slot Drift but useful
signal to Analog Trajectory. A very long gap might be informative
to Latent Hunger State but distort Survival parameter fitting.
Outlier identification and handling should be model-specific, not
shared infrastructure.

## Deprioritized Ideas

- **Gaussian Process regression:** adds a heavy dependency and
  hyperparameter-search surface for little conceptual gain over a
  simpler small-data ML approach.
- **Trend-Aware Cadence:** too close to a cleaner version of the
  original Recent Cadence to earn a slot in a rebuilt lineup.
