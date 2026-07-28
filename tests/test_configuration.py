import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import configuration
import run_state
import trace_pipeline


class Configuration(unittest.TestCase):
    def test_deep_merge_preserves_nested_siblings(self):
        merged = configuration.deep_merge(
            {"role": {"runtime": "pi", "model": "a"}},
            {"role": {"model": "b"}})
        self.assertEqual(merged, {"role": {"runtime": "pi", "model": "b"}})

    def test_dotted_set_and_get(self):
        value = {}
        configuration.dotted_set(value, "pipeline.trace.max_attempts", 3)
        self.assertEqual(configuration.dotted_get(
            value, "pipeline.trace.max_attempts"), 3)

    def test_parse_value_accepts_json_and_plain_strings(self):
        self.assertEqual(configuration.parse_value("12"), 12)
        self.assertEqual(configuration.parse_value("pi"), "pi")

    def test_declining_confirmation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / ".moonshiner"
            with mock.patch.object(configuration, "PROJECT_ROOT", root), \
                 mock.patch.object(configuration, "PROJECT_STATE", state), \
                 mock.patch.object(configuration, "LOCAL_PATH", state / "config.json"):
                accepted = configuration.confirm_project(
                    input_fn=lambda _: "n", output_fn=lambda _: None)
            self.assertFalse(accepted)
            self.assertFalse(state.exists())

    def test_confirmation_creates_local_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / ".moonshiner"
            local = state / "config.json"
            with mock.patch.object(configuration, "PROJECT_ROOT", root), \
                 mock.patch.object(configuration, "PROJECT_STATE", state), \
                 mock.patch.object(configuration, "LOCAL_PATH", local):
                accepted = configuration.confirm_project(
                    input_fn=lambda _: "", output_fn=lambda _: None)
                self.assertTrue(configuration.project_confirmed())
            saved = json.loads(local.read_text())
            self.assertTrue(accepted)
            self.assertEqual(saved["workspace"]["confirmed_root"], str(root))
            self.assertEqual(saved["storage"]["root"], str(state))


class RunLedger(unittest.TestCase):
    def test_a_stale_outcome_cannot_replay_an_attempt_number(self):
        """A late set_job must not lower the attempt counter.

        A worker finishing an attempt races the next claim, and the deferred
        requeue re-states an older number. Lowering the counter makes the next
        claim reuse an existing (run_id, seed_id, number), whose IntegrityError
        took down the whole coordinator rather than just the seed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = run_state.connect(pathlib.Path(tmp) / "state.sqlite3")
            run_id = run_state.create_run(db, "trace", {}, {}, ["a"])
            first = run_state.claim_job(db, run_id, "worker-1")["attempts"] + 1
            run_state.start_attempt(db, run_id, "a", first)
            run_state.finish_attempt(db, run_id, "a", first, "retry")
            second = run_state.claim_job(db, run_id, "worker-2")["attempts"] + 1
            run_state.start_attempt(db, run_id, "a", second)
            # The first worker's requeue lands late, carrying its old number.
            run_state.set_job(db, run_id, "a", "deferred", first, "late")
            db.execute("UPDATE jobs SET status='retry' WHERE run_id=? AND seed_id=?",
                       (run_id, "a"))
            db.commit()
            third = run_state.claim_job(db, run_id, "worker-3")["attempts"] + 1
            self.assertGreater(third, second)
            run_state.start_attempt(db, run_id, "a", third)  # must not raise
            db.close()

    def test_run_job_attempt_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = run_state.connect(pathlib.Path(tmp) / "state.sqlite3")
            run_id = run_state.create_run(db, "trace", {}, {"max": 2}, ["a"])
            run_state.start_attempt(db, run_id, "a", 1)
            run_state.finish_attempt(db, run_id, "a", 1, "accepted", {"tokens": 4})
            run_state.set_run_status(db, run_id, "complete")
            summary = run_state.summaries(db, run_id)[0]
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(summary["accepted"], 1)
            self.assertEqual(run_state.job_rows(db, run_id)[0]["attempts"], 1)
            db.close()


class SafeSelection(unittest.TestCase):
    def _args(self, **overrides):
        values = {"only": None, "limit": 0, "all": False}
        values.update(overrides)
        return type("Args", (), values)()

    @mock.patch.object(trace_pipeline, "select_seeds")
    @mock.patch("import_existing.imported_task_ids", return_value=set())
    def test_default_selects_one(self, _imported, load):
        load.return_value = [{"id": "a"}, {"id": "b"}]
        self.assertEqual([s["id"] for s in trace_pipeline._selected(self._args())], ["a"])
        self.assertNotIn("kind", load.call_args.kwargs)

    @mock.patch.object(trace_pipeline, "select_seeds")
    @mock.patch("import_existing.imported_task_ids", return_value=set())
    def test_all_is_explicit(self, _imported, load):
        load.return_value = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(
            len(trace_pipeline._selected(self._args(all=True))), 2)
        self.assertNotIn("kind", load.call_args.kwargs)

    @mock.patch.object(trace_pipeline, "select_seeds")
    @mock.patch("import_existing.imported_task_ids", return_value=set())
    def test_catalog_filters_do_not_create_a_second_loader(self, _imported, load):
        load.return_value = [{"id": "coding-seed"}, {"id": "tool-use-seed"}]
        selected = trace_pipeline._selected(self._args(
            all=True, category=["instruction-following"], tag=["multi-turn"]))
        self.assertEqual([seed["id"] for seed in selected],
                         ["coding-seed", "tool-use-seed"])
        self.assertEqual(load.call_args.kwargs["categories"],
                         {"instruction-following"})
        self.assertEqual(load.call_args.kwargs["tags"], {"multi-turn"})
        self.assertNotIn("kind", load.call_args.kwargs)

    @mock.patch.object(trace_pipeline, "select_seeds")
    @mock.patch("import_existing.imported_task_ids", return_value=set())
    def test_default_queue_uses_the_one_catalog_loader(self, _imported, load):
        load.return_value = [{"id": "a"}, {"id": "b"}]
        trace_pipeline._selected(self._args(all=True))
        self.assertNotIn("kind", load.call_args.kwargs)
        self.assertNotIn("require_authored", load.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()


class SeedRolesAreConfiguration(unittest.TestCase):
    """The teacher's metered budget buys traces, and nothing else.

    Setup overwrote seed_author with the teacher pick, so every project
    authored seeds on the account funding the traces of the model being
    distilled. The seed roles come from configuration.
    """

    def test_setup_does_not_overwrite_the_seed_roles(self):
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "moonshiner.py").read_text()
        setup = source[source.index("def _setup"):]
        setup = setup[:setup.index('update_local("storage.root"')]
        self.assertNotIn('update_local(f"{target}.runtime"', setup)
        self.assertNotIn('"seed_author"', setup)

    def test_the_shipped_default_does_not_author_on_the_teacher(self):
        config = json.loads((pathlib.Path(__file__).resolve().parents[1]
                             / "config.json").read_text())
        self.assertNotEqual(config["teacher"]["runtime"],
                            config["seed_author"]["runtime"])
