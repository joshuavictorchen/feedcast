"""Pytest fixtures for synthetic simulation tests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from feedcast.data import Activity

from .export import export_path_for_activities, write_nara_export


@pytest.fixture
def replay_output_dir(tmp_path: Path) -> Path:
    """Return a temporary replay output directory."""
    output_dir = tmp_path / ".replay-results"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def write_simulation_export(
    tmp_path: Path,
) -> Callable[[Sequence[Activity]], Path]:
    """Return a helper that writes a replay-compatible synthetic export."""

    def _write(
        activities: Sequence[Activity],
        *,
        filename_date: str | None = None,
    ) -> Path:
        if filename_date is None:
            export_path = export_path_for_activities(tmp_path, activities)
        else:
            export_path = tmp_path / f"export_narababy_silas_{filename_date}.csv"
        return write_nara_export(export_path, activities)

    return _write
