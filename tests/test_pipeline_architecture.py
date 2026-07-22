import inspect
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import export_hf_next_steps  # noqa: E402
import publish_queue  # noqa: E402
import migrate_canonical_dataset  # noqa: E402
import publish  # noqa: E402


class OnePipelineInvariant(unittest.TestCase):
    def test_publisher_has_exactly_one_formatter_and_no_schema_dispatch(self):
        source = inspect.getsource(publish_queue)
        self.assertEqual(source.count('"src/export_hf_next_steps.py"'), 1)
        for forbidden in (
                "publication_shape", "export_whole_trajectories",
                "teacher_model ==", "teacher_model in", "hf_dataset =="):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("migrate_canonical_dataset", source)

    def test_publication_format_never_dispatches_on_model_or_dataset(self):
        source = inspect.getsource(publish)
        self.assertNotIn("teacher_model ==", source)
        self.assertNotIn("hf_dataset ==", source)
        self.assertIn("publication_format", source)
        self.assertEqual(source.count(".create_commit("), 1)
        self.assertNotIn('"hf", "upload"', source)

    def test_every_model_emits_the_identical_canonical_columns(self):
        def record(model):
            return {"meta": {
                "task": "same-task", "source_trajectory_id": "source-id",
                "source_sha256": "a" * 64, "lang": "en",
                "category": "tool-calling", "domain": "instruction-following",
                "verifier": "judge", "teacher_runtime": "native-harness",
                "teacher_model": model, "reasoning_effort": "max",
                "provider": "configured-provider", "observed_models": [model],
                "model_attested": True, "trace_format": "native-v1",
                "tools_used": ["search"],
                "derivation": "cumulative-next-assistant-v1",
                "assistant_step": 1, "assistant_steps": 1,
                "target_message_index": 1, "original_n_messages": 2},
                "messages": [{"role": "user", "content": "research"},
                             {"role": "assistant", "content": "done"}],
                "tools": []}
        keys = [list(export_hf_next_steps.build_row(record(model), "train"))
                for model in ("anthropic/claude-fable-5", "moonshotai/kimi-k3",
                              "acme/future-model")]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[1], keys[2])
        self.assertEqual(keys[0], export_hf_next_steps.PUBLISH_KEY_ORDER)

    def test_one_time_migration_feeds_the_one_canonical_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            data = pathlib.Path(directory)
            next_step = data / "next_step"; next_step.mkdir()
            tools = [{"type": "function", "function": {
                "name": "search", "parameters": {"type": "object"}}}]
            source = {"meta": {"teacher_runtime": "native-current",
                                "trace_format": "native-v1"}, "tools": tools}
            (next_step / "train.jsonl").write_text(json.dumps(source) + "\n")
            (next_step / "val.jsonl").write_text("")
            path = data / "traces.jsonl"
            path.write_text(json.dumps({"task": "research-one", "lang": "en",
                "category": "Tool calling", "teacher_runtime": "native-legacy",
                "teacher_model": "acme/model", "provider": "provider",
                "reasoning_effort": "max", "model_attested": True,
                "observed_models": ["acme/model"], "trace_format": "native-v1",
                "messages": [{"role": "user", "content": "research"},
                    {"role": "assistant", "content": "first"},
                    {"role": "assistant", "content": "final"}]}) + "\n")
            sync = data / "hf-sync"; sync.mkdir()
            dataset = "owner/model-traces"
            marker_name = __import__("hashlib").sha256(
                f"{dataset}:traces.jsonl".encode()).hexdigest()[:16]
            marker = sync / f"{marker_name}.json"
            marker.write_text(json.dumps({"dataset": dataset,
                                          "filename": "traces.jsonl"}))
            with mock.patch.object(migrate_canonical_dataset, "DATA", data), \
                 mock.patch.object(migrate_canonical_dataset, "CONFIG",
                                   {"build": {"val_frac": 0.0}, "publish": {
                                       "hf_dataset": dataset,
                                       "filename": "traces.jsonl"}}):
                self.assertEqual(migrate_canonical_dataset.migrate(path), (1, 2))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(list(row) == export_hf_next_steps.PUBLISH_KEY_ORDER
                                for row in rows))
            self.assertTrue(path.with_name("traces.jsonl.pre-canonical").is_file())
            self.assertEqual(json.loads(marker.read_text())["bootstrap_rows"], 2)

    def test_historical_canonical_rows_are_upgraded_without_trace_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            data = pathlib.Path(directory)
            messages = [{"role": "user", "content": "inspect the repository"},
                        {"role": "assistant", "content": "I will inspect it."}]
            tools = [{"type": "function", "function": {
                "name": "read_file", "parameters": {"type": "object"}}}]
            historical = {
                "task": "historical-one", "source_trajectory_id": "source-one",
                "source_trajectory_sha256": "a" * 64, "lang": "en",
                "category": "Debugging", "kind": "trace", "domain": "coding",
                "security_task": False, "verifier": "judge", "split": "train",
                "session_id": "session-one", "teacher_model": "acme/model",
                "reasoning_effort": "high", "trace_format": "native-rollout",
                "tools_used": ["read_file"], "trace_part": 1, "trace_parts": 1,
                "continuation": False, "derivation": "cumulative-next-assistant-v1",
                "assistant_step": 1, "assistant_steps": 1,
                "target_message_index": 1, "original_n_messages": 2,
                "n_messages": 2, "messages": messages,
                "tools": json.dumps(tools)}
            path = data / "traces.jsonl"
            path.write_text(json.dumps(historical) + "\n")
            with mock.patch.object(migrate_canonical_dataset, "DATA", data), \
                 mock.patch.object(migrate_canonical_dataset, "CONFIG", {}):
                self.assertEqual(migrate_canonical_dataset.migrate(path), (1, 1))
            row = json.loads(path.read_text())
            self.assertEqual(list(row), export_hf_next_steps.PUBLISH_KEY_ORDER)
            self.assertEqual(row["messages"], messages)
            self.assertEqual(json.loads(row["tools"]), tools)
            self.assertEqual(row["source_trajectory_id"], "source-one")
            self.assertEqual(row["source_trajectory_sha256"], "a" * 64)
            self.assertIsNone(row["teacher_runtime"])
            self.assertIsNone(row["provider"])
            self.assertFalse(row["model_attested"])

    def test_migration_uses_the_single_imported_source_when_mirror_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            data = pathlib.Path(directory)
            imported = data / "imported" / "owner-dataset" / "rows.jsonl"
            imported.parent.mkdir(parents=True)
            imported.write_text("{}\n")
            expected = data / "hf-publish" / "traces.jsonl"
            with mock.patch.object(migrate_canonical_dataset, "DATA", data):
                self.assertEqual(migrate_canonical_dataset.migration_path(), expected)
            self.assertEqual(expected.read_text(), "{}\n")


if __name__ == "__main__":
    unittest.main()
