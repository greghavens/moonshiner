from __future__ import annotations

import json
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
import behavior_trace  # noqa: E402
import build_dataset  # noqa: E402


class AcceptanceSchemaTests(unittest.TestCase):
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
            with mock.patch.object(publish_queue, "TRACES", traces):
                ready = publish_queue.accepted_tasks()
            self.assertEqual([task for _, task in ready], ["coding-seed"])

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
            self.assertEqual([task for _, task in ready], [seed_id])
            restored = json.loads(
                (traces / "reviews" / f"{seed_id}.json").read_text())
            self.assertTrue(is_accepted(restored))

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

    def test_behavior_judge_verdict_routes_to_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            raw = root / "raw"
            reviews = root / "reviews"
            raw.mkdir(); reviews.mkdir()
            seed_path = root / "task.json"
            seed_path.write_text("{}")
            seed = {"id": "tool-seed", "prompt": "do it", "_path": seed_path}
            (raw / "tool-seed.jsonl").write_text(
                json.dumps({"role": "assistant", "content": "done"}) + "\n")
            result = SimpleNamespace(
                verdict={"accepted": True, "reason": "correct"},
                return_code=0, timed_out=False, model_attested=True, error=None)
            judge = SimpleNamespace(
                name="test-judge", role={"model": "judge-model"},
                run_review=mock.Mock(return_value=result))
            with mock.patch.object(behavior_trace, "RAW", raw), \
                 mock.patch.object(behavior_trace, "REVIEWS", reviews), \
                 mock.patch.object(behavior_trace, "grade",
                                   return_value={"accepted": False,
                                                 "reason": "diagnostic mismatch"}):
                review = behavior_trace.judge_trace(seed, judge)
            self.assertTrue(is_accepted(review))
            self.assertEqual(review["reason"], "correct")

    def test_publisher_subprocess_uses_project_storage_context(self):
        with mock.patch.object(publish_queue.subprocess, "run") as run:
            publish_queue.run("src/build_dataset.py", "--quiet")
        run.assert_called_once_with([
            sys.executable,
            str(publish_queue.ROOT / "src/build_dataset.py"), "--quiet"],
            cwd=publish_queue.PROJECT_ROOT, check=True)

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
            raw = pathlib.Path(directory)
            with mock.patch.object(build_dataset, "RAW", raw):
                path = build_dataset.raw_trace_path(
                    "paid-seed", {"raw_path": "traces/raw/paid-seed.events.jsonl"})
            self.assertEqual(path, raw / "paid-seed.events.jsonl")


if __name__ == "__main__":
    unittest.main()
