"""Accepting a trace with no judge behind it -- and saying so.

Self-distillation wants the teacher's real output distribution, failures
included. Publishing only judge-accepted traces selects for the traces the
teacher happened to get right, which is a different distribution from the one
the student is being trained to reproduce; downgrading reasoning effort on
retry biases it the same way. Both can be turned off, and both default to the
behavior every existing run already has.

What must not happen is an unjudged trace that is indistinguishable from a
judged one, anywhere downstream.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import moonshiner as m  # noqa: E402
import build_dataset  # noqa: E402
import reasoning_stepdown  # noqa: E402
import screen_traces  # noqa: E402
import trace_pipeline  # noqa: E402
from review_contract import is_accepted  # noqa: E402


class TheRecordSaysNoJudgeStandsBehindIt(unittest.TestCase):
    def setUp(self):
        self.traces = pathlib.Path(
            tempfile.mkdtemp(prefix="moonshiner-unjudged-"))
        self.addCleanup(shutil.rmtree, self.traces, ignore_errors=True)
        for name in ("meta", "raw", "diffs"):
            (self.traces / name).mkdir(parents=True)
        self.seed = {"id": "unjudged-seed", "prompt": "Do the thing."}
        # A trace whose deterministic gates cannot pass: the raw artifact the
        # meta points at is not there. Acceptance must not depend on them.
        (self.traces / "meta" / "unjudged-seed.json").write_text(json.dumps({
            "id": "unjudged-seed",
            "raw_path": "raw/unjudged-seed.jsonl",
            "raw_sha256": hashlib.sha256(b"absent").hexdigest(),
            "diff_sha256": hashlib.sha256(b"absent").hexdigest(),
            "trace_format": "moonshiner-vllm-openai-v1",
            "teacher": {"runtime": "vllm", "model_attested": True},
        }))
        self.review = screen_traces.unjudged_review(self.seed,
                                                    traces_root=self.traces)

    def test_the_acceptance_carries_the_fact_that_it_was_not_reviewed(self):
        self.assertTrue(is_accepted(self.review))
        self.assertEqual(self.review["status"], screen_traces.UNJUDGED_STATUS)
        self.assertIsNone(self.review["verdict"])
        self.assertTrue(self.review["judge"]["bypassed"])
        self.assertIsNone(self.review["judge"]["runtime"])
        self.assertIsNone(self.review["judge"]["model"])
        self.assertFalse(self.review["judge"]["model_attested"])
        self.assertIn("pipeline.trace.skip_judging", self.review["reason"])

    def test_the_screening_label_is_not_the_reviewed_one(self):
        # Every published row states how it was screened. An unjudged trace
        # must not inherit a label that promises an independent review.
        self.assertEqual(self.review["screening"],
                         screen_traces.UNJUDGED_SCREENING)
        self.assertNotEqual(self.review["screening"], build_dataset.SCREENING)
        self.assertNotIn("independent-review", build_dataset.UNJUDGED_VERIFIER)
        self.assertIn("independent-review", build_dataset.VERIFIER)

    def test_the_deterministic_screen_is_evidence_and_not_a_gate(self):
        # It costs no model call, so it still runs and is recorded -- but a
        # failing gate cannot withhold an acceptance the operator asked for.
        self.assertFalse(self.review["deterministic"]["passed"])
        self.assertFalse(self.review["deterministic"]["gates"]["raw_fresh"])
        self.assertTrue(self.review["accepted"])

    def test_it_is_written_where_every_later_stage_looks_for_it(self):
        path = self.traces / "reviews" / "unjudged-seed.json"
        self.assertEqual(json.loads(path.read_text()), self.review)


class ThePipelineNeverCallsAJudgeItWasToldToSkip(unittest.TestCase):
    def setUp(self):
        self.source = inspect.getsource(trace_pipeline.main)

    def test_the_judge_is_not_even_preflighted(self):
        # Preflight authenticates a metered runtime. Bypassed judging means no
        # judge is configured at all, so requiring its credentials would make
        # the setting unusable on exactly the machine it exists for.
        self.assertIn("if not skip_judging:\n            worker_judge.preflight(",
                      self.source)

    def test_the_bypass_returns_before_the_re_review_loop(self):
        # `is_judge_error` is true when the deterministic setup gate fails, so
        # an unjudged review reaching the re-review loop would re-run a judge
        # that does not exist, three times, and stop the queue.
        bypass = self.source.index("if skip_judging:")
        rejudge = self.source.index("review = screen(")
        self.assertLess(bypass, rejudge)
        self.assertIn("return", self.source[bypass:rejudge])
        self.assertIn("unjudged_review(seed)", self.source[bypass:rejudge])

    def test_the_run_states_plainly_that_nothing_will_review_it(self):
        self.assertIn("BYPASSED", self.source)

    def test_a_resumed_run_keeps_the_setting_it_started_with(self):
        # The ledger, not the current config file, decides what a resumption
        # does: a run half-published as unjudged must not finish as judged.
        self.assertIn('limits["skip_judging"] = skip_judging', self.source)
        self.assertIn('prior_limits.get("skip_judging"', self.source)


class BothKnobsDefaultToTodaysBehavior(unittest.TestCase):
    def setUp(self):
        self.defaults = json.loads(
            (ROOT / "config.json").read_text())["pipeline"]["trace"]

    def test_judging_is_on_unless_it_is_turned_off(self):
        self.assertIs(self.defaults["skip_judging"], False)

    def test_the_reasoning_step_down_is_on_unless_it_is_turned_off(self):
        self.assertIs(self.defaults["step_down_reasoning_on_failure"], True)

    def test_both_settings_refuse_anything_that_is_not_a_boolean(self):
        for key in ("pipeline.trace.skip_judging",
                    "pipeline.trace.step_down_reasoning_on_failure"):
            with self.subTest(key=key), self.assertRaises(SystemExit):
                m._config(["set", key, "sometimes"])

    def test_a_disabled_step_down_holds_the_configured_effort(self):
        self.assertEqual(
            reasoning_stepdown.reasoning_schedule(3, False, "xhigh"),
            ["xhigh", "xhigh", "xhigh"])


if __name__ == "__main__":
    unittest.main()
