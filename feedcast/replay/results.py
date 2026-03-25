"""Replay result persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_RESULTS_DIR = Path(".replay-results")


def save_results(
    *,
    mode: str,
    model_slug: str,
    payload: dict[str, Any],
    output_dir: Path = DEFAULT_RESULTS_DIR,
) -> Path:
    """Persist one replay artifact to the local gitignored results directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"{timestamp}-{model_slug}-{mode}.json"
    payload["results_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
