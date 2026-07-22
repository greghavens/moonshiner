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


class OnePipelineInvariant(unittest.TestCase):
    def test_publisher_has_exactly_one_formatter_and_no_schema_dispatch(self):
        source = inspect.getsource(publish_queue)
        self.assertEqual(source.count('"src/export_hf_next_steps.py"'), 1)
        for forbidden in (
                "publication_shape", "export_whole_trajectories",
                "teacher_model ==", "teacher_model in", "hf_dataset =="):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("migrate_canonical_dataset", source)

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
            source = {"meta": {"teacher_runtime": "native",
                                "trace_format": "native-v1"}, "tools": tools}
            (next_step / "train.jsonl").write_text(json.dumps(source) + "\n")
            (next_step / "val.jsonl").write_text("")
            path = data / "traces.jsonl"
            path.write_text(json.dumps({"task": "research-one", "lang": "en",
                "category": "Tool calling", "teacher_runtime": "native",
                "teacher_model": "acme/model", "provider": "provider",
                "reasoning_effort": "max", "model_attested": True,
                "observed_models": ["acme/model"], "trace_format": "native-v1",
                "messages": [{"role": "user", "content": "research"},
                    {"role": "assistant", "content": "first"},
                    {"role": "assistant", "content": "final"}]}) + "\n")
            with mock.patch.object(migrate_canonical_dataset, "DATA", data), \
                 mock.patch.object(migrate_canonical_dataset, "CONFIG",
                                   {"build": {"val_frac": 0.0}}):
                self.assertEqual(migrate_canonical_dataset.migrate(path), (1, 2))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(list(row) == export_hf_next_steps.PUBLISH_KEY_ORDER
                                for row in rows))
            self.assertTrue(path.with_name("traces.jsonl.pre-canonical").is_file())


if __name__ == "__main__":
    unittest.main()
