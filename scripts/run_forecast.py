"""Command-line entrypoint for generating the latest feeding forecast report."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from feedcast.pipeline import main

if __name__ == "__main__":
    main()
