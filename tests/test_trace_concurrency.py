"""Transactional trace claiming and paid-call accounting."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import unittest
import sqlite3
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_state import (abandon_claim, claim_job, connect, create_run, finish_attempt,
                       live_trace_run_ids, set_job, start_attempt)  # noqa: E402
import trace_pipeline  # noqa: E402


class TraceConcurrency(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.directory.name) / "ledger.sqlite3"
        db = connect(self.path)
        self.run_id = create_run(db, "trace", {}, {"max_attempts": 2},
                                 [f"seed-{index:02d}" for index in range(20)])
        db.close()

    def tearDown(self):
        self.directory.cleanup()

    def test_runtime_console_lookup_does_not_resolve_the_python_symlink(self):
        with mock.patch.object(trace_pipeline.sys, "executable",
                               "/installed/runtime/bin/python"), \
             mock.patch.object(pathlib.Path, "is_file", return_value=True), \
             mock.patch.object(pathlib.Path, "resolve",
                               side_effect=AssertionError("must not resolve runtime symlink")):
            self.assertEqual(trace_pipeline._moonshiner_executable(),
                             "/installed/runtime/bin/moonshiner")

    def test_parallel_claims_are_unique(self):
        claimed = []
        lock = threading.Lock()

        def worker(index):
            db = connect(self.path)
            while claim := claim_job(db, self.run_id, f"worker-{index}"):
                with lock:
                    claimed.append(claim["seed_id"])
                set_job(db, self.run_id, claim["seed_id"], "accepted", 1)
            db.close()

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(claimed), 20)
        self.assertEqual(len(set(claimed)), 20)

    def test_tail_retry_selection_prefers_every_first_attempt(self):
        db = connect(self.path)
        start_attempt(db, self.run_id, "seed-00", 1)
        finish_attempt(db, self.run_id, "seed-00", 1, "retry")
        db.close()
        args = type("Args", (), {"only": None, "category": None, "tag": None,
            "name": None, "max_attempts": 2, "limit": 0, "all": True})()
        seeds = [{"id": "seed-00"}, {"id": "seed-01"}]
        with mock.patch.object(trace_pipeline, "connect", side_effect=lambda: connect(self.path)), \
             mock.patch.object(trace_pipeline, "select_seeds", return_value=seeds), \
             mock.patch.object(trace_pipeline, "CONFIG",
                               {"pipeline": {"trace": {"retry_order": "tail"}}}), \
             mock.patch("seed_inventory.accepted_ids", return_value=set()), \
             mock.patch("common.synthetic_tool_contract", return_value=None):
            selected = trace_pipeline._selected(args)
        self.assertEqual([seed["id"] for seed in selected], ["seed-01", "seed-00"])

    def test_queue_dispatches_one_seed_per_process(self):
        args = type("Args", (), {"max_attempts": 3})()
        completed = mock.Mock(returncode=0)
        seeds = [{"id": "seed-a"}, {"id": "seed-b"}, {"id": "seed-c"}]
        with mock.patch.object(trace_pipeline, "_moonshiner_executable",
                               return_value="/installed/bin/moonshiner"), \
             mock.patch.object(trace_pipeline.subprocess, "run", return_value=completed) as run:
            self.assertEqual(trace_pipeline._run_individual_trace_jobs(seeds, args, 2), 0)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertEqual({command[command.index("--only") + 1] for command in commands},
                         {"seed-a", "seed-b", "seed-c"})
        self.assertTrue(all(command.count("--only") == 1 for command in commands))
        self.assertTrue(all("--max-calls" not in command for command in commands))
        self.assertTrue(all(command[0] == "/installed/bin/moonshiner"
                            for command in commands))

    def test_queue_never_exceeds_configured_workers(self):
        args = type("Args", (), {"max_attempts": 2, "workers": 2})()
        seeds = [{"id": f"seed-{index}"} for index in range(5)]
        active = 0
        peak = 0
        lock = threading.Lock()

        def run(command, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            threading.Event().wait(0.02)
            with lock:
                active -= 1
            return mock.Mock(returncode=0)

        with mock.patch.object(trace_pipeline, "_moonshiner_executable",
                               return_value="/installed/bin/moonshiner"), \
             mock.patch.object(trace_pipeline.subprocess, "run", side_effect=run):
            self.assertEqual(trace_pipeline._run_individual_trace_jobs(seeds, args, 2), 0)
        self.assertEqual(peak, 2)

    def test_expired_claim_is_recovered_once(self):
        db = connect(self.path)
        first = claim_job(db, self.run_id, "dead-worker", lease_seconds=-1)
        recovered = claim_job(db, self.run_id, "replacement")
        self.assertEqual(recovered["seed_id"], first["seed_id"])
        row = db.execute("SELECT lease_owner,status FROM jobs WHERE run_id=? AND seed_id=?",
                         (self.run_id, first["seed_id"])).fetchone()
        self.assertEqual(tuple(row), ("replacement", "running"))
        db.close()

    def test_expired_claim_is_not_reported_as_a_live_trace(self):
        db = connect(self.path)
        claim_job(db, self.run_id, "dead-worker", lease_seconds=-1)
        self.assertNotIn(self.run_id, live_trace_run_ids(db))
        db.close()

    def test_unexpired_claim_is_reported_as_a_live_trace(self):
        db = connect(self.path)
        claim_job(db, self.run_id, "live-worker", lease_seconds=120)
        self.assertIn(self.run_id, live_trace_run_ids(db))
        db.close()

    def test_failed_worker_returns_claim_immediately(self):
        db = connect(self.path)
        first = claim_job(db, self.run_id, "failed-worker")
        abandon_claim(db, self.run_id, first["seed_id"], "failed-worker", "transport failed")
        replacement = claim_job(db, self.run_id, "replacement")
        self.assertEqual(replacement["seed_id"], first["seed_id"])
        self.assertEqual(replacement["last_error"], "transport failed")
        db.close()

    def test_existing_ledger_migrates_lease_columns_without_losing_jobs(self):
        legacy = pathlib.Path(self.directory.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.executescript("""
          CREATE TABLE runs (id TEXT PRIMARY KEY, kind TEXT, status TEXT,
            created_at TEXT, updated_at TEXT, config_json TEXT, limits_json TEXT,
            error TEXT, model_calls INTEGER DEFAULT 0);
          CREATE TABLE jobs (run_id TEXT, seed_id TEXT, status TEXT, attempts INTEGER,
            last_error TEXT, updated_at TEXT, PRIMARY KEY(run_id,seed_id));
          CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            seed_id TEXT, number INTEGER, status TEXT, started_at TEXT, finished_at TEXT,
            teacher_usage_json TEXT, review_json TEXT, error TEXT, artifact_path TEXT,
            UNIQUE(run_id,seed_id,number));
          INSERT INTO runs VALUES ('old','trace','running','x','x','{}','{}',NULL,0);
          INSERT INTO jobs VALUES ('old','seed','pending',0,NULL,'x');
        """)
        db.commit(); db.close()
        migrated = connect(legacy)
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(jobs)")}
        self.assertTrue({"lease_owner", "lease_expires_at"} <= columns)
        self.assertEqual(migrated.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
        migrated.close()

    def test_reauthored_seed_does_not_inherit_superseded_trace_attempts(self):
        db = connect(self.path)
        old_trace = create_run(db, "trace", {}, {}, ["seed-revised"])
        start_attempt(db, old_trace, "seed-revised", 1)
        finish_attempt(db, old_trace, "seed-revised", 1, "exhausted")
        seed_run = create_run(db, "seed", {}, {}, ["seed-revised"])
        start_attempt(db, seed_run, "seed-revised", 1)
        finish_attempt(db, seed_run, "seed-revised", 1, "accepted")
        from run_state import trace_attempt_counts_for_current_seed_revision
        self.assertEqual(
            trace_attempt_counts_for_current_seed_revision(db).get("seed-revised", 0), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
