"""Unit tests for secret scrubbing before cloud export."""

import unittest

from turingmind_mcp.secret_scrub import scrub_secrets


class TestSecretScrub(unittest.TestCase):
    def test_scrubs_openai_key(self):
        text = "Use key sk-abcdefghijklmnopqrstuvwxyz1234567890 here"
        result = scrub_secrets(text)
        self.assertIn("[REDACTED_SECRET]", result)
        self.assertNotIn("sk-abc", result)

    def test_scrubs_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = scrub_secrets(text)
        self.assertIn("[REDACTED_SECRET]", result)

    def test_passthrough_empty(self):
        self.assertIsNone(scrub_secrets(None))
        self.assertEqual(scrub_secrets(""), "")
