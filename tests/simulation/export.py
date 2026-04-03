"""Synthetic export helpers for replay-compatible simulation tests."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

from feedcast.data import (
    Activity,
    DATA_FLOOR,
    DEFAULT_BREASTFEED_OZ_PER_30_MIN,
    EXPORT_TIMESTAMP_FORMAT,
)

from .factories import sort_activities


CSV_HEADERS = [
    "Type",
    "Start Date/time",
    "Start Date/time (Epoch)",
    "[Bottle Feed] Breast Milk Volume",
    "[Bottle Feed] Breast Milk Volume Unit",
    "[Bottle Feed] Formula Volume",
    "[Bottle Feed] Formula Volume Unit",
    "[Bottle Feed] Volume",
    "[Bottle Feed] Volume Unit",
    "[Breastfeed] Left Duration (Seconds)",
    "[Breastfeed] Right Duration (Seconds)",
]


def write_nara_export(path: Path, activities: Sequence[Activity]) -> Path:
    """Write synthetic activities to a replay-compatible Nara CSV."""
    ordered = validate_export_activities(activities)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for activity in ordered:
            writer.writerow(_activity_row(activity))

    return path


def export_path_for_activities(directory: Path, activities: Sequence[Activity]) -> Path:
    """Build a canonical export filename from the latest activity date."""
    ordered = validate_export_activities(activities)
    latest_activity = max(
        activity.end if activity.kind == "breastfeed" else activity.start
        for activity in ordered
    )
    return directory / f"export_narababy_silas_{latest_activity:%Y%m%d}.csv"


def validate_export_activities(activities: Sequence[Activity]) -> list[Activity]:
    """Fail fast on synthetic activities that production parsing would distort."""
    if not activities:
        raise ValueError("Need at least one activity to write a synthetic export.")

    ordered = sort_activities(activities)
    for activity in ordered:
        if activity.kind not in {"bottle", "breastfeed"}:
            raise ValueError(f"Unsupported synthetic activity kind: {activity.kind!r}.")
        if activity.start < DATA_FLOOR:
            raise ValueError(
                "Synthetic export activity starts before feedcast.data.DATA_FLOOR; "
                "production parsing would silently drop it."
            )

        if activity.kind == "bottle":
            if activity.end != activity.start:
                raise ValueError("Bottle activities must end at their start timestamp.")
            if activity.volume_oz <= 0:
                raise ValueError("Bottle activities must have positive volume.")
            continue

        if activity.end <= activity.start:
            raise ValueError("Breastfeed activities must end after they start.")

        duration_seconds = int(round((activity.end - activity.start).total_seconds()))
        expected_volume = DEFAULT_BREASTFEED_OZ_PER_30_MIN * (duration_seconds / 1800)
        if abs(activity.volume_oz - expected_volume) > 1e-9:
            raise ValueError(
                "Breastfeed activity volume must match the production duration "
                "heuristic so exports round-trip cleanly."
            )

    return ordered


def _activity_row(activity: Activity) -> dict[str, str]:
    """Convert one synthetic Activity into the export CSV row shape."""
    base_row = {
        "Type": "Bottle Feed" if activity.kind == "bottle" else "Breastfeed",
        "Start Date/time": activity.start.strftime(EXPORT_TIMESTAMP_FORMAT),
        "Start Date/time (Epoch)": str(int(activity.start.timestamp())),
        "[Bottle Feed] Breast Milk Volume": "",
        "[Bottle Feed] Breast Milk Volume Unit": "",
        "[Bottle Feed] Formula Volume": "",
        "[Bottle Feed] Formula Volume Unit": "",
        "[Bottle Feed] Volume": "",
        "[Bottle Feed] Volume Unit": "",
        "[Breastfeed] Left Duration (Seconds)": "",
        "[Breastfeed] Right Duration (Seconds)": "",
    }

    if activity.kind == "bottle":
        base_row["[Bottle Feed] Volume"] = str(activity.volume_oz)
        base_row["[Bottle Feed] Volume Unit"] = "oz"
        return base_row

    duration_seconds = int(round((activity.end - activity.start).total_seconds()))
    base_row["[Breastfeed] Left Duration (Seconds)"] = str(duration_seconds)
    base_row["[Breastfeed] Right Duration (Seconds)"] = "0"
    return base_row
