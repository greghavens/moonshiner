"""Contracts for the opt-in synthetic correction companion pipeline."""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import synthetic_corrections as corrections  # noqa: E402
import publish_queue  # noqa: E402
import hf_sync  # noqa: E402
from run_state import connect, create_run, finish_attempt, start_attempt  # noqa: E402


class SyntheticCorrectionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.db_path = self.root / "ledger.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def _attempt(self, db, kind, seed_id, status, artifact=None):
        run = create_run(db, kind, {}, {}, [seed_id])
        start_attempt(db, run, seed_id, 1)
        finish_attempt(db, run, seed_id, 1, status,
                       artifact_path=str(artifact) if artifact else None)
        return run

    def test_disabled_by_default_and_two_attempt_default(self):
        settings = corrections.settings({"judge": {
            "runtime": "codex", "model": "judge-model", "reasoning": "xhigh"},
            "publish": {"hf_dataset": "owner/source"}})
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["max_attempts"], 2)
        self.assertEqual(settings["runtime"], "codex")
        self.assertEqual(settings["model"], "judge-model")

    def test_companion_name_defaults_from_primary(self):
        self.assertEqual(corrections.default_dataset("owner/source"),
                         "owner/source-synthetic-corrections")
        self.assertIsNone(corrections.default_dataset(None))

    def test_only_current_revision_exhausted_never_accepted_are_selected_oldest_first(self):
        db = connect(self.db_path)
        old = self.root / "old"; old.mkdir()
        new = self.root / "new"; new.mkdir()
        self._attempt(db, "trace", "oldest", "exhausted", old)
        self._attempt(db, "trace", "later", "exhausted", new)
        self._attempt(db, "trace", "accepted", "exhausted", old)
        self._attempt(db, "trace", "accepted", "accepted", new)
        self._attempt(db, "trace", "revised", "exhausted", old)
        self._attempt(db, "seed", "revised", "accepted")
        selected = corrections.eligible_exhausted_attempts(db)
        self.assertEqual([item["seed_id"] for item in selected], ["oldest", "later"])
        db.close()

    def test_any_current_revision_acceptance_excludes_all_failures(self):
        db = connect(self.db_path)
        self._attempt(db, "trace", "passed-first", "accepted")
        self._attempt(db, "trace", "passed-first", "exhausted")
        self.assertNotIn("passed-first", {item["seed_id"] for item in
                                         corrections.eligible_exhausted_attempts(db)})
        db.close()

    def test_reviewer_receives_at_most_three_failures_and_only_one_candidate(self):
        db = connect(self.db_path)
        artifacts = []
        for number in range(5):
            artifact = self.root / f"failure-{number}"; artifact.mkdir()
            artifacts.append(str(artifact))
            self._attempt(db, "trace", "duplicate", "exhausted", artifact)
        candidates = corrections.eligible_exhausted_attempts(db)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["seed_id"], "duplicate")
        self.assertEqual(len(candidates[0]["failures"]), 3)
        self.assertEqual([item["artifact_path"] for item in candidates[0]["failures"]],
                         artifacts[-3:][::-1])
        db.close()

    def test_eligibility_is_narrow_and_fail_closed(self):
        prompt = corrections.eligibility_prompt(
            {"id": "one", "prompt": "Do the task"}, {"reason": "missing call"}, "trace")
        for required in ("reasoning is already substantially correct",
                         "missing an obvious tool call", "no refactoring",
                         "one or two files", "return ineligible"):
            self.assertIn(required, prompt.lower())
        self.assertFalse(corrections.validate_eligibility(None)[0])
        self.assertFalse(corrections.validate_eligibility({"eligible": True})[0])
        valid = {"eligible": True, "reasoning_already_correct": True,
                 "minor_change": True, "source_failure_index": 1,
                 "repair_instructions": "Make the missing call."}
        self.assertTrue(corrections.validate_eligibility(valid, failure_count=3)[0])

    def test_eligibility_selects_one_of_three_failures_needing_least_correction(self):
        failures = [
            {"review": {"reason": "large"}, "trace": "attempt zero"},
            {"review": {"reason": "one missing call"}, "trace": "attempt one"},
            {"review": {"reason": "broken design"}, "trace": "attempt two"},
        ]
        prompt = corrections.eligibility_prompt(
            {"id": "one", "prompt": "Do the task"}, {"failures": failures}, "")
        self.assertIn("source_failure_index", prompt)
        self.assertIn("smallest valid correction", prompt)
        self.assertIn("FAILURE 0", prompt)
        self.assertIn("FAILURE 2", prompt)
        self.assertFalse(corrections.validate_eligibility({
            "eligible": True, "reasoning_already_correct": True,
            "minor_change": True, "source_failure_index": 3,
            "repair_instructions": "fix"}, failure_count=3)[0])
        verdict = {"eligible": True, "reasoning_already_correct": True,
                   "minor_change": True, "source_failure_index": 1,
                   "repair_instructions": "one missing call"}
        self.assertEqual(corrections.selected_failure(
            {"failures": failures}, verdict), failures[1])

    def test_outputs_are_isolated_from_primary(self):
        paths = corrections.correction_paths(self.root)
        self.assertNotEqual(paths.traces, self.root / "traces")
        self.assertTrue(str(paths.traces).endswith("synthetic-corrections/traces"))
        self.assertTrue(str(paths.publish).endswith("synthetic-corrections/hf-publish"))

    def test_rejection_returns_to_tail_with_feedback_and_acceptance_publishes(self):
        queue = corrections.CorrectionQueue(["a", "b"], max_attempts=2)
        first = queue.pop(); self.assertEqual(first.seed_id, "a")
        queue.record_judgment(first, {"accepted": False, "reason": "missing file"})
        self.assertEqual([item.seed_id for item in queue.pending], ["b", "a"])
        retry = queue.pending[-1]
        self.assertIn("missing file", retry.feedback)
        queue.record_judgment(retry, {"accepted": True})
        self.assertIn("a", queue.accepted)
        self.assertNotIn("a", queue.exhausted)

    def test_second_rejection_exhausts_and_never_publishes(self):
        queue = corrections.CorrectionQueue(["a"], max_attempts=2)
        first = queue.pop()
        queue.record_judgment(first, {"accepted": False, "reason": "one"})
        second = queue.pop()
        queue.record_judgment(second, {"accepted": False, "reason": "two"})
        self.assertIn("a", queue.exhausted)
        self.assertNotIn("a", queue.accepted)

    def test_accepted_correction_cannot_enter_primary_publish_selection(self):
        db = connect(self.db_path)
        self._attempt(db, "synthetic-correction", "corrected", "accepted")
        self._attempt(db, "trace", "primary", "accepted")
        self.assertEqual(corrections.accepted_ids_for_publish(db, "trace"), {"primary"})
        self.assertEqual(
            corrections.accepted_ids_for_publish(db, "synthetic-correction"),
            {"corrected"})
        db.close()

    def test_one_publish_queue_routes_correction_to_configured_companion(self):
        config = {"publish": {"hf_dataset": "owner/source"},
                  "synthetic_corrections": {"hf_dataset": "owner/corrections"}}
        primary = corrections.publication_target("trace", config, self.root)
        corrected = corrections.publication_target(
            "synthetic-correction", config, self.root)
        self.assertEqual(primary.dataset, "owner/source")
        self.assertEqual(corrected.dataset, "owner/corrections")
        self.assertNotEqual(primary.source_root, corrected.source_root)
        self.assertIs(corrections.publish_worker, publish_queue.main)

    def test_hf_bootstrap_uses_explicit_companion_target_not_primary(self):
        target = self.root / "companion" / "traces.jsonl"
        with mock.patch.object(hf_sync, "DATA", self.root), \
             mock.patch.object(hf_sync, "RUNS", self.root / "runs"), \
             mock.patch.object(hf_sync, "CONFIG", {"publish": {
                 "hf_dataset": "owner/primary"}}), \
             mock.patch.object(hf_sync, "_dataset_info", return_value=None):
            state = hf_sync.ensure_local_dataset(
                target=target, dataset="owner/companion")
        self.assertEqual(state["dataset"], "owner/companion")

    def test_no_separate_judge_or_publish_queue_implementation(self):
        source = pathlib.Path(corrections.__file__).read_text()
        self.assertNotIn("class CorrectionJudgeQueue", source)
        self.assertNotIn("class CorrectionPublishQueue", source)
        self.assertNotIn("def publish_corrections", source)
        publisher_source = pathlib.Path(publish_queue.__file__).read_text()
        self.assertIn("process_companion_once()", publisher_source)

    def test_shared_publish_queue_passes_only_explicit_companion_target(self):
        paths = corrections.correction_paths(self.root)
        opts = {"enabled": True, "hf_dataset": "owner/corrections"}
        with mock.patch("synthetic_corrections.settings", return_value=opts), \
             mock.patch("synthetic_corrections.correction_paths", return_value=paths), \
             mock.patch("synthetic_corrections.build_companion") as build, \
             mock.patch.object(publish_queue, "load_config", return_value={}), \
             mock.patch.object(publish_queue, "accepted_tasks",
                               return_value=[(1.0, "fixed", 7)]), \
             mock.patch.object(publish_queue, "batch_size", return_value=1), \
             mock.patch.object(publish_queue, "tracing_has_unfinished_work",
                               return_value=False), \
             mock.patch("hf_sync.ensure_local_dataset"), \
             mock.patch.object(publish_queue, "run") as run, \
             mock.patch.object(publish_queue, "verify_remote"):
            self.assertTrue(publish_queue.process_companion_once())
        build.assert_called_once_with(paths, {})
        command = run.call_args.args
        self.assertIn("owner/corrections", command)
        self.assertIn(str(paths.publish), command)
        self.assertNotIn("owner/source", command)

    def test_companion_export_preserves_existing_unrelated_rows(self):
        existing = [{"task": "old", "assistant_step": 1},
                    {"task": "redo", "assistant_step": 1}]
        generated = [{"task": "redo", "assistant_step": 1},
                     {"task": "new", "assistant_step": 1}]
        merged = corrections.merge_canonical_rows(existing, generated)
        self.assertEqual([row["task"] for row in merged], ["old", "redo", "new"])

    def test_correction_candidate_then_existing_judge_share_no_extra_queue(self):
        runtime = mock.Mock()
        judge = mock.Mock()
        seed = {"id": "a", "prompt": "Do it"}
        with mock.patch.object(corrections, "create_candidate", return_value={"passed": True}) as create, \
             mock.patch.object(corrections, "screen", return_value={"accepted": True}) as screen:
            result = corrections.correct_once(seed, runtime, judge, self.root,
                                              "Make the missing tool call", self.root / "failure")
        self.assertTrue(result["accepted"])
        create.assert_called_once()
        self.assertIs(create.call_args.args[1], runtime)
        screen.assert_called_once()
        self.assertIs(screen.call_args.kwargs["judge"], judge)
        self.assertEqual(screen.call_args.kwargs["traces_root"],
                         corrections.correction_paths(self.root).traces)

    def test_reasoning_must_be_byte_for_byte_preserved(self):
        source = [{"role": "assistant", "reasoning_content": "keep this",
                   "content": "done"}]
        corrected = [{"role": "assistant", "reasoning_content": "changed",
                      "content": "done"}]
        self.assertFalse(corrections.correction_delta(source, corrected)[
            "reasoning_unchanged"])
        corrected[0]["reasoning_content"] = "keep this"
        self.assertTrue(corrections.correction_delta(source, corrected)[
            "reasoning_unchanged"])
        source = [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hidden"}]}]
        corrected = [{"role": "assistant", "content": [
            {"type": "thinking", "thinking": "rewritten"}]}]
        self.assertFalse(corrections.correction_delta(source, corrected)[
            "reasoning_unchanged"])

    def test_correction_judge_explicitly_checks_reasoning_and_minimality(self):
        prompt = corrections.correction_judge_prompt(
            "original", "corrected", {"reasoning_unchanged": True,
                                      "changed_messages": 1})
        self.assertIn("reasoning", prompt.lower())
        self.assertIn("unchanged", prompt.lower())
        self.assertIn("minimal", prompt.lower())
        self.assertIn("automatic rejection", prompt.lower())

    def test_nonminimal_or_reasoning_rewrite_fails_before_normal_judge(self):
        self.assertFalse(corrections.validate_correction_delta({
            "reasoning_unchanged": False, "changed_messages": 1,
            "changed_files": 0})[0])
        self.assertFalse(corrections.validate_correction_delta({
            "reasoning_unchanged": True, "changed_messages": 8,
            "changed_files": 6})[0])

    def test_changed_file_count_compares_failed_and_corrected_patches(self):
        before = "diff --git a/a.py b/a.py\n-old\n+bad\n"
        after = ("diff --git a/a.py b/a.py\n-old\n+good\n"
                 "diff --git a/new.py b/new.py\n+new\n")
        self.assertEqual(corrections.changed_patch_files(before, after), 2)

    def test_corrected_canonical_trace_round_trips_without_primary_format_branch(self):
        messages = [{"role": "assistant", "reasoning_content": "same",
                     "tool_calls": [{"id": "one", "type": "function",
                                     "function": {"name": "search",
                                                  "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "one", "content": "result"}]
        path = self.root / "corrected.json"
        corrections.write_corrected_trace(path, messages)
        parsed, parsed_tools = corrections.read_corrected_trace(path)
        self.assertEqual(parsed, messages)
        self.assertEqual(parsed_tools, {})
        self.assertNotIn("tools", json.loads(path.read_text()))

    def test_added_tool_call_requires_a_matching_result(self):
        bad = [{"role": "assistant", "tool_calls": [{"id": "x", "type": "function",
                "function": {"name": "search", "arguments": "{}"}}]}]
        self.assertFalse(corrections.validate_tool_pairs(bad)[0])
        bad.append({"role": "tool", "tool_call_id": "x", "content": "result"})
        self.assertTrue(corrections.validate_tool_pairs(bad)[0])

    def test_card_is_labeled_and_links_primary(self):
        rendered = corrections.companion_notice("owner/source", "Model Name")
        self.assertIn("Synthetic Corrections", rendered)
        self.assertIn("https://huggingface.co/datasets/owner/source", rendered)
        self.assertIn("companion", rendered.lower())

    def test_companion_card_does_not_claim_corrections_are_uncorrected_real_traces(self):
        from export_hf_card import build_card
        row = {"task": "a", "source_trajectory_id": "a:1", "split": "train",
               "lang": "en", "category": "Tool calling", "domain": "agent",
               "assistant_steps": 1, "assistant_step": 1, "model_attested": True,
               "teacher_model": "model", "provider": "provider", "tools_used": [],
               "messages": [{"role": "assistant", "content": "done"}], "tools": "[]"}
        card = build_card([row], config={"publish": {}, "teacher": {}, "judge": {},
                                                "runtimes": {}},
                          publish_dir=self.root, companion_primary="owner/source")
        self.assertIn("synthetically corrected", card.lower())
        self.assertNotIn("**All real model trajectories.**", card)
        self.assertNotIn("supplies no demonstration content", card)

    def test_dry_run_makes_no_runtime_calls(self):
        runtime = mock.Mock()
        judge = mock.Mock()
        report = corrections.run(dry_run=True, config={"synthetic_corrections": {
            "enabled": True}, "judge": {"runtime": "codex", "model": "j"},
            "publish": {"hf_dataset": "owner/source"}}, db_path=self.db_path,
            runtime=runtime, judge=judge)
        self.assertEqual(report["model_calls"], 0)
        runtime.run_trace.assert_not_called()
        judge.run_review.assert_not_called()

    def test_status_reports_durable_correction_totals(self):
        db = connect(self.db_path)
        self._attempt(db, "synthetic-correction", "accepted-one", "accepted")
        self._attempt(db, "synthetic-correction", "exhausted-one", "exhausted")
        db.close()
        report = corrections.status_report(config={"synthetic_corrections": {
            "enabled": True}, "judge": {"runtime": "codex", "model": "j"},
            "publish": {"hf_dataset": "owner/source"}}, db_path=self.db_path)
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["exhausted"], 1)

    def test_paid_run_requires_explicit_yes(self):
        with self.assertRaises(SystemExit):
            corrections.main(["run"])

    def test_configure_defaults_to_judge_companion_name_and_two_attempts(self):
        config = {"judge": {"runtime": "codex", "model": "judge-model",
                            "reasoning": "xhigh"},
                  "publish": {"hf_dataset": "owner/source"},
                  "runtimes": {"codex": {"cli": "codex"}}}
        updates = {}
        with mock.patch("configuration.load_config", return_value=config), \
             mock.patch("configuration.update_local",
                        side_effect=lambda key, value: updates.__setitem__(key, value)), \
             mock.patch("builtins.input", side_effect=["", "", "", ""]):
            self.assertEqual(corrections.main(["configure"]), 0)
        self.assertEqual(updates["synthetic_corrections.runtime"], "codex")
        self.assertEqual(updates["synthetic_corrections.model"], "judge-model")
        self.assertEqual(updates["synthetic_corrections.max_attempts"], 2)
        self.assertEqual(updates["synthetic_corrections.hf_dataset"],
                         "owner/source-synthetic-corrections")


if __name__ == "__main__":
    unittest.main()
