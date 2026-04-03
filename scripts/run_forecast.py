"""Command-line entrypoint for generating the latest feeding forecast report."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from feedcast.pipeline import main


def cli() -> None:
    """Parse CLI arguments and run the forecast pipeline."""
    parser = argparse.ArgumentParser(
        description="Forecast Silas's next 24 hours of bottle feeds."
    )
    parser.add_argument(
        "--export-path",
        type=Path,
        default=None,
        help="Optional explicit export CSV. Defaults to the latest matching file.",
    )
    parser.add_argument(
        "--agent",
        choices=["claude", "codex"],
        default="claude",
        help="Agent CLI to use (default: claude).",
    )
    parser.add_argument(
        "--skip-tuning",
        action="store_true",
        help="Skip agent model tuning.",
    )
    parser.add_argument(
        "--skip-insights",
        action="store_true",
        help="Skip agent trend insights.",
    )
    parser.add_argument(
        "--skip-agent-inference",
        action="store_true",
        help="Skip agent inference forecast.",
    )
    args = parser.parse_args()

    main(
        export_path=args.export_path,
        agent=args.agent,
        skip_tuning=args.skip_tuning,
        skip_insights=args.skip_insights,
        skip_agent_inference=args.skip_agent_inference,
    )


if __name__ == "__main__":
    cli()
