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


class RunLedger(unittest.TestCase):
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

    @mock.patch.object(trace_pipeline, "quarantined_tasks", return_value=set())
    @mock.patch.object(trace_pipeline, "load_seeds")
    def test_default_selects_one(self, load, _quarantine):
        load.return_value = [{"id": "a"}, {"id": "b"}]
        self.assertEqual([s["id"] for s in trace_pipeline._selected(self._args())], ["a"])

    @mock.patch.object(trace_pipeline, "quarantined_tasks", return_value=set())
    @mock.patch.object(trace_pipeline, "load_seeds")
    def test_all_is_explicit(self, load, _quarantine):
        load.return_value = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(len(trace_pipeline._selected(self._args(all=True))), 2)


if __name__ == "__main__":
    unittest.main()
