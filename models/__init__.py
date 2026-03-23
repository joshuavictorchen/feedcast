"""Scripted model registry and consensus blend."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

from data import (
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
from .shared import (
    CONSENSUS_MATCH_WINDOW_MINUTES,
    ForecastUnavailable,
    normalize_forecast_points,
)

CONSENSUS_BLEND_NAME = "Consensus Blend"
CONSENSUS_BLEND_SLUG = "consensus_blend"
CONSENSUS_BLEND_METHODOLOGY = (
    "Median-timestamp ensemble across the scripted base models. It groups upcoming "
    "feeds by time proximity rather than forecast index, then averages their "
    "projected volumes."
)

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
    PHASE_NOWCAST_SLUG,
    GAP_CONDITIONAL_SLUG,
    RECENT_CADENCE_SLUG,
]


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


def run_all_models(
    activities: list[Activity],
    cutoff: datetime,
    horizon_hours: int,
) -> list[Forecast]:
    """Run the scripted model lineup against one cutoff."""
    event_cache = build_event_cache(activities)
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
    base_forecasts: list[Forecast],
    consensus_forecast: Forecast,
    ranked_slugs: list[str] | None = None,
) -> str:
    """Choose the featured forecast slug.

    Args:
        base_forecasts: Scripted base model forecasts.
        consensus_forecast: Consensus forecast built from the scripted models.
        ranked_slugs: Optional best-to-worst scripted model ranking from the
            backtest harness.

    Returns:
        Slug of the featured forecast.

    Raises:
        ForecastUnavailable: If nothing available can be featured.
    """
    if consensus_forecast.available:
        return consensus_forecast.slug

    available_forecasts = {
        forecast.slug: forecast for forecast in base_forecasts if forecast.available
    }

    if ranked_slugs is not None:
        for slug in ranked_slugs:
            if slug in available_forecasts:
                return slug

    for slug in STATIC_FEATURED_TIEBREAKER:
        if slug in available_forecasts:
            return slug

    raise ForecastUnavailable("No scripted forecast is available to feature.")


def _blend_consensus_points_by_time(
    component_forecasts: dict[str, Forecast],
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> tuple[list[ForecastPoint], int]:
    """Blend component forecasts using time-based grouping."""
    del cutoff, horizon_hours

    component_indices = {slug: 0 for slug in component_forecasts}
    points: list[ForecastPoint] = []
    skipped_outliers = 0
    match_window = timedelta(minutes=CONSENSUS_MATCH_WINDOW_MINUTES)

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
        anchor_time = datetime.fromtimestamp(float(np.median(candidate_timestamps)))
        cluster_start = anchor_time - match_window
        cluster_end = anchor_time + match_window

        leading_outliers = [
            slug for slug, point in next_candidates if point.time < cluster_start
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
            earliest_slug = min(next_candidates, key=lambda item: item[1].time)[0]
            component_indices[earliest_slug] += 1
            skipped_outliers += 1
            continue

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
