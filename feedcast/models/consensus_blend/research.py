"""Consensus Blend research: evaluate the production exact selector.

Run from the repo root:
    .venv/bin/python -m feedcast.models.consensus_blend.research

This script evaluates the production immutable-candidate selector on
recent retrospective cutoffs and sweeps nearby selector constants.
The goal is to keep tuning grounded in the real ``score_forecast()``
metric rather than proxy cluster statistics.
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
from feedcast.evaluation.scoring import score_forecast
from feedcast.models import build_event_cache, run_all_models_from_cache
from feedcast.models.consensus_blend.model import (
    ANCHOR_RADIUS_MINUTES,
    MAX_CANDIDATE_SPREAD_MINUTES,
    SELECTION_CONFLICT_WINDOW_MINUTES,
    SPREAD_PENALTY_PER_HOUR,
    CandidateCluster,
    _candidates_to_forecast_points,
    _collapse_forecast_dict,
    _majority_floor,
    generate_candidate_clusters,
    run_consensus_blend,
    select_candidate_sequence,
)
from feedcast.models.shared import normalize_forecast_points

OUTPUT_DIR = Path(__file__).parent
MAX_MATCH_GAP_HOURS = 2.0
RECENCY_HALF_LIFE_DAYS = 4.0


def main() -> None:
    """Run the consensus blend research report."""
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
    cutoffs = _pick_retrospective_cutoffs(events, cutoff)

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log()

    _analyze_interfeed_gaps(events, cutoff, log)
    _analyze_model_agreement(events, event_cache, cutoffs, log)
    _report_production_scores(events, event_cache, cutoffs, log)
    _sweep_selector_parameters(events, event_cache, cutoffs, log)

    results_path = OUTPUT_DIR / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


def _pick_retrospective_cutoffs(
    events: list[FeedEvent],
    latest_cutoff: datetime,
    max_cutoffs: int = 5,
) -> list[datetime]:
    """Pick the last feed time of each recent complete day."""
    daily: dict[str, list[FeedEvent]] = defaultdict(list)
    for event in events:
        daily[str(event.time.date())].append(event)

    sorted_days = sorted(daily.keys())
    if sorted_days and sorted_days[-1] == str(latest_cutoff.date()):
        sorted_days = sorted_days[:-1]

    cutoffs: list[datetime] = []
    for day_str in reversed(sorted_days):
        last_feed = max(daily[day_str], key=lambda event: event.time)
        cutoffs.append(last_feed.time)
        if len(cutoffs) >= max_cutoffs:
            break
    cutoffs.reverse()
    return cutoffs


def _analyze_interfeed_gaps(
    events: list[FeedEvent],
    cutoff: datetime,
    log,
) -> None:
    """Show inter-feed gaps day-by-day, most recent first."""
    log("=== INTER-FEED GAP ANALYSIS ===")
    log()

    daily: dict[str, list[FeedEvent]] = defaultdict(list)
    for event in events:
        daily[str(event.time.date())].append(event)

    weighted_gaps: list[tuple[float, float]] = []
    log(f"{'Date':<12} {'Feeds':>5}  {'Gaps (min)':50s}  {'Min':>5}  {'Med':>5}")
    for day_str in sorted(daily, reverse=True)[:10]:
        feeds = sorted(daily[day_str], key=lambda event: event.time)
        gaps = [
            (feeds[index + 1].time - feeds[index].time).total_seconds() / 60.0
            for index in range(len(feeds) - 1)
        ]
        age_days = (cutoff - feeds[-1].time).total_seconds() / 86400.0
        weight = 2.0 ** (-age_days / RECENCY_HALF_LIFE_DAYS)
        weighted_gaps.extend((gap, weight) for gap in gaps)

        gap_text = "  ".join(f"{gap:.0f}" for gap in gaps) if gaps else "--"
        min_gap = f"{min(gaps):.0f}" if gaps else "--"
        median_gap = f"{np.median(gaps):.0f}" if gaps else "--"
        log(
            f"{day_str:<12} {len(feeds):>5}  "
            f"{gap_text:50s}  {min_gap:>5}  {median_gap:>5}"
        )

    if weighted_gaps:
        values = np.array([gap for gap, _ in weighted_gaps], dtype=float)
        weights = np.array([weight for _, weight in weighted_gaps], dtype=float)
        order = np.argsort(values)
        sorted_values = values[order]
        cumulative = np.cumsum(weights[order])
        cumulative /= cumulative[-1]
        p25 = sorted_values[np.searchsorted(cumulative, 0.25)]
        p50 = sorted_values[np.searchsorted(cumulative, 0.50)]
        log()
        log(
            f"Recency-weighted (half-life {RECENCY_HALF_LIFE_DAYS}d): "
            f"P25={p25:.0f}  Median={p50:.0f}  "
            f"Min={np.min(values):.0f}  Max={np.max(values):.0f}"
        )
    log()


def _match_predictions_to_actuals(
    predictions: list[ForecastPoint],
    actuals: list[FeedEvent],
) -> list[tuple[int, int]]:
    """Match predicted points to actual feeds using Hungarian assignment."""
    if not predictions or not actuals:
        return []

    size = len(predictions) + len(actuals)
    cost = np.full((size, size), 1e6, dtype=float)
    for predicted_index, point in enumerate(predictions):
        for actual_index, event in enumerate(actuals):
            error_hours = abs((point.time - event.time).total_seconds()) / 3600.0
            if error_hours <= MAX_MATCH_GAP_HOURS:
                cost[predicted_index, actual_index] = error_hours

    row_indices, column_indices = linear_sum_assignment(cost)
    return [
        (row_index, column_index)
        for row_index, column_index in zip(row_indices, column_indices)
        if row_index < len(predictions)
        and column_index < len(actuals)
        and cost[row_index, column_index] <= MAX_MATCH_GAP_HOURS
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
        actuals = [event for event in events if cutoff < event.time <= horizon_end]
        if not actuals:
            continue

        forecasts = run_all_models_from_cache(event_cache, cutoff, HORIZON_HOURS)
        actual_to_predictions: dict[int, list[datetime]] = defaultdict(list)
        for forecast in forecasts:
            if not forecast.available or not forecast.points:
                continue
            matches = _match_predictions_to_actuals(forecast.points, actuals)
            for predicted_index, actual_index in matches:
                actual_to_predictions[actual_index].append(
                    forecast.points[predicted_index].time
                )

        spreads = [
            (max(times) - min(times)).total_seconds() / 60.0
            for times in actual_to_predictions.values()
            if len(times) >= 2
        ]
        all_spreads.extend(spreads)
        log(
            f"Cutoff {cutoff.date()} {cutoff.strftime('%H:%M')}: "
            f"{len(actuals)} actuals, {len(spreads)} multi-model matches"
        )

    if all_spreads:
        spread_array = np.array(all_spreads, dtype=float)
        log()
        log(
            f"Spread: P50={np.percentile(spread_array, 50):.0f}  "
            f"P75={np.percentile(spread_array, 75):.0f}  "
            f"P90={np.percentile(spread_array, 90):.0f}  "
            f"Max={np.max(spread_array):.0f}"
        )
    log()


def _report_production_scores(
    events: list[FeedEvent],
    event_cache: dict,
    cutoffs: list[datetime],
    log,
) -> None:
    """Report retrospective scores for the production selector."""
    log("=== PRODUCTION EXACT SELECTOR SCORES ===")
    log()
    log(
        "Production selector constants: "
        f"radius={ANCHOR_RADIUS_MINUTES}m  "
        f"max_spread={MAX_CANDIDATE_SPREAD_MINUTES}m  "
        f"conflict={SELECTION_CONFLICT_WINDOW_MINUTES}m  "
        f"spread_penalty={SPREAD_PENALTY_PER_HOUR:.2f}/h"
    )
    log()

    production_rows: list[tuple[float, dict[str, float | int | str]]] = []

    for cutoff in cutoffs:
        horizon_end = cutoff + timedelta(hours=HORIZON_HOURS)
        actuals = [event for event in events if cutoff < event.time <= horizon_end]
        if len(actuals) < 2:
            continue

        observed_until = min(horizon_end, max(event.time for event in events))
        history_at_cutoff = [event for event in events if event.time <= cutoff]
        base_forecasts = run_all_models_from_cache(event_cache, cutoff, HORIZON_HOURS)
        available = {
            forecast.slug: forecast
            for forecast in base_forecasts
            if forecast.available and forecast.points
        }
        if len(available) < 2:
            continue

        weight = _recency_weight(cutoff, cutoffs[-1])

        production_forecast = run_consensus_blend(
            base_forecasts,
            history_at_cutoff,
            cutoff,
            HORIZON_HOURS,
        )
        production_score = score_forecast(
            production_forecast.points,
            actuals,
            cutoff,
            observed_until,
        )
        production_rows.append(
            (
                weight,
                {
                    "cutoff": str(cutoff),
                    "score": production_score.score,
                    "count_score": production_score.count_score,
                    "timing_score": production_score.timing_score,
                    "predicted": production_score.predicted_episode_count,
                    "actual": production_score.actual_episode_count,
                },
            )
        )

    log(
        f"{'Cutoff':<22} {'Actual':>6}  "
        f"{'Prod_N':>6} {'Prod_Scr':>8} {'Prod_Cnt':>8} {'Prod_Tim':>8}"
    )
    for _, production in production_rows:
        log(
            f"{production['cutoff']:<22} {int(production['actual']):>6}  "
            f"{int(production['predicted']):>6} {production['score']:>8.1f} "
            f"{production['count_score']:>8.1f} {production['timing_score']:>8.1f}"
        )

    if production_rows:
        log()
        log("Recency-weighted means:")
        log(
            "  Production: "
            f"score={_weighted_mean(production_rows, 'score'):.1f}  "
            f"count={_weighted_mean(production_rows, 'count_score'):.1f}  "
            f"timing={_weighted_mean(production_rows, 'timing_score'):.1f}"
        )
    log()


def _sweep_selector_parameters(
    events: list[FeedEvent],
    event_cache: dict,
    cutoffs: list[datetime],
    log,
) -> None:
    """Sweep nearby selector settings around the production constants."""
    log("=== SELECTOR PARAMETER SWEEP ===")
    log()
    log(
        "Scores the immutable-candidate exact selector directly against "
        "the retrospective scorer."
    )
    log()

    # Pre-compute model outputs per cutoff to avoid redundant work.
    cutoff_data: list[
        tuple[float, list[FeedEvent], list[FeedEvent], dict[str, Forecast]]
    ] = []
    for cutoff in cutoffs:
        horizon_end = cutoff + timedelta(hours=HORIZON_HOURS)
        actuals = [event for event in events if cutoff < event.time <= horizon_end]
        if len(actuals) < 2:
            continue
        observed_until = min(horizon_end, max(event.time for event in events))
        history_at_cutoff = [event for event in events if event.time <= cutoff]
        base_forecasts = run_all_models_from_cache(event_cache, cutoff, HORIZON_HOURS)
        available = {
            forecast.slug: forecast
            for forecast in base_forecasts
            if forecast.available and forecast.points
        }
        if len(available) < 2:
            continue
        # Collapse model predictions into episodes before candidate generation,
        # matching production behavior in _blend_by_sequence_selection().
        available = _collapse_forecast_dict(available)
        weight = _recency_weight(cutoff, cutoffs[-1])
        cutoff_data.append(
            (weight, cutoff, actuals, observed_until, history_at_cutoff, available)
        )

    # Pre-generate candidates per cutoff per (radius, spread) pair to avoid
    # redundant candidate generation across spread_penalty variations.
    candidate_cache: dict[
        tuple[int, int, int], list[tuple[float, list[CandidateCluster], int, list[FeedEvent]]]
    ] = {}
    for radius_minutes in [90, 120]:
        for max_spread_minutes in [150, 180]:
            cache_key = (radius_minutes, max_spread_minutes)
            entries = []
            for (
                weight,
                cutoff,
                actuals,
                observed_until,
                history_at_cutoff,
                available,
            ) in cutoff_data:
                majority_floor = _majority_floor(len(available))
                candidates = generate_candidate_clusters(
                    available,
                    radius_minutes=radius_minutes,
                    max_spread_minutes=max_spread_minutes,
                )
                entries.append(
                    (weight, cutoff, actuals, observed_until, history_at_cutoff,
                     candidates, majority_floor)
                )
            candidate_cache[cache_key] = entries

    rows: list[dict[str, float]] = []
    for (radius_minutes, max_spread_minutes), entries in candidate_cache.items():
        for conflict_minutes in [75, 90, 105]:
            for spread_penalty in [0.25, 1.0, 2.0, 5.0]:
                sweep_rows: list[tuple[float, dict[str, float | int]]] = []
                for (
                    weight, cutoff, actuals, observed_until, history_at_cutoff,
                    candidates, majority_floor,
                ) in entries:
                    selected = select_candidate_sequence(
                        candidates,
                        majority_floor=majority_floor,
                        conflict_minutes=conflict_minutes,
                        spread_penalty_per_hour=spread_penalty,
                    )
                    points = normalize_forecast_points(
                        _candidates_to_forecast_points(
                            selected, history_at_cutoff
                        ),
                        cutoff,
                        HORIZON_HOURS,
                    )
                    score = score_forecast(
                        points, actuals, cutoff, observed_until
                    )
                    sweep_rows.append(
                        (
                            weight,
                            {
                                "score": score.score,
                                "count_score": score.count_score,
                                "timing_score": score.timing_score,
                                "predicted": score.predicted_episode_count,
                            },
                        )
                    )

                rows.append(
                    {
                        "radius_minutes": radius_minutes,
                        "max_spread_minutes": max_spread_minutes,
                        "conflict_minutes": conflict_minutes,
                        "spread_penalty": spread_penalty,
                        "score": _weighted_mean(sweep_rows, "score"),
                        "count_score": _weighted_mean(sweep_rows, "count_score"),
                        "timing_score": _weighted_mean(sweep_rows, "timing_score"),
                        "predicted": _weighted_mean(sweep_rows, "predicted"),
                    }
                )

    rows.sort(
        key=lambda row: (
            -float(row["score"]),
            -float(row["timing_score"]),
            -float(row["count_score"]),
        )
    )
    log(
        f"{'Radius':>6} {'Spread':>6} {'Conflict':>8} {'Penalty':>7}  "
        f"{'Score':>8} {'Count':>8} {'Timing':>8} {'Pred':>6}"
    )
    for row in rows[:15]:
        marker = ""
        if (
            int(row["radius_minutes"]) == ANCHOR_RADIUS_MINUTES
            and int(row["max_spread_minutes"]) == MAX_CANDIDATE_SPREAD_MINUTES
            and int(row["conflict_minutes"]) == SELECTION_CONFLICT_WINDOW_MINUTES
            and float(row["spread_penalty"]) == SPREAD_PENALTY_PER_HOUR
        ):
            marker = "  <- production"
        log(
            f"{int(row['radius_minutes']):>6} {int(row['max_spread_minutes']):>6} "
            f"{int(row['conflict_minutes']):>8} {row['spread_penalty']:>7.2f}  "
            f"{row['score']:>8.1f} {row['count_score']:>8.1f} "
            f"{row['timing_score']:>8.1f} {row['predicted']:>6.1f}{marker}"
        )
    log()


def _recency_weight(cutoff: datetime, latest_cutoff: datetime) -> float:
    """Return the recency weight for one retrospective cutoff."""
    age_days = (latest_cutoff - cutoff).total_seconds() / 86400.0
    return 2.0 ** (-age_days / RECENCY_HALF_LIFE_DAYS)


def _weighted_mean(
    rows: list[tuple[float, dict[str, float | int | str]]],
    key: str,
) -> float:
    """Return the weighted mean for one numeric result field."""
    weights = np.array([weight for weight, _ in rows], dtype=float)
    values = np.array([float(result[key]) for _, result in rows], dtype=float)
    return float(np.average(values, weights=weights))


if __name__ == "__main__":
    main()
