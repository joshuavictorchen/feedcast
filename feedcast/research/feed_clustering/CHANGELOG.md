# Changelog

Tracks hypothesis, method, and conclusion changes for the feed clustering rule. Add newest entries first.

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
