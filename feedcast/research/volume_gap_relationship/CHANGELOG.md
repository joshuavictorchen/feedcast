# Changelog

Tracks hypothesis, method, and conclusion changes for the volume-gap relationship. Add newest entries first.

## Refresh on latest export | 2026-04-09

### Problem

The original article was based on the March 23 export. The repo's shared
research layer needed the latest-export refresh so cross-cutting volume
claims were not lagging behind the model research.

### Solution

Re-ran the analysis on `exports/export_narababy_silas_20260327.csv`.
The relationship remains supported:

- bottle-only pairs: 120
- overall correlation: `r=0.334` (`p=0.0002`)
- slope: `+0.294` hours per additional ounce
- merged-view correlation: `r=0.331`

Daytime remains significant (`r=0.279`, `p=0.021`); overnight remains
positive but underpowered (`r=0.261`, `p=0.061`). Only 3 analyzed pairs
change under the 45-minute merge heuristic, so the main conclusion is
stable.

Export: `exports/export_narababy_silas_20260327.csv`.

## Initial analysis | 2026-03-24

### Problem

Latent Hunger and other models assume larger feeds produce longer
subsequent gaps, but this had not been tested against the actual data.

### Solution

Measured Pearson r=0.357 (p=0.0012) across 80 bottle-only pairs.
Slope +0.334 hours per additional ounce. Verdict: supported, but the
effect is modest — one signal among several, not a standalone rule.

Export: `exports/export_narababy_silas_20260323.csv`.
