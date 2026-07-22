"""Contracts for the optional, judge-gated reasoning-effort retry policy."""
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import ast

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reasoning_stepdown import (next_reasoning_stage, reasoning_schedule,
                                runtime_for_stage)  # noqa: E402
from run_state import connect, create_run, finish_attempt, start_attempt  # noqa: E402
from unittest import mock
import seed_inventory  # noqa: E402


class ReasoningScheduleContracts(unittest.TestCase):
    def test_ordinary_trace_path_never_feeds_judge_feedback_to_author(self):
        tree = ast.parse((ROOT / "src" / "trace_pipeline.py").read_text())
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "id", None) == "trace_task"]
        self.assertTrue(calls)
        self.assertTrue(all("feedback" not in {item.arg for item in call.keywords}
                            for call in calls))

    def test_disabled_policy_never_changes_configured_effort(self):
        self.assertEqual(reasoning_schedule(5, False, "max"),
                         ["max"] * 5)

    def test_two_attempts_are_xhigh_then_medium(self):
        self.assertEqual(reasoning_schedule(2, True, "max"),
                         ["xhigh", "medium"])

    def test_three_attempts_are_xhigh_medium_low(self):
        self.assertEqual(reasoning_schedule(3, True, "max"),
                         ["xhigh", "medium", "low"])

    def test_attempts_above_three_repeat_the_cycle(self):
        self.assertEqual(reasoning_schedule(9, True, "max"),
                         ["xhigh", "medium", "low"] * 3)

    def test_only_the_first_missing_stage_is_scheduled(self):
        required = reasoning_schedule(3, True, "max")
        self.assertEqual(next_reasoning_stage(required, []), "xhigh")
        self.assertEqual(next_reasoning_stage(required, ["xhigh"]), "medium")
        self.assertEqual(next_reasoning_stage(required, ["xhigh", "medium"]), "low")
        self.assertIsNone(next_reasoning_stage(required,
                                               ["xhigh", "medium", "low"]))

    def test_legacy_repeated_xhigh_does_not_consume_medium_or_low(self):
        required = reasoning_schedule(3, True, "max")
        self.assertEqual(next_reasoning_stage(required, ["max", "max"]), "medium")

    def test_repeated_cycle_counts_each_required_occurrence(self):
        required = reasoning_schedule(5, True, "max")
        self.assertEqual(next_reasoning_stage(
            required, ["xhigh", "medium", "low"]), "xhigh")

    def test_pi_maps_canonical_xhigh_to_native_max_without_mutating_runtime(self):
        runtime = type("Pi", (), {"name": "pi", "role": {
            "model": "teacher", "reasoning": "max"}})()
        adjusted = runtime_for_stage(runtime, "xhigh")
        self.assertEqual(adjusted.role["reasoning"], "max")
        self.assertEqual(runtime.role["reasoning"], "max")
        self.assertIsNot(adjusted, runtime)

    def test_codex_uses_canonical_effort_verbatim(self):
        runtime = type("Codex", (), {"name": "codex", "role": {
            "model": "teacher", "reasoning": "xhigh"}})()
        self.assertEqual(runtime_for_stage(runtime, "medium").role["reasoning"],
                         "medium")

    def test_unsupported_harness_fails_instead_of_claiming_a_fake_stepdown(self):
        runtime = type("Claude", (), {"name": "claude-code", "role": {
            "model": "teacher", "reasoning": "xhigh"}})()
        with self.assertRaisesRegex(ValueError, "does not expose"):
            runtime_for_stage(runtime, "xhigh")


class ReasoningLedgerContracts(unittest.TestCase):
    def test_status_uses_missing_stages_not_legacy_physical_attempt_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ledger.sqlite3"
            db = connect(path)
            run_id = create_run(db, "trace", {}, {"max_attempts": 3}, ["seed"])
            for number in (1, 2):
                start_attempt(db, run_id, "seed", number,
                              reasoning_stage="xhigh", reasoning_effort="max")
                finish_attempt(db, run_id, "seed", number, "exhausted")
            db.close()
            config = {"teacher": {"reasoning": "max"}, "pipeline": {"trace": {
                "step_down_reasoning_on_failure": True}}}
            with mock.patch("run_state.connect", side_effect=lambda: connect(path)), \
                 mock.patch("common.CONFIG", config):
                state = seed_inventory.trace_state(
                    3, target={"seed"}, ready={"seed"}, accepted=set())
            self.assertEqual(state["waiting"], {"seed"})
            self.assertEqual(state["exhausted"], set())

    def test_schema_migration_records_stage_and_native_effort(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "ledger.sqlite3"
            legacy = sqlite3.connect(path)
            legacy.executescript("""
              CREATE TABLE runs (id TEXT PRIMARY KEY, kind TEXT, status TEXT,
                created_at TEXT, updated_at TEXT, config_json TEXT,
                limits_json TEXT, error TEXT, model_calls INTEGER DEFAULT 0);
              CREATE TABLE jobs (run_id TEXT, seed_id TEXT, status TEXT,
                attempts INTEGER DEFAULT 0, last_error TEXT, updated_at TEXT,
                lease_owner TEXT, lease_expires_at TEXT,
                PRIMARY KEY(run_id,seed_id));
              CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT, seed_id TEXT, number INTEGER, status TEXT,
                started_at TEXT, finished_at TEXT, teacher_usage_json TEXT,
                review_json TEXT, error TEXT, artifact_path TEXT,
                UNIQUE(run_id,seed_id,number));
            """)
            legacy.close()
            db = connect(path)
            run_id = create_run(db, "trace", {}, {"max_attempts": 3}, ["seed"])
            start_attempt(db, run_id, "seed", 1, reasoning_stage="xhigh",
                          reasoning_effort="max")
            finish_attempt(db, run_id, "seed", 1, "retry")
            row = db.execute("SELECT reasoning_stage,reasoning_effort FROM attempts").fetchone()
            self.assertEqual(tuple(row), ("xhigh", "max"))
            db.close()


if __name__ == "__main__":
    unittest.main()
