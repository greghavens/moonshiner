import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import import_existing
from expand_next_steps import expand_record


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

    def test_cumulative_rows_import_once_per_task_and_reexpand_once(self):
        def cumulative(task, answers):
            messages = [{"role": "user", "content": f"do {task}"}]
            rows = []
            for step, answer in enumerate(answers, 1):
                messages.append({"role": "assistant", "content": answer})
                rows.append({
                    "task": task, "lang": "en", "category": "coding",
                    "teacher_runtime": "native", "teacher_model": "model",
                    "reasoning_effort": "high", "provider": "provider",
                    "observed_models": ["model"], "model_attested": True,
                    "trace_format": "native-v1", "tools_used": [],
                    "derivation": "cumulative-next-assistant-v1",
                    "assistant_step": step, "assistant_steps": len(answers),
                    "target_message_index": len(messages) - 1,
                    "original_n_messages": 1 + len(answers),
                    "n_messages": len(messages),
                    "messages": list(messages),
                })
            return rows

        unrelated = {"messages": [{"role": "user", "content": "keep exactly"}],
                     "meta": {"task": "unrelated"}}
        rows = (cumulative("fable-task", ["f1", "f2"])
                + cumulative("kimi-task", ["k1", "k2", "k3"])
                + cumulative("glm-task", ["g1", "g2", "g3", "g4"])
                + [unrelated])
        path = self.source / "train.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        patches = (
            mock.patch.object(import_existing, "DATA", self.data),
            mock.patch.object(import_existing, "TRACES", self.traces),
            mock.patch.object(import_existing, "INDEX",
                              self.traces / "imported_index.json"))
        with patches[0], patches[1], patches[2]:
            result = import_existing.import_directory(self.source, "canonical")
            output = self.data / "imported" / "canonical" / "rows.jsonl"
            imported = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(result["prepared_rows"], 4)
            self.assertEqual(
                sum(len(expand_record(row)) for row in imported
                    if row["meta"]["task"] != "unrelated"), 9)
            self.assertEqual(next(row["messages"] for row in imported
                                  if row["meta"]["task"] == "unrelated"),
                             unrelated["messages"])

            path.write_text(path.read_text() + "".join(
                json.dumps(row) + "\n"
                for row in cumulative("kimi-task", ["new-1", "new-2"])))
            import_existing.import_directory(self.source, "canonical")
            replaced = [json.loads(line) for line in output.read_text().splitlines()]
            kimi = [row for row in replaced if row["meta"]["task"] == "kimi-task"]
            self.assertEqual(len(kimi), 1)
            self.assertEqual(kimi[0]["messages"][-1]["content"], "new-2")
            self.assertEqual(next(row["messages"] for row in replaced
                                  if row["meta"]["task"] == "unrelated"),
                             unrelated["messages"])
            before_restart = output.read_bytes()
            import_existing.import_directory(self.source, "canonical")
            self.assertEqual(output.read_bytes(), before_restart)


if __name__ == "__main__":
    unittest.main()
