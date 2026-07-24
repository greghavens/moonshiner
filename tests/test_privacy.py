"""Credential and identifying-data redaction gates."""
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from privacy import findings, object_findings, redact, sanitize_object


class Privacy(unittest.TestCase):
    def test_redacted_credential_assignment_is_clean(self):
        redacted, count = redact("api_key=supersecretvalue")
        self.assertEqual(redacted, "[REDACTED_SECRET]")
        self.assertEqual(count, 1)
        self.assertEqual(findings(redacted), [])

    def test_dictionary_keys_are_scrubbed(self):
        scrubbed = sanitize_object({"person@example.com": "value"})
        self.assertEqual(scrubbed, {"[REDACTED_EMAIL]": "value"})
        self.assertEqual(findings(str(scrubbed)), [])

    def test_live_secret_rotation_is_seen_without_cache(self):
        with mock.patch.dict(os.environ, {"VENDOR_API_KEY": "rotated-secret-value"}):
            cleaned, count = redact("token=rotated-secret-value")
        self.assertNotIn("rotated-secret-value", cleaned)
        self.assertGreater(count, 0)

    def test_private_key_and_email_are_blocked(self):
        text = "contact person@example.com\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
        self.assertIn("credential pattern", findings(text))
        self.assertIn("email address", findings(text))

    def test_object_findings_checks_values_without_json_escape_boundaries(self):
        clean = {"messages": [{"content": r'C:\work \"quoted\"'}]}
        self.assertEqual(object_findings(clean), [])
        self.assertIn(
            "email address",
            object_findings({"messages": [{"content": "person@example.com"}]}))

    def test_nested_object_scrub_resolves_live_secrets_once(self):
        value = {"a": ["one", {"b": "two"}], "c": "three"}
        with mock.patch("privacy.live_secret_values",
                        return_value=()) as resolve:
            sanitize_object(value)
        resolve.assert_called_once()

    def test_nested_object_validation_resolves_live_secrets_once(self):
        value = {"a": ["one", {"b": "two"}], "c": "three"}
        with mock.patch("privacy.live_secret_values",
                        return_value=()) as resolve:
            object_findings(value)
        resolve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
