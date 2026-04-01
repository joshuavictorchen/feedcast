"""Consensus Blend forecast model.

The production runtime collapses each component model's predictions into
episodes, builds immutable majority-supported candidate feed slots
around each episode-level prediction, then solves an exact set-packing
problem that picks the best non-overlapping sequence without reusing
any model prediction twice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import combinations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from feedcast.clustering import group_into_episodes
from feedcast.data import FeedEvent, Forecast, ForecastPoint
from feedcast.models.shared import load_methodology, normalize_forecast_points

MODEL_NAME = "Consensus Blend"
MODEL_SLUG = "consensus_blend"
MODEL_METHODOLOGY = load_methodology(__file__)

# --- Production selector constants ---

# Candidate slots are built around every model prediction. Keep the anchor
# radius wide enough to capture legitimate multi-model agreement regions;
# tighter filtering happens via the spread cap and exact selector.
ANCHOR_RADIUS_MINUTES = 120

# Candidate slots wider than this are too diffuse to treat as one feed.
MAX_CANDIDATE_SPREAD_MINUTES = 150

# Selected consensus slots closer than this are competing explanations for
# the same real feed. Canonical tuning currently favors a wider conflict
# window than the raw gap context alone would suggest: stronger duplicate
# suppression improves timing more than it harms close-episode recall on
# the current ensemble.
SELECTION_CONFLICT_WINDOW_MINUTES = 135

# Support is the primary signal. Spread breaks ties in favor of tighter slots.
SPREAD_PENALTY_PER_HOUR = 0.25

# --- Availability floor ---

MIN_CONSENSUS_MODELS = 2

# Minimum gap between feeds when computing gap_hours for forecast points.
MIN_INTERVAL_HOURS = 1.5


@dataclass(frozen=True)
class CandidateCluster:
    """One immutable majority-supported candidate feed slot."""

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
    """Build candidate slots and select an exact non-overlapping sequence."""
    # Collapse each model's predictions into episodes before voting.
    # This prevents cluster-internal predictions from creating spurious
    # candidate slots or inflating model agreement.
    collapsed_forecasts = _collapse_forecast_dict(component_forecasts)
    majority_floor = _majority_floor(len(collapsed_forecasts))
    candidates = generate_candidate_clusters(collapsed_forecasts)
    selected = select_candidate_sequence(candidates, majority_floor)
    points = _candidates_to_forecast_points(selected, history)
    diagnostics = {
        "algorithm": "exact-majority-set-packing",
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
    """Return immutable majority-supported candidate feed slots.

    For each anchor time, the generator enumerates every majority-sized
    model subset and asks each model in that subset for its nearest point
    inside the shared radius. Candidates are deduplicated by the exact set
    of model points they consume.
    """
    available = {
        slug: forecast
        for slug, forecast in component_forecasts.items()
        if forecast.points
    }
    if len(available) < MIN_CONSENSUS_MODELS:
        return []

    majority_floor = _majority_floor(len(available))
    all_model_slugs = sorted(available)
    anchor_times = [
        point.time for forecast in available.values() for point in forecast.points
    ]

    candidates: list[CandidateCluster] = []
    seen_point_keys: set[tuple[str, ...]] = set()

    for anchor_time in anchor_times:
        for subset_size in range(majority_floor, len(all_model_slugs) + 1):
            for subset in combinations(all_model_slugs, subset_size):
                candidate = _build_candidate_for_anchor(
                    anchor_time=anchor_time,
                    subset=subset,
                    available=available,
                    radius_minutes=radius_minutes,
                    max_spread_minutes=max_spread_minutes,
                )
                if candidate is None or candidate.point_key in seen_point_keys:
                    continue
                seen_point_keys.add(candidate.point_key)
                candidates.append(candidate)

    return sorted(candidates, key=lambda candidate: candidate.time)


def _build_candidate_for_anchor(
    anchor_time: datetime,
    subset: tuple[str, ...],
    available: dict[str, Forecast],
    radius_minutes: int,
    max_spread_minutes: int,
) -> CandidateCluster | None:
    """Build one candidate slot for one anchor and one model subset."""
    chosen_points: list[tuple[str, int, ForecastPoint]] = []
    for slug in subset:
        nearest = _nearest_point_within_radius(
            forecast=available[slug],
            anchor_time=anchor_time,
            radius_minutes=radius_minutes,
        )
        if nearest is None:
            return None
        chosen_points.append((slug, nearest[0], nearest[1]))

    chosen_points.sort(key=lambda item: f"{item[0]}:{item[1]}")
    timestamps = np.array(
        [point.time.timestamp() for _, _, point in chosen_points],
        dtype=float,
    )
    spread_minutes = (float(np.max(timestamps)) - float(np.min(timestamps))) / 60.0
    if spread_minutes > max_spread_minutes:
        return None

    volumes = np.array(
        [point.volume_oz for _, _, point in chosen_points],
        dtype=float,
    )
    return CandidateCluster(
        time=datetime.fromtimestamp(float(np.median(timestamps))),
        volume_oz=float(np.median(volumes)),
        support=len(chosen_points),
        spread_minutes=spread_minutes,
        models=tuple(slug for slug, _, _ in chosen_points),
        point_key=tuple(f"{slug}:{index}" for slug, index, _ in chosen_points),
    )


def _nearest_point_within_radius(
    forecast: Forecast,
    anchor_time: datetime,
    radius_minutes: int,
) -> tuple[int, ForecastPoint] | None:
    """Return the nearest forecast point inside the shared anchor radius."""
    best_index = -1
    best_point: ForecastPoint | None = None
    best_distance = float("inf")

    for index, point in enumerate(forecast.points):
        distance_minutes = abs((point.time - anchor_time).total_seconds()) / 60.0
        if distance_minutes <= radius_minutes and distance_minutes < best_distance:
            best_index = index
            best_point = point
            best_distance = distance_minutes

    if best_point is None:
        return None
    return best_index, best_point


# ===================================================================
# Sequence selection (exact set-packing via MILP)
# ===================================================================


def select_candidate_sequence(
    candidates: list[CandidateCluster],
    majority_floor: int,
    conflict_minutes: int = SELECTION_CONFLICT_WINDOW_MINUTES,
    spread_penalty_per_hour: float = SPREAD_PENALTY_PER_HOUR,
) -> list[CandidateCluster]:
    """Select the highest-utility valid candidate sequence exactly.

    The MILP has one binary decision per candidate. Constraints enforce:
      - each model point is used at most once
      - candidates inside the conflict window cannot both survive
    """
    eligible = [
        candidate for candidate in candidates if candidate.support >= majority_floor
    ]
    if not eligible:
        return []

    weights = np.array(
        [
            _candidate_utility(candidate, spread_penalty_per_hour)
            for candidate in eligible
        ],
        dtype=float,
    )

    rows: list[np.ndarray] = []
    lower_bounds: list[float] = []
    upper_bounds: list[float] = []
    point_memberships: dict[str, list[int]] = defaultdict(list)
    for candidate_index, candidate in enumerate(eligible):
        for point_key in candidate.point_key:
            point_memberships[point_key].append(candidate_index)

    for indices in point_memberships.values():
        row = np.zeros(len(eligible), dtype=float)
        row[indices] = 1.0
        rows.append(row)
        lower_bounds.append(-np.inf)
        upper_bounds.append(1.0)

    for left_index, left_candidate in enumerate(eligible):
        for right_index in range(left_index + 1, len(eligible)):
            right_candidate = eligible[right_index]
            gap_minutes = (
                abs((left_candidate.time - right_candidate.time).total_seconds()) / 60.0
            )
            if gap_minutes >= conflict_minutes:
                continue
            row = np.zeros(len(eligible), dtype=float)
            row[left_index] = 1.0
            row[right_index] = 1.0
            rows.append(row)
            lower_bounds.append(-np.inf)
            upper_bounds.append(1.0)

    if not rows:
        return [
            candidate for candidate, weight in zip(eligible, weights) if weight > 0.0
        ]

    result = milp(
        c=-weights,
        integrality=np.ones(len(eligible), dtype=int),
        bounds=Bounds(
            lb=np.zeros(len(eligible), dtype=float),
            ub=np.ones(len(eligible), dtype=float),
        ),
        constraints=LinearConstraint(
            np.vstack(rows),
            np.array(lower_bounds, dtype=float),
            np.array(upper_bounds, dtype=float),
        ),
    )
    if not result.success:
        raise RuntimeError(
            f"Consensus Blend MILP failed: {result.message or 'unknown error'}"
        )

    selected = [eligible[index] for index, value in enumerate(result.x) if value > 0.5]
    return sorted(selected, key=lambda candidate: candidate.time)


def _candidate_utility(
    candidate: CandidateCluster,
    spread_penalty_per_hour: float,
) -> float:
    """Return the utility used by the exact selector."""
    return candidate.support * 10.0 - spread_penalty_per_hour * (
        candidate.spread_minutes / 60.0
    )


# ===================================================================
# Helpers
# ===================================================================


def _collapse_to_episode_points(
    points: list[ForecastPoint],
) -> list[ForecastPoint]:
    """Collapse forecast points into episode-level representatives.

    Groups close-together predictions into episodes using the shared
    cluster rule, then converts each episode back to a single
    ForecastPoint with the canonical timestamp and summed volume.
    """
    if not points:
        return []
    episodes = group_into_episodes(points)
    collapsed: list[ForecastPoint] = []
    for episode in episodes:
        previous_time = collapsed[-1].time if collapsed else None
        gap_hours = (
            (episode.time - previous_time).total_seconds() / 3600.0
            if previous_time is not None
            else 0.0
        )
        collapsed.append(
            ForecastPoint(
                time=episode.time,
                volume_oz=episode.volume_oz,
                gap_hours=gap_hours,
            )
        )
    return collapsed


def _collapse_forecast_dict(
    forecasts: dict[str, Forecast],
) -> dict[str, Forecast]:
    """Return a new dict with each forecast's points collapsed into episodes.

    Used by the blend and research sweep to pre-collapse model predictions
    before candidate generation.
    """
    return {
        slug: replace(forecast, points=_collapse_to_episode_points(forecast.points))
        for slug, forecast in forecasts.items()
    }


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
