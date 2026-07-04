"""Tests for git churn bootstrap behavior."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from turingmind_mcp.git_churn import GitChurnSnapshot, collect_git_churn


class TestGitChurnBootstrap(unittest.TestCase):
    def test_bootstrap_returns_empty_churn(self):
        workspace = Path(tempfile.mkdtemp())
        with mock.patch(
            "turingmind_mcp.git_churn._run_git",
            side_effect=lambda _root, *args: "abc123" if args[0] == "rev-parse" else "",
        ):
            snap = collect_git_churn(workspace, since_ref=None)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.head, "abc123")
        self.assertEqual(len(snap.modified), 0)
        self.assertEqual(len(snap.deleted), 0)

    def test_no_new_commits_returns_empty_churn(self):
        workspace = Path(tempfile.mkdtemp())
        with mock.patch(
            "turingmind_mcp.git_churn._run_git",
            return_value="abc123",
        ):
            snap = collect_git_churn(workspace, since_ref="abc123")
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertEqual(snap.modified, frozenset())
        self.assertEqual(snap.deleted, frozenset())
