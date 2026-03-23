"""Domain types and CSV loading utilities for Nara Baby exports."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ML_TO_FLOZ = 0.033814
BIRTH_DATE = datetime(2026, 2, 27)
DATA_FLOOR = datetime(2026, 3, 15)
HORIZON_HOURS = 24

SNACK_THRESHOLD_OZ = 1.5
MIN_INTERVAL_HOURS = 1.5
MAX_INTERVAL_HOURS = 6.0
MIN_POINT_GAP_MINUTES = 45

DEFAULT_BREASTFEED_OZ_PER_30_MIN = 0.5
DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES = 45

EXPORT_FILENAME_PATTERN = re.compile(r"export_narababy_silas_(\d{8}).*\.csv$")
EXPORT_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_BOTTLE_VOLUME_FIELDS = (
    ("[Bottle Feed] Breast Milk Volume", "[Bottle Feed] Breast Milk Volume Unit"),
    ("[Bottle Feed] Formula Volume", "[Bottle Feed] Formula Volume Unit"),
)
_FINGERPRINT_FIELDS = (
    "Type",
    "Start Date/time (Epoch)",
    "[Bottle Feed] Breast Milk Volume",
    "[Bottle Feed] Breast Milk Volume Unit",
    "[Bottle Feed] Formula Volume",
    "[Bottle Feed] Formula Volume Unit",
    "[Bottle Feed] Volume",
    "[Bottle Feed] Volume Unit",
    "[Breastfeed] Left Duration (Seconds)",
    "[Breastfeed] Right Duration (Seconds)",
)


@dataclass(frozen=True)
class Activity:
    """A parsed feeding-related activity from the export."""

    kind: str
    start: datetime
    end: datetime
    volume_oz: float
    raw_fields: dict[str, str]


@dataclass(frozen=True)
class FeedEvent:
    """A bottle-centered event used by forecast models."""

    time: datetime
    volume_oz: float
    bottle_volume_oz: float
    breastfeeding_volume_oz: float


@dataclass(frozen=True)
class ForecastPoint:
    """A single predicted feed."""

    time: datetime
    volume_oz: float
    gap_hours: float

    def to_dict(self) -> dict[str, str | float]:
        """Return a JSON-serializable representation."""
        return {
            "time": self.time.isoformat(),
            "volume_oz": round(self.volume_oz, 3),
            "gap_hours": round(self.gap_hours, 3),
        }


@dataclass
class Forecast:
    """One model or agent forecast."""

    name: str
    slug: str
    points: list[ForecastPoint]
    methodology: str
    diagnostics: dict[str, object]
    available: bool = True
    error_message: str | None = None


@dataclass(frozen=True)
class ExportSnapshot:
    """The selected export and its stable metadata."""

    export_path: Path
    activities: list[Activity]
    latest_activity_time: datetime
    dataset_id: str
    source_hash: str


def find_latest_export(exports_dir: Path = Path("exports")) -> Path:
    """Return the latest export path.

    Args:
        exports_dir: Directory containing raw Nara exports.

    Returns:
        The latest matching export file.

    Raises:
        FileNotFoundError: If no matching export exists.
    """
    candidates: list[tuple[str, int, str, Path]] = []
    for path in exports_dir.glob("export_narababy_silas_*.csv"):
        match = EXPORT_FILENAME_PATTERN.match(path.name)
        if match is None:
            continue
        candidates.append((match.group(1), path.stat().st_mtime_ns, path.name, path))

    if not candidates:
        raise FileNotFoundError(f"No matching exports found in {exports_dir}.")

    _, _, _, latest_path = max(candidates)
    return latest_path


def load_export_snapshot(
    exports_dir: Path = Path("exports"),
    export_path: Path | None = None,
) -> ExportSnapshot:
    """Load the selected export and compute stable metadata.

    Args:
        exports_dir: Directory containing raw Nara exports.
        export_path: Optional explicit export path.

    Returns:
        Export metadata and parsed activities.

    Raises:
        ValueError: If the export contains no relevant activities.
    """
    selected_path = export_path or find_latest_export(exports_dir)
    activities = load_activities(selected_path)
    if not activities:
        raise ValueError(f"No feeding activity found in {selected_path}.")

    latest_activity_time = max(
        activity.end if activity.kind == "breastfeed" else activity.start
        for activity in activities
    )
    return ExportSnapshot(
        export_path=selected_path,
        activities=activities,
        latest_activity_time=latest_activity_time,
        dataset_id=dataset_fingerprint(activities),
        source_hash=file_sha256(selected_path),
    )


def load_activities(path: Path) -> list[Activity]:
    """Parse bottle feeds and breastfeeds from an export CSV.

    Args:
        path: Export CSV path.

    Returns:
        Parsed feeding activities at or after the global data floor.
    """
    activities: list[Activity] = []

    # Nara exports are CSVs and may include a BOM depending on how they were
    # generated. Using utf-8-sig avoids leaking that detail into callers.
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return activities

        for row in reader:
            raw_start = _row_value(row, "Start Date/time")
            if not raw_start:
                continue

            start = datetime.strptime(raw_start, EXPORT_TIMESTAMP_FORMAT)
            if start < DATA_FLOOR:
                continue

            activity_type = _row_value(row, "Type")
            raw_fields = _copy_raw_fields(row)

            if activity_type == "Bottle Feed":
                volume_oz = parse_bottle_volume_oz(row)
                if volume_oz <= 0:
                    continue
                activities.append(
                    Activity(
                        kind="bottle",
                        start=start,
                        end=start,
                        volume_oz=volume_oz,
                        raw_fields=raw_fields,
                    )
                )
                continue

            if activity_type == "Breastfeed":
                left_seconds = _int_or_zero(
                    _row_value(row, "[Breastfeed] Left Duration (Seconds)")
                )
                right_seconds = _int_or_zero(
                    _row_value(row, "[Breastfeed] Right Duration (Seconds)")
                )
                duration_seconds = left_seconds + right_seconds
                if duration_seconds <= 0:
                    continue
                activities.append(
                    Activity(
                        kind="breastfeed",
                        start=start,
                        end=start + timedelta(seconds=duration_seconds),
                        volume_oz=DEFAULT_BREASTFEED_OZ_PER_30_MIN
                        * (duration_seconds / 1800),
                        raw_fields=raw_fields,
                    )
                )

    activities.sort(key=lambda activity: (activity.start, activity.end, activity.kind))
    return activities


def parse_bottle_volume_oz(row: dict[str, str | None]) -> float:
    """Parse bottle-feed volume as fluid ounces.

    Args:
        row: CSV row from a Nara export.

    Returns:
        Total bottle volume in fluid ounces.
    """
    total_floz = 0.0
    for volume_key, unit_key in _BOTTLE_VOLUME_FIELDS:
        raw_volume = _row_value(row, volume_key)
        if not raw_volume:
            continue
        total_floz += _to_floz(float(raw_volume), _row_value(row, unit_key))

    if total_floz > 0:
        return total_floz

    raw_total = _row_value(row, "[Bottle Feed] Volume")
    if not raw_total:
        return 0.0

    return _to_floz(float(raw_total), _row_value(row, "[Bottle Feed] Volume Unit"))


def build_feed_events(
    activities: list[Activity],
    merge_window_minutes: int | None,
) -> list[FeedEvent]:
    """Construct bottle-centered events from raw activities.

    Breastfeeding is an optional volume adjustment only. Event timestamps stay
    anchored on the logged bottle-feed start time so model timing targets stay
    directly comparable.

    Args:
        activities: Parsed activities ordered by time.
        merge_window_minutes: Merge window for attributing breastfeed volume to
            the next bottle feed. `None` disables the merge.

    Returns:
        Bottle-centered feed events.
    """
    bottles = sorted(
        (activity for activity in activities if activity.kind == "bottle"),
        key=lambda activity: activity.start,
    )
    breastfeeds = sorted(
        (activity for activity in activities if activity.kind == "breastfeed"),
        key=lambda activity: activity.end,
    )

    events: list[FeedEvent] = []
    breastfeed_index = 0

    for bottle in bottles:
        breastfeeding_volume_oz = 0.0
        if merge_window_minutes is not None:
            while (
                breastfeed_index < len(breastfeeds)
                and breastfeeds[breastfeed_index].end <= bottle.start
            ):
                breastfeed = breastfeeds[breastfeed_index]
                gap_minutes = (bottle.start - breastfeed.end).total_seconds() / 60
                if gap_minutes <= merge_window_minutes:
                    breastfeeding_volume_oz += breastfeed.volume_oz
                breastfeed_index += 1

        events.append(
            FeedEvent(
                time=bottle.start,
                volume_oz=bottle.volume_oz + breastfeeding_volume_oz,
                bottle_volume_oz=bottle.volume_oz,
                breastfeeding_volume_oz=breastfeeding_volume_oz,
            )
        )

    return events


def dataset_fingerprint(activities: list[Activity]) -> str:
    """Return a stable dataset fingerprint for parsed activities.

    The fingerprint intentionally uses raw CSV fields rather than interpreted
    model features so future heuristic changes do not rewrite dataset identity.

    Args:
        activities: Parsed feeding activities.

    Returns:
        Stable SHA-256 dataset identifier prefixed with `sha256:`.
    """
    records = sorted(_fingerprint_record(activity) for activity in activities)
    payload = json.dumps(records, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hash of a file's raw bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def hour_of_day(timestamp: datetime) -> float:
    """Return the decimal hour-of-day for a timestamp."""
    return timestamp.hour + (timestamp.minute / 60) + (timestamp.second / 3600)


def daily_feed_counts(events: list[FeedEvent]) -> dict[date, int]:
    """Count feeds per local calendar day."""
    counts: dict[date, int] = {}
    for event in events:
        counts[event.time.date()] = counts.get(event.time.date(), 0) + 1
    return counts


def _copy_raw_fields(row: dict[str, str | None]) -> dict[str, str]:
    """Normalize raw CSV row values into plain strings."""
    return {
        key: "" if value is None else value.strip()
        for key, value in row.items()
        if key is not None
    }


def _row_value(row: dict[str, str | None], key: str) -> str:
    """Return a stripped CSV field value, failing fast if the column is missing."""
    if key not in row:
        raise KeyError(f"Missing required CSV column: {key}")
    value = row[key]
    return "" if value is None else value.strip()


def _int_or_zero(raw_value: str) -> int:
    """Parse an integer field, treating blank strings as zero."""
    return int(raw_value) if raw_value else 0


def _to_floz(volume: float, unit: str) -> float:
    """Convert a bottle volume to fluid ounces."""
    return volume * ML_TO_FLOZ if unit.upper() == "ML" else volume


def _fingerprint_record(activity: Activity) -> list[str]:
    """Return the raw-field record used for dataset identity."""
    return [activity.raw_fields.get(field, "") for field in _FINGERPRINT_FIELDS]
