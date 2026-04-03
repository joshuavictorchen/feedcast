"""Factories for synthetic Activity histories used in simulation tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Sequence

from feedcast.data import Activity, DEFAULT_BREASTFEED_OZ_PER_30_MIN


ScheduleEntry = tuple[datetime, float]


def bottle_activity(time: datetime, volume_oz: float) -> Activity:
    """Build one bottle Activity.

    Args:
        time: Bottle-feed timestamp.
        volume_oz: Bottle volume in fluid ounces.

    Returns:
        An Activity matching the production bottle-feed shape.
    """
    if volume_oz <= 0:
        raise ValueError("Bottle volume must be positive.")

    return Activity(
        kind="bottle",
        start=time,
        end=time,
        volume_oz=volume_oz,
        raw_fields={},
    )


def breastfeed_activity(start: datetime, duration_minutes: float) -> Activity:
    """Build one breastfeed Activity using the production duration heuristic.

    The parser derives breastfeed volume from duration, so the synthetic
    factory does the same to guarantee round-trippable exports.

    Args:
        start: Breastfeed start timestamp.
        duration_minutes: Total duration in minutes.

    Returns:
        An Activity matching the production breastfeed shape.
    """
    if duration_minutes <= 0:
        raise ValueError("Breastfeed duration must be positive.")

    duration_seconds = int(round(duration_minutes * 60))
    if duration_seconds <= 0:
        raise ValueError("Breastfeed duration rounds to zero seconds.")

    volume_oz = DEFAULT_BREASTFEED_OZ_PER_30_MIN * (duration_seconds / 1800)
    end = start + timedelta(seconds=duration_seconds)
    return Activity(
        kind="breastfeed",
        start=start,
        end=end,
        volume_oz=volume_oz,
        raw_fields={},
    )


def bottle_activities_from_schedule(
    schedule: Sequence[ScheduleEntry],
) -> list[Activity]:
    """Convert a `(timestamp, volume_oz)` bottle schedule into Activities."""
    return sort_activities(
        bottle_activity(time=timestamp, volume_oz=volume_oz)
        for timestamp, volume_oz in schedule
    )


def sort_activities(activities: Iterable[Activity]) -> list[Activity]:
    """Return activities in production sort order."""
    return sorted(
        activities,
        key=lambda activity: (activity.start, activity.end, activity.kind),
    )
