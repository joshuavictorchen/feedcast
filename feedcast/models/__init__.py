"""Register the scripted models and build the scripted consensus forecast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from feedcast.data import (
    Activity,
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    FeedEvent,
    Forecast,
    build_feed_events,
)
from .analog_trajectory import (
    MODEL_METHODOLOGY as ANALOG_TRAJECTORY_METHODOLOGY,
    MODEL_NAME as ANALOG_TRAJECTORY_NAME,
    MODEL_SLUG as ANALOG_TRAJECTORY_SLUG,
    forecast_analog_trajectory,
)
from .consensus_blend import (
    MODEL_SLUG as CONSENSUS_BLEND_SLUG,
    run_consensus_blend,
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
from .survival_hazard import (
    MODEL_METHODOLOGY as SURVIVAL_HAZARD_METHODOLOGY,
    MODEL_NAME as SURVIVAL_HAZARD_NAME,
    MODEL_SLUG as SURVIVAL_HAZARD_SLUG,
    forecast_survival_hazard,
)
from .shared import ForecastUnavailable

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
        name=SURVIVAL_HAZARD_NAME,
        slug=SURVIVAL_HAZARD_SLUG,
        methodology=SURVIVAL_HAZARD_METHODOLOGY,
        merge_window_minutes=None,
        forecast_fn=forecast_survival_hazard,
    ),
]

STATIC_FEATURED_TIEBREAKER = [
    SLOT_DRIFT_SLUG,
    ANALOG_TRAJECTORY_SLUG,
    LATENT_HUNGER_SLUG,
    SURVIVAL_HAZARD_SLUG,
]
FEATURED_DEFAULT = CONSENSUS_BLEND_SLUG


def get_model_spec(slug: str) -> ModelSpec | None:
    """Return the scripted model spec for one slug, if registered."""
    for spec in MODELS:
        if spec.slug == slug:
            return spec
    return None


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
