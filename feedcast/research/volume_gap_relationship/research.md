# Feed Volume vs. Subsequent Gap

## Last run

| Field | Value |
|---|---|
| Date | 2026-04-09 |
| Export | `exports/export_narababy_silas_20260327.csv` |
| Dataset | `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d` |
| Command | `.venv/bin/python -m feedcast.research.volume_gap_relationship.analysis` |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Overview

Larger bottle feeds tend to be followed by longer gaps before the next
bottle-centered feed.

## Methods

- Data source: `exports/export_narababy_silas_20260327.csv`
- Dataset fingerprint:
  `sha256:118402965157e786a84c2650be6c0b631ac39860edd3a09410cbfd856be0706d`
- Primary view: bottle-only events
- Sensitivity check: 45-minute breastfeed merge heuristic
- Analysis unit: each feed except the final observed feed, paired with
  the gap until the next feed
- Checks: overall correlation, daypart splits, recent-window stability,
  volume-bin summaries, and merge sensitivity

## Results

- Bottle-only view: 120 observed pairs, Pearson `r = 0.334`
  (`p = 0.0002`), with a fitted slope of `+0.294` hours per additional
  ounce.
- After a simple daypart split, the daytime relationship remains
  positive: `r = 0.279` across 68 pairs (`p = 0.021`). Overnight is
  also positive at `r = 0.261` across 52 pairs, but the current sample
  is still too small to treat that overnight estimate as strong
  evidence (`p = 0.061`).
- Small feeds and large feeds still separate meaningfully in practice:
  feeds below 2.5 oz are followed by about a 1.96-hour mean gap, while
  3.5-5.0 oz feeds are followed by about a 2.80-hour mean gap.
- The relationship is still present in recent data:
  - last 3 days: `r = 0.369` across 26 pairs (`p = 0.064`)
  - last 5 days: `r = 0.302` across 45 pairs (`p = 0.044`)
  - last 7 days: `r = 0.351` across 64 pairs (`p = 0.004`)
- Breastfeed merging still barely changes the result on the current
  export: only 3 analyzed pairs receive added volume under the merged
  view, and the overall correlation moves from `0.334` to `0.331`.

## Conclusions

Supported on the current dataset. Larger feeds are followed by longer
gaps often enough to treat volume as a real input signal, but the effect
is modest rather than dominant. It should inform models and agents as
one useful signal, not as a standalone rule.

This result is descriptive, not causal. Some of the pattern overlaps
with time-of-day effects, and future exports may change the strength of
the relationship.

## Open questions

- Does the same relationship hold when measured at the episode level
  rather than on raw bottle pairs?
- Does the overnight estimate stabilize as more data accumulates?

## Artifacts

- [`artifacts/research_results.txt`](artifacts/research_results.txt)
- [`artifacts/summary.json`](artifacts/summary.json)
- [`artifacts/bottle_only_pairs.csv`](artifacts/bottle_only_pairs.csv)
- [`artifacts/merged_45_min_pairs.csv`](artifacts/merged_45_min_pairs.csv)
- [`artifacts/bottle_only_volume_gap_scatter.png`](artifacts/bottle_only_volume_gap_scatter.png)
