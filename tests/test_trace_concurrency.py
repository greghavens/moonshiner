"""Transactional trace claiming and paid-call accounting."""
from __future__ import annotations

import contextlib
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import sqlite3
import fcntl
import inspect
import io
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_state import (abandon_claim, claim_job, connect, create_run, finish_attempt,
                       enqueue_traces, live_trace_run_ids, pending_trace_queue_entries,
                       set_job, start_attempt,
                       trace_attempt_counts_for_current_seed_revision,
                       trace_reasoning_efforts_for_current_seed_revision)  # noqa: E402
import trace_pipeline  # noqa: E402


class TraceConcurrency(unittest.TestCase):
    def test_second_project_coordinator_exits_before_selecting_or_tracing(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = pathlib.Path(directory)
            lock = (runs / "trace-coordinator.lock").open("a+")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch("common.RUNS", runs), \
                 mock.patch.object(trace_pipeline, "_selected") as selected, \
                 mock.patch.object(trace_pipeline, "get_teacher") as teacher:
                self.assertEqual(trace_pipeline.main(["--all", "--dry-run"]), 2)
            selected.assert_not_called()
            teacher.assert_not_called()
            lock.close()

    def test_generic_fresh_queue_entry_requires_new_acceptance(self):
        db = connect(self.path)
        start_attempt(db, self.run_id, "seed-00", 1,
                      reasoning_stage="xhigh", reasoning_effort="max")
        finish_attempt(db, self.run_id, "seed-00", 1, "accepted")
        enqueue_traces(db, ["seed-00"], front=True, fresh_attempts=True)
        self.assertEqual([row["seed_id"] for row in pending_trace_queue_entries(db)],
                         ["seed-00"])
        self.assertEqual(trace_attempt_counts_for_current_seed_revision(db).get(
            "seed-00", 0), 0)
        self.assertEqual(trace_reasoning_efforts_for_current_seed_revision(
            db, "seed-00"), [])
        second = create_run(db, "trace", {}, {"max_attempts": 3}, ["seed-00"])
        start_attempt(db, second, "seed-00", 1,
                      reasoning_stage="xhigh", reasoning_effort="max")
        finish_attempt(db, second, "seed-00", 1, "accepted")
        self.assertEqual(pending_trace_queue_entries(db), [])
        db.close()

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

    def test_completed_workspace_is_removed_after_durable_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            workspaces = pathlib.Path(directory) / "workspaces"
            workspace = workspaces / "seed-a"
            workspace.mkdir(parents=True)
            (workspace / "large-flat-file").write_text("trace workspace")
            with mock.patch.object(trace_pipeline, "WORKSPACES", workspaces):
                trace_pipeline.remove_completed_workspace(
                    {"_workspace_path": str(workspace)})
            self.assertFalse(workspace.exists())

    def test_accepted_workspace_cleanup_refuses_paths_outside_workspace_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            workspaces = root / "workspaces"; workspaces.mkdir()
            outside = root / "canonical-traces"; outside.mkdir()
            with mock.patch.object(trace_pipeline, "WORKSPACES", workspaces):
                with self.assertRaisesRegex(ValueError, "outside"):
                    trace_pipeline.remove_completed_workspace(
                        {"_workspace_path": str(outside)})
            self.assertTrue(outside.exists())

    def test_every_completed_generated_attempt_path_removes_its_workspace(self):
        # Four ways an attempt finishes: accepted, judged and not accepted,
        # deferred before judgment, and accepted unjudged when judging is
        # bypassed. Each leaves a workspace behind.
        source = inspect.getsource(trace_pipeline.main)
        generated = source[source.index("record = trace_task("):]
        self.assertEqual(generated.count("remove_completed_workspace(record)"), 4)

    def test_a_deferred_attempt_returns_before_the_judge_is_called(self):
        # There is no candidate to read, so screen() would hunt for a raw trace
        # that was never written and raise — arriving back here as the
        # infrastructure failure that deferring exists to avoid.
        source = inspect.getsource(trace_pipeline.main)
        generated = source[source.index("record = trace_task("):]
        deferral = generated.index('key.startswith("deferred_")')
        self.assertLess(deferral, generated.index("screen(seed, worker_judge)"))
        self.assertIn("return", generated[deferral:generated.index(
            "screen(seed, worker_judge)")])

    def test_no_single_kind_of_deferral_is_singled_out(self):
        # Matching one kind's key means the next kind added upstream walks into
        # the judge instead — this branch defeated by an omission rather than a
        # change, which is how the interactive-question deferral would have
        # arrived if the test only knew about safeguard refusals.
        source = inspect.getsource(trace_pipeline.main)
        generated = source[source.index("record = trace_task("):]
        self.assertNotIn('record.get("deferred_safeguard_refusal")', generated)

    def test_a_garbled_verdict_is_re_judged_before_it_stops_the_queue(self):
        # `screen` budgets judge output faults — it says "will re-review
        # (attempt 1/3)" and only stops counting at the limit — but that budget
        # is only spent by whoever calls it again. The continuous queue never
        # returns to `needs_first_pass`, so escalating the first judge_error
        # straight to an infrastructure failure stopped the whole run on one
        # garbled reply, with the re-review it had just promised never made.
        source = inspect.getsource(trace_pipeline.main)
        generated = source[source.index("record = trace_task("):]
        loop = generated.index("while (is_judge_error(review)")
        self.assertIn("JUDGE_ERROR_LIMIT", generated[loop:])
        self.assertLess(loop, generated.index("finish_infrastructure_failure"))
        self.assertEqual(generated[loop:].count("screen(seed, worker_judge)"), 1)

    def test_a_re_judge_is_a_metered_call_like_any_other(self):
        # Every judge run bills the judge model. Re-reviewing without counting
        # it puts the run over its model-call budget without ever showing why.
        source = inspect.getsource(trace_pipeline.main)
        generated = source[source.index("record = trace_task("):]
        loop = generated[generated.index("while (is_judge_error(review)"):]
        rejudge = loop[:loop.index("screen(seed, worker_judge)")]
        self.assertIn("record_model_call(worker_db, run_id)", rejudge)

    def test_infrastructure_failure_alert_is_immediate_and_high_visibility(self):
        output = io.StringIO()
        with mock.patch.object(trace_pipeline.sys, "stderr", output):
            trace_pipeline.alert_infrastructure_failure("task-a", "disk full")
        written = output.getvalue()
        self.assertIn("[INFRASTRUCTURE FAILURE] task-a", written)
        self.assertIn("disk full", written)
        self.assertIn("the queue is stopping", written)
        self.assertIn("=" * 72, written)

    def test_setup_failure_finishes_once_and_blocks_retry(self):
        db = connect(self.path)
        claim = claim_job(db, self.run_id, "worker")
        start_attempt(db, self.run_id, claim["seed_id"], 1)
        output = io.StringIO()
        # A broken environment stops the run; it never blocks one seed and
        # carries on, which hid a broken sandbox for a day.
        with mock.patch.object(trace_pipeline.sys, "stderr", output), \
             self.assertRaises(trace_pipeline.InfrastructureFailure):
            trace_pipeline.finish_infrastructure_failure(
                db, self.run_id, claim["seed_id"], 1,
                {"deterministic": {
                    "failures": ["setup failed: DNS unavailable"]}})
        job = db.execute(
            "SELECT status,attempts FROM jobs WHERE run_id=? AND seed_id=?",
            (self.run_id, claim["seed_id"])).fetchone()
        self.assertEqual(tuple(job), ("infrastructure_blocked", 1))
        self.assertNotEqual(claim_job(db, self.run_id, "replacement")["seed_id"],
                            claim["seed_id"])
        written = output.getvalue()
        self.assertIn(f"[INFRASTRUCTURE FAILURE] {claim['seed_id']}", written)
        self.assertIn("setup failed: DNS unavailable", written)
        db.close()

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

    def test_front_queue_entry_precedes_ordinary_catalog_work(self):
        db = connect(self.path)
        enqueue_traces(db, ["seed-01"], front=True, fresh_attempts=True)
        db.close()
        args = type("Args", (), {"only": None, "category": None, "tag": None,
            "name": None, "max_attempts": 3, "limit": 0, "all": True})()
        seeds = [{"id": "seed-00"}, {"id": "seed-01"}]
        with mock.patch.object(trace_pipeline, "connect", side_effect=lambda: connect(self.path)), \
             mock.patch.object(trace_pipeline, "select_seeds", return_value=seeds), \
             mock.patch.object(trace_pipeline, "CONFIG",
                               {"pipeline": {"trace": {"retry_order": "immediate"}}}), \
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

    def test_failed_worker_blocks_claim_without_retry(self):
        db = connect(self.path)
        first = claim_job(db, self.run_id, "failed-worker")
        start_attempt(db, self.run_id, first["seed_id"], 1)
        abandon_claim(db, self.run_id, first["seed_id"], "failed-worker", "transport failed")
        job = db.execute(
            "SELECT status,last_error FROM jobs WHERE run_id=? AND seed_id=?",
            (self.run_id, first["seed_id"])).fetchone()
        attempt = db.execute(
            "SELECT status,error FROM attempts WHERE run_id=? AND seed_id=?",
            (self.run_id, first["seed_id"])).fetchone()
        self.assertEqual(tuple(job), ("infrastructure_blocked", "transport failed"))
        self.assertEqual(tuple(attempt),
                         ("infrastructure_error", "transport failed"))
        replacement = claim_job(db, self.run_id, "replacement")
        self.assertNotEqual(replacement["seed_id"], first["seed_id"])
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


class PublishingNeverStopsTracing(unittest.TestCase):
    """Every trace process starts the publisher before it traces anything."""

    def _ensure(self, active_code, start_code, stderr=""):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["systemctl", "--user", "is-active"]:
                return subprocess.CompletedProcess(command, active_code)
            return subprocess.CompletedProcess(command, start_code, "", stderr)

        with mock.patch.dict(trace_pipeline.CONFIG,
                             {"publish": {"hf_dataset": "example/dataset"}}), \
             mock.patch.object(trace_pipeline, "_moonshiner_executable",
                               lambda: "/usr/bin/moonshiner"), \
             mock.patch.object(trace_pipeline.subprocess, "run", fake_run), \
             contextlib.redirect_stdout(io.StringIO()) as printed:
            trace_pipeline.ensure_publish_queue()
        return calls, printed.getvalue()

    def test_losing_the_race_to_start_the_publisher_does_not_raise(self):
        # Two trace processes reach this at once on any multi-worker pass, and
        # is-active is still false for a unit that is only deactivating. The
        # loser is told the name is taken, which reports a publisher that is
        # already running — not a reason to stop tracing.
        calls, printed = self._ensure(1, 1, "Failed to start transient service "
                                      "unit: Unit m-publish.service was "
                                      "already loaded or has a fragment file.")
        self.assertEqual(len(calls), 2)
        self.assertIn("tracing continues", printed)
        self.assertIn("already loaded", printed)

    def test_an_absent_publisher_is_started(self):
        calls, printed = self._ensure(1, 0)
        self.assertEqual(calls[1][0], "systemd-run")
        self.assertEqual(calls[1][-1], "publish-queue-worker")
        self.assertEqual(printed, "")

    def test_a_running_publisher_is_left_alone(self):
        calls, printed = self._ensure(0, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(printed, "")


if __name__ == "__main__":
    unittest.main()
