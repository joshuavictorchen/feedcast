"""Tests for report diagnostics and agent-insights rendering."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from feedcast.clustering import BASE_GAP_MINUTES
from feedcast.data import ExportSnapshot, Forecast, ForecastPoint
from feedcast.report import _forecast_diagnostics_entry, _render_report
from feedcast.tracker import Retrospective


def _point(cutoff: datetime, offset_hours: float, volume: float = 3.0) -> ForecastPoint:
    """Build one forecast point at a fixed offset from cutoff."""
    return ForecastPoint(
        time=cutoff + timedelta(hours=offset_hours),
        volume_oz=volume,
        gap_hours=offset_hours,
    )


CUTOFF = datetime(2026, 3, 25, 0, 0, 0)


class DiagnosticsEntryTests(unittest.TestCase):
    """Episode count diagnostics per forecast."""

    def test_cluster_predictions_counted_correctly(self) -> None:
        """Diagnostics reflect raw vs episode counts."""
        gap_hours = (BASE_GAP_MINUTES - 10) / 60
        points = [
            _point(CUTOFF, 3.0, volume=3.5),
            _point(CUTOFF, 3.0 + gap_hours, volume=1.0),
            _point(CUTOFF, 6.0, volume=3.5),
        ]
        forecast = Forecast(
            name="Test",
            slug="test",
            points=points,
            methodology="Test methodology.",
            diagnostics={},
        )
        entry = _forecast_diagnostics_entry(forecast)
        self.assertEqual(entry["raw_point_count"], 3)
        self.assertEqual(entry["episode_count"], 2)
        self.assertEqual(entry["collapsed_attachments"], 1)

    def test_no_clusters_zero_attachments(self) -> None:
        """When no clustering occurs, collapsed_attachments is zero."""
        points = [
            _point(CUTOFF, 3.0),
            _point(CUTOFF, 6.0),
        ]
        forecast = Forecast(
            name="Clean",
            slug="clean",
            points=points,
            methodology="Clean methodology.",
            diagnostics={},
        )
        entry = _forecast_diagnostics_entry(forecast)
        self.assertEqual(entry["raw_point_count"], 2)
        self.assertEqual(entry["episode_count"], 2)
        self.assertEqual(entry["collapsed_attachments"], 0)

    def test_unavailable_forecast_zero_counts(self) -> None:
        """Unavailable forecast has zero counts."""
        forecast = Forecast(
            name="Broken",
            slug="broken",
            points=[],
            methodology="",
            diagnostics={},
            available=False,
            error_message="Failed.",
        )
        entry = _forecast_diagnostics_entry(forecast)
        self.assertEqual(entry["raw_point_count"], 0)
        self.assertEqual(entry["episode_count"], 0)
        self.assertEqual(entry["collapsed_attachments"], 0)


# ---------------------------------------------------------------------------
# Agent insights rendering
# ---------------------------------------------------------------------------

_RENDER_SNAPSHOT = ExportSnapshot(
    export_path=Path("exports/fake.csv"),
    activities=[],
    latest_activity_time=CUTOFF,
    dataset_id="sha256:fake",
    source_hash="sha256:fake",
)
_RENDER_FORECAST = Forecast(
    name="Test Model",
    slug="test_model",
    points=[_point(CUTOFF, 3.0)],
    methodology="Test methodology.",
    diagnostics={},
)
_RENDER_META = {"git_commit": "abc1234", "git_dirty": False}


def _render_to_tmpdir(agent_insights: str | None = None) -> Path:
    """Render a minimal report into a temp directory and return it."""
    output_dir = Path(tempfile.mkdtemp(prefix="test-report-"))
    _render_report(
        output_dir=output_dir,
        snapshot=_RENDER_SNAPSHOT,
        all_forecasts=[_RENDER_FORECAST],
        featured_slug="test_model",
        cutoff=CUTOFF,
        retrospective=Retrospective(available=False),
        historical_accuracy=[],
        tracker_meta=_RENDER_META,
        agent_insights=agent_insights,
    )
    return output_dir


class AgentInsightsRenderTests(unittest.TestCase):
    """Agent insights integration in report rendering."""

    def _render(self, agent_insights: str | None = None) -> Path:
        output_dir = _render_to_tmpdir(agent_insights)
        self.addCleanup(shutil.rmtree, output_dir)
        return output_dir

    def test_insights_file_written(self) -> None:
        """agent-insights.md is published when insights are provided."""
        output_dir = self._render("Test trend content here.")
        insights_path = output_dir / "agent-insights.md"
        self.assertTrue(insights_path.exists())
        self.assertIn("Test trend content here.", insights_path.read_text())

    def test_insights_rendered_in_report(self) -> None:
        """Template includes the insights section."""
        output_dir = self._render("Test trend content here.")
        report_text = (output_dir / "report.md").read_text()
        self.assertIn("## Trend Insights", report_text)
        self.assertIn("Test trend content here.", report_text)

    def test_report_strips_embedded_insights_heading(self) -> None:
        """Main report owns the section heading for embedded insights."""
        content = "## Trend Insights — Last 14 Days\n\nTest trend content here."
        output_dir = self._render(content)
        report_text = (output_dir / "report.md").read_text()
        self.assertEqual(report_text.count("## Trend Insights"), 1)
        self.assertNotIn("## Trend Insights — Last 14 Days", report_text)
        self.assertIn("Test trend content here.", report_text)

    def test_standalone_insights_artifact_keeps_original_heading(self) -> None:
        """Standalone insights artifact remains a complete markdown document."""
        content = "## Trend Insights — Last 14 Days\n\nTest trend content here."
        output_dir = self._render(content)
        insights_text = (output_dir / "agent-insights.md").read_text()
        self.assertIn("## Trend Insights — Last 14 Days", insights_text)

    def test_no_file_when_none(self) -> None:
        """agent-insights.md is NOT published when insights are None."""
        output_dir = self._render(None)
        self.assertFalse((output_dir / "agent-insights.md").exists())

    def test_no_section_when_none(self) -> None:
        """Template omits the insights section when None."""
        output_dir = self._render(None)
        report_text = (output_dir / "report.md").read_text()
        self.assertNotIn("## Trend Insights", report_text)

    def test_methodology_section_prioritizes_cross_model_outputs(self) -> None:
        """Agent Inference and Consensus Blend lead the Methodologies section."""
        def _forecast(slug: str, name: str) -> Forecast:
            return Forecast(
                name=name,
                slug=slug,
                points=[_point(CUTOFF, 3.0)],
                methodology=f"{name} methodology body.",
                diagnostics={},
            )

        # Pipeline order: scripted models, then consensus, then agent
        forecasts = [
            _forecast("slot_drift", "Slot Drift"),
            _forecast("analog_trajectory", "Analog Trajectory"),
            _forecast("latent_hunger", "Latent Hunger State"),
            _forecast("survival_hazard", "Survival Hazard"),
            _forecast("consensus_blend", "Consensus Blend"),
            _forecast("agent_inference", "Agent Inference"),
        ]

        output_dir = Path(tempfile.mkdtemp(prefix="test-report-"))
        self.addCleanup(shutil.rmtree, output_dir)
        _render_report(
            output_dir=output_dir,
            snapshot=_RENDER_SNAPSHOT,
            all_forecasts=forecasts,
            featured_slug="consensus_blend",
            cutoff=CUTOFF,
            retrospective=Retrospective(available=False),
            historical_accuracy=[],
            tracker_meta=_RENDER_META,
            agent_insights=None,
        )

        report_text = (output_dir / "report.md").read_text()
        expected_order = [
            "### Agent Inference",
            "### Consensus Blend (featured)",
            "### Slot Drift",
            "### Analog Trajectory",
            "### Latent Hunger State",
            "### Survival Hazard",
        ]
        positions = [report_text.find(heading) for heading in expected_order]
        for heading, pos in zip(expected_order, positions):
            self.assertGreater(pos, -1, f"{heading} missing from report")
        self.assertEqual(
            positions,
            sorted(positions),
            f"Methodology section out of order: {list(zip(expected_order, positions))}",
        )

    def test_methodology_section_omits_absent_priority_slugs(self) -> None:
        """Consensus Blend leads when Agent Inference is skipped."""
        forecast_consensus = Forecast(
            name="Consensus Blend",
            slug="consensus_blend",
            points=[_point(CUTOFF, 3.0)],
            methodology="Consensus body.",
            diagnostics={},
        )
        forecast_scripted = Forecast(
            name="Slot Drift",
            slug="slot_drift",
            points=[_point(CUTOFF, 3.0)],
            methodology="Slot drift body.",
            diagnostics={},
        )

        output_dir = Path(tempfile.mkdtemp(prefix="test-report-"))
        self.addCleanup(shutil.rmtree, output_dir)
        _render_report(
            output_dir=output_dir,
            snapshot=_RENDER_SNAPSHOT,
            all_forecasts=[forecast_scripted, forecast_consensus],
            featured_slug="consensus_blend",
            cutoff=CUTOFF,
            retrospective=Retrospective(available=False),
            historical_accuracy=[],
            tracker_meta=_RENDER_META,
            agent_insights=None,
        )

        report_text = (output_dir / "report.md").read_text()
        consensus_pos = report_text.find("### Consensus Blend (featured)")
        slot_pos = report_text.find("### Slot Drift")
        self.assertGreater(consensus_pos, -1)
        self.assertGreater(slot_pos, -1)
        self.assertLess(consensus_pos, slot_pos)

    def test_report_strips_embedded_methodology_heading(self) -> None:
        """Methodology content should not duplicate the report-owned heading."""
        forecast = Forecast(
            name="Method With Heading",
            slug="method_with_heading",
            points=[_point(CUTOFF, 3.0)],
            methodology="# Internal Methodology Title\n\nMethod body.",
            diagnostics={},
        )
        output_dir = Path(tempfile.mkdtemp(prefix="test-report-"))
        self.addCleanup(shutil.rmtree, output_dir)

        _render_report(
            output_dir=output_dir,
            snapshot=_RENDER_SNAPSHOT,
            all_forecasts=[forecast],
            featured_slug="method_with_heading",
            cutoff=CUTOFF,
            retrospective=Retrospective(available=False),
            historical_accuracy=[],
            tracker_meta=_RENDER_META,
            agent_insights=None,
        )

        report_text = (output_dir / "report.md").read_text()
        self.assertIn("### Method With Heading (featured)", report_text)
        self.assertNotIn("# Internal Methodology Title", report_text)
        self.assertIn("Method body.", report_text)


if __name__ == "__main__":
    unittest.main()
