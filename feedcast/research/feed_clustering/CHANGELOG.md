# Changelog

Tracks hypothesis, method, and conclusion changes for the feed clustering rule. Add newest entries first.

## Latest-export refresh blocked pending label extension | 2026-04-09

### Problem

The current shared episode-boundary rule was derived from a labeled
March 25 export, but the latest export now contains more bottle-feed
boundaries than `labels.yaml` covers. Treating the older label set as if
it validated the latest export would overstate the evidence.

### Solution

Attempted to re-run `analysis.py` on the latest export and let the
script fail fast. Current state:

- `labels.yaml`: 96 labeled boundaries
- latest export: 120 boundaries
- result: refresh blocked until the 24 new boundaries are labeled

The rule itself remains the current shared rule, but the docs now state
it as "supported on the current labeled dataset" rather than "refreshed
on the latest export."

Export attempted: `exports/export_narababy_silas_20260327.csv`.

## Initial analysis and rule derivation | 2026-03-26

### Problem

No principled rule for grouping close-together feeds into episodes.
Models and evaluation needed a consistent episode boundary definition
derived from data rather than an arbitrary threshold.

### Solution

Hand-labeled 96 feed boundaries in `labels.yaml`. Derived a piecewise
rule (gap ≤ 73 min OR gap ≤ 80 min with second feed ≤ 1.50 oz) that
achieves fp=0, fn=0 on all labeled boundaries. Implemented in
`feedcast/clustering.py`.

Export: `exports/export_narababy_silas_20260325.csv`.
