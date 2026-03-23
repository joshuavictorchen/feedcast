# Model Notes

Domain observations and working theories about feeding patterns. These
notes capture knowledge that should inform model design. Models are not
required to follow them — they are a reference point, not a specification.

## Observations

- Larger feeds tend to be followed by longer gaps.
- The number of daily feeds is relatively stable, even as timing shifts.
- No clear day/night feeding pattern yet (as of ~24 days old).
- The schedule appears stable but shifting gradually over time.
- The 1.5 oz snack threshold is arbitrary and may need adjustment.

## Working Theory

There are a mostly-set number of distinct feeds each day. The timing of
those feeds can shift forward or back, and there may be a trend to those
shifts that can be extrapolated. The core prediction problem may be less
about modeling individual gaps and more about identifying the daily
template and how it drifts.

## Unobserved Variables

Factors that likely influence feeding timing but aren't captured in the
data:

- Sleep state and wake windows
- Growth spurts and developmental leaps
- Fussiness and comfort feeding
- Breastfeeding volume (logged but estimated, not measured)

These are real drivers. For now, the only available signal is prior feed
times and volumes, so that is what we work with.

## Open Questions

- Is breastfeeding volume signal or noise? The 0.5 oz/30 min estimate
  is not measured intake. Two of three models use it; one does not.
- How should snacks be handled? The current 1.5 oz cutoff is arbitrary.
  Identifying "when snacks happen" may not be the right framing — a
  model might handle all feeds uniformly.
- Are time-of-day features (sin/cos hour) capturing real structure, or
  fitting noise given limited usable data?
- How much variance is explained by prior feeding cadence versus
  external factors we cannot observe?

## Current Model Critique

Assumptions encoded by prior agents that deserve scrutiny:

- **Breastfeed-aware framing:** Phase Nowcast and Gap-Conditional both
  foreground breastfeed awareness in their methodology descriptions.
  Breastfeeding volume is estimated, not measured, and may be mostly
  noise.
- **Snack thresholding:** Recent Cadence filters to "full feeds" using
  the 1.5 oz cutoff. This may discard useful signal or create
  artificial distinctions.
- **Hour-of-day features:** All models use time-of-day in some form,
  but there is no clear day/night feeding pattern yet. These features
  may be overfitting with limited data.
- **Gap-centric modeling:** All three models frame the problem as
  "predict the gap to the next feed." The working theory suggests a
  different framing — a shifting daily template — may be more natural.
- **Time-of-day volume profiles:** All models project volume from
  12-bin time-of-day profiles. With limited data, many bins are empty
  and fall back to global averages.
