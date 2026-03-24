"""Test whether larger feeds are followed by longer subsequent gaps.

Run from the repo root:
    .venv/bin/python -m feedcast.research.volume_gap_relationship.analysis

This script writes committed artifacts for the current export so the findings
remain reproducible and easy to revisit as new data arrives.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

_MPL_CONFIG_DIR = TemporaryDirectory(prefix="agent-volume-gap-")
os.environ.setdefault("MPLCONFIGDIR", _MPL_CONFIG_DIR.name)

import matplotlib
import numpy as np
from scipy.stats import linregress, pearsonr

from feedcast.data import (
    DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES,
    FeedEvent,
    build_feed_events,
    hour_of_day,
    load_export_snapshot,
)

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUTPUT_DIR = Path(__file__).parent
ARTIFACTS_DIR = OUTPUT_DIR / "artifacts"
RECENT_WINDOWS_DAYS = (3, 5, 7)
VOLUME_BINS = (
    (0.0, 1.5),
    (1.5, 2.5),
    (2.5, 3.5),
    (3.5, 5.0),
)


@dataclass(frozen=True)
class GapPair:
    """One observed feed paired with the next observed gap."""

    event_time: datetime
    next_event_time: datetime
    hour_of_day: float
    daypart: str
    volume_oz: float
    bottle_volume_oz: float
    breastfeeding_volume_oz: float
    next_gap_hours: float


def main() -> None:
    """Run the research analysis and write committed artifacts."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = load_export_snapshot()
    output_capture = StringIO()
    run_timestamp = datetime.now().isoformat(timespec="seconds")
    pairs_by_view: dict[str, list[GapPair]] = {}

    def log(text: str = "") -> None:
        print(text)
        output_capture.write(text + "\n")

    view_specs = (
        ("bottle_only", None),
        ("merged_45_min", DEFAULT_BREASTFEED_MERGE_WINDOW_MINUTES),
    )
    summaries: dict[str, dict[str, object]] = {}

    log(f"Export: {snapshot.export_path}")
    log(f"Dataset: {snapshot.dataset_id}")
    log(f"Source hash: {snapshot.source_hash}")
    log(f"Cutoff: {snapshot.latest_activity_time.isoformat(timespec='seconds')}")
    log(f"Run: {run_timestamp}")
    log()

    for view_name, merge_window_minutes in view_specs:
        events = build_feed_events(snapshot.activities, merge_window_minutes)
        pairs = _build_gap_pairs(events)
        pairs_by_view[view_name] = pairs
        summary = _summarize_pairs(pairs, snapshot.latest_activity_time)
        summaries[view_name] = summary

        pairs_path = ARTIFACTS_DIR / f"{view_name}_pairs.csv"
        _write_pairs_csv(pairs_path, pairs)
        log(f"=== {view_name.upper()} ===")
        log(f"Pairs: {summary['pair_count']}")
        log(f"Pearson r: {summary['overall']['pearson_r']:.3f}")
        log(f"P-value: {summary['overall']['pearson_pvalue']:.4f}")
        log(f"Slope: {summary['overall']['slope_hours_per_oz']:.3f} hours/oz")
        log()

    scatter_path = ARTIFACTS_DIR / "bottle_only_volume_gap_scatter.png"
    _write_scatter_plot(
        scatter_path,
        pairs_by_view["bottle_only"],
        summaries["bottle_only"],
    )
    log(f"Scatter plot: {_display_path(scatter_path)}")

    summary_path = ARTIFACTS_DIR / "summary.json"
    summary_payload = {
        "export_path": str(snapshot.export_path),
        "dataset_id": snapshot.dataset_id,
        "source_hash": snapshot.source_hash,
        "cutoff": snapshot.latest_activity_time.isoformat(timespec="seconds"),
        "run_timestamp": run_timestamp,
        "views": summaries,
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2) + "\n")
    log(f"Summary JSON: {_display_path(summary_path)}")

    results_path = ARTIFACTS_DIR / "research_results.txt"
    log(f"Results file: {_display_path(results_path)}")
    results_path.write_text(output_capture.getvalue())


def _build_gap_pairs(events: list[FeedEvent]) -> list[GapPair]:
    """Return adjacent feed pairs for volume-to-next-gap analysis.

    Args:
        events: Bottle-centered feed events sorted by time.

    Returns:
        One record per event except the final event, which has no following gap.
    """
    if len(events) < 2:
        raise ValueError("Need at least two feed events to analyze next-gap behavior.")

    pairs: list[GapPair] = []
    for index in range(len(events) - 1):
        current = events[index]
        following = events[index + 1]
        hour = hour_of_day(current.time)
        pairs.append(
            GapPair(
                event_time=current.time,
                next_event_time=following.time,
                hour_of_day=hour,
                daypart=_daypart_from_hour(hour),
                volume_oz=current.volume_oz,
                bottle_volume_oz=current.bottle_volume_oz,
                breastfeeding_volume_oz=current.breastfeeding_volume_oz,
                next_gap_hours=(following.time - current.time).total_seconds() / 3600,
            )
        )
    return pairs


def _summarize_pairs(
    pairs: list[GapPair],
    cutoff: datetime,
) -> dict[str, object]:
    """Compute the descriptive statistics used in the findings.

    Args:
        pairs: Adjacent feed pairs built from one event view.
        cutoff: Latest activity time for recent-window summaries.

    Returns:
        JSON-serializable summary statistics.
    """
    volumes = np.array([pair.volume_oz for pair in pairs], dtype=float)
    gaps = np.array([pair.next_gap_hours for pair in pairs], dtype=float)
    overall = _summarize_numeric_relationship(volumes, gaps)

    summary: dict[str, object] = {
        "pair_count": len(pairs),
        "overall": overall,
        "recent_windows": [],
        "dayparts": {},
        "volume_bins": [],
        "merge_adjusted_pair_count": sum(
            1 for pair in pairs if pair.breastfeeding_volume_oz > 0
        ),
    }

    for days in RECENT_WINDOWS_DAYS:
        window_start = cutoff - timedelta(days=days)
        recent_pairs = [pair for pair in pairs if pair.event_time >= window_start]
        if len(recent_pairs) < 2:
            continue
        recent_volumes = np.array(
            [pair.volume_oz for pair in recent_pairs], dtype=float
        )
        recent_gaps = np.array(
            [pair.next_gap_hours for pair in recent_pairs],
            dtype=float,
        )
        recent_summary = _summarize_numeric_relationship(recent_volumes, recent_gaps)
        recent_summary["days"] = days
        recent_summary["pair_count"] = len(recent_pairs)
        summary["recent_windows"].append(recent_summary)

    for daypart in ("daytime", "overnight"):
        daypart_pairs = [pair for pair in pairs if pair.daypart == daypart]
        if len(daypart_pairs) < 2:
            continue
        daypart_volumes = np.array(
            [pair.volume_oz for pair in daypart_pairs], dtype=float
        )
        daypart_gaps = np.array(
            [pair.next_gap_hours for pair in daypart_pairs],
            dtype=float,
        )
        daypart_summary = _summarize_numeric_relationship(daypart_volumes, daypart_gaps)
        daypart_summary["pair_count"] = len(daypart_pairs)
        daypart_summary["mean_volume_oz"] = float(np.mean(daypart_volumes))
        daypart_summary["mean_gap_hours"] = float(np.mean(daypart_gaps))
        summary["dayparts"][daypart] = daypart_summary

    for lower_bound, upper_bound in VOLUME_BINS:
        in_bin = [pair for pair in pairs if lower_bound <= pair.volume_oz < upper_bound]
        if not in_bin:
            continue
        bin_gaps = np.array([pair.next_gap_hours for pair in in_bin], dtype=float)
        summary["volume_bins"].append(
            {
                "label": f"[{lower_bound}, {upper_bound})",
                "pair_count": len(in_bin),
                "mean_gap_hours": float(np.mean(bin_gaps)),
                "median_gap_hours": float(np.median(bin_gaps)),
            }
        )

    return summary


def _summarize_numeric_relationship(
    volumes: np.ndarray,
    gaps: np.ndarray,
) -> dict[str, float]:
    """Return descriptive statistics for one set of volume-gap pairs.

    Args:
        volumes: Feed volumes in ounces.
        gaps: Hours until the next observed feed.

    Returns:
        Correlation and linear-fit summary fields.
    """
    regression = linregress(volumes, gaps)
    correlation = pearsonr(volumes, gaps)
    return {
        "volume_mean_oz": float(np.mean(volumes)),
        "volume_median_oz": float(np.median(volumes)),
        "gap_mean_hours": float(np.mean(gaps)),
        "gap_median_hours": float(np.median(gaps)),
        "pearson_r": float(correlation.statistic),
        "pearson_pvalue": float(correlation.pvalue),
        "slope_hours_per_oz": float(regression.slope),
        "intercept_hours": float(regression.intercept),
        "r_squared": float(regression.rvalue**2),
    }


def _write_pairs_csv(path: Path, pairs: list[GapPair]) -> None:
    """Write the raw pair data used by the research article.

    Args:
        path: CSV output path.
        pairs: Pair records to serialize.
    """
    fieldnames = [
        "event_time",
        "next_event_time",
        "hour_of_day",
        "daypart",
        "volume_oz",
        "bottle_volume_oz",
        "breastfeeding_volume_oz",
        "next_gap_hours",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for pair in pairs:
            writer.writerow(
                {
                    "event_time": pair.event_time.isoformat(timespec="seconds"),
                    "next_event_time": pair.next_event_time.isoformat(
                        timespec="seconds"
                    ),
                    "hour_of_day": f"{pair.hour_of_day:.3f}",
                    "daypart": pair.daypart,
                    "volume_oz": f"{pair.volume_oz:.3f}",
                    "bottle_volume_oz": f"{pair.bottle_volume_oz:.3f}",
                    "breastfeeding_volume_oz": f"{pair.breastfeeding_volume_oz:.3f}",
                    "next_gap_hours": f"{pair.next_gap_hours:.3f}",
                }
            )


def _write_scatter_plot(
    path: Path,
    pairs: list[GapPair],
    summary: dict[str, object],
) -> None:
    """Write a bottle-only scatter plot for quick visual inspection.

    Args:
        path: Image output path.
        pairs: Pair records to render.
        summary: Precomputed summary for annotation and regression line.
    """
    daytime_pairs = [pair for pair in pairs if pair.daypart == "daytime"]
    overnight_pairs = [pair for pair in pairs if pair.daypart == "overnight"]
    figure, axis = plt.subplots(figsize=(8, 5))

    axis.scatter(
        [pair.volume_oz for pair in daytime_pairs],
        [pair.next_gap_hours for pair in daytime_pairs],
        color="#d55e00",
        alpha=0.75,
        label="Daytime",
    )
    axis.scatter(
        [pair.volume_oz for pair in overnight_pairs],
        [pair.next_gap_hours for pair in overnight_pairs],
        color="#0072b2",
        alpha=0.75,
        label="Overnight",
    )

    overall = summary["overall"]
    x_values = np.linspace(
        min(pair.volume_oz for pair in pairs),
        max(pair.volume_oz for pair in pairs),
        100,
    )
    y_values = overall["slope_hours_per_oz"] * x_values + overall["intercept_hours"]
    axis.plot(x_values, y_values, color="#222222", linewidth=2, label="Overall fit")

    axis.set_title("Feed volume vs. next gap (bottle-only view)")
    axis.set_xlabel("Feed volume (oz)")
    axis.set_ylabel("Hours until next feed")
    axis.legend()
    axis.grid(alpha=0.2)
    axis.text(
        0.02,
        0.98,
        (
            f"r = {overall['pearson_r']:.3f}\n"
            f"slope = {overall['slope_hours_per_oz']:.3f} h/oz\n"
            f"n = {summary['pair_count']}"
        ),
        transform=axis.transAxes,
        va="top",
        ha="left",
        bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "#cccccc"},
    )

    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _display_path(path: Path) -> str:
    """Return a stable, repo-portable display path for generated artifacts."""
    return str(path.relative_to(OUTPUT_DIR))


def _daypart_from_hour(hour: float) -> str:
    """Map decimal hour-of-day to the shared daypart split."""
    if hour >= 20.0 or hour < 8.0:
        return "overnight"
    return "daytime"


if __name__ == "__main__":
    main()
