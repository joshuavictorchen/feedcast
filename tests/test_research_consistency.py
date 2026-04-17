"""Behavior tests for research doc and artifact consistency checks."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
import re
from pathlib import Path

from feedcast.research.consistency import _parse_baseline_params, find_consistency_issues


REPO_ROOT = Path(__file__).resolve().parents[1]


class ResearchConsistencyTests(unittest.TestCase):
    """Verify the research consistency guard against real repo workflows."""

    def test_clean_copied_dirs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            self._copy_tree(
                "feedcast/models/slot_drift",
                repo_root / "feedcast/models/slot_drift",
            )
            self._copy_tree(
                "feedcast/research/volume_gap_relationship",
                repo_root / "feedcast/research/volume_gap_relationship",
            )
            self._init_git_repo(repo_root)

            issues = find_consistency_issues(
                [
                    repo_root / "feedcast/models/slot_drift",
                    repo_root / "feedcast/research/volume_gap_relationship",
                ],
                repo_root=repo_root,
            )

        self.assertEqual(issues, [])

    def test_model_change_requires_docs_and_refreshed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            model_dir = repo_root / "feedcast/models/slot_drift"
            self._copy_tree("feedcast/models/slot_drift", model_dir)
            self._init_git_repo(repo_root)

            model_path = model_dir / "model.py"
            results_text = (model_dir / "artifacts/research_results.txt").read_text(
                encoding="utf-8",
            )
            baseline_params = _parse_baseline_params(results_text)
            self.assertIsNotNone(baseline_params)
            baseline_lookback = int(baseline_params["LOOKBACK_DAYS"])
            updated_lookback = baseline_lookback + 1

            updated_source, replacements = re.subn(
                r"LOOKBACK_DAYS = \d+",
                f"LOOKBACK_DAYS = {updated_lookback}",
                model_path.read_text(encoding="utf-8"),
                count=1,
            )
            self.assertEqual(replacements, 1)
            model_path.write_text(updated_source, encoding="utf-8")

            issues = find_consistency_issues([model_dir], repo_root=repo_root)

        output = "\n".join(issues)
        self.assertIn("model.py changed without a matching CHANGELOG.md update", output)
        self.assertIn("model.py changed without a matching research.md update", output)
        self.assertIn(f"baseline {baseline_params}", output)
        self.assertIn(f"'LOOKBACK_DAYS': {updated_lookback}", output)

    def test_agents_workspace_clean_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            self._copy_tree(
                "feedcast/agents",
                repo_root / "feedcast/agents",
            )
            self._init_git_repo(repo_root)

            issues = find_consistency_issues([], repo_root=repo_root)

        self.assertEqual(issues, [])

    def test_agents_model_change_requires_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            agents_dir = repo_root / "feedcast/agents"
            self._copy_tree("feedcast/agents", agents_dir)
            self._init_git_repo(repo_root)

            model_path = agents_dir / "model.py"
            model_path.write_text(
                model_path.read_text(encoding="utf-8") + "\n# tuning tweak\n",
                encoding="utf-8",
            )

            issues = find_consistency_issues([], repo_root=repo_root)

        output = "\n".join(issues)
        self.assertIn(
            "feedcast/agents: model.py changed without a matching CHANGELOG.md update",
            output,
        )

    def test_agents_extra_python_files_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            agents_dir = repo_root / "feedcast/agents"
            self._copy_tree("feedcast/agents", agents_dir)
            self._init_git_repo(repo_root)

            (agents_dir / "research_helper.py").write_text(
                "# exploration helper\n",
                encoding="utf-8",
            )

            issues = find_consistency_issues([], repo_root=repo_root)

        self.assertEqual(issues, [])

    def test_metadata_and_volatile_artifacts_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            research_dir = repo_root / "feedcast/research/volume_gap_relationship"
            self._copy_tree("feedcast/research/volume_gap_relationship", research_dir)
            self._init_git_repo(repo_root)

            results_path = research_dir / "artifacts/research_results.txt"
            results_path.write_text(
                results_path.read_text(encoding="utf-8").replace(
                    "Export: exports/export_narababy_silas_20260327.csv",
                    "Export: exports/export_narababy_silas_20260410.csv\nRun: 2026-04-10T00:47:28",
                ),
                encoding="utf-8",
            )
            summary_path = research_dir / "artifacts/summary.json"
            summary_path.write_text(
                summary_path.read_text(encoding="utf-8").replace(
                    '"views": {',
                    '"run_timestamp": "2026-04-10T00:47:28",\n  "views": {',
                ),
                encoding="utf-8",
            )

            issues = find_consistency_issues([research_dir], repo_root=repo_root)

        output = "\n".join(issues)
        self.assertIn("research.md Export (exports/export_narababy_silas_20260327.csv) does not match", output)
        self.assertIn("remove volatile Run timestamps", output)
        self.assertIn("remove volatile run_timestamp fields", output)

    def _copy_tree(self, source_relative: str, destination: Path) -> None:
        """Copy a tracked repo directory into a temporary git repo."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO_ROOT / source_relative, destination)

    def _init_git_repo(self, repo_root: Path) -> None:
        """Create a committed git repo so change detection has a baseline."""
        subprocess.run(
            ["git", "init"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "tests@example.com"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Feedcast Tests"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-m", "init"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
