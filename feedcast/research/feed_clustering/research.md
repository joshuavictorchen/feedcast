# Feed Clustering

## Last analysis

| Field | Value |
|---|---|
| Date | 2026-03-26 |
| Export | `exports/export_narababy_silas_20260325.csv` |
| Dataset | `sha256:eb791b625b6982da5b4e0d7d53e2b8ee4570b8b7db100ae3e063795cc54c5784` |
| Command | `.venv/bin/python -m feedcast.research.feed_clustering.analysis` |

> **Staleness check:** if the current export differs from the one
> listed here, re-run the command above to refresh results.

## Hypothesis

Some close-together bottle feeds are not independent hunger events. They are
continuations of the same feeding episode and should be collapsed before
evaluation and reporting.

## Methods

- Data source: `exports/export_narababy_silas_20260325.csv`
- Dataset fingerprint:
  `sha256:eb791b625b6982da5b4e0d7d53e2b8ee4570b8b7db100ae3e063795cc54c5784`
- Data floor: `2026-03-15` (`DATA_FLOOR` in `feedcast/data.py`)
- Analysis unit: each boundary between two consecutive bottle feeds
- Total bottle feeds: 97
- Total boundaries: 96
- Labeling scheme: `same_episode`, `new_episode`, `ambiguous`
- Current labels: 17 `same_episode`, 79 `new_episode`, 0 `ambiguous`
- Ranking objective: minimize false collapses first, then total errors

The current search intentionally stays simple. It evaluates:

- gap-only thresholds
- gap plus second-feed-volume thresholds
- a piecewise rule with a short-gap default window and a small-second-feed
  extension window

## Results

- The best conservative single-threshold rule is:

      same_episode if gap <= 73 minutes

  On the current labeled set, this yields:
  - false collapses: 0
  - missed clusters: 1
  - missed example: `03/22 16:36 -> 17:53` (`77.5m`, `4.00 -> 1.25`)
  - labeled `same_episode` gap summary: range `21.4-77.5` minutes, median
    `51.3` minutes, with `16 of 17` at or below `73` minutes

- No pure gap threshold fits the current labels perfectly. The reason is
  structural:
  - `03/22 16:36 -> 17:53` is a confirmed cluster at `77.5` minutes.
  - `03/23 10:26 -> 11:41` is a confirmed non-cluster at `74.8` minutes.
  - `03/21 12:02 -> 13:18` is a confirmed non-cluster at `76.1` minutes.

  That means gap alone cannot separate the labeled positives and negatives.

- The best simple piecewise rule found by the current search is:

      same_episode if gap <= 73 minutes,
      or if gap <= 78 minutes and second feed <= 1.50 oz

  On the current labeled set, this yields:
  - false collapses: 0
  - missed clusters: 0

- The labels show that the phenomenon is broader than just "small top-up after
  a larger feed." Confirmed `same_episode` boundaries include:
  - equal-volume pairs like `2.00 -> 2.00` and `1.50 -> 1.50`
  - a small-to-large pair (`1.00 -> 3.00`)

  So "small attachment" is a useful intuition, not a complete definition.

## Chosen Rule

    same_episode if gap <= 73 minutes,
    or if gap <= 80 minutes and second feed <= 1.50 oz

Two feeds are part of the same feeding episode if they are within 73 minutes
of each other, OR within 80 minutes if the later feed is small (at most
1.50 oz).

On the current labeled set: **fp=0, fn=0** (17/17 clusters detected, 0/79
false collapses).

### Why this rule

- The base gap of 73 minutes is the tightest threshold with zero false
  collapses. It captures 16 of 17 labeled clusters. The data forces this:
  a confirmed non-cluster at 74.8 minutes prevents any higher base gap.
- The extension to 80 minutes for small second feeds captures the one
  remaining cluster (03/22 16:36 → 17:53, 77.5 min, 4.00 → 1.25 oz) while
  rejecting the two non-clusters in that gap range (74.8 min / 2.00 oz and
  76.1 min / 3.00 oz — both have second-feed volumes above 1.50 oz).
- The extension window of 80 minutes (vs. the tightest fit of 78) provides
  headroom for future clusters with similar characteristics without changing
  behavior on current data.

### Alternatives considered

- **`gap <= 73` (gap-only):** Simplest, but misses one confirmed cluster.
  This is the conservative fallback if the piecewise rule proves too fragile.
- **`gap <= 73 OR (gap <= 78 AND vol_b <= 1.50)`:** Tightest perfect fit.
  Same behavior on current data, but less headroom.
- **`gap <= 60 OR (gap <= 80 AND vol_b <= 1.50)`:** Cleaner base threshold,
  but misses a confirmed cluster at 72.1 min / 2.60 oz that no small-feed
  extension can recover.

## Conclusion

Supported on the current labeled dataset.

The chosen rule is provisional. The dataset is small, labels may evolve, and
future exports may justify adjusting thresholds or marking edge cases as
ambiguous. The research process is documented for repeatability: re-label,
re-run `analysis.py`, and update the rule if the data shifts.

## Artifacts

- [`labels.yaml`](labels.yaml)
- [`artifacts/labeled_boundaries.csv`](artifacts/labeled_boundaries.csv)
- [`artifacts/candidate_rules.csv`](artifacts/candidate_rules.csv)
- [`artifacts/summary.json`](artifacts/summary.json)
- [`artifacts/research_results.txt`](artifacts/research_results.txt)
