"""Transactional trace claiming and paid-call accounting."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import unittest
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_state import (abandon_claim, claim_job, connect, create_run,
                       reserve_model_call, set_job)  # noqa: E402


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

    def test_model_call_ceiling_is_atomic(self):
        reservations = []
        lock = threading.Lock()

        def worker():
            db = connect(self.path)
            value = reserve_model_call(db, self.run_id, 7)
            with lock: reservations.append(value)
            db.close()

        threads = [threading.Thread(target=worker) for _ in range(30)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        granted = [value for value in reservations if value is not None]
        self.assertEqual(sorted(granted), list(range(1, 8)))
        self.assertEqual(len(granted), 7)

    def test_expired_claim_is_recovered_once(self):
        db = connect(self.path)
        first = claim_job(db, self.run_id, "dead-worker", lease_seconds=-1)
        recovered = claim_job(db, self.run_id, "replacement")
        self.assertEqual(recovered["seed_id"], first["seed_id"])
        row = db.execute("SELECT lease_owner,status FROM jobs WHERE run_id=? AND seed_id=?",
                         (self.run_id, first["seed_id"])).fetchone()
        self.assertEqual(tuple(row), ("replacement", "running"))
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


if __name__ == "__main__":
    unittest.main()
