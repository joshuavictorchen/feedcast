"""Consensus Blend forecast model.

The production runtime uses the lockstep median-timestamp algorithm.
A pool-then-cluster candidate generator is available for research but
is not yet validated against the retrospective scorer.  See research.py
and methodology.md for the planned replacement path.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

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

# --- Tuning parameters (production lockstep) ---

# Half-width of the window used to group model predictions into one feed.
MATCH_WINDOW_MINUTES = 90

# Minimum models required for any consensus point.
MIN_CONSENSUS_MODELS = 2

# --- Tuning parameters (candidate generator, research only) ---

# Maximum pairwise distance (minutes) for complete-linkage clustering.
CLUSTER_DISTANCE_MINUTES = 60

# Minimum distinct models for a candidate cluster to survive filtering.
MIN_CLUSTER_MODELS = 2


# ===================================================================
# Public entry point
# ===================================================================


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

    points, skipped_outliers = _blend_lockstep(
        component_forecasts, history,
    )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=MODEL_METHODOLOGY,
        diagnostics={
            "component_models": list(component_forecasts),
            "component_forecast_counts": {
                slug: len(forecast.points)
                for slug, forecast in component_forecasts.items()
            },
            "unavailable_components": unavailable_components,
            "skipped_outlier_points": skipped_outliers,
        },
    )


# ===================================================================
# Production blend: lockstep median-timestamp walk
# ===================================================================


def _blend_lockstep(
    component_forecasts: dict[str, Forecast],
    history: list[FeedEvent],
) -> tuple[list[ForecastPoint], int]:
    """Blend component forecasts using lockstep time-based grouping.

    The algorithm walks through all component models in lockstep.  On each
    iteration it:

      1. Collects the next unconsumed point from every model.
      2. Computes the median timestamp as an anchor.
      3. Discards points that fall before the anchor window (leading outliers).
      4. Groups the remaining points within +/- MATCH_WINDOW_MINUTES of the
         anchor into a cluster.
      5. If the cluster has >= 2 models, emits a consensus point at the
         median time with the mean volume.  Otherwise, discards the earliest
         candidate and retries.

    The loop ends when fewer than 2 models have points remaining.
    """
    component_indices = {slug: 0 for slug in component_forecasts}
    points: list[ForecastPoint] = []
    skipped_outliers = 0
    match_window = timedelta(minutes=MATCH_WINDOW_MINUTES)

    while True:
        # Gather the next unconsumed point from each model that still has one.
        next_candidates = [
            (slug, forecast.points[component_indices[slug]])
            for slug, forecast in component_forecasts.items()
            if component_indices[slug] < len(forecast.points)
        ]
        if len(next_candidates) < 2:
            break

        # Anchor the cluster window on the median of the candidate timestamps.
        candidate_timestamps = np.array(
            [point.time.timestamp() for _, point in next_candidates],
            dtype=float,
        )
        anchor_time = datetime.fromtimestamp(
            float(np.median(candidate_timestamps))
        )
        cluster_start = anchor_time - match_window
        cluster_end = anchor_time + match_window

        # Any point that falls before the window is a leading outlier --
        # skip it and re-anchor on the next iteration.
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

        # Form the cluster from points that fall within the window.
        cluster = [
            (slug, point)
            for slug, point in next_candidates
            if cluster_start <= point.time <= cluster_end
        ]
        if len(cluster) < 2:
            # Not enough agreement -- drop the earliest candidate and retry.
            earliest_slug = min(
                next_candidates, key=lambda item: item[1].time
            )[0]
            component_indices[earliest_slug] += 1
            skipped_outliers += 1
            continue

        # Emit the consensus point: median time, mean volume.
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


# ===================================================================
# Candidate generator: pool-then-cluster (research only)
# ===================================================================


def generate_candidate_clusters(
    component_forecasts: dict[str, Forecast],
) -> list[dict]:
    """Pool model predictions and cluster by temporal proximity.

    This is a candidate generator for research, not the production blend.
    Each returned cluster contains the models that contributed, the median
    timestamp, median volume, and cluster quality metrics (support, spread).
    A future sequence selector will choose the best non-conflicting subset.

    Args:
        component_forecasts: Available model forecasts keyed by slug.

    Returns:
        List of candidate cluster dicts, sorted chronologically.
    """
    # Pool all points tagged with source model.
    tagged_points: list[tuple[datetime, float, str]] = []
    for slug, forecast in component_forecasts.items():
        for point in forecast.points:
            tagged_points.append((point.time, point.volume_oz, slug))

    if len(tagged_points) < 2:
        return []

    # Cluster by timestamp proximity using complete linkage.
    timestamps = np.array(
        [t.timestamp() for t, _, _ in tagged_points], dtype=float
    )
    distance_minutes = (
        np.abs(timestamps[:, None] - timestamps[None, :]) / 60.0
    )
    condensed = squareform(distance_minutes)
    dendrogram = linkage(condensed, method="complete")
    labels = fcluster(
        dendrogram, t=CLUSTER_DISTANCE_MINUTES, criterion="distance"
    )

    # Group points by cluster label.
    raw_clusters: dict[int, list[tuple[datetime, float, str]]] = (
        defaultdict(list)
    )
    for index, label in enumerate(labels):
        raw_clusters[label].append(tagged_points[index])

    # Filter, deduplicate, and summarize each cluster.
    candidates: list[dict] = []
    for label in sorted(
        raw_clusters, key=lambda k: min(t for t, _, _ in raw_clusters[k])
    ):
        members = raw_clusters[label]
        distinct_models = {slug for _, _, slug in members}

        if len(distinct_models) < MIN_CLUSTER_MODELS:
            continue

        # Deduplicate: one point per model, keep closest to cluster median.
        median_ts = float(
            np.median([t.timestamp() for t, _, _ in members])
        )
        model_groups: dict[str, list[tuple[datetime, float, str]]] = (
            defaultdict(list)
        )
        for member in members:
            model_groups[member[2]].append(member)

        deduped: list[tuple[datetime, float, str]] = []
        for slug, slug_points in model_groups.items():
            best = min(
                slug_points,
                key=lambda p: abs(p[0].timestamp() - median_ts),
            )
            deduped.append(best)

        times = np.array(
            [t.timestamp() for t, _, _ in deduped], dtype=float
        )
        volumes = np.array([v for _, v, _ in deduped], dtype=float)

        candidates.append(
            {
                "time": datetime.fromtimestamp(float(np.median(times))),
                "volume_oz": float(np.median(volumes)),
                "models": sorted(distinct_models),
                "support": len(distinct_models),
                "spread_minutes": round(
                    (float(np.max(times)) - float(np.min(times))) / 60, 1
                ),
            }
        )

    return candidates
