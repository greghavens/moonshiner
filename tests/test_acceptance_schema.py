from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from common import deterministic_review_accepted  # noqa: E402
import publish_queue  # noqa: E402


class AcceptanceSchemaTests(unittest.TestCase):
    def test_accepts_both_coding_and_tool_use_deterministic_schemas(self):
        self.assertTrue(deterministic_review_accepted(
            {"deterministic": {"passed": True}}))
        self.assertTrue(deterministic_review_accepted(
            {"deterministic": {"accepted": True}}))
        self.assertFalse(deterministic_review_accepted(
            {"deterministic": {"passed": False}}))

    def test_publisher_discovers_accepted_coding_review(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = pathlib.Path(directory)
            (traces / "reviews").mkdir()
            (traces / "meta").mkdir()
            review = {
                "accepted": True,
                "deterministic": {"passed": True},
                "judge": {"model_attested": True},
            }
            (traces / "reviews" / "coding-seed.json").write_text(json.dumps(review))
            (traces / "meta" / "coding-seed.json").write_text("{}")
            with mock.patch.object(publish_queue, "TRACES", traces):
                ready = publish_queue.accepted_tasks()
            self.assertEqual([task for _, task in ready], ["coding-seed"])


if __name__ == "__main__":
    unittest.main()
