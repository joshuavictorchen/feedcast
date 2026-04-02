# CHANGELOG

## 2026-03-26 — Initial analysis and rule derivation

**New conclusion:** Piecewise rule (gap ≤ 73 min OR gap ≤ 80 min with
second feed ≤ 1.50 oz) achieves fp=0, fn=0 on 96 labeled boundaries.
Implemented in `feedcast/clustering.py`.
**What changed:** First analysis on `exports/export_narababy_silas_20260325.csv`.
Hand-labeled 96 boundaries in `labels.yaml`.
