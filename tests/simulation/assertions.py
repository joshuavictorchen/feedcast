"""Shared assertions for synthetic simulation tests."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from feedcast.data import ForecastPoint


def assert_forecast_times_close(
    points: Sequence[ForecastPoint],
    expected_times: Sequence[datetime],
    *,
    tolerance_minutes: float,
) -> None:
    """Assert that forecast point times match expected times within tolerance."""
    assert_datetimes_close(
        actual_times=[point.time for point in points],
        expected_times=expected_times,
        tolerance_minutes=tolerance_minutes,
        label="forecast times",
    )


def assert_datetimes_close(
    *,
    actual_times: Sequence[datetime],
    expected_times: Sequence[datetime],
    tolerance_minutes: float,
    label: str,
) -> None:
    """Assert that two datetime sequences are aligned within a tolerance."""
    if len(actual_times) != len(expected_times):
        raise AssertionError(
            f"{label} length mismatch: expected {len(expected_times)}, "
            f"got {len(actual_times)}."
        )

    tolerance_seconds = tolerance_minutes * 60
    for index, (actual, expected) in enumerate(zip(actual_times, expected_times), start=1):
        delta_seconds = abs((actual - expected).total_seconds())
        if delta_seconds > tolerance_seconds:
            delta_minutes = delta_seconds / 60
            raise AssertionError(
                f"{label} mismatch at position {index}: expected "
                f"{expected.isoformat()}, got {actual.isoformat()} "
                f"(delta={delta_minutes:.2f} minutes, "
                f"tolerance={tolerance_minutes:.2f} minutes)."
            )


def assert_value_within_tolerance(
    *,
    actual: float,
    expected: float,
    tolerance: float,
    name: str,
) -> None:
    """Assert that a scalar value is within an absolute tolerance."""
    delta = abs(actual - expected)
    if delta > tolerance:
        raise AssertionError(
            f"{name} outside tolerance: expected {expected}, got {actual} "
            f"(delta={delta}, tolerance={tolerance})."
        )


def assert_replay_best_param_within_tolerance(
    payload: dict[str, object],
    *,
    param_name: str,
    expected: float,
    tolerance: float,
) -> None:
    """Assert that a replay tune payload recovered the expected best parameter."""
    best = payload.get("best")
    if not isinstance(best, dict):
        raise AssertionError("Replay payload missing best candidate summary.")

    params = best.get("params")
    if not isinstance(params, dict):
        raise AssertionError("Replay payload missing best candidate params.")
    if param_name not in params:
        raise AssertionError(f"Replay payload missing best param {param_name!r}.")

    actual = params[param_name]
    if not isinstance(actual, (int, float)):
        raise AssertionError(
            f"Replay best param {param_name!r} is not numeric: {actual!r}."
        )

    assert_value_within_tolerance(
        actual=float(actual),
        expected=expected,
        tolerance=tolerance,
        name=f"best[{param_name}]",
    )
