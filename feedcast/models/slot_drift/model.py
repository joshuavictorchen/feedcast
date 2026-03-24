"""Slot Drift forecast model.

Predicts the next 24 hours by identifying recurring daily feed slots,
tracking how each slot drifts over time, and extrapolating to the
forecast day. See methodology.md in this directory for research and
design decisions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
from scipy.optimize import linear_sum_assignment

from feedcast.data import (
    FeedEvent,
    Forecast,
    ForecastPoint,
    hour_of_day,
)
from feedcast.models.shared import (
    ForecastUnavailable,
    load_methodology,
    normalize_forecast_points,
)

MODEL_NAME = "Slot Drift"
MODEL_SLUG = "slot_drift"
MODEL_METHODOLOGY = load_methodology(__file__)

# --- Tuning parameters (model-specific) ---

# How many days of history to consider for template building and drift.
LOOKBACK_DAYS = 7

# Minimum complete days required to produce a forecast.
MIN_COMPLETE_DAYS = 3

# Maximum time-of-day distance (hours) for a feed to match a slot.
# Feeds farther than this are left unmatched (cluster feeds, extras).
MATCH_COST_THRESHOLD_HOURS = 2.0

# Recency half-life for weighting days in drift and volume estimation.
DRIFT_WEIGHT_HALF_LIFE_DAYS = 3.0


def forecast_slot_drift(
    history: list[FeedEvent],
    cutoff: datetime,
    horizon_hours: int,
) -> Forecast:
    """Predict feeds by identifying daily slots and extrapolating drift.

    Args:
        history: Bottle-centered feed events up to the cutoff.
        cutoff: The latest observed activity time.
        horizon_hours: How many hours ahead to forecast.

    Returns:
        A Forecast with projected feed times and volumes.
    """
    # Group history into calendar days, identify recent complete days.
    daily_feeds = _group_by_day(history, cutoff)
    complete_days = _recent_complete_days(daily_feeds, cutoff)
    if len(complete_days) < MIN_COMPLETE_DAYS:
        raise ForecastUnavailable(
            f"Slot Drift needs at least {MIN_COMPLETE_DAYS} recent complete days, "
            f"found {len(complete_days)}."
        )

    # Determine canonical slot count from recent days.
    slot_count = _determine_slot_count(complete_days)

    # Build template from days with the canonical count, then refine it
    # by matching all days and recomputing slot centers.
    template = _build_initial_template(complete_days, slot_count)
    day_matches = _match_all_days(complete_days, template)
    template = _refine_template(day_matches, template)
    day_matches = _match_all_days(complete_days, template)

    # Compute per-slot drift and project to the forecast day.
    reference_date = cutoff.date()
    projections = _project_slots(day_matches, template, reference_date)

    # Identify which of today's slots are already filled.
    today_feeds = [
        event for event in history
        if event.time.date() == cutoff.date() and event.time <= cutoff
    ]
    filled_today = _filled_slots_today(today_feeds, template)

    # Build forecast points for remaining today + tomorrow.
    points = _build_forecast_points(
        projections, filled_today, cutoff, horizon_hours, history,
    )

    return Forecast(
        name=MODEL_NAME,
        slug=MODEL_SLUG,
        points=normalize_forecast_points(points, cutoff, horizon_hours),
        methodology=load_methodology(__file__),
        diagnostics=_build_diagnostics(
            slot_count, template, projections, day_matches,
            complete_days, filled_today,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _group_by_day(
    history: list[FeedEvent],
    cutoff: datetime,
) -> dict[date, list[FeedEvent]]:
    """Group feed events by calendar day, up to the cutoff."""
    daily: dict[date, list[FeedEvent]] = defaultdict(list)
    for event in history:
        if event.time <= cutoff:
            daily[event.time.date()].append(event)
    for feeds in daily.values():
        feeds.sort(key=lambda event: event.time)
    return dict(daily)


def _recent_complete_days(
    daily_feeds: dict[date, list[FeedEvent]],
    cutoff: datetime,
) -> list[tuple[date, list[FeedEvent]]]:
    """Return recent complete days within the lookback window.

    The cutoff day is always excluded (it is incomplete by definition).
    """
    lookback_start = (cutoff - timedelta(days=LOOKBACK_DAYS)).date()
    return [
        (day, daily_feeds[day])
        for day in sorted(daily_feeds)
        if lookback_start <= day < cutoff.date()
    ]


def _determine_slot_count(
    complete_days: list[tuple[date, list[FeedEvent]]],
) -> int:
    """Derive the canonical daily slot count from recent days.

    Uses the median feed count across complete days. The research shows
    this converges to 8-9 once the early chaotic days drop out of the
    lookback window.
    """
    counts = [len(feeds) for _, feeds in complete_days]
    return int(np.median(counts))


def _build_initial_template(
    complete_days: list[tuple[date, list[FeedEvent]]],
    slot_count: int,
) -> np.ndarray:
    """Build a starting template from days with the canonical feed count.

    For days with exactly slot_count feeds, collect sorted hour-of-day
    values and take the per-position median. If no day has exactly that
    count, use the closest available day.
    """
    exact_days = [
        (day, feeds) for day, feeds in complete_days
        if len(feeds) == slot_count
    ]
    if not exact_days:
        # Fall back to the day(s) closest to slot_count
        exact_days = sorted(
            complete_days, key=lambda pair: abs(len(pair[1]) - slot_count),
        )[:2]

    slot_matrix = []
    for _, feeds in exact_days:
        hours = sorted(hour_of_day(event.time) for event in feeds)
        if len(hours) >= slot_count:
            slot_matrix.append(hours[:slot_count])

    if slot_matrix:
        return np.median(np.array(slot_matrix), axis=0)

    # Absolute fallback: evenly space across the day
    return np.linspace(0.5, 22.0, slot_count)


def _circular_distance(a: float, b: float, period: float = 24.0) -> float:
    """Circular distance between two hour-of-day values."""
    diff = abs(a - b) % period
    return min(diff, period - diff)


def _circular_deviation(
    observed: float, reference: float, period: float = 24.0,
) -> float:
    """Signed circular deviation (positive = later, negative = earlier)."""
    raw = (observed - reference) % period
    if raw > period / 2:
        raw -= period
    return raw


def _match_day_to_template(
    feeds: list[FeedEvent],
    template: np.ndarray,
) -> tuple[dict[int, tuple[FeedEvent, float]], list[FeedEvent]]:
    """Match one day's feeds to template slots via Hungarian assignment.

    Returns:
        matched: slot_index -> (event, observed hour-of-day)
        unmatched: feeds that exceeded the cost threshold or had no slot
    """
    hours = np.array([hour_of_day(event.time) for event in feeds])
    feed_count = len(hours)
    slot_count = len(template)

    # Build the cost matrix: feeds (rows) x slots (columns).
    cost = np.zeros((feed_count, slot_count))
    for i in range(feed_count):
        for j in range(slot_count):
            cost[i, j] = _circular_distance(hours[i], template[j])

    row_indices, col_indices = linear_sum_assignment(cost)

    matched: dict[int, tuple[FeedEvent, float]] = {}
    matched_feed_indices: set[int] = set()
    for row, col in zip(row_indices, col_indices):
        if cost[row, col] <= MATCH_COST_THRESHOLD_HOURS:
            matched[col] = (feeds[row], float(hours[row]))
            matched_feed_indices.add(row)

    unmatched = [feeds[i] for i in range(feed_count) if i not in matched_feed_indices]
    return matched, unmatched


def _match_all_days(
    complete_days: list[tuple[date, list[FeedEvent]]],
    template: np.ndarray,
) -> list[tuple[date, dict[int, tuple[FeedEvent, float]], list[FeedEvent]]]:
    """Match every recent day's feeds to the template."""
    return [
        (day, *_match_day_to_template(feeds, template))
        for day, feeds in complete_days
    ]


def _circular_mean(
    hours: list[float], reference: float, period: float = 24.0,
) -> float:
    """Circular mean of hour-of-day values, anchored to a reference."""
    deviations = [_circular_deviation(h, reference, period) for h in hours]
    return (reference + float(np.mean(deviations))) % period


def _refine_template(
    day_matches: list[tuple[date, dict[int, tuple[FeedEvent, float]], list[FeedEvent]]],
    template: np.ndarray,
) -> np.ndarray:
    """Recompute template slot positions from matched observations."""
    refined = np.copy(template)
    for slot_idx in range(len(template)):
        observations = [
            hour for _, matched, _ in day_matches
            if slot_idx in matched
            for hour in [matched[slot_idx][1]]
        ]
        if observations:
            refined[slot_idx] = _circular_mean(observations, template[slot_idx])
    return refined


def _project_slots(
    day_matches: list[tuple[date, dict[int, tuple[FeedEvent, float]], list[FeedEvent]]],
    template: np.ndarray,
    reference_date: date,
) -> list[dict]:
    """Compute per-slot drift and project positions to the reference date.

    For each slot, fits a recency-weighted linear trend on the signed
    deviations from the template position. The projection at the
    reference date (day_offset = 0) gives the expected slot time.

    Returns a list of slot dicts sorted by projected hour:
        slot_index, projected_hour, drift_rate, avg_volume, observation_count
    """
    decay = np.log(2) / DRIFT_WEIGHT_HALF_LIFE_DAYS
    projections = []

    for slot_idx in range(len(template)):
        offsets = []
        deviations = []
        volumes = []
        weights = []

        for day, matched, _ in day_matches:
            if slot_idx not in matched:
                continue
            event, hour = matched[slot_idx]
            day_offset = (day - reference_date).days
            deviation = _circular_deviation(hour, template[slot_idx])
            age_days = (reference_date - day).days
            weight = np.exp(-decay * age_days)

            offsets.append(day_offset)
            deviations.append(deviation)
            volumes.append(event.volume_oz)
            weights.append(weight)

        if not offsets:
            # No observations for this slot; use template position.
            projections.append({
                "slot_index": slot_idx,
                "projected_hour": float(template[slot_idx]),
                "drift_rate": 0.0,
                "avg_volume": 3.0,
                "observation_count": 0,
            })
            continue

        offset_array = np.array(offsets, dtype=float)
        deviation_array = np.array(deviations, dtype=float)
        volume_array = np.array(volumes, dtype=float)
        weight_array = np.array(weights, dtype=float)

        # Weighted linear regression: deviation = intercept + slope * day_offset
        # At the reference date (day_offset = 0), the projected deviation
        # equals the intercept.
        if len(offsets) >= 2:
            intercept, slope = _weighted_linreg_1d(
                offset_array, deviation_array, weight_array,
            )
        else:
            intercept = deviation_array[0]
            slope = 0.0

        projected_hour = float(template[slot_idx]) + intercept
        avg_volume = float(np.average(volume_array, weights=weight_array))

        projections.append({
            "slot_index": slot_idx,
            "projected_hour": projected_hour,
            "drift_rate": float(slope),
            "avg_volume": avg_volume,
            "observation_count": len(offsets),
        })

    projections.sort(key=lambda p: p["projected_hour"] % 24)
    return projections


def _weighted_linreg_1d(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray,
) -> tuple[float, float]:
    """Weighted linear regression for one predictor, returning (intercept, slope)."""
    normalized_weights = weights / weights.sum()
    design = np.column_stack([np.ones(len(x)), x])
    weight_matrix = np.diag(normalized_weights)
    try:
        coefficients = np.linalg.solve(
            design.T @ weight_matrix @ design,
            design.T @ weight_matrix @ y,
        )
        return float(coefficients[0]), float(coefficients[1])
    except np.linalg.LinAlgError:
        return float(np.average(y, weights=normalized_weights)), 0.0


def _filled_slots_today(
    today_feeds: list[FeedEvent],
    template: np.ndarray,
) -> set[int]:
    """Identify which template slots have already been filled today."""
    if not today_feeds:
        return set()
    matched, _ = _match_day_to_template(today_feeds, template)
    return set(matched.keys())


def _build_forecast_points(
    projections: list[dict],
    filled_today: set[int],
    cutoff: datetime,
    horizon_hours: int,
    history: list[FeedEvent],
) -> list[ForecastPoint]:
    """Build forecast points from projected slot positions.

    Projects today's unfilled slots and all of tomorrow's slots, then
    filters to the forecast window.
    """
    reference_midnight = datetime.combine(cutoff.date(), datetime.min.time())
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    points: list[ForecastPoint] = []

    for day_offset in range(2):
        for projection in projections:
            slot_idx = projection["slot_index"]

            # Skip slots already filled today.
            if day_offset == 0 and slot_idx in filled_today:
                continue

            # Compute absolute feed time: base day + projected hour + drift.
            feed_time = reference_midnight + timedelta(
                hours=(24 * day_offset)
                + projection["projected_hour"]
                + (projection["drift_rate"] * day_offset),
            )

            if feed_time <= cutoff or feed_time >= horizon_end:
                continue

            previous_time = points[-1].time if points else history[-1].time
            gap_hours = (feed_time - previous_time).total_seconds() / 3600

            points.append(ForecastPoint(
                time=feed_time,
                volume_oz=max(projection["avg_volume"], 0.5),
                gap_hours=max(gap_hours, 0.1),
            ))

    points.sort(key=lambda point: point.time)

    # Recompute gaps after sorting (the interleaving of today/tomorrow
    # slots may have changed the order).
    recomputed: list[ForecastPoint] = []
    for point in points:
        previous_time = recomputed[-1].time if recomputed else history[-1].time
        gap_hours = (point.time - previous_time).total_seconds() / 3600
        recomputed.append(ForecastPoint(
            time=point.time,
            volume_oz=point.volume_oz,
            gap_hours=max(gap_hours, 0.1),
        ))
    return recomputed


def _build_diagnostics(
    slot_count: int,
    template: np.ndarray,
    projections: list[dict],
    day_matches: list[tuple[date, dict[int, tuple[FeedEvent, float]], list[FeedEvent]]],
    complete_days: list[tuple[date, list[FeedEvent]]],
    filled_today: set[int],
) -> dict:
    """Build diagnostics dict for the report and debugging."""
    def _hour_str(h: float) -> str:
        hour = int(h) % 24
        minute = int((h % 1) * 60)
        return f"{hour:02d}:{minute:02d}"

    return {
        "slot_count": slot_count,
        "template_hours": [round(float(h), 2) for h in template],
        "template_times": [_hour_str(h) for h in template],
        "complete_days_used": len(complete_days),
        "daily_feed_counts": {
            str(day): len(feeds) for day, feeds in complete_days
        },
        "per_slot": [
            {
                "slot": p["slot_index"] + 1,
                "template": _hour_str(template[p["slot_index"]]),
                "projected": _hour_str(p["projected_hour"]),
                "drift_hours_per_day": round(p["drift_rate"], 3),
                "avg_volume_oz": round(p["avg_volume"], 2),
                "observations": p["observation_count"],
            }
            for p in projections
        ],
        "per_day_match_quality": {
            str(day): {
                "total_feeds": len(matched) + len(unmatched),
                "matched": len(matched),
                "unmatched": len(unmatched),
            }
            for day, matched, unmatched in day_matches
        },
        "filled_slots_today": sorted(int(s) for s in filled_today),
    }
