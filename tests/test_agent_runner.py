"""Tests for reusable agent invocation and forecast validation."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from feedcast.agent_runner import invoke_agent, validate_agent_forecast


class InvokeAgentTests(unittest.TestCase):
    """Agent CLI invocation behavior."""

    def test_invoke_agent_renders_prompt_and_dispatches_claude(self) -> None:
        """Prompt placeholders should be rendered before dispatch."""
        with tempfile.TemporaryDirectory(
            prefix="feedcast-agent-runner-test-"
        ) as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.md"
            prompt_path.write_text(
                "Hello {{name}} from {{place}}.\n",
                encoding="utf-8",
            )
            expected = subprocess.CompletedProcess(
                args=["claude"],
                returncode=0,
                stdout="ok",
                stderr="",
            )

            with patch(
                "feedcast.agent_runner.subprocess.run",
                return_value=expected,
            ) as run_mock:
                result = invoke_agent(
                    "claude",
                    prompt_path,
                    {"name": "Silas", "place": "home"},
                    timeout=123,
                )

        self.assertIs(result, expected)
        call_args, call_kwargs = run_mock.call_args
        command = call_args[0]
        self.assertEqual(
            command[:6],
            ["claude", "--model", "claude-opus-4-6", "--effort", "max", "-p"],
        )
        self.assertEqual(command[-1], "Hello Silas from home.\n")
        self.assertEqual(call_kwargs["timeout"], 123)
        self.assertTrue(call_kwargs["check"])

    def test_invoke_agent_rejects_missing_context(self) -> None:
        """Missing placeholders should fail before invoking the CLI."""
        with tempfile.TemporaryDirectory(
            prefix="feedcast-agent-runner-test-"
        ) as temp_dir:
            prompt_path = Path(temp_dir) / "prompt.md"
            prompt_path.write_text("Hello {{name}}.\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unresolved placeholders"):
                invoke_agent("claude", prompt_path, {})


class ValidateAgentForecastTests(unittest.TestCase):
    """Forecast payload validation behavior."""

    def test_validate_agent_forecast_returns_points(self) -> None:
        """A valid payload should deserialize into forecast points."""
        with tempfile.TemporaryDirectory(
            prefix="feedcast-agent-runner-test-"
        ) as temp_dir:
            forecast_path = Path(temp_dir) / "forecast.json"
            forecast_path.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {"time": "2026-03-25T03:00:00", "volume_oz": 3.5},
                            {"time": "2026-03-25T06:30:00", "volume_oz": 4.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            points = validate_agent_forecast(
                forecast_path,
                latest_activity_time=datetime(2026, 3, 25, 0, 0, 0),
            )

        self.assertEqual(len(points), 2)
        self.assertEqual(points[0].gap_hours, 3.0)
        self.assertEqual(points[1].gap_hours, 3.5)

    def test_validate_agent_forecast_rejects_non_increasing_times(self) -> None:
        """Forecast times must be strictly increasing after the cutoff."""
        with tempfile.TemporaryDirectory(
            prefix="feedcast-agent-runner-test-"
        ) as temp_dir:
            forecast_path = Path(temp_dir) / "forecast.json"
            forecast_path.write_text(
                json.dumps(
                    {
                        "feeds": [
                            {"time": "2026-03-25T03:00:00", "volume_oz": 3.5},
                            {"time": "2026-03-25T02:30:00", "volume_oz": 4.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "strictly increasing"):
                validate_agent_forecast(
                    forecast_path,
                    latest_activity_time=datetime(2026, 3, 25, 0, 0, 0),
                )
