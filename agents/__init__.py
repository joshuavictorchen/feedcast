"""Shared LLM agent runner for forecast generation."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from data import ExportSnapshot, Forecast, ForecastPoint, HORIZON_HOURS

AGENT_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class AgentSpec:
    """One forecast agent definition."""

    name: str
    slug: str
    workspace: str


AGENTS = [
    AgentSpec("Claude Forecast", "claude_forecast", "claude"),
    AgentSpec("Codex Forecast", "codex_forecast", "codex"),
]


def run_all_agents(snapshot: ExportSnapshot) -> list[Forecast]:
    """Run all configured agents for one export snapshot.

    Args:
        snapshot: Selected export metadata and parsed activities.

    Returns:
        Agent forecasts in the configured display order.

    Raises:
        RuntimeError: If any agent fails to produce valid output.
    """
    forecasts: list[Forecast] = []
    for spec in AGENTS:
        forecasts.append(run_agent_forecast(spec, snapshot))
    return forecasts


def run_agent_forecast(spec: AgentSpec, snapshot: ExportSnapshot) -> Forecast:
    """Run one agent and consume its workspace outputs.

    Args:
        spec: Agent configuration.
        snapshot: Selected export metadata.

    Returns:
        Parsed forecast result for report rendering and tracking.

    Raises:
        RuntimeError: If the agent process fails or writes invalid output.
    """
    workspace = _agents_dir() / spec.workspace
    workspace.mkdir(parents=True, exist_ok=True)
    forecast_path = workspace / "forecast.json"
    methodology_path = workspace / "methodology.md"
    _remove_stale_outputs(forecast_path, methodology_path)

    prompt = _build_prompt(snapshot.export_path, workspace)
    runner = _agents_dir() / "run.sh"
    if not runner.exists():
        raise RuntimeError(f"Agent runner not found: {runner}")

    try:
        subprocess.run(
            [str(runner), spec.workspace],
            cwd=_repo_root(),
            input=prompt,
            text=True,
            capture_output=True,
            timeout=AGENT_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip()
        raise RuntimeError(
            f"{spec.name} failed with exit code {error.returncode}: {stderr or 'no stderr'}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"{spec.name} exceeded {AGENT_TIMEOUT_SECONDS} seconds."
        ) from error

    if not forecast_path.exists():
        raise RuntimeError(f"{spec.name} did not write {forecast_path}.")
    if not methodology_path.exists():
        raise RuntimeError(f"{spec.name} did not write {methodology_path}.")

    points = _load_forecast_points(forecast_path, snapshot.latest_activity_time)
    methodology = methodology_path.read_text(encoding="utf-8").strip()
    if not methodology:
        raise RuntimeError(f"{spec.name} wrote an empty {methodology_path.name}.")

    return Forecast(
        name=spec.name,
        slug=spec.slug,
        points=points,
        methodology=methodology,
        diagnostics={
            "workspace": str(workspace.relative_to(_repo_root())),
            "forecast_count": len(points),
        },
    )


def prompt_hash() -> str:
    """Return the SHA-256 hash of the shared static prompt."""
    prompt_path = _agents_dir() / "prompt" / "prompt.md"
    digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _build_prompt(export_path: Path, workspace: Path) -> str:
    """Return the full prompt passed to one agent."""
    static_prompt = (
        (_agents_dir() / "prompt" / "prompt.md").read_text(encoding="utf-8").strip()
    )
    return (
        f"Export CSV to use: {export_path.resolve()}\n"
        f"Your workspace: {workspace.resolve()}\n\n"
        f"{static_prompt}\n"
    )


def _load_forecast_points(
    forecast_path: Path,
    latest_activity_time: datetime,
) -> list[ForecastPoint]:
    """Load and validate one agent forecast payload."""
    payload = json.loads(forecast_path.read_text(encoding="utf-8"))
    feeds = payload.get("feeds")
    if not isinstance(feeds, list) or not feeds:
        raise RuntimeError(f"{forecast_path} must contain a non-empty 'feeds' list.")

    points: list[ForecastPoint] = []
    previous_time = latest_activity_time
    horizon_end = latest_activity_time + timedelta(hours=HORIZON_HOURS)

    for item in feeds:
        if not isinstance(item, dict):
            raise RuntimeError(f"{forecast_path} contains a non-object feed entry.")
        if "time" not in item or "volume_oz" not in item:
            raise RuntimeError(
                f"{forecast_path} feed entries must include 'time' and 'volume_oz'."
            )

        point_time = _parse_datetime(item["time"], forecast_path)
        if point_time <= previous_time:
            raise RuntimeError(
                f"{forecast_path} feed times must be strictly increasing and after "
                "the latest recorded activity."
            )
        if point_time >= horizon_end:
            raise RuntimeError(
                f"{forecast_path} contains a feed outside the 24-hour horizon."
            )
        volume_oz = float(item["volume_oz"])
        if not math.isfinite(volume_oz) or volume_oz <= 0:
            raise RuntimeError(
                f"{forecast_path} feed volumes must be positive finite numbers."
            )

        gap_hours = (point_time - previous_time).total_seconds() / 3600
        points.append(
            ForecastPoint(
                time=point_time,
                volume_oz=volume_oz,
                gap_hours=gap_hours,
            )
        )
        previous_time = point_time

    return points


def _parse_datetime(raw_value: object, forecast_path: Path):
    """Parse one ISO timestamp from an agent output file."""
    if not isinstance(raw_value, str):
        raise RuntimeError(f"{forecast_path} uses a non-string feed time.")
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError as error:
        raise RuntimeError(
            f"{forecast_path} contains a non-ISO feed time: {raw_value!r}"
        ) from error


def _remove_stale_outputs(*paths: Path) -> None:
    """Delete old required outputs so stale files cannot satisfy a failed run."""
    for path in paths:
        if path.exists():
            path.unlink()


def _agents_dir() -> Path:
    """Return the tracked agents directory."""
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    """Return the repository root."""
    return _agents_dir().parent
