"""Tests for pipeline pre-flight checks."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from feedcast.pipeline import _assert_clean_git_worktree


class CleanWorktreeTests(unittest.TestCase):
    """Pre-flight git cleanliness checks."""

    def test_clean_worktree_passes(self) -> None:
        """An empty porcelain status should pass."""
        result = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout="",
            stderr="",
        )

        with patch("feedcast.pipeline.subprocess.run", return_value=result):
            _assert_clean_git_worktree()

    def test_dirty_worktree_raises(self) -> None:
        """Any porcelain output should fail fast."""
        result = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"],
            returncode=0,
            stdout=" M feedcast/pipeline.py\n",
            stderr="",
        )

        with patch("feedcast.pipeline.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "dirty git worktree"):
                _assert_clean_git_worktree()
