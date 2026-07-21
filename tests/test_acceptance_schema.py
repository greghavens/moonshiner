from __future__ import annotations

import json
import inspect
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from review_contract import is_accepted, verdict_accepts  # noqa: E402
import publish_queue  # noqa: E402
import trace_pipeline  # noqa: E402
import run_state  # noqa: E402
import build_dataset  # noqa: E402
import seed_inventory  # noqa: E402


class AcceptanceSchemaTests(unittest.TestCase):
    def test_published_hf_tasks_are_completed_imports(self):
        import import_existing
        with tempfile.TemporaryDirectory() as directory:
            data = pathlib.Path(directory)
            ledger = data / "hf-sync" / "published-trajectories.json"
            ledger.parent.mkdir(parents=True)
            ledger.write_text(json.dumps({"published_tasks": ["already-paid"]}))
            with mock.patch.object(import_existing, "DATA", data), \
                 mock.patch.object(import_existing, "_load_index",
                                   return_value={"task_ids": ["legacy"]}):
                self.assertEqual(import_existing.imported_task_ids(),
                                 {"legacy", "already-paid"})

    def test_queue_transition_uses_only_judge_acceptance(self):
        self.assertTrue(is_accepted(
            {"accepted": True, "judge": {"runtime": "test"}}))
        self.assertFalse(is_accepted(
            {"accepted": False}))
        self.assertFalse(is_accepted(
            {"accepted": True, "status": "deterministic_pass"}))
        self.assertTrue(verdict_accepts({"accepted": True, "reason": "ok"}))

    def test_publisher_discovers_accepted_coding_review(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = pathlib.Path(directory)
            (traces / "reviews").mkdir()
            (traces / "meta").mkdir()
            review = {
                "accepted": True,
                "deterministic": {"passed": True},
                "judge": {"model_attested": True},
            }
            (traces / "reviews" / "coding-seed.json").write_text(json.dumps(review))
            (traces / "meta" / "coding-seed.json").write_text("{}")
            with mock.patch.object(publish_queue, "TRACES", traces), \
                 mock.patch.object(seed_inventory, "TRACES", traces), \
                 mock.patch("import_existing.imported_task_ids", return_value=set()):
                ready = publish_queue.accepted_tasks()
            self.assertEqual([task for _, task, _ in ready], ["coding-seed"])

    def test_durable_acceptance_can_never_be_selected_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            traces = root / "traces"
            (traces / "reviews").mkdir(parents=True)
            # Reproduce the historical failure: a later rejected review has
            # overwritten the accepted review, but the durable attempt remains.
            (traces / "reviews" / "already-paid.json").write_text(
                json.dumps({"id": "already-paid", "accepted": False}))
            database = root / "runs.sqlite3"
            def test_connect():
                return run_state.connect(database)

            db = test_connect()
            run_id = run_state.create_run(db, "trace", {}, {}, ["already-paid"])
            run_state.start_attempt(db, run_id, "already-paid", 1)
            run_state.finish_attempt(db, run_id, "already-paid", 1, "accepted")
            db.close()

            args = SimpleNamespace(only=None, category=None, tag=None,
                                   kind="all", name=None, max_attempts=2,
                                   limit=0, all=True)
            seed = {"id": "already-paid"}
            with mock.patch("common.TRACES", traces), \
                 mock.patch.object(trace_pipeline, "connect",
                                   side_effect=test_connect), \
                 mock.patch.object(trace_pipeline, "select_seeds",
                                   return_value=[seed]), \
                 mock.patch("import_existing.imported_task_ids",
                            return_value=set()):
                self.assertEqual(trace_pipeline._selected(args), [])

    def test_seed_author_acceptance_does_not_complete_trace_work(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / "runs.sqlite3"
            real_connect = run_state.connect
            db = real_connect(database)
            run_id = run_state.create_run(db, "seed-author", {}, {},
                                          ["new-security-seed"])
            run_state.start_attempt(db, run_id, "new-security-seed", 1)
            run_state.finish_attempt(db, run_id, "new-security-seed", 1,
                                     "accepted")
            db.close()
            args = SimpleNamespace(only=None, category=None, tag=None,
                                   kind="all", name=None, max_attempts=2,
                                   limit=0, all=True)
            seed = {"id": "new-security-seed"}
            with mock.patch.object(trace_pipeline, "connect",
                                   side_effect=lambda: real_connect(database)), \
                    mock.patch.object(trace_pipeline, "select_seeds",
                                      return_value=[seed]), \
                    mock.patch("import_existing.imported_task_ids",
                               return_value=set()):
                self.assertEqual(trace_pipeline._selected(args), [seed])

    def test_reauthored_seed_invalidates_imported_and_filesystem_trace_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            traces = root / "traces"
            (traces / "reviews").mkdir(parents=True)
            seed_id = "replaced-seed"
            (traces / "reviews" / f"{seed_id}.json").write_text(
                json.dumps({"id": seed_id, "accepted": True,
                            "judge": {"runtime": "legacy"}}))
            database = root / "runs.sqlite3"
            real_connect = run_state.connect
            db = real_connect(database)
            old_trace = run_state.create_run(db, "trace", {}, {}, [seed_id])
            run_state.start_attempt(db, old_trace, seed_id, 1)
            run_state.finish_attempt(db, old_trace, seed_id, 1, "accepted")
            replacement = run_state.create_run(db, "seed", {}, {}, [seed_id])
            run_state.start_attempt(db, replacement, seed_id, 1)
            run_state.finish_attempt(db, replacement, seed_id, 1, "accepted")
            db.close()
            args = SimpleNamespace(only=None, category=None, tag=None,
                                   name=None, max_attempts=2, limit=0, all=True)
            with mock.patch.object(trace_pipeline, "connect",
                                   side_effect=lambda: real_connect(database)), \
                 mock.patch.object(trace_pipeline, "select_seeds",
                                   return_value=[{"id": seed_id}]), \
                 mock.patch.object(seed_inventory, "TRACES", traces), \
                 mock.patch("import_existing.imported_task_ids",
                            return_value={seed_id}):
                self.assertEqual(trace_pipeline._selected(args), [{"id": seed_id}])

    def test_new_trace_acceptance_after_reauthor_completes_trace_work(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / "runs.sqlite3"
            db = run_state.connect(database)
            seed_id = "replacement-completed"
            seed_run = run_state.create_run(db, "seed", {}, {}, [seed_id])
            run_state.start_attempt(db, seed_run, seed_id, 1)
            run_state.finish_attempt(db, seed_run, seed_id, 1, "accepted")
            trace_run = run_state.create_run(db, "trace", {}, {}, [seed_id])
            run_state.start_attempt(db, trace_run, seed_id, 1)
            run_state.finish_attempt(db, trace_run, seed_id, 1, "accepted")
            self.assertEqual(run_state.accepted_attempt_versions(db, "seed"),
                             {seed_id: 1})
            self.assertEqual(run_state.accepted_attempt_versions(db, "trace"),
                             {seed_id: 2})
            with self.assertRaises(ValueError):
                run_state.accepted_attempt_versions(db, "anything")
            db.close()

    def test_old_trace_attempts_do_not_exhaust_reauthored_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / "runs.sqlite3"
            db = run_state.connect(database)
            seed_id = "replacement-attempt-budget"
            old = run_state.create_run(db, "trace", {}, {}, [seed_id])
            for number, status in ((1, "retry"), (2, "exhausted")):
                run_state.start_attempt(db, old, seed_id, number)
                run_state.finish_attempt(db, old, seed_id, number, status)
            replacement = run_state.create_run(db, "seed", {}, {}, [seed_id])
            run_state.start_attempt(db, replacement, seed_id, 1)
            run_state.finish_attempt(db, replacement, seed_id, 1, "accepted")
            self.assertEqual(
                run_state.trace_attempt_counts_for_current_seed_revision(db), {})
            db.close()

    def test_hidden_accepted_artifact_is_restored_for_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            traces = root / "traces"
            archive = root / "accepted-artifact"
            for child in ("reviews", "meta", "raw", "diffs"):
                (traces / child).mkdir(parents=True)
            archive.mkdir()
            seed_id = "hidden-acceptance"
            (traces / "reviews" / f"{seed_id}.json").write_text(
                json.dumps({"id": seed_id, "accepted": False}))
            (archive / "reviews.json").write_text(
                json.dumps({"id": seed_id, "accepted": True,
                            "judge": {"runtime": "test"}}))
            (archive / "meta.json").write_text(json.dumps({
                "id": seed_id, "raw_path": f"traces/raw/{seed_id}.jsonl"}))
            (archive / f"{seed_id}.jsonl").write_text("accepted raw\n")
            database = root / "runs.sqlite3"
            db = run_state.connect(database)
            run_id = run_state.create_run(db, "trace", {}, {}, [seed_id])
            run_state.start_attempt(db, run_id, seed_id, 1)
            run_state.finish_attempt(db, run_id, seed_id, 1, "accepted",
                                     artifact_path=str(archive))
            db.close()

            real_connect = run_state.connect
            with mock.patch.object(publish_queue, "TRACES", traces), \
                 mock.patch.object(run_state, "connect",
                                   side_effect=lambda: real_connect(database)):
                self.assertEqual(publish_queue.restore_hidden_acceptances(), [seed_id])
                ready = publish_queue.accepted_tasks()
            self.assertEqual([task for _, task, _ in ready], [seed_id])
            restored = json.loads(
                (traces / "reviews" / f"{seed_id}.json").read_text())
            self.assertTrue(is_accepted(restored))

    def test_seed_judge_artifact_can_never_be_restored_as_a_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            traces = root / "traces"
            archive = root / "seed-artifact"
            for child in ("reviews", "meta", "raw"):
                (traces / child).mkdir(parents=True)
            archive.mkdir()
            seed_id = "seed-only"
            (archive / "reviews.json").write_text(json.dumps({
                "id": seed_id, "accepted": True, "judge": {"runtime": "codex"}}))
            (archive / "meta.json").write_text(json.dumps({
                "id": seed_id, "raw_path": f"traces/raw/{seed_id}.jsonl"}))
            (archive / f"{seed_id}.jsonl").write_text("not a trace\n")
            database = root / "runs.sqlite3"
            real_connect = run_state.connect
            db = real_connect(database)
            run_id = run_state.create_run(db, "seed", {}, {}, [seed_id])
            run_state.start_attempt(db, run_id, seed_id, 1)
            run_state.finish_attempt(db, run_id, seed_id, 1, "accepted",
                                     artifact_path=str(archive))
            db.close()
            with mock.patch.object(publish_queue, "TRACES", traces), \
                 mock.patch.object(run_state, "connect",
                                   side_effect=lambda: real_connect(database)):
                self.assertEqual(publish_queue.restore_hidden_acceptances(), [])

    def test_infrastructure_block_is_not_blindly_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / "runs.sqlite3"
            real_connect = run_state.connect
            db = real_connect(database)
            run_id = run_state.create_run(db, "trace", {}, {}, ["blocked-seed"])
            run_state.set_job(db, run_id, "blocked-seed",
                              "infrastructure_blocked", 0, "judge failed")
            db.close()
            args = SimpleNamespace(only=None, category=None, tag=None,
                                   kind="all", name=None, max_attempts=2,
                                   limit=0, all=True)
            with mock.patch.object(trace_pipeline, "connect",
                                   side_effect=lambda: real_connect(database)), \
                 mock.patch.object(trace_pipeline, "select_seeds",
                                   return_value=[{"id": "blocked-seed"}]), \
                 mock.patch("import_existing.imported_task_ids",
                            return_value=set()):
                self.assertEqual(trace_pipeline._selected(args), [])

    def test_synthetic_trace_format_cannot_build_a_dataset_row(self):
        row, error = build_dataset.build_row(
            {"id": "tool-seed"},
            {"trace_format": "moonshiner-behavior-openai-v1"})
        self.assertIsNone(row)
        self.assertEqual(error, "synthetic tool transcript is prohibited")

    def test_publisher_subprocess_uses_project_storage_context(self):
        with mock.patch.object(publish_queue.subprocess, "run") as run:
            publish_queue.run("src/build_dataset.py", "--quiet")
        run.assert_called_once_with([
            sys.executable,
            str(publish_queue.ROOT / "src/build_dataset.py"), "--quiet"],
            cwd=publish_queue.PROJECT_ROOT, check=True)

    def test_local_append_never_substitutes_for_remote_upload(self):
        source = inspect.getsource(publish_queue.main)
        self.assertNotIn("already_present", source)
        self.assertNotIn("[acknowledged existing]", source)

    def test_built_tasks_reads_formatted_trajectory_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            data = pathlib.Path(directory)
            (data / "next_step").mkdir()
            (data / "next_step" / "train.jsonl").write_text(
                json.dumps({"meta": {"task": "ready-one"}}) + "\n")
            (data / "next_step" / "val.jsonl").write_text(
                json.dumps({"meta": {"task": "ready-two"}}) + "\n")
            with mock.patch.object(publish_queue, "DATA", data):
                self.assertEqual(publish_queue.built_tasks(),
                                 {"ready-one", "ready-two"})

    def test_builder_uses_pi_recorded_events_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = pathlib.Path(directory)
            with mock.patch.object(build_dataset, "TRACES", storage / "traces"):
                path = build_dataset.raw_trace_path(
                    "paid-seed", {"raw_path": "traces/raw/paid-seed.events.jsonl"})
            self.assertEqual(path, storage / "traces/raw/paid-seed.events.jsonl")


if __name__ == "__main__":
    unittest.main()
