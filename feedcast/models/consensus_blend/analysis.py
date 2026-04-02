"""Consensus Blend research: evaluate the production exact selector.

Run from the repo root:
    .venv/bin/python -m feedcast.models.consensus_blend.analysis

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

from feedcast.clustering import FeedEpisode, group_into_episodes
from feedcast.data import (
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    HORIZON_HOURS,
    FeedEvent,
    Forecast,
    ForecastPoint,
    build_feed_events,
    load_export_snapshot,
)
from feedcast.evaluation.windows import (
    evaluate_multi_window,
    generate_episode_boundary_cutoffs,
    recency_weight,
)
from feedcast.models import run_all_models
from feedcast.replay import score_model
from feedcast.models.consensus_blend.model import (
    ANCHOR_RADIUS_MINUTES,
    MAX_CANDIDATE_SPREAD_MINUTES,
    SELECTION_CONFLICT_WINDOW_MINUTES,
    SPREAD_PENALTY_PER_HOUR,
    _candidates_to_forecast_points,
    _collapse_forecast_dict,
    _collapse_to_episode_points,
    _majority_floor,
    generate_candidate_clusters,
    select_candidate_sequence,
)
from feedcast.models.shared import normalize_forecast_points

OUTPUT_DIR = Path(__file__).parent
MAX_MATCH_GAP_HOURS = 2.0

# Replay defaults — the sweep and canonical evaluation must use the same
# weighting so the sweep optimizes the same objective the canonical score
# reports. These match score_model() defaults in feedcast/replay/runner.py.
CANONICAL_LOOKBACK_HOURS = 96.0
CANONICAL_HALF_LIFE_HOURS = 36.0

# Inter-episode gap analysis uses a longer half-life for its own
# diagnostic weighting (unrelated to canonical scoring).
GAP_ANALYSIS_HALF_LIFE_HOURS = 4.0 * 24.0  # 4 days

SWEEP_RADIUS_MINUTES = [60, 90, 120, 150]
SWEEP_MAX_SPREAD_MINUTES = [90, 120, 150, 180]
# Stop at 150 minutes because the recency-weighted lower quartile of real
# episode gaps is ~147 minutes on the current export; much wider conflict
# windows would suppress a large share of legitimate close episodes.
SWEEP_CONFLICT_MINUTES = [75, 90, 105, 120, 135, 150]
SWEEP_SPREAD_PENALTIES = [0.25, 1.0, 2.0, 5.0]


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

    # Bottle-only events for canonical scoring and cutoff generation.
    # The scorer operates on bottle-only events; using merged events here
    # would be inconsistent with score_model / replay.
    scoring_events = build_feed_events(
        snapshot.activities, merge_window_minutes=None,
    )
    scoring_episodes = group_into_episodes(scoring_events)
    cutoffs = generate_episode_boundary_cutoffs(
        scoring_episodes, cutoff, lookback_hours=CANONICAL_LOOKBACK_HOURS,
    )

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Cutoff: {cutoff}")
    log(f"Evaluation windows: {len(cutoffs)}")
    log(f"Run: {datetime.now().isoformat(timespec='seconds')}")
    log()

    episodes = group_into_episodes(events)
    _analyze_inter_episode_gaps(episodes, cutoff, log)

    # Diagnostic sections use a subset of cutoffs to avoid running
    # all models at every episode-boundary window (expensive).
    diagnostic_cutoffs = cutoffs[-5:]
    _analyze_model_agreement(episodes, snapshot.activities, diagnostic_cutoffs, log)

    # Canonical production score via shared infrastructure.
    _report_canonical_score(snapshot, log)

    # Selector sweep via evaluate_multi_window.
    _sweep_selector_parameters(
        scoring_events, events, snapshot.activities, cutoffs, cutoff, log,
    )

    artifacts_dir = OUTPUT_DIR / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    results_path = artifacts_dir / "research_results.txt"
    results_path.write_text(output_capture.getvalue())
    log(f"\nResults saved to {results_path}")


def _report_canonical_score(snapshot, log) -> None:
    """Report canonical multi-window evaluation via score_model."""
    log(f"{'=' * 60}")
    log("CANONICAL MULTI-WINDOW EVALUATION")
    log(f"{'=' * 60}")
    log()
    log("Production-constant evaluation via score_model (same")
    log("infrastructure as the replay CLI).")
    log()

    canonical = score_model("consensus_blend", export_path=snapshot.export_path)
    rw = canonical["replay_windows"]
    agg = rw["aggregate"]
    log(f"Aggregate:  headline={agg['headline']:.1f}  count={agg['count']:.1f}  "
        f"timing={agg['timing']:.1f}")
    log(f"Windows:    {rw['scored_window_count']} scored / {rw['window_count']} total "
        f"({rw['availability_ratio'] * 100:.1f}% availability)")
    log(f"Half-life:  {rw['half_life_hours']}h  Lookback: {rw['lookback_hours']}h")
    log()
    log("Per-window breakdown:")
    log(f"  {'Cutoff':<22} {'Weight':>7} {'Head':>7} {'Count':>7} {'Time':>7}  Status")
    for w in rw["per_window"]:
        if w["score"] is not None:
            s = w["score"]
            log(f"  {w['cutoff']:<22} {w['weight']:>7.4f} {s['headline']:>7.1f} "
                f"{s['count']:>7.1f} {s['timing']:>7.1f}  {w['status']}")
        else:
            log(f"  {w['cutoff']:<22} {w['weight']:>7.4f} {'--':>7} {'--':>7} "
                f"{'--':>7}  {w['status']}")
    log()


def _analyze_inter_episode_gaps(
    episodes: list[FeedEpisode],
    cutoff: datetime,
    log,
) -> None:
    """Show inter-episode gaps day-by-day, most recent first."""
    log("=== INTER-EPISODE GAP ANALYSIS ===")
    log()

    daily: dict[str, list[FeedEpisode]] = defaultdict(list)
    for episode in episodes:
        daily[str(episode.time.date())].append(episode)

    weighted_gaps: list[tuple[float, float]] = []
    log(f"{'Date':<12} {'Episodes':>8}  {'Gaps (min)':50s}  {'Min':>5}  {'Med':>5}")
    for day_str in sorted(daily, reverse=True)[:10]:
        day_episodes = sorted(daily[day_str], key=lambda episode: episode.time)
        gaps = [
            (day_episodes[index + 1].time - day_episodes[index].time).total_seconds()
            / 60.0
            for index in range(len(day_episodes) - 1)
        ]
        age_hours = (cutoff - day_episodes[-1].time).total_seconds() / 3600.0
        weight = recency_weight(age_hours=age_hours, half_life_hours=GAP_ANALYSIS_HALF_LIFE_HOURS)
        weighted_gaps.extend((gap, weight) for gap in gaps)

        gap_text = "  ".join(f"{gap:.0f}" for gap in gaps) if gaps else "--"
        min_gap = f"{min(gaps):.0f}" if gaps else "--"
        median_gap = f"{np.median(gaps):.0f}" if gaps else "--"
        log(
            f"{day_str:<12} {len(day_episodes):>8}  "
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
            f"Recency-weighted (half-life {GAP_ANALYSIS_HALF_LIFE_HOURS / 24:.0f}d): "
            f"P25={p25:.0f}  Median={p50:.0f}  "
            f"Min={np.min(values):.0f}  Max={np.max(values):.0f}"
        )
    log()


def _match_predictions_to_actuals(
    predictions: list[ForecastPoint],
    actuals: list[FeedEvent] | list[FeedEpisode],
) -> list[tuple[int, int]]:
    """Match predicted points to actuals using Hungarian assignment.

    Only ``.time`` is accessed on each actual, so this accepts both
    FeedEvent and FeedEpisode lists.
    """
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
    episodes: list[FeedEpisode],
    activities: list,
    cutoffs: list[datetime],
    log,
) -> None:
    """Measure inter-model prediction spread per actual episode.

    Actuals are episode-level (matching the scorer's ontology). Model
    predictions are collapsed into episodes before matching, consistent
    with what the consensus blend sees after its pre-voting collapse.
    """
    log("=== INTER-MODEL PREDICTION SPREAD ===")
    log()

    all_spreads: list[float] = []
    for cutoff in cutoffs:
        horizon_end = cutoff + timedelta(hours=HORIZON_HOURS)
        actual_episodes = [
            episode for episode in episodes if cutoff < episode.time <= horizon_end
        ]
        if not actual_episodes:
            continue

        forecasts = run_all_models(activities, cutoff, HORIZON_HOURS)
        episode_to_predictions: dict[int, list[datetime]] = defaultdict(list)
        for forecast in forecasts:
            if not forecast.available or not forecast.points:
                continue
            # Collapse model predictions to episodes, matching production.
            collapsed_points = _collapse_to_episode_points(forecast.points)
            matches = _match_predictions_to_actuals(collapsed_points, actual_episodes)
            for predicted_index, actual_index in matches:
                episode_to_predictions[actual_index].append(
                    collapsed_points[predicted_index].time
                )

        spreads = [
            (max(times) - min(times)).total_seconds() / 60.0
            for times in episode_to_predictions.values()
            if len(times) >= 2
        ]
        all_spreads.extend(spreads)
        log(
            f"Cutoff {cutoff.date()} {cutoff.strftime('%H:%M')}: "
            f"{len(actual_episodes)} episodes, "
            f"{len(spreads)} multi-model matches"
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


def _sweep_selector_parameters(
    scoring_events: list[FeedEvent],
    events: list[FeedEvent],
    activities: list,
    cutoffs: list[datetime],
    latest_activity_time: datetime,
    log,
) -> None:
    """Sweep nearby selector settings via evaluate_multi_window.

    Uses pre-cached model outputs per cutoff and pre-generated candidate
    clusters per (radius, spread) pair. Each (radius, spread, conflict,
    penalty) configuration is scored via ``evaluate_multi_window`` using
    bottle-only scoring events for consistency with the canonical scorer.
    """
    log("=== SELECTOR PARAMETER SWEEP ===")
    log()
    log(
        "Scores the immutable-candidate exact selector via multi-window "
        "canonical evaluation (evaluate_multi_window)."
    )
    log()

    # Pre-compute model outputs per cutoff. Model outputs use merged
    # events (matching production), but scoring uses bottle-only events
    # (passed to evaluate_multi_window as scoring_events).
    cutoff_cache: dict[datetime, tuple | None] = {}
    for cutoff in cutoffs:
        base_forecasts = run_all_models(activities, cutoff, HORIZON_HOURS)
        available = {
            forecast.slug: forecast
            for forecast in base_forecasts
            if forecast.available and forecast.points
        }
        if len(available) < 2:
            cutoff_cache[cutoff] = None
            continue
        # Collapse model predictions into episodes before candidate
        # generation, matching production behavior.
        available = _collapse_forecast_dict(available)
        history_at_cutoff = [e for e in events if e.time <= cutoff]
        cutoff_cache[cutoff] = (available, history_at_cutoff)

    # Pre-generate candidates per (radius, spread) per cutoff to avoid
    # redundant candidate generation across conflict/penalty variations.
    candidate_cache: dict[tuple[int, int], dict[datetime, tuple | None]] = {}
    for radius_minutes in SWEEP_RADIUS_MINUTES:
        for max_spread_minutes in SWEEP_MAX_SPREAD_MINUTES:
            per_cutoff: dict[datetime, tuple | None] = {}
            for cutoff in cutoffs:
                if cutoff_cache[cutoff] is None:
                    per_cutoff[cutoff] = None
                    continue
                available, history = cutoff_cache[cutoff]
                majority_floor = _majority_floor(len(available))
                candidates = generate_candidate_clusters(
                    available,
                    radius_minutes=radius_minutes,
                    max_spread_minutes=max_spread_minutes,
                )
                per_cutoff[cutoff] = (candidates, majority_floor, history)
            candidate_cache[(radius_minutes, max_spread_minutes)] = per_cutoff

    rows: list[dict[str, float]] = []
    for (radius_minutes, max_spread_minutes), per_cutoff in candidate_cache.items():
        for conflict_minutes in SWEEP_CONFLICT_MINUTES:
            for spread_penalty in SWEEP_SPREAD_PENALTIES:

                # Factory function to bind loop variables into the closure.
                def _make_forecast_fn(pc, conf, pen):
                    def forecast_fn(cutoff: datetime) -> Forecast:
                        entry = pc[cutoff]
                        if entry is None:
                            return Forecast(
                                name="Consensus Blend",
                                slug="consensus_blend",
                                points=[],
                                methodology="",
                                diagnostics={},
                                available=False,
                            )
                        candidates, majority_floor, history = entry
                        selected = select_candidate_sequence(
                            candidates,
                            majority_floor=majority_floor,
                            conflict_minutes=conf,
                            spread_penalty_per_hour=pen,
                        )
                        points = normalize_forecast_points(
                            _candidates_to_forecast_points(selected, history),
                            cutoff,
                            HORIZON_HOURS,
                        )
                        # Mirror production semantics: if the selector
                        # yields no points, the forecast is unavailable.
                        if not points:
                            return Forecast(
                                name="Consensus Blend",
                                slug="consensus_blend",
                                points=[],
                                methodology="",
                                diagnostics={},
                                available=False,
                                error_message="selector produced no points",
                            )
                        return Forecast(
                            name="Consensus Blend",
                            slug="consensus_blend",
                            points=points,
                            methodology="",
                            diagnostics={},
                            available=True,
                        )
                    return forecast_fn

                fn = _make_forecast_fn(per_cutoff, conflict_minutes, spread_penalty)
                result = evaluate_multi_window(
                    fn, scoring_events, cutoffs,
                    latest_activity_time,
                    half_life_hours=CANONICAL_HALF_LIFE_HOURS,
                )
                rows.append({
                    "radius_minutes": radius_minutes,
                    "max_spread_minutes": max_spread_minutes,
                    "conflict_minutes": conflict_minutes,
                    "spread_penalty": spread_penalty,
                    "score": result.headline_score,
                    "count_score": result.count_score,
                    "timing_score": result.timing_score,
                    "scored_windows": result.scored_window_count,
                    "window_count": result.window_count,
                })

    # Rank by availability tier first (most scored windows wins), then
    # by headline score within that tier — same policy as tune_model.
    rows.sort(
        key=lambda row: (
            -int(row["scored_windows"]),
            -float(row["score"]),
            -float(row["timing_score"]),
            -float(row["count_score"]),
        )
    )
    log(
        f"{'Radius':>6} {'Spread':>6} {'Conflict':>8} {'Penalty':>7}  "
        f"{'Score':>8} {'Count':>8} {'Timing':>8} {'Win':>7}"
    )
    for row in rows:
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
            f"{row['score']:>8.3f} {row['count_score']:>8.3f} "
            f"{row['timing_score']:>8.3f} "
            f"{int(row['scored_windows']):>3}/{int(row['window_count'])}"
            f"{marker}"
        )
    log()


if __name__ == "__main__":
    main()
