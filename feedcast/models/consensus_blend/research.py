"""Consensus Blend research: evaluate blend strategies on the real scorer.

Run from the repo root:
    .venv/bin/python -m feedcast.models.consensus_blend.research

This script evaluates the production lockstep blend and experimental
candidate-cluster blend against the retrospective scorer on recent
cutoffs.  It also analyzes inter-feed gaps and inter-model agreement
to inform future threshold and utility tuning.

Emphasis is on recent trends: cutoffs are recency-weighted when
computing summary statistics, and the most recent days appear first.

Update this script and re-run when new exports arrive or when
iterating on the blend algorithm.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from feedcast.data import (
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    HORIZON_HOURS,
    FeedEvent,
    ForecastPoint,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.evaluation.scoring import ScoringConfig, score_forecast
from feedcast.models import (
    MODELS,
    build_event_cache,
    run_all_models_from_cache,
)
from feedcast.models.consensus_blend.model import (
    _blend_lockstep,
    generate_candidate_clusters,
    MIN_INTERVAL_HOURS,
)
from feedcast.models.shared import normalize_forecast_points

# Output is saved alongside the script for reproducibility.
OUTPUT_DIR = Path(__file__).parent

# Match tolerance when aligning model predictions to actual feeds.
MAX_MATCH_GAP_HOURS = 2.0

# Recency half-life for weighting cutoff-level scores.
RECENCY_HALF_LIFE_DAYS = 4.0


def main() -> None:
    """Run the evaluation."""
    output_capture = StringIO()

    def log(text: str = "") -> None:
        print(text)
        output_capture.write(text + "\n")

    snapshot = load_export_snapshot()
    cutoff = snapshot.latest_activity_time
    events = build_feed_events(
        snapshot.activities,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    )
    event_cache = build_event_cache(snapshot.activities)

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log()

    cutoffs = _pick_retrospective_cutoffs(events, cutoff)
    _analyze_interfeed_gaps(events, cutoff, log)
    _analyze_model_agreement(events, event_cache, cutoffs, log)
    _evaluate_blends_on_scorer(events, event_cache, cutoffs, log)

    results_path = OUTPUT_DIR / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


# ====================================================================
# Cutoff selection
# ====================================================================


def _pick_retrospective_cutoffs(
    events: list[FeedEvent],
    latest_cutoff: datetime,
    max_cutoffs: int = 5,
) -> list[datetime]:
    """Pick the last feed time of each recent complete day.

    Skips the latest (incomplete) day and selects up to ``max_cutoffs``
    preceding days.
    """
    daily: dict[str, list[FeedEvent]] = defaultdict(list)
    for event in events:
        daily[str(event.time.date())].append(event)

    sorted_days = sorted(daily.keys())
    if sorted_days and sorted_days[-1] == str(latest_cutoff.date()):
        sorted_days = sorted_days[:-1]

    cutoffs: list[datetime] = []
    for day_str in reversed(sorted_days):
        last_feed = max(daily[day_str], key=lambda e: e.time)
        cutoffs.append(last_feed.time)
        if len(cutoffs) >= max_cutoffs:
            break

    cutoffs.reverse()
    return cutoffs


# ====================================================================
# Section 1: Inter-feed gap analysis (recency-weighted)
# ====================================================================


def _analyze_interfeed_gaps(
    events: list[FeedEvent],
    cutoff: datetime,
    log,
) -> None:
    """Show inter-feed gaps day-by-day, most recent first, with
    recency-weighted summary statistics."""
    log("=== INTER-FEED GAP ANALYSIS ===")
    log()

    daily: dict[str, list[FeedEvent]] = defaultdict(list)
    for event in events:
        daily[str(event.time.date())].append(event)

    # Collect gaps with per-day recency weights.
    weighted_gaps: list[tuple[float, float]] = []  # (gap_minutes, weight)
    sorted_days = sorted(daily, reverse=True)

    log(f"{'Date':<12} {'Feeds':>5}  {'Gaps (min)':50s}  {'Min':>5}  {'Med':>5}")
    for day_str in sorted_days[:10]:
        feeds = sorted(daily[day_str], key=lambda e: e.time)
        gaps = [
            (feeds[i + 1].time - feeds[i].time).total_seconds() / 60
            for i in range(len(feeds) - 1)
        ]
        age_days = (cutoff - feeds[-1].time).total_seconds() / 86400
        weight = 2.0 ** (-age_days / RECENCY_HALF_LIFE_DAYS)
        for gap in gaps:
            weighted_gaps.append((gap, weight))

        gap_str = "  ".join(f"{g:.0f}" for g in gaps) if gaps else "--"
        min_gap = f"{min(gaps):.0f}" if gaps else "--"
        med_gap = f"{np.median(gaps):.0f}" if gaps else "--"
        log(
            f"{day_str:<12} {len(feeds):>5}  "
            f"{gap_str:50s}  {min_gap:>5}  {med_gap:>5}"
        )

    if weighted_gaps:
        values = np.array([g for g, _ in weighted_gaps])
        weights = np.array([w for _, w in weighted_gaps])
        # Weighted percentiles via sorted cumulative weight.
        order = np.argsort(values)
        sorted_values = values[order]
        cum_weight = np.cumsum(weights[order])
        cum_weight /= cum_weight[-1]
        p25 = sorted_values[np.searchsorted(cum_weight, 0.25)]
        p50 = sorted_values[np.searchsorted(cum_weight, 0.50)]
        log()
        log(
            f"Recency-weighted (half-life {RECENCY_HALF_LIFE_DAYS}d): "
            f"P25={p25:.0f}  Median={p50:.0f}  "
            f"Min={np.min(values):.0f}  Max={np.max(values):.0f}"
        )
    log()


# ====================================================================
# Section 2: Inter-model prediction spread
# ====================================================================


def _match_predictions_to_actuals(
    predictions: list[ForecastPoint],
    actuals: list[FeedEvent],
) -> list[tuple[int, int]]:
    """Match predicted points to actual feeds using Hungarian assignment."""
    n_pred = len(predictions)
    n_actual = len(actuals)
    if n_pred == 0 or n_actual == 0:
        return []

    size = n_pred + n_actual
    cost = np.full((size, size), 1e6, dtype=float)
    for i in range(n_pred):
        for j in range(n_actual):
            error_hours = abs(
                (predictions[i].time - actuals[j].time).total_seconds()
            ) / 3600
            if error_hours <= MAX_MATCH_GAP_HOURS:
                cost[i, j] = error_hours

    row_indices, col_indices = linear_sum_assignment(cost)
    return [
        (row, col)
        for row, col in zip(row_indices, col_indices)
        if row < n_pred and col < n_actual and cost[row, col] <= MAX_MATCH_GAP_HOURS
    ]


def _analyze_model_agreement(
    events: list[FeedEvent],
    event_cache: dict,
    cutoffs: list[datetime],
    log,
) -> None:
    """Measure inter-model spread when predicting the same actual feed."""
    log("=== INTER-MODEL PREDICTION SPREAD ===")
    log()

    all_spreads: list[float] = []

    for cutoff in cutoffs:
        horizon_end = cutoff + timedelta(hours=HORIZON_HOURS)
        actuals = [e for e in events if cutoff < e.time <= horizon_end]
        if not actuals:
            continue

        forecasts = run_all_models_from_cache(event_cache, cutoff, HORIZON_HOURS)
        actual_to_predictions: dict[int, list[tuple[str, datetime]]] = (
            defaultdict(list)
        )
        for forecast in forecasts:
            if not forecast.available or not forecast.points:
                continue
            matches = _match_predictions_to_actuals(forecast.points, actuals)
            for pred_idx, actual_idx in matches:
                actual_to_predictions[actual_idx].append(
                    (forecast.slug, forecast.points[pred_idx].time)
                )

        spreads: list[float] = []
        for actual_idx in sorted(actual_to_predictions):
            preds = actual_to_predictions[actual_idx]
            if len(preds) >= 2:
                times = [t for _, t in preds]
                spreads.append(
                    (max(times) - min(times)).total_seconds() / 60
                )

        all_spreads.extend(spreads)
        log(
            f"Cutoff {cutoff.date()} {cutoff.strftime('%H:%M')}: "
            f"{len(actuals)} actuals, {len(spreads)} multi-model matches"
        )

    if all_spreads:
        arr = np.array(all_spreads)
        log()
        log(
            f"Spread: P50={np.percentile(arr, 50):.0f}  "
            f"P75={np.percentile(arr, 75):.0f}  "
            f"P90={np.percentile(arr, 90):.0f}  "
            f"Max={np.max(arr):.0f}"
        )
    log()


# ====================================================================
# Section 3: Blend evaluation on the real scorer
# ====================================================================


def _evaluate_blends_on_scorer(
    events: list[FeedEvent],
    event_cache: dict,
    cutoffs: list[datetime],
    log,
) -> None:
    """Score the production lockstep blend and candidate-cluster blend
    against actual feeds using the repo's retrospective scorer."""
    log("=== RETROSPECTIVE SCORER EVALUATION ===")
    log()
    log("Compares the production lockstep blend against candidate-cluster")
    log("strategies on actual retrospective cutoffs.")
    log()

    config = ScoringConfig()
    lockstep_scores: list[tuple[float, dict]] = []
    candidate_scores: list[tuple[float, dict]] = []

    for cutoff in cutoffs:
        horizon_end = cutoff + timedelta(hours=HORIZON_HOURS)
        actuals = [e for e in events if cutoff < e.time <= horizon_end]
        if len(actuals) < 2:
            continue

        # Determine the observation window end.
        observed_until = min(horizon_end, max(e.time for e in events))

        forecasts = run_all_models_from_cache(event_cache, cutoff, HORIZON_HOURS)
        available = {
            f.slug: f for f in forecasts if f.available and f.points
        }
        if len(available) < 2:
            continue

        # Recency weight for this cutoff.
        age_days = (cutoffs[-1] - cutoff).total_seconds() / 86400
        weight = 2.0 ** (-age_days / RECENCY_HALF_LIFE_DAYS)

        # --- Lockstep blend ---
        history_at_cutoff = [e for e in events if e.time <= cutoff]
        lockstep_points, _ = _blend_lockstep(available, history_at_cutoff)
        lockstep_normalized = normalize_forecast_points(
            lockstep_points, cutoff, HORIZON_HOURS
        )
        lockstep_result = score_forecast(
            lockstep_normalized, actuals, cutoff, observed_until, config
        )
        lockstep_scores.append((weight, {
            "cutoff": str(cutoff),
            "score": lockstep_result.score,
            "count_score": lockstep_result.count_score,
            "timing_score": lockstep_result.timing_score,
            "predicted": lockstep_result.predicted_count,
            "actual": lockstep_result.actual_count,
        }))

        # --- Candidate clusters (raw, no sequence selection yet) ---
        candidates = generate_candidate_clusters(available)
        candidate_points = normalize_forecast_points(
            [
                ForecastPoint(
                    time=c["time"],
                    volume_oz=c["volume_oz"],
                    gap_hours=0.0,
                )
                for c in candidates
            ],
            cutoff,
            HORIZON_HOURS,
        )
        candidate_result = score_forecast(
            candidate_points, actuals, cutoff, observed_until, config
        )
        candidate_scores.append((weight, {
            "cutoff": str(cutoff),
            "score": candidate_result.score,
            "count_score": candidate_result.count_score,
            "timing_score": candidate_result.timing_score,
            "predicted": candidate_result.predicted_count,
            "actual": candidate_result.actual_count,
        }))

    # Print per-cutoff comparison.
    log(
        f"{'Cutoff':<22} {'Actual':>6}  "
        f"{'Lock_N':>6} {'Lock_Scr':>8} {'Lock_Cnt':>8} {'Lock_Tim':>8}  "
        f"{'Cand_N':>6} {'Cand_Scr':>8} {'Cand_Cnt':>8} {'Cand_Tim':>8}"
    )
    for (lw, ld), (_, cd) in zip(lockstep_scores, candidate_scores):
        log(
            f"{ld['cutoff']:<22} {ld['actual']:>6}  "
            f"{ld['predicted']:>6} {ld['score']:>8.1f} {ld['count_score']:>8.1f} {ld['timing_score']:>8.1f}  "
            f"{cd['predicted']:>6} {cd['score']:>8.1f} {cd['count_score']:>8.1f} {cd['timing_score']:>8.1f}"
        )

    # Recency-weighted summary.
    if lockstep_scores:
        log()

        def _weighted_mean(scores, key):
            weights = np.array([w for w, _ in scores])
            values = np.array([d[key] for _, d in scores])
            return float(np.average(values, weights=weights))

        log("Recency-weighted means:")
        log(
            f"  Lockstep:  score={_weighted_mean(lockstep_scores, 'score'):.1f}  "
            f"count={_weighted_mean(lockstep_scores, 'count_score'):.1f}  "
            f"timing={_weighted_mean(lockstep_scores, 'timing_score'):.1f}"
        )
        log(
            f"  Candidate: score={_weighted_mean(candidate_scores, 'score'):.1f}  "
            f"count={_weighted_mean(candidate_scores, 'count_score'):.1f}  "
            f"timing={_weighted_mean(candidate_scores, 'timing_score'):.1f}"
        )
    log()


if __name__ == "__main__":
    main()
