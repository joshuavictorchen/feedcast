"""Shared scripted-model utilities.

These helpers cover the common mechanics reused by more than one model:
forecast normalization, methodology loading, and the ForecastUnavailable
exception.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from feedcast.data import ForecastPoint


class ForecastUnavailable(RuntimeError):
    """Raised when a model cannot produce a forecast for the given cutoff."""


def load_methodology(model_file: str) -> str:
    """Load the report methodology from a model's methodology.md file.

    Reads everything before the first ## heading. The # title line is
    stripped. This lets methodology.md contain both the report-ready
    text and supplementary sections (design decisions, research) that
    don't appear in the report.

    Args:
        model_file: The __file__ of the calling model module.

    Returns:
        The methodology text for the report.
    """
    path = Path(model_file).parent / "methodology.md"
    lines = path.read_text().splitlines()
    methodology_lines: list[str] = []
    for line in lines:
        # Skip the title line
        if line.startswith("# ") and not methodology_lines:
            continue
        # Stop at the first section heading
        if line.startswith("## "):
            break
        methodology_lines.append(line)
    return "\n".join(methodology_lines).strip()


def normalize_forecast_points(
    points: list[ForecastPoint],
    cutoff: datetime,
    horizon_hours: int,
) -> list[ForecastPoint]:
    """Filter and sort forecast points within the horizon window.

    Keeps points strictly inside (cutoff, horizon_end), sorted by time.
    Recomputes gap_hours from adjacent points and clips volume to a sane
    range. Does NOT adjust timestamps — model output is preserved as-is.
    """
    horizon_end = cutoff + timedelta(hours=horizon_hours)
    window = sorted(
        (p for p in points if cutoff < p.time < horizon_end),
        key=lambda item: item.time,
    )

    normalized: list[ForecastPoint] = []
    for point in window:
        gap_hours = point.gap_hours
        if normalized:
            gap_hours = (point.time - normalized[-1].time).total_seconds() / 3600

        normalized.append(
            ForecastPoint(
                time=point.time,
                volume_oz=float(np.clip(point.volume_oz, 0.1, 8.0)),
                gap_hours=float(max(gap_hours, 0.1)),
            )
        )

    return normalized
