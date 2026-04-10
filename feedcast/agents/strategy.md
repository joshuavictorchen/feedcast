# Agent Strategy — Empirical Cadence Projection

This document describes the agentic inference model's approach and guides
future agents on how to use, evaluate, and evolve it.

## Approach

The model forecasts feeding episodes by projecting forward from
recency-weighted empirical gap patterns, split by day-part (overnight
vs. daytime). It is deliberately non-parametric: it uses observed gap
medians rather than fitting a distribution (Weibull, exponential, etc.).

### Algorithm

1. Load the export CSV, build bottle-only feed events, collapse into
   episodes using the shared clustering rule (`feedcast/clustering.py`).
2. Filter to the lookback window (7 days, or back to DATA_FLOOR).
3. Compute inter-episode gaps tagged by the time-of-day of the feed
   that *starts* the gap:
   - **Overnight** (19:00–07:00): feeds after 7 PM or before 7 AM
   - **Daytime** (07:00–19:00): feeds during the day
4. Compute recency-weighted medians for each day-part (48h half-life).
   Fall back to the overall median if a bucket has fewer than 3 gaps.
5. For the **first predicted feed**: use a conditional survival estimate
   (filter to gaps longer than elapsed time, take weighted median of
   remaining times). This is better than naive subtraction when the
   baby has been awake longer than the median gap.
6. For **subsequent feeds**: step forward using the unconditional
   day-part median gap.
7. **Count calibration**: if the projected count differs from the
   recency-weighted mean of recent daily episode counts by more than
   30%, scale all gaps proportionally and re-project.
8. Write `forecast.json` to the workspace.

### How to Run

From the repo root:

```bash
.venv/bin/python feedcast/agents/model.py \
    --export exports/export_narababy_silas_YYYYMMDD.csv \
    --cutoff 2026-03-27T21:00:33 --horizon 24
```

The script writes `forecast.json` to `feedcast/agents/` and prints
diagnostics to stdout.

## Tuning Constants

| Constant | Value | Rationale |
| -------- | ----- | --------- |
| `LOOKBACK_DAYS` | 7 | Balances data availability against staleness. The recency weighting naturally de-emphasizes older data. |
| `RECENCY_HALF_LIFE_HOURS` | 48 | Aggressive 2-day half-life. Data from 5+ days ago gets <20% weight. Tested: 48h outperforms 72h and 36h on multi-cutoff retrospective. |
| `OVERNIGHT_START_HOUR` | 19 | Captures pre-bed feeds at 7–8 PM. The survival hazard model uses 20; testing showed 19 improves count accuracy on evening cutoffs. |
| `OVERNIGHT_END_HOUR` | 7 | Morning transition. |
| `MIN_GAP_HOURS` | 1.0 | Prevents degenerate cascading of very short predicted gaps. |
| `MIN_DAYPART_GAPS` | 3 | Minimum gaps needed in a day-part bucket before using its median; fewer falls back to overall. |
| `COUNT_CALIBRATION_THRESHOLD` | 0.30 | Only fires for large mismatches. Gentle threshold avoids over-correction. |

## Performance

Tested against 3 available exports (2026-03-23, 03-25, 03-27).

**Single retrospective** (cutoff 2026-03-25T00:34:39, scored against
2026-03-27 actuals):

| Model | Headline | Count | Timing |
| ----- | -------- | ----- | ------ |
| Slot Drift | 69.0 | 91.9 | 51.8 |
| **Agent** | **67.8** | **85.9** | **53.6** |
| Survival Hazard | 67.0 | 87.0 | 51.5 |
| Latent Hunger | 63.3 | 85.7 | 46.8 |
| Consensus Blend | 62.4 | 81.3 | 47.9 |
| Analog Trajectory | 60.0 | 81.3 | 44.3 |

**Multi-cutoff mean** (5 cutoffs across the data range):

| Model | Headline | Count | Timing |
| ----- | -------- | ----- | ------ |
| Survival Hazard | 71.1 | 92.5 | 55.1 |
| Latent Hunger | 65.1 | 90.6 | 47.7 |
| Slot Drift | 63.6 | 84.6 | 49.9 |
| **Agent** | **62.5** | **92.5** | **43.9** |
| Analog Trajectory | 61.9 | 86.3 | 45.0 |

### Strengths

- **Count accuracy**: Tied with survival hazard for best count score
  (92.5). The recency weighting and count calibration keep episode count
  close to recent daily patterns.
- **Adaptability**: Future agents can tune constants, change the
  algorithm, or replace it entirely.
- **Simplicity**: No fitted distributions, no optimization, no tuning
  pipeline. Pure empirical observation with recency weighting.

### Weaknesses

- **Timing on evening cutoffs**: When the cutoff falls in the evening
  (19:00–22:00), the model often under-predicts the "going to bed" gap.
  The overnight gap median (~3.3h) is dominated by frequent mid-night
  wake-feed-sleep cycles and under-represents the longer first sleep
  stretch. This is the single biggest performance drag.
- **Cascading first-feed error**: A bad first-feed prediction shifts
  all subsequent predictions. Early feeds get higher scoring weight, so
  this compounds.
- **Fixed gap stepping**: After the first feed, the model uses
  unconditional day-part medians. It doesn't adapt based on how the
  forecast is unfolding.

## Open Questions for Future Agents

These are the most promising directions for improvement, ordered by
expected impact:

1. **3-bucket day-part split**: Evening (19:00–22:00), deep night
   (22:00–07:00), daytime (07:00–19:00). The evening bucket would
   capture the longer "going to bed" gap. Risk: the evening bucket may
   have too few observations (<5 per 7-day window) for a reliable
   median. Test with MIN_DAYPART_GAPS=2 or fall back to overall.

2. **Borrow from survival hazard**: The Weibull conditional survival
   for the first feed is structurally better than median-based
   estimation. Consider: fit a simple parametric form (even just
   mean + std) to each day-part's gap distribution, and use the
   conditional median for the first feed.

3. **Trend detection**: Fit a linear trend to daily episode counts or
   median gaps. If the baby is trending toward fewer, longer-spaced
   feeds, extrapolate. Currently the recency weighting captures trend
   implicitly, but explicit trend detection could help more.

4. **Higher gap percentile**: Using the 55th–60th percentile instead of
   the median for overnight gaps would produce slightly longer gaps,
   reducing over-prediction. Untested.

5. **Adaptive gap stepping**: After placing each feed, adjust the
   next gap based on how the forecast is unfolding relative to the
   expected daily count. If the model has already placed "too many"
   feeds by mid-afternoon, stretch the remaining gaps.

## Guidance for Future Agents

- **Start by reading this document and the latest retrospective
  scores** in `tracker.json`. If the model is performing well, small
  constant adjustments may be all that's needed. If it's performing
  poorly, consider structural changes.
- **Run `model.py` first, then decide** whether to use its output
  as-is, adjust it, or replace it entirely.
- **Document what you change and why** in this file and in
  `CHANGELOG.md`. Future agents will read your notes.
- **The model is a tool, not a constraint.** If you find a better
  approach, replace `model.py` entirely. The workspace is yours.
- **Test against multiple cutoff points** when making changes. A single
  retrospective can be misleading — the 5-cutoff test shows much more
  variance than the single retrospective suggests.
