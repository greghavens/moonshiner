"""Deterministic screening gates and repair-feedback synthesis. Model-free.

Only offline behavior is exercised: the freshness gate (seed fingerprint), the
feedback text a rejection turns into, and patch replay against a throwaway git
workspace (a real ``git apply`` — an invalid flag there once auto-rejected
every trace). Judge paths need a runtime and are covered by the pipeline.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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


class PatchReplay(unittest.TestCase):
    """apply_candidate_patch drives a real ``git apply`` in a throwaway repo."""

    def _workspace(self) -> pathlib.Path:
        workspace = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(workspace)]))
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
        (workspace / "hello.txt").write_text("one\n")
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
        return workspace

    def test_valid_patch_applies(self):
        workspace = self._workspace()
        patch = ("diff --git a/hello.txt b/hello.txt\n"
                 "--- a/hello.txt\n+++ b/hello.txt\n"
                 "@@ -1 +1 @@\n-one\n+two\n")
        ok, detail = scr.apply_candidate_patch(workspace, patch)
        self.assertTrue(ok, detail)
        self.assertEqual((workspace / "hello.txt").read_text(), "two\n")

    def test_git_rejects_flags_not_content(self):
        # A garbage patch must fail on CONTENT; if git errors on our own
        # command line instead (e.g. a bad --whitespace value), the stderr
        # names the option and every screen would fail closed.
        workspace = self._workspace()
        ok, detail = scr.apply_candidate_patch(workspace, "not a patch\n")
        self.assertFalse(ok)
        self.assertNotIn("unrecognized whitespace option", detail)

    def test_empty_patch_is_ok(self):
        ok, detail = scr.apply_candidate_patch(self._workspace(), "   \n")
        self.assertTrue(ok)
        self.assertEqual(detail, "(empty patch)")


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


class JudgeErrorLane(unittest.TestCase):
    """Malformed judge output re-reviews the same trace; judged rejections
    still reject. Keeps a judge-side fault (e.g. a verdict truncated before
    its closing brace) from routing to the repair lane's paid re-generation."""

    GOOD_BLOCKS = {c: {"found": False} for c in scr.REVIEW_CATEGORIES}
    META_BASE = {"passed": True, "raw_sha256": "r", "diff_sha256": "d",
                 "seed_fingerprint": "f"}
    REVIEW_BASE = {"raw_sha256": "r", "diff_sha256": "d",
                   "seed_fingerprint": "f"}

    def _well_formed(self, **over) -> dict:
        verdict = dict(self.GOOD_BLOCKS, requirements=[], verdict="accept")
        verdict.update(over)
        return verdict

    def test_truncated_or_missing_verdict_is_malformed(self):
        self.assertTrue(scr.verdict_is_malformed(None))
        # what the parser falls back to when the top-level object is truncated
        self.assertTrue(scr.verdict_is_malformed(
            {"requirement": "x", "status": "met"}))
        self.assertTrue(scr.verdict_is_malformed(
            dict(self.GOOD_BLOCKS, verdict="accept")))  # no requirements list

    def test_judged_rejection_is_not_malformed(self):
        self.assertFalse(scr.verdict_is_malformed(
            self._well_formed(verdict="reject")))
        finding = self._well_formed()
        finding["added_scope"] = {"found": True, "detail": "extra file"}
        self.assertFalse(scr.verdict_is_malformed(finding))

    def _tree(self, review: dict | None, meta: dict) -> None:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        (tmp / "meta").mkdir()
        (tmp / "reviews").mkdir()
        (tmp / "meta" / "unit-x.json").write_text(json.dumps(meta))
        if review is not None:
            (tmp / "reviews" / "unit-x.json").write_text(json.dumps(review))
        for attr, path in (("META", tmp / "meta"), ("REVIEWS", tmp / "reviews")):
            patcher = mock.patch.object(scr, attr, path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_judge_error_keeps_seed_pending(self):
        self._tree(dict(self.REVIEW_BASE, status="judge_error",
                        accepted=False, judge_errors=1), self.META_BASE)
        self.assertTrue(scr.needs_first_pass({"id": "unit-x"}))

    def test_judge_error_stops_pending_at_limit(self):
        self._tree(dict(self.REVIEW_BASE, status="judge_error", accepted=False,
                        judge_errors=scr.JUDGE_ERROR_LIMIT), self.META_BASE)
        self.assertFalse(scr.needs_first_pass({"id": "unit-x"}))

    def test_real_rejection_is_not_pending(self):
        self._tree(dict(self.REVIEW_BASE, status="review_reject",
                        accepted=False), self.META_BASE)
        self.assertFalse(scr.needs_first_pass({"id": "unit-x"}))

    def test_prior_errors_reset_when_trace_changes(self):
        self._tree(dict(self.REVIEW_BASE, status="judge_error", accepted=False,
                        judge_errors=2), self.META_BASE)
        self.assertEqual(scr._prior_judge_errors("unit-x", self.META_BASE), 2)
        changed = dict(self.META_BASE, raw_sha256="other")
        self.assertEqual(scr._prior_judge_errors("unit-x", changed), 0)


if __name__ == "__main__":
    unittest.main()
