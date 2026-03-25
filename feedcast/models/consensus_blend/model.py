"""Consensus Blend forecast model.

The production runtime builds majority-supported candidate feed slots
around each model prediction, then selects the best non-overlapping
sequence. A lockstep baseline remains in this module for research and
retrospective comparison.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from feedcast.data import (
    FeedEvent,
    Forecast,
    ForecastPoint,
    MIN_INTERVAL_HOURS,
)
from feedcast.models.shared import (
    load_methodology,
    normalize_forecast_points,
)

MODEL_NAME = "Consensus Blend"
MODEL_SLUG = "consensus_blend"
MODEL_METHODOLOGY = load_methodology(__file__)

# --- Production selector constants ---

# Candidate slots are built by asking which model predictions sit near a
# proposed anchor time. A 2-hour radius is wide enough to recover majority
# agreement when models disagree substantially about one real feed.
ANCHOR_RADIUS_MINUTES = 120

# Candidate clusters wider than this are too diffuse to treat as one feed.
MAX_CANDIDATE_SPREAD_MINUTES = 180

# Selected consensus slots closer than this are treated as competing
# explanations for the same real-world feed.
SELECTION_CONFLICT_WINDOW_MINUTES = 75

# Tighter candidate slots are preferred when support is equal.
SPREAD_PENALTY_PER_HOUR = 0.25

# --- Availability floor ---

MIN_CONSENSUS_MODELS = 2

# --- Research baseline constants ---

LOCKSTEP_MATCH_WINDOW_MINUTES = 90


@dataclass(frozen=True)
class CandidateCluster:
    """One majority-supported candidate feed slot."""

    time: datetime
    volume_oz: float
    support: int
    spread_minutes: float
    models: tuple[str, ...]
    point_key: tuple[str, ...]


def run_consensus_blend(
    base_forecasts: list[Forecast],
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Blend scripted forecasts into a single consensus forecast.

    Args:
        base_forecasts: Forecasts from the scripted model lineup.
        history: Bottle-centered feed events up to the cutoff.
        cutoff: Latest observed activity time.
        horizon_hours: Forecast window in hours.

    Returns:
        A single consensus Forecast.
    """
    component_forecasts = {
        forecast.slug: forecast
        for forecast in base_forecasts
        if forecast.available and forecast.points
    }
    unavailable_components = {
        forecast.slug: forecast.error_message or "unavailable"
        for forecast in base_forecasts
        if not forecast.available
    }

    if len(component_forecasts) < MIN_CONSENSUS_MODELS:
        return Forecast(
            name=MODEL_NAME,
            slug=MODEL_SLUG,
            points=[],
            methodology=MODEL_METHODOLOGY,
            diagnostics={
                "component_models": list(component_forecasts),
                "unavailable_components": unavailable_components,
            },
            available=False,
            error_message=(
                f"Consensus Blend needs at least {MIN_CONSENSUS_MODELS} "
                "component forecasts."
            ),
        )

    points, selector_diagnostics = _blend_by_sequence_selection(
        component_forecasts, history
    )
    normalized_points = normalize_forecast_points(points, cutoff, horizon_hours)
    if not normalized_points:
        return Forecast(
            name=MODEL_NAME,
            slug=MODEL_SLUG,
            points=[],
            methodology=MODEL_METHODOLOGY,
            diagnostics={
                "component_models": list(component_forecasts),
                "component_forecast_counts": {
                    slug: len(forecast.points)
                    for slug, forecast in component_forecasts.items()
                },
                "unavailable_components": unavailable_components,
                **selector_diagnostics,
            },
            available=False,
            error_message="Consensus Blend found no majority-supported feed slots.",
        )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalized_points,
        methodology=MODEL_METHODOLOGY,
        diagnostics={
            "component_models": list(component_forecasts),
            "component_forecast_counts": {
                slug: len(forecast.points)
                for slug, forecast in component_forecasts.items()
            },
            "unavailable_components": unavailable_components,
            **selector_diagnostics,
        },
    )


def _blend_by_sequence_selection(
    component_forecasts: dict[str, Forecast],
    history: list[FeedEvent],
) -> tuple[list[ForecastPoint], dict]:
    """Build majority-supported candidate slots and select a sequence.

    The selector treats each candidate slot as one possible explanation for
    a real feed. Weighted interval scheduling keeps the strongest sequence
    of non-overlapping majority candidates instead of emitting every local
    agreement region.
    """
    candidates = generate_candidate_clusters(component_forecasts)
    selected = select_candidate_sequence(candidates)
    points = _candidates_to_forecast_points(selected, history)
    majority_floor = _majority_floor(len(component_forecasts))
    diagnostics = {
        "algorithm": "anchor-majority-sequence",
        "majority_floor": majority_floor,
        "anchor_radius_minutes": ANCHOR_RADIUS_MINUTES,
        "max_candidate_spread_minutes": MAX_CANDIDATE_SPREAD_MINUTES,
        "selection_conflict_window_minutes": SELECTION_CONFLICT_WINDOW_MINUTES,
        "spread_penalty_per_hour": SPREAD_PENALTY_PER_HOUR,
        "candidate_count": len(candidates),
        "selected_candidate_count": len(selected),
        "selected_candidates": [
            {
                "time": candidate.time.isoformat(sep=" "),
                "support": candidate.support,
                "spread_minutes": round(candidate.spread_minutes, 1),
                "models": list(candidate.models),
            }
            for candidate in selected
        ],
    }
    return points, diagnostics


def generate_candidate_clusters(
    component_forecasts: dict[str, Forecast],
    radius_minutes: int = ANCHOR_RADIUS_MINUTES,
    max_spread_minutes: int = MAX_CANDIDATE_SPREAD_MINUTES,
) -> list[CandidateCluster]:
    """Return majority-supported candidate feed slots.

    Each model contributes at most one point to a candidate slot: the point
    nearest the anchor time. Candidates are deduplicated by the exact set of
    model points they consume.
    """
    available = {
        slug: forecast
        for slug, forecast in component_forecasts.items()
        if forecast.points
    }
    if len(available) < MIN_CONSENSUS_MODELS:
        return []

    majority_floor = _majority_floor(len(available))
    tagged_points: list[tuple[datetime, float, str, int]] = []
    for slug, forecast in available.items():
        for index, point in enumerate(forecast.points):
            tagged_points.append((point.time, point.volume_oz, slug, index))

    candidates: list[CandidateCluster] = []
    seen_point_keys: set[tuple[str, ...]] = set()

    for anchor_time, _, _, _ in tagged_points:
        chosen_points: list[tuple[str, int, ForecastPoint]] = []
        for slug, forecast in available.items():
            nearby_points = [
                (
                    abs((point.time - anchor_time).total_seconds()) / 60.0,
                    index,
                    point,
                )
                for index, point in enumerate(forecast.points)
                if abs((point.time - anchor_time).total_seconds()) / 60.0
                <= radius_minutes
            ]
            if not nearby_points:
                continue

            _, index, point = min(nearby_points, key=lambda item: item[0])
            chosen_points.append((slug, index, point))

        if len(chosen_points) < majority_floor:
            continue

        point_key = tuple(
            sorted(f"{slug}:{index}" for slug, index, _ in chosen_points)
        )
        if point_key in seen_point_keys:
            continue

        timestamps = np.array(
            [point.time.timestamp() for _, _, point in chosen_points],
            dtype=float,
        )
        spread_minutes = (
            float(np.max(timestamps)) - float(np.min(timestamps))
        ) / 60.0
        if spread_minutes > max_spread_minutes:
            continue

        volumes = np.array(
            [point.volume_oz for _, _, point in chosen_points], dtype=float
        )
        seen_point_keys.add(point_key)
        candidates.append(
            CandidateCluster(
                time=datetime.fromtimestamp(float(np.median(timestamps))),
                volume_oz=float(np.median(volumes)),
                support=len(chosen_points),
                spread_minutes=spread_minutes,
                models=tuple(sorted(slug for slug, _, _ in chosen_points)),
                point_key=point_key,
            )
        )

    return sorted(candidates, key=lambda candidate: candidate.time)


def select_candidate_sequence(
    candidates: list[CandidateCluster],
    conflict_minutes: int = SELECTION_CONFLICT_WINDOW_MINUTES,
    spread_penalty_per_hour: float = SPREAD_PENALTY_PER_HOUR,
    max_points: int | None = None,
) -> list[CandidateCluster]:
    """Select the best non-overlapping candidate sequence.

    A candidate's utility is driven primarily by majority support, with a
    small penalty for diffuse clusters. The selector can optionally impose
    a soft count budget via ``max_points``, though production currently
    leaves it unconstrained because the recent scorer favored that choice.
    """
    if not candidates:
        return []

    ordered_candidates = sorted(candidates, key=lambda candidate: candidate.time)
    candidate_times = [candidate.time for candidate in ordered_candidates]
    previous_compatible_index = [
        bisect_right(
            candidate_times,
            candidate.time - timedelta(minutes=conflict_minutes),
        )
        - 1
        for candidate in ordered_candidates
    ]

    def utility(candidate: CandidateCluster) -> float:
        return (
            candidate.support * 10.0
            - spread_penalty_per_hour * (candidate.spread_minutes / 60.0)
        )

    if max_points is None:
        best_scores = [0.0] * (len(ordered_candidates) + 1)
        for index in range(1, len(ordered_candidates) + 1):
            take_score = utility(ordered_candidates[index - 1]) + best_scores[
                previous_compatible_index[index - 1] + 1
            ]
            best_scores[index] = max(best_scores[index - 1], take_score)

        selected: list[CandidateCluster] = []
        index = len(ordered_candidates) - 1
        while index >= 0:
            take_score = utility(ordered_candidates[index]) + best_scores[
                previous_compatible_index[index] + 1
            ]
            if take_score > best_scores[index]:
                selected.append(ordered_candidates[index])
                index = previous_compatible_index[index]
            else:
                index -= 1
        return list(reversed(selected))

    best_scores = [
        [0.0] * (max_points + 1)
        for _ in range(len(ordered_candidates) + 1)
    ]
    choose_candidate = [
        [False] * (max_points + 1)
        for _ in range(len(ordered_candidates) + 1)
    ]
    for index in range(1, len(ordered_candidates) + 1):
        candidate_value = utility(ordered_candidates[index - 1])
        for count in range(max_points + 1):
            best_score = best_scores[index - 1][count]
            if count > 0:
                take_score = candidate_value + best_scores[
                    previous_compatible_index[index - 1] + 1
                ][count - 1]
                if take_score > best_score:
                    best_score = take_score
                    choose_candidate[index][count] = True
            best_scores[index][count] = best_score

    best_count = max(
        range(max_points + 1),
        key=lambda count: best_scores[len(ordered_candidates)][count],
    )
    selected: list[CandidateCluster] = []
    index = len(ordered_candidates)
    count = best_count
    while index > 0 and count >= 0:
        if choose_candidate[index][count]:
            selected.append(ordered_candidates[index - 1])
            index = previous_compatible_index[index - 1] + 1
            count -= 1
        else:
            index -= 1
    return list(reversed(selected))


def _candidates_to_forecast_points(
    selected_candidates: list[CandidateCluster],
    history: list[FeedEvent],
) -> list[ForecastPoint]:
    """Convert selected candidate slots to normalized forecast points."""
    points: list[ForecastPoint] = []
    for candidate in selected_candidates:
        previous_time = points[-1].time if points else history[-1].time
        gap_hours = max(
            (candidate.time - previous_time).total_seconds() / 3600.0,
            MIN_INTERVAL_HOURS,
        )
        points.append(
            ForecastPoint(
                time=candidate.time,
                volume_oz=candidate.volume_oz,
                gap_hours=gap_hours,
            )
        )
    return points


def _majority_floor(component_count: int) -> int:
    """Return the simple-majority support floor."""
    return component_count // 2 + 1


def _blend_lockstep(
    component_forecasts: dict[str, Forecast],
    history: list[FeedEvent],
) -> tuple[list[ForecastPoint], int]:
    """Blend component forecasts using the legacy lockstep walk.

    This remains available as the research baseline and fallback
    comparison point for future selector tuning.
    """
    component_indices = {slug: 0 for slug in component_forecasts}
    points: list[ForecastPoint] = []
    skipped_outliers = 0
    match_window = timedelta(minutes=LOCKSTEP_MATCH_WINDOW_MINUTES)

    while True:
        next_candidates = [
            (slug, forecast.points[component_indices[slug]])
            for slug, forecast in component_forecasts.items()
            if component_indices[slug] < len(forecast.points)
        ]
        if len(next_candidates) < 2:
            break

        candidate_timestamps = np.array(
            [point.time.timestamp() for _, point in next_candidates],
            dtype=float,
        )
        anchor_time = datetime.fromtimestamp(
            float(np.median(candidate_timestamps))
        )
        cluster_start = anchor_time - match_window
        cluster_end = anchor_time + match_window

        leading_outliers = [
            slug
            for slug, point in next_candidates
            if point.time < cluster_start
        ]
        for slug in leading_outliers:
            component_indices[slug] += 1
            skipped_outliers += 1

        if leading_outliers:
            continue

        cluster = [
            (slug, point)
            for slug, point in next_candidates
            if cluster_start <= point.time <= cluster_end
        ]
        if len(cluster) < 2:
            earliest_slug = min(
                next_candidates, key=lambda item: item[1].time
            )[0]
            component_indices[earliest_slug] += 1
            skipped_outliers += 1
            continue

        timestamp_values = np.array(
            [point.time.timestamp() for _, point in cluster],
            dtype=float,
        )
        consensus_time = datetime.fromtimestamp(
            float(np.median(timestamp_values))
        )
        volume_values = np.array(
            [point.volume_oz for _, point in cluster], dtype=float
        )
        previous_time = points[-1].time if points else history[-1].time
        gap_hours = max(
            (consensus_time - previous_time).total_seconds() / 3600,
            MIN_INTERVAL_HOURS,
        )
        points.append(
            ForecastPoint(
                time=consensus_time,
                volume_oz=float(np.mean(volume_values)),
                gap_hours=gap_hours,
            )
        )
        for slug, _ in cluster:
            component_indices[slug] += 1

    return points, skipped_outliers
