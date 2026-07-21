from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import infrastructure_repair  # noqa: E402
from run_state import connect, create_run, finish_attempt, start_attempt  # noqa: E402
from toolchains import missing_executables  # noqa: E402


class InfrastructureRepairTests(unittest.TestCase):
    def test_extracts_only_explicit_missing_executables(self):
        self.assertEqual(missing_executables(
            "setup failed: bwrap: execvp go: No such file or directory"), ["go"])
        self.assertEqual(missing_executables(
            "candidate replay verification did not pass twice"), [])

    def test_reclassifies_only_after_sandbox_tool_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            run_id = create_run(db, "trace", {}, {"max_attempts": 2}, ["go-seed"])
            start_attempt(db, run_id, "go-seed", 1)
            review = {"deterministic": {"failures": [
                "setup failed: bwrap: execvp go: No such file or directory"]}}
            finish_attempt(db, run_id, "go-seed", 1, "exhausted", review=review)
            with mock.patch.object(infrastructure_repair, "sandbox_tool_ready",
                                   return_value=(True, "go version")):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual((result["attempts"], result["seeds"]), (1, 1))
            self.assertEqual(result["requeued"], 1)
            status = db.execute("SELECT status FROM attempts").fetchone()[0]
            self.assertEqual(status, "infrastructure_error")
            db.close()

    def test_does_not_reclassify_unrepaired_toolchain(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            run_id = create_run(db, "trace", {}, {"max_attempts": 2}, ["go-seed"])
            start_attempt(db, run_id, "go-seed", 1)
            review = {"deterministic": {"failures": [
                "setup failed: bwrap: execvp go: No such file or directory"]}}
            finish_attempt(db, run_id, "go-seed", 1, "exhausted", review=review)
            with mock.patch.object(infrastructure_repair, "sandbox_tool_ready",
                                   return_value=(False, "missing")):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual(result["attempts"], 0)
            self.assertEqual(result["requeued"], 0)
            self.assertEqual(db.execute("SELECT status FROM attempts").fetchone()[0],
                             "exhausted")
            db.close()

    def test_ignores_seed_author_attempts_entirely(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            run_id = create_run(db, "seed", {}, {"max_attempts": 2}, ["go-seed"])
            start_attempt(db, run_id, "go-seed", 1)
            review = {"deterministic": {"failures": [
                "setup failed: bwrap: execvp go: No such file or directory"]}}
            finish_attempt(db, run_id, "go-seed", 1, "exhausted", review=review)
            with mock.patch.object(infrastructure_repair, "sandbox_tool_ready") as ready:
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual(result["attempts"], 0)
            self.assertEqual(result["requeued"], 0)
            ready.assert_not_called()
            self.assertEqual(db.execute("SELECT status FROM attempts").fetchone()[0],
                             "exhausted")
            db.close()


if __name__ == "__main__":
    unittest.main()
