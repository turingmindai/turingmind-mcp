"""Unit tests for cloud memory sync (mocked Postgres)."""

import unittest
from unittest.mock import MagicMock, patch

from turingmind_mcp.v2_engine import postgres


class TestSyncMemoryEntries(unittest.TestCase):
    @patch.object(postgres, "get_connection")
    @patch.object(postgres, "psycopg2", MagicMock())
    def test_scrubs_secrets_in_content(self, mock_get_connection):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_connection.return_value.__enter__.return_value = mock_conn

        entries = [{
            "memory_id": "mem-1",
            "type": "explicit_rule",
            "content": "Never log sk-abcdefghijklmnopqrstuvwxyz1234567890",
            "scope": "repo",
            "status": "active",
            "confidence": 0.9,
        }]

        synced = postgres.sync_memory_entries("owner/repo", entries)

        self.assertEqual(synced, 1)
        mock_cursor.execute.assert_called_once()
        sql_args = mock_cursor.execute.call_args[0][1]
        self.assertIn("[REDACTED_SECRET]", sql_args[3])
        self.assertNotIn("sk-abc", sql_args[3])

    @patch.object(postgres, "psycopg2", None)
    def test_returns_zero_without_psycopg2(self):
        synced = postgres.sync_memory_entries("owner/repo", [{
            "memory_id": "m1",
            "type": "explicit_rule",
            "content": "ok",
            "scope": "repo",
            "status": "active",
        }])
        self.assertEqual(synced, 0)
