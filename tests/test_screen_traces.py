"""Deterministic screening gates and repair-feedback synthesis. Model-free.

Only offline behavior is exercised: the freshness gate (seed fingerprint) and
the feedback text a rejection turns into. Patch-replay/judge paths need a real
workspace + runtime and are covered by the pipeline, not here.
"""
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import common  # noqa: E402
import screen_traces as scr  # noqa: E402

# A raw_path that cannot exist, so no trace file is read and the scan stays
# fully offline regardless of what is (or isn't) on disk.
NOWHERE = "raw/__unit_test_no_such_trace__.jsonl"


class DeterministicGate(unittest.TestCase):
    def setUp(self):
        self.seed = common.load_seeds()[0]
        self.fingerprint = common.seed_fingerprint(self.seed)

    def test_seed_fresh_true_when_fingerprint_matches(self):
        meta = {"seed_fingerprint": self.fingerprint, "raw_path": NOWHERE}
        result = scr.deterministic_screen(self.seed, meta)
        self.assertTrue(result["gates"]["seed_fresh"])

    def test_stale_seed_is_fail_closed(self):
        meta = {"seed_fingerprint": "not-the-real-hash", "raw_path": NOWHERE}
        result = scr.deterministic_screen(self.seed, meta)
        self.assertFalse(result["gates"]["seed_fresh"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"][0],
                         "stale: seed changed since trace was generated")


class Feedback(unittest.TestCase):
    def test_deterministic_failures_become_bullets(self):
        review = {"deterministic": {"failures": ["patch replay mismatch"],
                                    "static_findings": []}}
        self.assertIn("patch replay mismatch", scr.feedback_from_review(review))

    def test_verdict_category_finding_is_reported(self):
        review = {"verdict": {"bugs_and_regressions":
                              {"found": True, "detail": "off-by-one"}}}
        text = scr.feedback_from_review(review)
        self.assertIn("bugs_and_regressions", text)
        self.assertIn("off-by-one", text)

    def test_reason_is_the_last_resort(self):
        self.assertIn("boom", scr.feedback_from_review({"reason": "boom"}))

    def test_static_finding_is_reported(self):
        review = {"deterministic": {"failures": [], "static_findings": [
            {"kind": "secret", "detail": "sk-live token in output"}]}}
        text = scr.feedback_from_review(review)
        self.assertIn("secret", text)


if __name__ == "__main__":
    unittest.main()
