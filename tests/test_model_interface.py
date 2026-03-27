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

from feedcast.data import Activity
from feedcast.models import MODELS, ModelSpec


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


if __name__ == "__main__":
    unittest.main()
