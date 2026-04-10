"""Analog Trajectory Retrieval forecast model.

Predicts the next 24 hours by finding similar historical states and
averaging their actual future trajectories. See methodology.md and
design.md in this directory for research and design decisions.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from feedcast.clustering import episodes_as_events
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
# All parameters below are tunable via analysis.py. Run the analysis
# script after new exports to validate or update these values.

# Per-feature weights for weighted Euclidean distance.
# Order: last_gap, mean_gap, last_volume, mean_volume, sin_hour, cos_hour.
# Higher weight = more influence on neighbor selection.
# "gap_hour" profile: gap and hour-of-day are the strongest retrieval
# cues, with volume deemphasized. On the current export the baby's
# timing structure (gap cadence and time-of-day) separates analogs more
# sharply than volume, which has grown noisier as patterns consolidate.
FEATURE_WEIGHTS = np.array([2.0, 2.0, 0.5, 0.5, 2.0, 2.0])

# Lookback window for rolling mean features (hours).
# Events within this window contribute to mean_gap and mean_volume.
# 24h widens rolling means to capture more of the current feeding
# rhythm. On the current export the baby's patterns benefit from
# broader context as gaps lengthen and the schedule consolidates.
LOOKBACK_HOURS = 24

# Number of nearest neighbors to retrieve.
# k=7 wins the widened full canonical replay sweep on the current
# export, slightly improving timing over the smaller neighborhoods.
K_NEIGHBORS = 7

# Minimum number of historical states with complete trajectories.
MIN_COMPLETE_STATES = 10

# Minimum prior events needed to compute features for a single state.
MIN_PRIOR_EVENTS = 3

# Half-life for recency weighting of neighbor states (hours).
# 120h keeps useful multi-day analogs in play while still preferring
# recent states. Confirmed as the canonical sweep winner on the current
# export at the new lookback and feature-weight settings.
RECENCY_HALF_LIFE_HOURS = 120

# Trajectory length aggregation method: "median" or "mean".
# "median" remains best under canonical replay.
TRAJECTORY_LENGTH_METHOD = "median"

# Trajectory alignment method: "gap" or "time_offset".
# "gap" blends inter-event gaps step-by-step and rolls forward.
# "time_offset" blends absolute offsets from the state event and
# positions feeds relative to cutoff. Canonical replay still prefers
# gap alignment on the current export.
ALIGNMENT = "gap"

# History source for state construction: "raw" or "episode".
# "raw" keeps every bottle event. "episode" collapses close-together
# bottle feeds into single feeding episodes before building states.
# Episode history materially improves both internal diagnostics and the
# canonical replay headline on the current export.
HISTORY_MODE = "episode"

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
    _validate_alignment(ALIGNMENT)
    _validate_history_mode(HISTORY_MODE)

    history = _build_history_events(activities, cutoff)

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
        query_normed,
        query["time"],
        states_normed,
        complete_states,
    )

    # Blend neighbor trajectories into a forecast.
    # The cutoff may be later than the last history event (e.g., if a
    # breastfeed ended after the last bottle, or if episode mode anchors
    # the last state at the start of a clustered feed). The blended
    # trajectories measure time from the last history event to the next
    # one, so we subtract elapsed time already spent since that anchor.
    elapsed_since_last_history_event = (
        cutoff - history[-1].time
    ).total_seconds() / 3600
    points = _blend_trajectories(
        neighbors,
        cutoff,
        horizon_hours,
        elapsed_since_last_history_event,
    )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=MODEL_METHODOLOGY,
        diagnostics=_build_diagnostics(
            query,
            neighbors,
            complete_states,
            feature_means,
            feature_stds,
            elapsed_since_last_history_event,
        ),
    )


def _build_history_events(
    activities: list[Activity],
    cutoff: datetime,
) -> list[FeedEvent]:
    """Build the event history used for analog states and query features."""
    raw_history = [
        event
        for event in build_feed_events(activities, merge_window_minutes=None)
        if event.time <= cutoff
    ]
    if HISTORY_MODE == "raw":
        return raw_history
    return episodes_as_events(raw_history)


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
        future_events = [e for e in history[index + 1 :] if e.time <= future_end]

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
    lookback_hours: float | None = None,
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
    if lookback_hours is None:
        lookback_hours = LOOKBACK_HOURS

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
    elapsed_since_last_history_event: float,
) -> list[ForecastPoint]:
    """Blend neighbor trajectories into forecast points.

    In "gap" alignment (default), each trajectory is a sequence of
    inter-event gaps. Blended gaps are rolled forward from the cutoff.

    In "time_offset" alignment, each trajectory is a sequence of
    absolute offsets from the state event time. Blended offsets are
    positioned relative to the cutoff (adjusted for elapsed time).

    The elapsed_since_last_history_event adjustment accounts for time
    already passed between the last history event and the cutoff.
    """
    use_offsets = ALIGNMENT == "time_offset"

    # Extract trajectories from each neighbor.
    # Gap mode: (gap_hours, volume) per step.
    # Offset mode: (offset_hours_from_state, volume) per step.
    trajectories: list[list[tuple[float, float]]] = []
    weights: list[float] = []

    for neighbor in neighbors:
        state = neighbor["state"]
        traj: list[tuple[float, float]] = []
        for j, future_event in enumerate(state["future_events"]):
            if use_offsets:
                value = (future_event.time - state["time"]).total_seconds() / 3600
            else:
                previous_time = (
                    state["time"] if j == 0 else state["future_events"][j - 1].time
                )
                value = (future_event.time - previous_time).total_seconds() / 3600
            traj.append((value, future_event.volume_oz))
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
        step_values: list[float] = []
        step_volumes: list[float] = []
        step_weights: list[float] = []

        for traj_idx, traj in enumerate(trajectories):
            if step < len(traj):
                value, volume = traj[step]
                step_values.append(value)
                step_volumes.append(volume)
                step_weights.append(float(weight_array[traj_idx]))

        if not step_values:
            break

        step_weight_array = np.array(step_weights, dtype=float)
        blended_value = float(np.average(step_values, weights=step_weight_array))
        blended_volume = max(
            float(np.average(step_volumes, weights=step_weight_array)), 0.5
        )

        if use_offsets:
            # Offsets are relative to state time (last bottle).
            # Subtract elapsed to get offset from cutoff.
            adjusted_offset = blended_value - elapsed_since_last_history_event
            if adjusted_offset <= 0:
                continue
            feed_time = cutoff + timedelta(hours=adjusted_offset)
            gap_hours = (feed_time - current_time).total_seconds() / 3600
            # Enforce minimum spacing between consecutive predictions.
            if gap_hours < 0.5:
                continue
        else:
            # Gap mode: roll forward from current position.
            if step == 0 and elapsed_since_last_history_event > 0:
                blended_value -= elapsed_since_last_history_event
            blended_value = max(blended_value, 0.5)
            feed_time = current_time + timedelta(hours=blended_value)
            gap_hours = blended_value

        if feed_time >= horizon_end:
            break

        points.append(
            ForecastPoint(
                time=feed_time,
                volume_oz=blended_volume,
                gap_hours=gap_hours,
            )
        )
        current_time = feed_time

    return points


def _validate_alignment(alignment: str) -> None:
    """Fail fast on unsupported trajectory alignment modes."""
    if alignment not in {"gap", "time_offset"}:
        raise ValueError(
            "ALIGNMENT must be 'gap' or 'time_offset'; " f"got {alignment!r}."
        )


def _validate_history_mode(history_mode: str) -> None:
    """Fail fast on unsupported state-history modes."""
    if history_mode not in {"raw", "episode"}:
        raise ValueError(
            "HISTORY_MODE must be 'raw' or 'episode'; " f"got {history_mode!r}."
        )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _build_diagnostics(
    query: dict,
    neighbors: list[dict],
    complete_states: list[dict],
    feature_means: np.ndarray,
    feature_stds: np.ndarray,
    elapsed_since_last_history_event: float,
) -> dict:
    """Build diagnostics dict for the report and debugging."""
    feature_names = [
        "last_gap",
        "mean_gap",
        "last_volume",
        "mean_volume",
        "sin_hour",
        "cos_hour",
    ]

    return {
        "complete_states": len(complete_states),
        "k_neighbors": K_NEIGHBORS,
        "lookback_hours": LOOKBACK_HOURS,
        "recency_half_life_hours": RECENCY_HALF_LIFE_HOURS,
        "trajectory_length_method": TRAJECTORY_LENGTH_METHOD,
        "alignment": ALIGNMENT,
        "history_mode": HISTORY_MODE,
        "feature_weights": {
            name: round(float(w), 3) for name, w in zip(feature_names, FEATURE_WEIGHTS)
        },
        "elapsed_since_last_history_event_hours": round(
            elapsed_since_last_history_event, 3
        ),
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
