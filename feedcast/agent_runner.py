"""Reusable CLI agent invocation and forecast validation utilities."""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from feedcast.data import ForecastPoint

logger = logging.getLogger(__name__)

AGENT_TIMEOUT_SECONDS = 1200
AGENT_TARGET_RUNTIME_SECONDS = 800
_PLACEHOLDER_PATTERN = re.compile(r"{{\s*([A-Za-z0-9_]+)\s*}}")


def invoke_agent(
    agent: str,
    prompt_path: Path,
    context: dict[str, str],
    timeout: int = AGENT_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Render a prompt file and invoke one CLI agent.

    Args:
        agent: Agent CLI to invoke (`claude` or `codex`).
        prompt_path: Prompt markdown file.
        context: Placeholder values used to render the prompt.
        timeout: Process timeout in seconds.

    Returns:
        The completed subprocess result.

    Raises:
        ValueError: If the prompt has unresolved placeholders or the agent is
            unsupported.
        RuntimeError: If the CLI exits non-zero or times out.
    """
    prompt = _render_prompt(prompt_path, context)

    try:
        return subprocess.run(
            _agent_command(agent, prompt),
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip()
        raise RuntimeError(
            f"{agent} failed with exit code {error.returncode}: "
            f"{stderr or 'no stderr'}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{agent} exceeded {timeout} seconds.") from error


def validate_agent_forecast(
    forecast_path: Path,
    latest_activity_time: datetime,
    horizon_hours: int | None = None,
) -> list[ForecastPoint]:
    """Load and validate one agent forecast payload.

    Args:
        forecast_path: Path to the agent-written `forecast.json`.
        latest_activity_time: Latest observed activity time in the export.
        horizon_hours: When set, points beyond the horizon are filtered out.

    Returns:
        Parsed forecast points in chronological order.

    Raises:
        RuntimeError: If the payload shape or values are invalid.
    """
    payload = json.loads(forecast_path.read_text(encoding="utf-8"))
    feeds = payload.get("feeds")
    if not isinstance(feeds, list) or not feeds:
        raise RuntimeError(f"{forecast_path} must contain a non-empty 'feeds' list.")

    horizon_end = (
        latest_activity_time + timedelta(hours=horizon_hours)
        if horizon_hours is not None
        else None
    )

    points: list[ForecastPoint] = []
    previous_time = latest_activity_time
    clipped_count = 0

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

        try:
            volume_oz = float(item["volume_oz"])
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                f"{forecast_path} feed volumes must be numeric values."
            ) from error

        if not math.isfinite(volume_oz) or volume_oz <= 0:
            raise RuntimeError(
                f"{forecast_path} feed volumes must be positive finite numbers."
            )

        # Filter points beyond the forecast horizon
        if horizon_end is not None and point_time > horizon_end:
            clipped_count += 1
            previous_time = point_time
            continue

        gap_hours = (point_time - previous_time).total_seconds() / 3600
        points.append(
            ForecastPoint(
                time=point_time,
                volume_oz=volume_oz,
                gap_hours=gap_hours,
            )
        )
        previous_time = point_time

    if clipped_count:
        logger.warning(
            "%s: filtered %d point(s) beyond %dh horizon",
            forecast_path, clipped_count, horizon_hours,
        )

    if not points:
        raise RuntimeError(
            f"{forecast_path} contains no feeds within the {horizon_hours}h horizon."
        )

    return points


def _render_prompt(prompt_path: Path, context: dict[str, str]) -> str:
    """Render `{{placeholder}}` values in one prompt file."""
    template = prompt_path.read_text(encoding="utf-8")
    missing_keys: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            missing_keys.add(key)
            return match.group(0)
        return context[key]

    rendered = _PLACEHOLDER_PATTERN.sub(replace, template)
    if missing_keys:
        missing_display = ", ".join(sorted(missing_keys))
        raise ValueError(
            f"{prompt_path} contains unresolved placeholders: {missing_display}."
        )
    return rendered


def _agent_command(agent: str, prompt: str) -> list[str]:
    """Return the CLI command used to invoke one agent."""
    if agent == "claude":
        return [
            "claude",
            "--model",
            "claude-opus-4-7",
            "--effort",
            "max",
            "-p",
            prompt,
        ]
    if agent == "codex":
        return [
            "codex",
            "exec",
            "--model",
            "gpt-5.4",
            "-c",
            'model_reasoning_effort="xhigh"',
            prompt,
        ]
    raise ValueError(f"Unsupported agent: {agent}")


def _parse_datetime(raw_value: object, forecast_path: Path) -> datetime:
    """Parse one ISO timestamp from an agent output file."""
    if not isinstance(raw_value, str):
        raise RuntimeError(f"{forecast_path} uses a non-string feed time.")
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError as error:
        raise RuntimeError(
            f"{forecast_path} contains a non-ISO feed time: {raw_value!r}"
        ) from error


def _repo_root() -> Path:
    """Return the repository root."""
    return Path(__file__).resolve().parent.parent
