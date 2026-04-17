# Agent Strategy: Four-Bucket Cadence Projection

Durable notes for the agentic inference model in `model.py`. Pairs
with `methodology.md` (report-facing per-run description) and
`CHANGELOG.md` (reverse-chronological behavior log).

## Approach

The model forecasts feeding episodes by projecting forward from
recency-weighted empirical gap patterns, split across four
clock-hour sub-periods of the feed that starts each gap: evening
(19:00-22:00), deep night (22:00-03:00), early morning
(03:00-07:00), and daytime (07:00-19:00).

The approach is non-parametric: it uses observed gap medians
rather than fitting a distribution (Weibull, exponential, etc.).
This avoids shape assumptions that may not hold for a fast-changing
newborn.

### Algorithm

1. Load the export CSV, build bottle-only feed events, collapse
   into episodes using the shared clustering rule
   (`feedcast/clustering.py`).
2. Filter to the lookback window (7 days, or back to `DATA_FLOOR`).
3. For each inter-episode gap, classify by the clock hour of the
   feed that starts the gap into one of four sub-periods.
4. Compute the recency-weighted median gap per sub-period (48-hour
   half-life). Sub-periods with fewer than 3 gaps fall back to the
   overall recency-weighted median.
5. Compute the recency-weighted median episode volume.
6. Step forward from the cutoff, applying the sub-period gap that
   matches each predicted feed's start clock hour, until the
   horizon ends.
7. Write `forecast.json` to the workspace.

### How to Run

From the repo root:

```bash
.venv/bin/python feedcast/agents/model.py \
    --export exports/export_narababy_silas_YYYYMMDD.csv \
    --cutoff YYYY-MM-DDTHH:MM:SS --horizon 24
```

The script writes `forecast.json` to `feedcast/agents/` and prints
bucket medians and the projected feeds to stdout.

## Tuning Constants

| Constant | Value | Rationale |
| -------- | ----- | --------- |
| `LOOKBACK_DAYS` | 7 | Balances data availability against staleness. Recency weighting naturally de-emphasizes older data. |
| `HALF_LIFE_HOURS` | 48 | Aggressive 2-day half-life. Data from 5+ days ago gets <20% weight. |
| `MIN_BUCKET_GAPS` | 3 | Minimum gaps needed in a sub-period before using its median; fewer falls back to the overall median. |
| `MIN_GAP_HOURS` | 1.0 | Floor on projected gaps to prevent degenerate cascading. |
| `MIN_EPISODES` | 5 | Minimum total episodes in the lookback window before a forecast is produced. |
| `DEFAULT_VOLUME_OZ` | 4.0 | Fallback when no non-zero volumes exist in the window. |

Sub-period boundaries (clock hours 3, 7, 19, 22) are hardcoded in
`classify()`. Future agents may adjust them if data warrants.

## Strengths

- **Sub-period resolution.** Four buckets distinguish the evening
  pre-bed gap, mid-night wake-feed-sleep intervals, pre-dawn gaps,
  and shorter daytime gaps.
- **Simplicity.** No fitted distributions, no optimization, no
  tuning pipeline. Pure empirical observation with recency
  weighting.
- **Adaptability.** Future agents can tune constants, change
  sub-period boundaries, or replace the algorithm entirely.

## Weaknesses

- **Sparse evening bucket.** The evening sub-period (19:00-22:00)
  spans only three hours, so it often has few observations in a
  7-day window. `MIN_BUCKET_GAPS=3` guards against this by falling
  back to the overall median, but fallback blurs the bucket
  distinction.
- **No first-feed conditioning.** The forecast steps forward from
  the cutoff using the cutoff-hour bucket median. It does not
  account for how long the baby has already gone since the last
  recorded feed, so early placement can be systematically off when
  the cutoff is far from the most recent episode.
- **Fixed gap stepping.** After each placed feed, the next gap
  comes from the unconditional sub-period median. The forecast
  does not adapt based on how it is unfolding (e.g., stretching
  remaining gaps if too many feeds are already placed for the day).

## Open Questions for Future Agents

Ordered by expected impact:

1. **Conditional first-feed estimate.** Reintroduce a conditional
   survival step for the first predicted feed: given how long the
   baby has gone since the last feed, take the weighted median of
   remaining times among observed gaps longer than that elapsed.
   Present in the prior two-bucket `model.py`; not carried into
   the four-bucket rewrite.

2. **Count calibration.** If the projected 24h count diverges from
   the recency-weighted daily episode count by more than some
   threshold, scale all gaps proportionally and re-project.
   Present in the prior two-bucket `model.py`; not carried into
   the four-bucket rewrite.

3. **Sub-period boundary tuning.** Current boundaries are
   intuitive but not empirically tested. Try shifts (e.g.,
   deep-night 22-02, early-morning 02-07) and measure retrospective
   score changes.

4. **Higher gap percentile.** Using the 55th-60th percentile
   instead of the median for evening or deep-night gaps would
   produce slightly longer gaps, reducing over-prediction when the
   baby has a longer first sleep stretch. Untested.

5. **Adaptive gap stepping.** After each placed feed, adjust the
   next gap based on how the forecast is unfolding relative to the
   expected daily episode count.

## Guidance for Future Agents

- **Start by reading `methodology.md`, `CHANGELOG.md`, and the
  latest retrospective scores in `tracker.json`.** If the model is
  performing well, small constant adjustments may be all that's
  needed. If it's performing poorly, consider structural changes.
- **Run `model.py` first, then decide** whether to use its output
  as-is, adjust it, or rewrite the script.
- **Keep `model.py` as the canonical forecast.** The pipeline runs
  it and the committed `forecast.json` must come from it. Other
  `.py` files for research or helpers are fine alongside it.
- **Document material changes** in `CHANGELOG.md` and refresh this
  file or `design.md` when the durable approach shifts. A
  repository consistency check requires a `CHANGELOG.md` entry
  whenever `model.py` changes.
- **Test against multiple cutoff points** when making changes. A
  single retrospective can be misleading; multi-cutoff variance is
  real.
