"""Consensus Blend forecast model.

The production runtime builds majority-supported candidate feed slots
around each model prediction, then selects the best non-overlapping
sequence where each model prediction is used at most once.
"""

from __future__ import annotations

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
# proposed anchor time.  A 2-hour radius recovers majority agreement even
# when models disagree substantially.  The exhaustive selector handles
# the resulting point-sharing correctly via single-use enforcement.
ANCHOR_RADIUS_MINUTES = 120

# Candidate clusters wider than this are too diffuse to treat as one feed.
MAX_CANDIDATE_SPREAD_MINUTES = 180

# Selected consensus slots closer than this are treated as competing
# explanations for the same real-world feed.  Aligned with
# MIN_INTERVAL_HOURS (90 min), the physiological floor for distinct feeds.
SELECTION_CONFLICT_WINDOW_MINUTES = 90

# Tighter candidate slots are preferred when support is equal.
SPREAD_PENALTY_PER_HOUR = 0.25

# --- Availability floor ---

MIN_CONSENSUS_MODELS = 2


@dataclass(frozen=True)
class CandidateCluster:
    """One majority-supported candidate feed slot."""

    time: datetime
    volume_oz: float
    support: int
    spread_minutes: float
    models: tuple[str, ...]
    point_key: tuple[str, ...]
    # Per-point data retained for rebuild during single-use enforcement.
    point_timestamps: tuple[float, ...]
    point_volumes: tuple[float, ...]


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

    The selector treats each candidate slot as one possible explanation
    for a real feed.  Greedy selection by descending utility picks the
    strongest candidates first, enforcing both temporal non-overlap and
    single-use model points in one pass.
    """
    majority_floor = _majority_floor(len(component_forecasts))
    candidates = generate_candidate_clusters(component_forecasts)
    selected = select_candidate_sequence(candidates, majority_floor)
    points = _candidates_to_forecast_points(selected, history)
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


# ===================================================================
# Candidate generation
# ===================================================================


def generate_candidate_clusters(
    component_forecasts: dict[str, Forecast],
    radius_minutes: int = ANCHOR_RADIUS_MINUTES,
    max_spread_minutes: int = MAX_CANDIDATE_SPREAD_MINUTES,
) -> list[CandidateCluster]:
    """Return majority-supported candidate feed slots.

    Each model contributes at most one point to a candidate slot: the
    point nearest the anchor time.  Candidates are deduplicated by the
    exact set of model points they consume.
    """
    available = {
        slug: forecast
        for slug, forecast in component_forecasts.items()
        if forecast.points
    }
    if len(available) < MIN_CONSENSUS_MODELS:
        return []

    majority_floor = _majority_floor(len(available))

    # Collect every model prediction as a potential anchor.
    anchor_times: list[datetime] = []
    for forecast in available.values():
        for point in forecast.points:
            anchor_times.append(point.time)

    candidates: list[CandidateCluster] = []
    seen_point_keys: set[tuple[str, ...]] = set()

    for anchor_time in anchor_times:
        # Pull in the nearest prediction from each model within radius.
        chosen: list[tuple[str, int, float, float]] = []
        for slug, forecast in available.items():
            best_distance = float("inf")
            best_index = -1
            best_timestamp = 0.0
            best_volume = 0.0
            for index, point in enumerate(forecast.points):
                distance = abs(
                    (point.time - anchor_time).total_seconds()
                ) / 60.0
                if distance <= radius_minutes and distance < best_distance:
                    best_distance = distance
                    best_index = index
                    best_timestamp = point.time.timestamp()
                    best_volume = point.volume_oz
            if best_index >= 0:
                chosen.append((slug, best_index, best_timestamp, best_volume))

        if len(chosen) < majority_floor:
            continue

        # Sort so point_key, point_timestamps, and point_volumes are aligned.
        chosen.sort(key=lambda item: f"{item[0]}:{item[1]}")

        point_key = tuple(f"{slug}:{index}" for slug, index, _, _ in chosen)
        if point_key in seen_point_keys:
            continue

        timestamps = np.array([ts for _, _, ts, _ in chosen], dtype=float)
        volumes = np.array([vol for _, _, _, vol in chosen], dtype=float)
        spread = (float(np.max(timestamps)) - float(np.min(timestamps))) / 60.0
        if spread > max_spread_minutes:
            continue

        seen_point_keys.add(point_key)
        candidates.append(
            CandidateCluster(
                time=datetime.fromtimestamp(float(np.median(timestamps))),
                volume_oz=float(np.median(volumes)),
                support=len(chosen),
                spread_minutes=spread,
                models=tuple(sorted(slug for slug, _, _, _ in chosen)),
                point_key=point_key,
                point_timestamps=tuple(ts for _, _, ts, _ in chosen),
                point_volumes=tuple(vol for _, _, _, vol in chosen),
            )
        )

    return sorted(candidates, key=lambda c: c.time)


# ===================================================================
# Sequence selection (greedy with single-use enforcement)
# ===================================================================


def select_candidate_sequence(
    candidates: list[CandidateCluster],
    majority_floor: int,
    conflict_minutes: int = SELECTION_CONFLICT_WINDOW_MINUTES,
    spread_penalty_per_hour: float = SPREAD_PENALTY_PER_HOUR,
) -> list[CandidateCluster]:
    """Select a high-utility non-overlapping, non-reusing sequence.

    Uses backtracking search with upper-bound pruning over
    forward-ordered subsequences.  This is not globally optimal --
    it cannot discover sequences where an earlier candidate becomes
    valid only after a later candidate claims shared points -- but
    it covers the vast majority of practical cases and runs in
    milliseconds for ~17 candidates.

    Constraints enforced jointly:
      - Temporal non-overlap (conflict window).
      - Single-use model points (each ``slug:index`` claimed at most once).
      - Majority support after removing claimed points.

    Args:
        candidates: Candidate clusters from ``generate_candidate_clusters``.
        majority_floor: Minimum distinct models for a valid candidate.
        conflict_minutes: Temporal proximity that makes two candidates
            competing explanations for the same feed.
        spread_penalty_per_hour: Utility penalty per hour of intra-cluster
            spread.

    Returns:
        Selected candidates sorted chronologically.
    """
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda c: c.time)

    def utility(candidate: CandidateCluster) -> float:
        return (
            candidate.support * 10.0
            - spread_penalty_per_hour * (candidate.spread_minutes / 60.0)
        )

    # Suffix sums of original utilities for upper-bound pruning.
    suffix_utility = [0.0] * (len(ordered) + 1)
    for i in range(len(ordered) - 1, -1, -1):
        suffix_utility[i] = suffix_utility[i + 1] + utility(ordered[i])

    best: dict = {"utility": 0.0, "selection": []}

    def search(
        index: int,
        selected: list[CandidateCluster],
        claimed: frozenset[str],
        total_utility: float,
    ) -> None:
        if total_utility > best["utility"]:
            best["utility"] = total_utility
            best["selection"] = list(selected)

        for i in range(index, len(ordered)):
            # Pruning: remaining upper bound cannot beat current best.
            if total_utility + suffix_utility[i] <= best["utility"]:
                return

            candidate = ordered[i]

            # Single-use: find unclaimed contributing points.
            unclaimed_indices = [
                j
                for j, key in enumerate(candidate.point_key)
                if key not in claimed
            ]
            if len(unclaimed_indices) < majority_floor:
                continue

            # Rebuild the candidate from unclaimed evidence if needed.
            actual = candidate
            if len(unclaimed_indices) < len(candidate.point_key):
                actual = _rebuild_candidate(candidate, unclaimed_indices)

            # Temporal non-overlap with all previously selected.
            if _has_temporal_conflict(actual, selected, conflict_minutes):
                continue

            search(
                i + 1,
                selected + [actual],
                claimed | frozenset(actual.point_key),
                total_utility + utility(actual),
            )

    search(0, [], frozenset(), 0.0)
    return best["selection"]


def _rebuild_candidate(
    original: CandidateCluster,
    unclaimed_indices: list[int],
) -> CandidateCluster:
    """Rebuild a candidate from its unclaimed evidence only.

    Recomputes the median timestamp, median volume, support, spread,
    and model list from the subset of points that have not been claimed
    by a previously selected consensus feed.
    """
    timestamps = np.array(
        [original.point_timestamps[i] for i in unclaimed_indices],
        dtype=float,
    )
    volumes = np.array(
        [original.point_volumes[i] for i in unclaimed_indices],
        dtype=float,
    )
    spread = (
        (float(np.max(timestamps)) - float(np.min(timestamps))) / 60.0
        if len(timestamps) > 1
        else 0.0
    )
    return CandidateCluster(
        time=datetime.fromtimestamp(float(np.median(timestamps))),
        volume_oz=float(np.median(volumes)),
        support=len(unclaimed_indices),
        spread_minutes=spread,
        models=tuple(
            sorted(
                original.point_key[i].split(":")[0]
                for i in unclaimed_indices
            )
        ),
        point_key=tuple(original.point_key[i] for i in unclaimed_indices),
        point_timestamps=tuple(
            original.point_timestamps[i] for i in unclaimed_indices
        ),
        point_volumes=tuple(
            original.point_volumes[i] for i in unclaimed_indices
        ),
    )


def _has_temporal_conflict(
    candidate: CandidateCluster,
    selected: list[CandidateCluster],
    conflict_minutes: int,
) -> bool:
    """Return True if the candidate is too close to any selected candidate."""
    for existing in selected:
        gap_minutes = (
            abs((candidate.time - existing.time).total_seconds()) / 60.0
        )
        if gap_minutes < conflict_minutes:
            return True
    return False


# ===================================================================
# Helpers
# ===================================================================


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
