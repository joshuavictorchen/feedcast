"""Register the scripted models and build the scripted consensus forecast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

from feedcast.data import (
    Activity,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    FeedEvent,
    Forecast,
    ForecastPoint,
    MIN_INTERVAL_HOURS,
    build_feed_events,
)
from .gap_conditional import (
    MODEL_METHODOLOGY as GAP_CONDITIONAL_METHODOLOGY,
    MODEL_NAME as GAP_CONDITIONAL_NAME,
    MODEL_SLUG as GAP_CONDITIONAL_SLUG,
    forecast_gap_conditional,
)
from .phase_nowcast import (
    MODEL_METHODOLOGY as PHASE_NOWCAST_METHODOLOGY,
    MODEL_NAME as PHASE_NOWCAST_NAME,
    MODEL_SLUG as PHASE_NOWCAST_SLUG,
    forecast_phase_nowcast_hybrid,
)
from .recent_cadence import (
    MODEL_METHODOLOGY as RECENT_CADENCE_METHODOLOGY,
    MODEL_NAME as RECENT_CADENCE_NAME,
    MODEL_SLUG as RECENT_CADENCE_SLUG,
    forecast_recent_cadence,
)
from .analog_trajectory import (
    MODEL_METHODOLOGY as ANALOG_TRAJECTORY_METHODOLOGY,
    MODEL_NAME as ANALOG_TRAJECTORY_NAME,
    MODEL_SLUG as ANALOG_TRAJECTORY_SLUG,
    forecast_analog_trajectory,
)
from .latent_hunger import (
    MODEL_METHODOLOGY as LATENT_HUNGER_METHODOLOGY,
    MODEL_NAME as LATENT_HUNGER_NAME,
    MODEL_SLUG as LATENT_HUNGER_SLUG,
    forecast_latent_hunger,
)
from .slot_drift import (
    MODEL_METHODOLOGY as SLOT_DRIFT_METHODOLOGY,
    MODEL_NAME as SLOT_DRIFT_NAME,
    MODEL_SLUG as SLOT_DRIFT_SLUG,
    forecast_slot_drift,
)
from .shared import (
    CONSENSUS_MATCH_WINDOW_MINUTES,
    ForecastUnavailable,
    normalize_forecast_points,
)

CONSENSUS_BLEND_NAME = "Consensus Blend"
CONSENSUS_BLEND_SLUG = "consensus_blend"
CONSENSUS_BLEND_METHODOLOGY = """\
Median-timestamp ensemble across the scripted base models. It does
not align forecasts by feed index, because different models may
emit different numbers of future feeds. Instead, on each step it
takes the next unconsumed point from every available model,
computes the median timestamp as an anchor, and forms a cluster
from points within +/- 90 minutes of that anchor.

Points that fall earlier than the cluster window are discarded as
leading outliers. If fewer than two models fall into the current
cluster, the earliest candidate is discarded and the procedure
retries. Once a cluster contains at least two models, the
consensus point uses the median timestamp and mean volume across
that cluster, with its gap measured from the previous consensus
point. The process repeats until fewer than two models have
points left. This lets the blend stay robust when one model
predicts an extra snack feed or drifts earlier/later than the
others."""

ModelFn = Callable[[list[FeedEvent], datetime, int], Forecast]


@dataclass(frozen=True)
class ModelSpec:
    """One scripted model definition."""

    name: str
    slug: str
    methodology: str
    merge_window_minutes: int | None
    forecast_fn: ModelFn


MODELS = [
    ModelSpec(
        name=SLOT_DRIFT_NAME,
        slug=SLOT_DRIFT_SLUG,
        methodology=SLOT_DRIFT_METHODOLOGY,
        merge_window_minutes=None,
        forecast_fn=forecast_slot_drift,
    ),
    ModelSpec(
        name=ANALOG_TRAJECTORY_NAME,
        slug=ANALOG_TRAJECTORY_SLUG,
        methodology=ANALOG_TRAJECTORY_METHODOLOGY,
        merge_window_minutes=None,
        forecast_fn=forecast_analog_trajectory,
    ),
    ModelSpec(
        name=LATENT_HUNGER_NAME,
        slug=LATENT_HUNGER_SLUG,
        methodology=LATENT_HUNGER_METHODOLOGY,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
        forecast_fn=forecast_latent_hunger,
    ),
    ModelSpec(
        name=RECENT_CADENCE_NAME,
        slug=RECENT_CADENCE_SLUG,
        methodology=RECENT_CADENCE_METHODOLOGY,
        merge_window_minutes=None,
        forecast_fn=forecast_recent_cadence,
    ),
    ModelSpec(
        name=PHASE_NOWCAST_NAME,
        slug=PHASE_NOWCAST_SLUG,
        methodology=PHASE_NOWCAST_METHODOLOGY,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
        forecast_fn=forecast_phase_nowcast_hybrid,
    ),
    ModelSpec(
        name=GAP_CONDITIONAL_NAME,
        slug=GAP_CONDITIONAL_SLUG,
        methodology=GAP_CONDITIONAL_METHODOLOGY,
        merge_window_minutes=DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
        forecast_fn=forecast_gap_conditional,
    ),
]

STATIC_FEATURED_TIEBREAKER = [
    SLOT_DRIFT_SLUG,
    ANALOG_TRAJECTORY_SLUG,
    LATENT_HUNGER_SLUG,
    PHASE_NOWCAST_SLUG,
    GAP_CONDITIONAL_SLUG,
    RECENT_CADENCE_SLUG,
]
FEATURED_DEFAULT = CONSENSUS_BLEND_SLUG


def build_event_cache(
    activities: list[Activity],
) -> dict[int | None, list[FeedEvent]]:
    """Build the event histories needed by the scripted model lineup."""
    event_cache: dict[int | None, list[FeedEvent]] = {}
    for spec in MODELS:
        if spec.merge_window_minutes not in event_cache:
            event_cache[spec.merge_window_minutes] = build_feed_events(
                activities,
                spec.merge_window_minutes,
            )
    return event_cache


def run_all_models_from_cache(
    event_cache: dict[int | None, list[FeedEvent]],
    cutoff: datetime,
    horizon_hours: int,
) -> list[Forecast]:
    """Run the scripted model lineup against one cutoff using a cached event map."""
    forecasts: list[Forecast] = []
    for spec in MODELS:
        history = [
            event
            for event in event_cache[spec.merge_window_minutes]
            if event.time <= cutoff
        ]
        try:
            forecasts.append(spec.forecast_fn(history, cutoff, horizon_hours))
        except ForecastUnavailable as error:
            forecasts.append(
                Forecast(
                    name=spec.name,
                    slug=spec.slug,
                    points=[],
                    methodology=spec.methodology,
                    diagnostics={},
                    available=False,
                    error_message=str(error),
                )
            )
    return forecasts


def run_consensus_blend(
    base_forecasts: list[Forecast],
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Blend the scripted forecasts into a consensus forecast."""
    scripted_slugs = {spec.slug for spec in MODELS}
    component_forecasts = {
        forecast.slug: forecast
        for forecast in base_forecasts
        if forecast.slug in scripted_slugs and forecast.available and forecast.points
    }
    unavailable_components = {
        forecast.slug: forecast.error_message or "unavailable"
        for forecast in base_forecasts
        if forecast.slug in scripted_slugs and not forecast.available
    }
    if len(component_forecasts) < 2:
        return Forecast(
            name=CONSENSUS_BLEND_NAME,
            slug=CONSENSUS_BLEND_SLUG,
            points=[],
            methodology=CONSENSUS_BLEND_METHODOLOGY,
            diagnostics={
                "component_models": list(component_forecasts),
                "unavailable_components": unavailable_components,
            },
            available=False,
            error_message="Consensus Blend needs at least two component forecasts.",
        )

    points, skipped_outliers = _blend_consensus_points_by_time(
        component_forecasts,
        history,
        cutoff,
        horizon_hours,
    )
    return Forecast(
        name=CONSENSUS_BLEND_NAME,
        slug=CONSENSUS_BLEND_SLUG,
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=CONSENSUS_BLEND_METHODOLOGY,
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


def select_featured_forecast(
    forecasts: list[Forecast],
    default_slug: str = FEATURED_DEFAULT,
) -> str:
    """Choose the featured forecast slug.

    Args:
        forecasts: Scripted forecasts considered for featuring.
        default_slug: Configured default forecast slug.

    Returns:
        Slug of the featured forecast.

    Raises:
        ForecastUnavailable: If nothing available can be featured.
    """
    available_slugs = {
        forecast.slug
        for forecast in forecasts
        if forecast.available and forecast.points
    }
    if default_slug in available_slugs:
        return default_slug

    for slug in STATIC_FEATURED_TIEBREAKER:
        if slug in available_slugs:
            return slug

    raise ForecastUnavailable("No scripted forecast is available to feature.")


def _blend_consensus_points_by_time(
    component_forecasts: dict[str, Forecast],
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> tuple[list[ForecastPoint], int]:
    """Blend component forecasts using time-based grouping.

    The algorithm walks through all component models in lockstep. On each
    iteration it:

      1. Collects the next unconsumed point from every model.
      2. Computes the median timestamp as an anchor.
      3. Discards points that fall before the anchor window (leading outliers).
      4. Groups the remaining points within +/- CONSENSUS_MATCH_WINDOW_MINUTES
         of the anchor into a cluster.
      5. If the cluster has >= 2 models, emits a consensus point at the median
         time with the mean volume. Otherwise, discards the earliest candidate
         and retries.

    The loop ends when fewer than 2 models have points remaining.
    """
    del cutoff, horizon_hours

    component_indices = {slug: 0 for slug in component_forecasts}
    points: list[ForecastPoint] = []
    skipped_outliers = 0
    match_window = timedelta(minutes=CONSENSUS_MATCH_WINDOW_MINUTES)

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
        anchor_time = datetime.fromtimestamp(float(np.median(candidate_timestamps)))
        cluster_start = anchor_time - match_window
        cluster_end = anchor_time + match_window

        # Any point that falls before the window is a leading outlier — skip it
        # and re-anchor on the next iteration.
        leading_outliers = [
            slug for slug, point in next_candidates if point.time < cluster_start
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
            # Not enough agreement — drop the earliest candidate and retry.
            earliest_slug = min(next_candidates, key=lambda item: item[1].time)[0]
            component_indices[earliest_slug] += 1
            skipped_outliers += 1
            continue

        # Emit the consensus point: median time, mean volume.
        timestamp_values = np.array(
            [point.time.timestamp() for _, point in cluster],
            dtype=float,
        )
        consensus_time = datetime.fromtimestamp(float(np.median(timestamp_values)))
        volume_values = np.array([point.volume_oz for _, point in cluster], dtype=float)
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
