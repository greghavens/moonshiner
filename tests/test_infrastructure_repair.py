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

import common  # noqa: E402
import infrastructure_repair  # noqa: E402
from run_state import (connect, create_run, finish_attempt,  # noqa: E402
                       set_job, start_attempt)
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


def blocked_attempt(db, seed_id: str, error: str, *, status="infrastructure_error",
                    job_status="infrastructure_blocked"):
    """One attempt recorded exactly the way an infrastructure failure records it."""
    run_id = create_run(db, "trace", {}, {"max_attempts": 3}, [seed_id])
    start_attempt(db, run_id, seed_id, 1)
    finish_attempt(db, run_id, seed_id, 1, status, error=error)
    set_job(db, run_id, seed_id, job_status, 1, error)
    return run_id


class ClassesEachCarryTheirOwnProof(unittest.TestCase):
    """Every infrastructure class needs its own evidence that it is repaired.

    This command used to know one class -- a missing executable -- and to look
    for it only in attempts marked retry/exhausted/failed. Infrastructure
    failures are recorded as ``infrastructure_error``, so no row it read could
    hold one: it reported nothing while 18 seeds sat blocked.
    """

    def ledger(self, directory):
        return connect(pathlib.Path(directory) / "ledger.sqlite3")

    def test_sees_an_infrastructure_error_attempt_while_its_job_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            db = self.ledger(directory)
            blocked_attempt(db, "credit-seed", "ModelUnavailable: Payment Required")
            with mock.patch.object(infrastructure_repair, "provider_credit_ready",
                                   return_value=(True, "349.10 left")):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual((result["attempts"], result["requeued"]), (1, 1))
            self.assertEqual(
                db.execute("SELECT status FROM jobs").fetchone()[0], "retry")
            db.close()

    def test_ignores_an_infrastructure_error_attempt_whose_job_recovered(self):
        """A repaired seed must not be re-reported by every later run."""
        with tempfile.TemporaryDirectory() as directory:
            db = self.ledger(directory)
            blocked_attempt(db, "credit-seed", "ModelUnavailable: Payment Required",
                            job_status="retry")
            with mock.patch.object(infrastructure_repair, "probe") as probe:
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual(result["attempts"], 0)
            probe.assert_not_called()
            db.close()

    def test_an_unrepaired_class_leaves_its_seed_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            db = self.ledger(directory)
            blocked_attempt(db, "credit-seed", "ModelUnavailable: Payment Required")
            with mock.patch.object(infrastructure_repair, "provider_credit_ready",
                                   return_value=(False, "0.00 left")):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual((result["attempts"], result["requeued"]), (0, 0))
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0],
                             "infrastructure_blocked")
            db.close()

    def test_a_judge_flake_is_proven_by_the_judge_runtime_not_the_teacher(self):
        with tempfile.TemporaryDirectory() as directory:
            db = self.ledger(directory)
            blocked_attempt(db, "judge-seed", "judge verdict malformed")
            with mock.patch.object(infrastructure_repair, "runtime_ready",
                                   return_value=(True, "ok")) as ready:
                result = infrastructure_repair.repair(db, apply=True)
            ready.assert_called_once_with("judge")
            self.assertEqual(result["attempts"], 1)
            db.close()

    def test_a_failure_of_no_known_class_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            db = self.ledger(directory)
            blocked_attempt(db, "odd-seed",
                            "candidate replay verification did not pass twice")
            with mock.patch.object(infrastructure_repair, "probe") as probe:
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual((result["attempts"], result["checks"]), (0, {}))
            probe.assert_not_called()
            db.close()


class ContentFilteredSeedsAreRefused(unittest.TestCase):
    """A refusal is a property of the prompt, not of the environment.

    Re-running one buys another refusal at the price of a full prompt, so no
    proof of repair exists and none is looked for.
    """

    def test_a_content_filtered_seed_is_never_probed_or_requeued(self):
        with tempfile.TemporaryDirectory() as directory:
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            blocked_attempt(db, "filtered-seed",
                            "TraceHarnessInfrastructureFailure: safeguard_refusal")
            with mock.patch.object(infrastructure_repair, "probe") as probe:
                result = infrastructure_repair.repair(db, apply=True)
            probe.assert_not_called()
            self.assertEqual((result["attempts"], result["requeued"]), (0, 0))
            self.assertEqual(result["refused"], ["filtered-seed"])
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0],
                             "infrastructure_blocked")
            db.close()

    def test_the_filter_wins_over_the_harness_failure_wrapped_around_it(self):
        self.assertEqual(infrastructure_repair.classify(
            "TraceHarnessInfrastructureFailure: ModelUnavailable: contentfiltered"),
            ("content-filter", "content-filter"))


class ProviderCreditIsAskedFor(unittest.TestCase):
    def probe_credit(self, **urlopen):
        config = {"teacher": {"runtime": "opencode"},
                  "runtimes": {"opencode": {"base_url": "https://example/api/v1"}}}
        with mock.patch("configuration.load_config", return_value=config), \
             mock.patch("runtimes.auth.load_provider_key", return_value="k"), \
             mock.patch.object(infrastructure_repair.urllib.request, "urlopen",
                               **urlopen):
            return infrastructure_repair.provider_credit_ready("teacher")

    def response(self, payload):
        body = mock.MagicMock()
        body.read.return_value = json.dumps(payload).encode()
        body.__enter__.return_value = body
        return body

    def test_fails_closed_when_the_provider_cannot_be_asked(self):
        """Requeueing into an empty account re-blocks every seed it touches."""
        ready, detail = self.probe_credit(side_effect=OSError("connection refused"))
        self.assertFalse(ready)
        self.assertIn("connection refused", detail)

    def test_a_positive_balance_is_proof_and_an_empty_one_is_not(self):
        ready, detail = self.probe_credit(return_value=self.response(
            {"data": {"total_credits": 350.74, "total_usage": 1.74}}))
        self.assertTrue(ready)
        self.assertIn("349.00", detail)
        ready, _ = self.probe_credit(return_value=self.response(
            {"data": {"total_credits": 12.0, "total_usage": 12.0}}))
        self.assertFalse(ready)

    def test_a_response_without_a_credit_total_proves_nothing(self):
        ready, _ = self.probe_credit(return_value=self.response({"data": {}}))
        self.assertFalse(ready)


class StaleWorkspacesAreClearedPerWorkspace(unittest.TestCase):
    """Two seeds fail this way independently; clearing one proves nothing
    about the other. Caching the proof by class cleared only the first."""

    def test_every_named_workspace_is_cleared_not_just_the_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "workspaces"
            first, second = root / "seed-a", root / "seed-b"
            for workspace in (first, second):
                (workspace / "tmp" / "opencode" / "hide").mkdir(parents=True)
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            for seed_id, workspace in (("seed-a", first), ("seed-b", second)):
                blocked_attempt(db, seed_id, "PermissionError: [Errno 13] Permission "
                                f"denied: '{workspace}/tmp/opencode/hide'")
            with mock.patch.object(common, "WORKSPACES", root):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual((result["attempts"], result["requeued"]), (2, 2))
            self.assertEqual(len(result["checks"]), 2)
            db.close()

    def test_a_workspace_that_cannot_be_cleared_stays_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "workspaces"
            workspace = root / "seed-a"
            workspace.mkdir(parents=True)
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            blocked_attempt(db, "seed-a", "PermissionError: [Errno 13] Permission "
                            f"denied: '{workspace}/tmp/opencode/hide'")
            with mock.patch.object(common, "WORKSPACES", root), \
                 mock.patch.object(common, "remove_workspace",
                                   side_effect=OSError("still busy")):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual(result["attempts"], 0)
            self.assertTrue(workspace.exists())
            db.close()

    def test_a_dry_run_clears_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "workspaces"
            workspace = root / "seed-a"
            workspace.mkdir(parents=True)
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            blocked_attempt(db, "seed-a", "PermissionError: [Errno 13] Permission "
                            f"denied: '{workspace}/tmp/opencode/hide'")
            with mock.patch.object(common, "WORKSPACES", root):
                result = infrastructure_repair.repair(db, apply=False)
            self.assertTrue(workspace.exists())
            self.assertEqual(db.execute("SELECT status FROM jobs").fetchone()[0],
                             "infrastructure_blocked")
            self.assertEqual(result["attempts"], 1)
            db.close()

    def test_a_permission_error_naming_no_workspace_proves_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory) / "workspaces"; root.mkdir(parents=True)
            db = connect(pathlib.Path(directory) / "ledger.sqlite3")
            blocked_attempt(db, "seed-a",
                            "PermissionError: [Errno 13] Permission denied: '/etc/shadow'")
            with mock.patch.object(common, "WORKSPACES", root):
                result = infrastructure_repair.repair(db, apply=True)
            self.assertEqual(result["attempts"], 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
