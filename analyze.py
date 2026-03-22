"""Run the Silas feeding forecast pipeline."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

# Matplotlib needs a writable config directory in this environment.
mpl_config_dir = Path(".mpl-cache")
mpl_config_dir.mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir.resolve()))

from forecasting import run_forecasting_pipeline
from reporting import write_reports


def main() -> None:
    """Run the forecast pipeline and write a new report set."""
    parser = argparse.ArgumentParser(description="Forecast Silas bottle feeds from the latest Nara export.")
    parser.add_argument(
        "--export-path",
        type=Path,
        default=None,
        help="Optional explicit export CSV. Defaults to the newest file in exports/.",
    )
    parser.add_argument(
        "--analysis-time",
        default=None,
        help="Optional override for the forecast cutoff in ISO format, e.g. 2026-03-22T13:30:00.",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=Path("reports"),
        help="Output root for generated reports.",
    )
    args = parser.parse_args()

    analysis_time = None
    if args.analysis_time:
        analysis_time = datetime.fromisoformat(args.analysis_time)

    result = run_forecasting_pipeline(
        export_path=args.export_path,
        analysis_time=analysis_time,
    )
    run_dir = write_reports(result, output_root=args.reports_dir)

    print(f"Export:     {result.snapshot.export_path}")
    print(f"Run ID:     {result.run_id}")
    print(f"Cutoff:     {result.analysis_time.isoformat(sep=' ')}")
    print(f"Headliner:  {result.headliner.definition.title}")
    if result.headliner.forecast.points:
        first_point = result.headliner.forecast.points[0]
        print(
            "First feed: "
            f"{first_point.time.strftime('%Y-%m-%d %I:%M %p')} "
            f"at {first_point.volume_oz:.1f} oz"
        )
    print(f"Summary:    {run_dir / 'summary.md'}")
    print(f"Metrics:    {run_dir / 'metrics.json'}")


if __name__ == "__main__":
    main()
