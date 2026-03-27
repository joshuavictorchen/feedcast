"""Analog Trajectory Retrieval forecast model.

Predicts the next 24 hours by finding similar historical states and
averaging their actual future trajectories. See methodology.md and
design.md in this directory for research and design decisions.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from feedcast.data import (
    Activity,
    FeedEvent,
    Forecast,
    ForecastPoint,
    build_feed_events,
    hour_of_day,
)
from feedcast.models.shared import (
    ForecastUnavailable,
    load_methodology,
    normalize_forecast_points,
)

MODEL_NAME = "Analog Trajectory"
MODEL_SLUG = "analog_trajectory"
MODEL_METHODOLOGY = load_methodology(__file__)

# --- Tuning parameters (model-specific) ---
# All parameters below are tunable via research.py. Run the research
# script after new exports to validate or update these values.

# Per-feature weights for weighted Euclidean distance.
# Order: last_gap, mean_gap, last_volume, mean_volume, sin_hour, cos_hour.
# Higher weight = more influence on neighbor selection.
# "hour_emphasis" profile: time-of-day dominates similarity; gap and
# volume features at equal lower weight. Selected via research.py
# joint sweep (fold-causal, full-trajectory evaluation).
FEATURE_WEIGHTS = np.array([1.0, 1.0, 1.0, 1.0, 2.0, 2.0])

# Lookback window for rolling mean features (hours).
# Events within this window contribute to mean_gap and mean_volume.
# 72h provides a stable 3-day rolling average. Selected via research.py
# joint sweep (fold-causal, full-trajectory evaluation).
LOOKBACK_HOURS = 72

# Number of nearest neighbors to retrieve.
# k=7 is the consistent top performer across weight profiles in the
# joint sweep. Selected via research.py (fold-causal, full-trajectory).
K_NEIGHBORS = 7

# Minimum number of historical states with complete trajectories.
MIN_COMPLETE_STATES = 10

# Minimum prior events needed to compute features for a single state.
MIN_PRIOR_EVENTS = 3

# Half-life for recency weighting of neighbor states (hours).
# 36h means a state from 1.5 days ago gets half the weight of an
# equivalent state right now. Selected via research.py sweep.
RECENCY_HALF_LIFE_HOURS = 36

# Trajectory length aggregation method: "median" or "mean".
TRAJECTORY_LENGTH_METHOD = "median"

# A trajectory is "complete" if it has at least one event this many
# hours past the state time. This avoids including states whose
# 24h future was cut short by the export boundary.
TRAJECTORY_COMPLETENESS_HOURS = 20


def forecast_analog_trajectory(
    activities: list[Activity],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Predict feeds by retrieving and averaging similar historical trajectories.

    Args:
        activities: Raw feeding activities from the export.
        cutoff: The latest observed activity time.
        horizon_hours: How many hours ahead to forecast.

    Returns:
        A Forecast with projected feed times and volumes.
    """
    # Build bottle-only events and filter to cutoff.
    history = [
        e for e in build_feed_events(activities, merge_window_minutes=None)
        if e.time <= cutoff
    ]

    # Build the library of historical states and their future trajectories.
    states = _build_state_library(history, cutoff, horizon_hours)
    complete_states = [s for s in states if s["complete"]]

    if len(complete_states) < MIN_COMPLETE_STATES:
        raise ForecastUnavailable(
            f"Analog Trajectory needs at least {MIN_COMPLETE_STATES} complete "
            f"historical states, found {len(complete_states)}."
        )

    # Build the query state from the latest event.
    query = _build_query_state(history, cutoff)

    # Normalize features across all complete states + the query.
    all_features = np.array([s["features"] for s in complete_states])
    feature_means = all_features.mean(axis=0)
    feature_stds = all_features.std(axis=0)
    feature_stds[feature_stds == 0] = 1.0
    query_normed = (query["features"] - feature_means) / feature_stds
    states_normed = (all_features - feature_means) / feature_stds

    # Find K nearest neighbors with recency + distance weighting.
    neighbors = _find_neighbors(
        query_normed, query["time"], states_normed, complete_states,
    )

    # Blend neighbor trajectories into a forecast.
    # The cutoff may be later than the last bottle event (e.g., if a
    # breastfeed ended after the last bottle). The blended gaps measure
    # time from last bottle to next bottle, so we subtract the elapsed
    # time since the last bottle from the first blended gap.
    elapsed_since_last_bottle = (
        cutoff - history[-1].time
    ).total_seconds() / 3600
    points = _blend_trajectories(
        neighbors, cutoff, horizon_hours, elapsed_since_last_bottle,
    )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=MODEL_METHODOLOGY,
        diagnostics=_build_diagnostics(
            query, neighbors, complete_states, feature_means, feature_stds,
            elapsed_since_last_bottle,
        ),
    )


# ---------------------------------------------------------------------------
# State library
# ---------------------------------------------------------------------------


def _build_state_library(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> list[dict]:
    """Build the library of historical states with their future trajectories.

    Each event (from MIN_PRIOR_EVENTS onward) becomes a candidate state.
    The "trajectory" is the sequence of events in the next horizon_hours.
    A state is "complete" if the trajectory extends at least
    TRAJECTORY_COMPLETENESS_HOURS past the state time.
    """
    states: list[dict] = []
    for index in range(MIN_PRIOR_EVENTS, len(history)):
        event = history[index]
        # Skip events after the cutoff (shouldn't happen, but guard).
        if event.time > cutoff:
            break

        features = _state_features(history, index)
        future_end = event.time + timedelta(hours=horizon_hours)
        future_events = [
            e for e in history[index + 1 :] if e.time <= future_end
        ]

        # A trajectory is complete if there's at least one future event
        # far enough out that we're confident we captured most of the day.
        has_late_event = any(
            e.time >= event.time + timedelta(hours=TRAJECTORY_COMPLETENESS_HOURS)
            for e in history[index + 1 :]
        )

        states.append(
            {
                "index": index,
                "time": event.time,
                "features": features,
                "future_events": future_events,
                "future_count": len(future_events),
                "complete": has_late_event and len(future_events) >= 3,
            }
        )

    return states


def _state_features(
    history: list[FeedEvent],
    index: int,
    lookback_hours: float = LOOKBACK_HOURS,
) -> np.ndarray:
    """Compute the feature vector for one historical state.

    Args:
        history: Full event history.
        index: Index of the event to compute features for.
        lookback_hours: Time window for rolling mean features. Events
            within this window contribute to mean_gap and mean_volume.

    Features (6 dimensions):
      0: last_gap     - gap before this event (hours)
      1: mean_gap     - mean of gaps within lookback window
      2: last_volume  - volume of this event (oz)
      3: mean_volume  - mean volume of events within lookback window
      4: sin_hour     - sin(2*pi*hour/24) for circular time encoding
      5: cos_hour     - cos(2*pi*hour/24) for circular time encoding
    """
    event = history[index]
    lookback_cutoff = event.time - timedelta(hours=lookback_hours)

    # Find the earliest event within the lookback window.
    lookback_start = index
    while lookback_start > 0 and history[lookback_start - 1].time >= lookback_cutoff:
        lookback_start -= 1

    # Collect gaps within the lookback window.
    gaps: list[float] = []
    for j in range(lookback_start, index + 1):
        if j > 0:
            gap = (history[j].time - history[j - 1].time).total_seconds() / 3600
            gaps.append(gap)

    # Collect volumes within the lookback window.
    volumes = [history[j].volume_oz for j in range(lookback_start, index + 1)]

    hour = hour_of_day(event.time)

    return np.array(
        [
            gaps[-1] if gaps else 3.0,
            float(np.mean(gaps)) if gaps else 3.0,
            event.volume_oz,
            float(np.mean(volumes)),
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
        ],
        dtype=float,
    )


def _build_query_state(
    history: list[FeedEvent],
    cutoff: datetime,
) -> dict:
    """Build the query state from the latest observed event."""
    last_index = len(history) - 1
    return {
        "index": last_index,
        "time": cutoff,
        "features": _state_features(history, last_index),
    }


# ---------------------------------------------------------------------------
# Neighbor retrieval
# ---------------------------------------------------------------------------


def _find_neighbors(
    query_normed: np.ndarray,
    query_time: datetime,
    states_normed: np.ndarray,
    complete_states: list[dict],
) -> list[dict]:
    """Find the K nearest neighbors with recency + distance weighting.

    Applies per-feature weights (FEATURE_WEIGHTS) to the normalized
    feature vectors before computing Euclidean distance. This is
    equivalent to weighted Euclidean distance:
    sqrt(sum(w_i * (x_i - y_i)^2)).

    Returns a list of neighbor dicts with keys: state, distance, weight.
    """
    decay = np.log(2) / RECENCY_HALF_LIFE_HOURS

    # Apply per-feature weights for weighted Euclidean distance.
    sqrt_weights = np.sqrt(FEATURE_WEIGHTS)
    query_weighted = query_normed * sqrt_weights
    states_weighted = states_normed * sqrt_weights

    candidates: list[tuple[int, float]] = []
    for i in range(len(complete_states)):
        distance = float(np.linalg.norm(query_weighted - states_weighted[i]))
        candidates.append((i, distance))

    candidates.sort(key=lambda x: x[1])
    nearest = candidates[:K_NEIGHBORS]

    neighbors: list[dict] = []
    for state_index, distance in nearest:
        state = complete_states[state_index]
        age_hours = (query_time - state["time"]).total_seconds() / 3600
        recency_weight = float(np.exp(-decay * max(age_hours, 0)))
        # Combined weight: recency / (distance + epsilon).
        # Epsilon prevents division by zero for exact matches.
        weight = recency_weight / (distance + 0.01)
        neighbors.append(
            {
                "state": state,
                "distance": distance,
                "recency_weight": recency_weight,
                "weight": weight,
            }
        )

    return neighbors


# ---------------------------------------------------------------------------
# Trajectory blending
# ---------------------------------------------------------------------------


def _blend_trajectories(
    neighbors: list[dict],
    cutoff: datetime,
    horizon_hours: int,
    elapsed_since_last_bottle: float,
) -> list[ForecastPoint]:
    """Blend neighbor trajectories into forecast points.

    Each neighbor's trajectory is represented as a sequence of (gap, volume)
    pairs. The blended forecast averages these gap-by-gap using neighbor
    weights, then rolls forward from the cutoff to produce absolute times.

    The first blended gap is reduced by elapsed_since_last_bottle to account
    for time already passed between the last bottle and the cutoff (which
    may differ when the latest activity is a breastfeed).
    """
    # Extract gap/volume trajectories from each neighbor.
    trajectories: list[list[tuple[float, float]]] = []
    weights: list[float] = []

    for neighbor in neighbors:
        state = neighbor["state"]
        traj: list[tuple[float, float]] = []
        for j, future_event in enumerate(state["future_events"]):
            previous_time = (
                state["time"] if j == 0 else state["future_events"][j - 1].time
            )
            gap = (future_event.time - previous_time).total_seconds() / 3600
            traj.append((gap, future_event.volume_oz))
        if traj:
            trajectories.append(traj)
            weights.append(neighbor["weight"])

    if not trajectories:
        return []

    weight_array = np.array(weights, dtype=float)

    # Aggregate trajectory lengths to determine forecast length.
    # Median avoids being pulled by outlier trajectories; mean gives
    # more weight to longer trajectories. Selected via research.
    traj_lengths = [len(t) for t in trajectories]
    if TRAJECTORY_LENGTH_METHOD == "median":
        forecast_length = int(np.median(traj_lengths))
    else:
        forecast_length = int(np.mean(traj_lengths))

    # Blend step by step.
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    current_time = cutoff
    points: list[ForecastPoint] = []

    for step in range(forecast_length):
        # Collect gap and volume from each trajectory that has this step.
        step_gaps: list[float] = []
        step_volumes: list[float] = []
        step_weights: list[float] = []

        for traj_idx, traj in enumerate(trajectories):
            if step < len(traj):
                gap, volume = traj[step]
                step_gaps.append(gap)
                step_volumes.append(volume)
                step_weights.append(float(weight_array[traj_idx]))

        if not step_gaps:
            break

        step_weight_array = np.array(step_weights, dtype=float)
        blended_gap = float(np.average(step_gaps, weights=step_weight_array))
        blended_volume = float(np.average(step_volumes, weights=step_weight_array))

        # On the first step, subtract time already elapsed since the last
        # bottle so the forecast starts from cutoff, not from the last bottle.
        if step == 0 and elapsed_since_last_bottle > 0:
            blended_gap = blended_gap - elapsed_since_last_bottle

        # Enforce minimum gap to avoid degenerate predictions.
        blended_gap = max(blended_gap, 0.5)
        blended_volume = max(blended_volume, 0.5)

        feed_time = current_time + timedelta(hours=blended_gap)
        if feed_time >= horizon_end:
            break

        points.append(
            ForecastPoint(
                time=feed_time,
                volume_oz=blended_volume,
                gap_hours=blended_gap,
            )
        )
        current_time = feed_time

    return points


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _build_diagnostics(
    query: dict,
    neighbors: list[dict],
    complete_states: list[dict],
    feature_means: np.ndarray,
    feature_stds: np.ndarray,
    elapsed_since_last_bottle: float,
) -> dict:
    """Build diagnostics dict for the report and debugging."""
    feature_names = [
        "last_gap", "mean_gap", "last_volume", "mean_volume",
        "sin_hour", "cos_hour",
    ]

    return {
        "complete_states": len(complete_states),
        "k_neighbors": K_NEIGHBORS,
        "lookback_hours": LOOKBACK_HOURS,
        "recency_half_life_hours": RECENCY_HALF_LIFE_HOURS,
        "trajectory_length_method": TRAJECTORY_LENGTH_METHOD,
        "feature_weights": {
            name: round(float(w), 3)
            for name, w in zip(feature_names, FEATURE_WEIGHTS)
        },
        "elapsed_since_last_bottle_hours": round(elapsed_since_last_bottle, 3),
        "query_features": {
            name: round(float(val), 3)
            for name, val in zip(feature_names, query["features"])
        },
        "feature_normalization": {
            name: {"mean": round(float(m), 3), "std": round(float(s), 3)}
            for name, m, s in zip(feature_names, feature_means, feature_stds)
        },
        "neighbors": [
            {
                "state_time": neighbor["state"]["time"].isoformat(),
                "distance": round(neighbor["distance"], 3),
                "recency_weight": round(neighbor["recency_weight"], 3),
                "weight": round(neighbor["weight"], 3),
                "trajectory_length": neighbor["state"]["future_count"],
            }
            for neighbor in neighbors
        ],
    }
