"""Tests for episode-aware report diagnostics."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from feedcast.clustering import BASE_GAP_MINUTES
from feedcast.data import Forecast, ForecastPoint
from feedcast.report import _forecast_diagnostics_entry


def _point(cutoff: datetime, offset_hours: float, volume: float = 3.0) -> ForecastPoint:
    """Build one forecast point at a fixed offset from cutoff."""
    return ForecastPoint(
        time=cutoff + timedelta(hours=offset_hours),
        volume_oz=volume,
        gap_hours=offset_hours,
    )


CUTOFF = datetime(2026, 3, 25, 0, 0, 0)


class DiagnosticsEntryTests(unittest.TestCase):
    """Episode count diagnostics per forecast."""

    def test_cluster_predictions_counted_correctly(self) -> None:
        """Diagnostics reflect raw vs episode counts."""
        gap_hours = (BASE_GAP_MINUTES - 10) / 60
        points = [
            _point(CUTOFF, 3.0, volume=3.5),
            _point(CUTOFF, 3.0 + gap_hours, volume=1.0),
            _point(CUTOFF, 6.0, volume=3.5),
        ]
        forecast = Forecast(
            name="Test",
            slug="test",
            points=points,
            methodology="Test methodology.",
            diagnostics={},
        )
        entry = _forecast_diagnostics_entry(forecast)
        self.assertEqual(entry["raw_point_count"], 3)
        self.assertEqual(entry["episode_count"], 2)
        self.assertEqual(entry["collapsed_attachments"], 1)

    def test_no_clusters_zero_attachments(self) -> None:
        """When no clustering occurs, collapsed_attachments is zero."""
        points = [
            _point(CUTOFF, 3.0),
            _point(CUTOFF, 6.0),
        ]
        forecast = Forecast(
            name="Clean",
            slug="clean",
            points=points,
            methodology="Clean methodology.",
            diagnostics={},
        )
        entry = _forecast_diagnostics_entry(forecast)
        self.assertEqual(entry["raw_point_count"], 2)
        self.assertEqual(entry["episode_count"], 2)
        self.assertEqual(entry["collapsed_attachments"], 0)

    def test_unavailable_forecast_zero_counts(self) -> None:
        """Unavailable forecast has zero counts."""
        forecast = Forecast(
            name="Broken",
            slug="broken",
            points=[],
            methodology="",
            diagnostics={},
            available=False,
            error_message="Failed.",
        )
        entry = _forecast_diagnostics_entry(forecast)
        self.assertEqual(entry["raw_point_count"], 0)
        self.assertEqual(entry["episode_count"], 0)
        self.assertEqual(entry["collapsed_attachments"], 0)


if __name__ == "__main__":
    unittest.main()
