# Feed Volume vs. Subsequent Gap

## Hypothesis

Larger bottle feeds tend to be followed by longer gaps before the next
bottle-centered feed.

## Methods

- Data source: `exports/export_narababy_silas_20260323.csv`
- Dataset fingerprint: `sha256:7b6cdd2f60a01fa673d275d709782826dd0e7f61fe13491b571813fcf2492cc0`
- Primary view: bottle-only events
- Sensitivity check: 45-minute breastfeed merge heuristic
- Analysis unit: each feed except the final observed feed, paired with the gap
  until the next feed
- Checks: overall correlation, daypart splits, recent-window stability, and
  merge sensitivity

## Results

- Bottle-only view: 80 observed pairs, Pearson `r = 0.357` (`p = 0.0012`),
  with a fitted slope of `+0.334` hours per additional ounce.
- After a simple daypart split, the daytime relationship remains positive:
  `r = 0.292` across 46 pairs (`p = 0.049`). Overnight is also positive at
  `r = 0.221` across 34 pairs, but the current sample is too small to treat
  that overnight estimate as strong evidence (`p = 0.210`).
- Small feeds and large feeds separate meaningfully in practice:
  feeds below 2.5 oz are followed by a mean 1.91-hour gap, while 3.5-5.0 oz
  feeds are followed by a mean 2.82-hour gap.
- The relationship is still present in recent data:
  last 5 days `r = 0.353`; last 3 days `r = 0.483`.
- Breastfeed merging barely changes the result on the current export:
  only 2 analyzed pairs receive added volume, and the overall correlation
  moves from `0.357` to `0.351`.

## Conclusion

Supported on the current dataset. Larger feeds are followed by longer gaps
often enough to treat volume as a real input signal, but the effect is modest
rather than dominant. It should inform models and agents as one useful signal,
not as a standalone rule.

This result is descriptive, not causal. Some of the pattern overlaps with
time-of-day effects, and future exports may change the strength of the
relationship.

## Artifacts

- [`artifacts/research_results.txt`](artifacts/research_results.txt)
- [`artifacts/summary.json`](artifacts/summary.json)
- [`artifacts/bottle_only_pairs.csv`](artifacts/bottle_only_pairs.csv)
- [`artifacts/merged_45_min_pairs.csv`](artifacts/merged_45_min_pairs.csv)
- [`artifacts/bottle_only_volume_gap_scatter.png`](artifacts/bottle_only_volume_gap_scatter.png)
