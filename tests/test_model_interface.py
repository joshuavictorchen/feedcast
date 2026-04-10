"""Interface tests for the model-owned input shaping boundary.

Architectural invariant: shared orchestration passes raw activities to
models and does not choose model-specific input shaping (breastfeed
merge policy, episode collapsing, etc.). Models own their event
construction.

The pipeline may still build events for its own non-model consumers
(consensus blend history, report charts, scoring actuals). Those are
explicit pipeline policy choices, not model-input shaping.
"""

from __future__ import annotations

import dataclasses
import inspect
import unittest
from datetime import datetime
from unittest.mock import patch

from feedcast.data import Activity, Forecast, ForecastPoint
from feedcast.models import MODELS, ModelSpec, run_all_models
from feedcast.models.shared import ForecastUnavailable


class ModelSpecTests(unittest.TestCase):
    """ModelSpec should not control model input shaping."""

    def test_no_merge_window_field(self) -> None:
        """ModelSpec should not have a merge_window_minutes field."""
        field_names = {f.name for f in dataclasses.fields(ModelSpec)}
        self.assertNotIn(
            "merge_window_minutes",
            field_names,
            "ModelSpec should not control breastfeed merge policy; "
            "models own their input shaping.",
        )

    def test_all_models_accept_activities(self) -> None:
        """Every registered model function should accept list[Activity]."""
        for spec in MODELS:
            sig = inspect.signature(spec.forecast_fn)
            first_param = list(sig.parameters.values())[0]
            annotation = first_param.annotation
            self.assertIn(
                "Activity",
                str(annotation),
                f"{spec.slug}: first parameter should accept Activity, "
                f"got annotation {annotation!r}",
            )


class RunAllModelsLoggingTests(unittest.TestCase):
    """Per-model execution logging for the scripted lineup."""

    def test_run_all_models_logs_per_model_outcomes(self) -> None:
        def available_model(
            activities: list[Activity],
            cutoff: datetime,
            horizon_hours: int,
        ) -> Forecast:
            return Forecast(
                name="Available Model",
                slug="available_model",
                points=[
                    ForecastPoint(
                        time=datetime(2026, 4, 1, 15, 0),
                        volume_oz=3.5,
                        gap_hours=3.0,
                    )
                ],
                methodology="test",
                diagnostics={},
            )

        def unavailable_model(
            activities: list[Activity],
            cutoff: datetime,
            horizon_hours: int,
        ) -> Forecast:
            raise ForecastUnavailable("not enough history")

        test_models = [
            ModelSpec(
                name="Available Model",
                slug="available_model",
                methodology="test",
                forecast_fn=available_model,
            ),
            ModelSpec(
                name="Unavailable Model",
                slug="unavailable_model",
                methodology="test",
                forecast_fn=unavailable_model,
            ),
        ]

        with patch("feedcast.models.MODELS", test_models):
            with self.assertLogs("feedcast.models", level="INFO") as logs:
                forecasts = run_all_models(
                    activities=[],
                    cutoff=datetime(2026, 4, 1, 12, 0),
                    horizon_hours=24,
                )

        output = "\n".join(logs.output)
        self.assertEqual(len(forecasts), 2)
        self.assertIn("Scripted model [available_model]: starting", output)
        self.assertIn("Scripted model [available_model]: done", output)
        self.assertIn("Scripted model [unavailable_model]: starting", output)
        self.assertIn("Scripted model [unavailable_model]: unavailable", output)
        self.assertIn("not enough history", output)


if __name__ == "__main__":
    unittest.main()
