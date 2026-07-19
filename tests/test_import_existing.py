import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import import_existing


class ExistingImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "old"
        self.source.mkdir()
        self.data = self.root / "data"
        self.traces = self.root / "traces"

    def tearDown(self):
        self.temp.cleanup()

    def test_prepared_rows_are_sanitized_deduped_and_indexed(self):
        row = {"messages": [{"role": "user", "content": "contact jane@example.com sk-abcdefghijklmnop"}],
               "tools": [], "meta": {"task": "old-task"}}
        (self.source / "train.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
        with (mock.patch.object(import_existing, "DATA", self.data),
              mock.patch.object(import_existing, "TRACES", self.traces),
              mock.patch.object(import_existing, "INDEX", self.traces / "imported_index.json")):
            result = import_existing.import_directory(self.source, "legacy")
            self.assertEqual(result["prepared_rows"], 1)
            self.assertEqual(result["task_ids"], ["old-task"])
            text = (self.data / "imported" / "legacy" / "rows.jsonl").read_text()
            self.assertNotIn("jane@example.com", text)
            self.assertNotIn("sk-abcdefghijklmnop", text)

    def test_native_artifacts_are_preserved(self):
        (self.source / "traces" / "raw").mkdir(parents=True)
        (self.source / "traces" / "meta").mkdir(parents=True)
        (self.source / "traces" / "raw" / "task-a.jsonl").write_text("{}\n")
        (self.source / "traces" / "meta" / "task-a.json").write_text(
            json.dumps({"id": "task-a", "passed": True}))
        with (mock.patch.object(import_existing, "DATA", self.data),
              mock.patch.object(import_existing, "TRACES", self.traces),
              mock.patch.object(import_existing, "INDEX", self.traces / "imported_index.json")):
            result = import_existing.import_directory(self.source, "legacy")
            self.assertEqual(result["artifacts"], 2)
            self.assertEqual(result["task_ids"], ["task-a"])
            self.assertTrue((self.traces / "raw" / "task-a.jsonl").exists())

    def test_reimport_adds_new_rows_without_duplicating_old_rows(self):
        first = {"messages": [{"role": "user", "content": "one"}],
                 "tools": [], "meta": {"task": "one"}}
        second = {"messages": [{"role": "user", "content": "two"}],
                  "tools": [], "meta": {"task": "two"}}
        path = self.source / "train.jsonl"
        path.write_text(json.dumps(first) + "\n")
        with (mock.patch.object(import_existing, "DATA", self.data),
              mock.patch.object(import_existing, "TRACES", self.traces),
              mock.patch.object(import_existing, "INDEX", self.traces / "imported_index.json")):
            import_existing.import_directory(self.source, "legacy")
            path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
            result = import_existing.import_directory(self.source, "legacy")
            self.assertEqual(result["prepared_rows"], 2)
            self.assertEqual(result["task_ids"], ["one", "two"])


if __name__ == "__main__":
    unittest.main()
