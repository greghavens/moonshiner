"""Credential and identifying-data redaction gates."""
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from privacy import findings, redact


class Privacy(unittest.TestCase):
    def test_redacted_credential_assignment_is_clean(self):
        redacted, count = redact("api_key=supersecretvalue")
        self.assertEqual(redacted, "[REDACTED_SECRET]")
        self.assertEqual(count, 1)
        self.assertEqual(findings(redacted), [])

    def test_live_secret_rotation_is_seen_without_cache(self):
        with mock.patch.dict(os.environ, {"VENDOR_API_KEY": "rotated-secret-value"}):
            cleaned, count = redact("token=rotated-secret-value")
        self.assertNotIn("rotated-secret-value", cleaned)
        self.assertGreater(count, 0)

    def test_private_key_and_email_are_blocked(self):
        text = "contact person@example.com\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
        self.assertIn("credential pattern", findings(text))
        self.assertIn("email address", findings(text))


if __name__ == "__main__":
    unittest.main()
