import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import parquet_publication as parquet  # noqa: E402


def row(task, step=1, total=1, answer="ok"):
    return {
        "task": task, "lang": "en", "category": "Tool calling",
        "split": "train", "source_trajectory_id": task,
        "source_trajectory_sha256": (task[-1:] or "a") * 64,
        "teacher_runtime": "pi", "teacher_model": "configured/model",
        "reasoning_effort": "max", "provider": "configured",
        "observed_models": ["configured/model"], "model_attested": True,
        "trace_format": "pi-coding-agent-json-v3", "domain": "tools",
        "derivation": "cumulative-next-assistant-v1",
        "assistant_step": step, "assistant_steps": total,
        "target_message_index": 1, "original_n_messages": 2,
        "n_messages": 2,
        "messages": [{"role": "user", "content": "do it"},
                     {"role": "assistant", "content": answer}],
        "tools": "[]", "tools_used": [],
    }


class ParquetPublication(unittest.TestCase):
    def write_rows(self, path, rows):
        path.write_text("".join(json.dumps(item) + "\n" for item in rows))

    def test_initial_publish_creates_immutable_active_shards(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); source = root / "traces.jsonl"
            rows = [row(f"task-{index}") for index in range(12)]
            self.write_rows(source, rows)
            manifest = parquet.sync(source, root, changed_tasks={r["task"] for r in rows},
                                    trajectories_per_shard=10)
            self.assertEqual(manifest["format"], parquet.FORMAT)
            self.assertEqual(manifest["trajectory_count"], 12)
            self.assertEqual(manifest["row_count"], 12)
            self.assertEqual(len(manifest["active_shards"]), 2)
            self.assertEqual(parquet.read_active_rows(root), rows)

    def test_replacement_supersedes_one_task_without_losing_shard_neighbors(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); source = root / "traces.jsonl"
            original = [row(f"task-{index}") for index in range(12)]
            self.write_rows(source, original)
            first = parquet.sync(source, root, changed_tasks={r["task"] for r in original},
                                 trajectories_per_shard=10)
            old_paths = set(first["active_shards"])
            replacement = [dict(item) for item in original]
            replacement[3] = row("task-3", answer="replacement")
            self.write_rows(source, replacement)
            second = parquet.sync(source, root, changed_tasks={"task-3"},
                                  trajectories_per_shard=10)
            rows = parquet.read_active_rows(root)
            self.assertEqual(len(rows), 12)
            self.assertEqual({r["task"] for r in rows}, {r["task"] for r in original})
            self.assertEqual(next(r for r in rows if r["task"] == "task-3")
                             ["messages"][-1]["content"], "replacement")
            self.assertTrue(old_paths - set(second["active_shards"]))
            self.assertTrue(all((root / path).is_file() for path in old_paths))

    def test_noop_sync_does_not_rewrite_active_shards(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); source = root / "traces.jsonl"
            rows = [row("task-a")]
            self.write_rows(source, rows)
            first = parquet.sync(source, root, changed_tasks={"task-a"})
            mtimes = {p: (root / p).stat().st_mtime_ns
                      for p in first["active_shards"]}
            second = parquet.sync(source, root, changed_tasks=set())
            self.assertEqual(first["active_shards"], second["active_shards"])
            self.assertEqual(mtimes, {p: (root / p).stat().st_mtime_ns
                                     for p in second["active_shards"]})

    def test_new_canonical_task_is_discovered_without_explicit_task_argument(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); source = root / "traces.jsonl"
            original = [row("task-a")]
            self.write_rows(source, original)
            parquet.sync(source, root, changed_tasks={"task-a"})
            expanded = original + [row("task-b")]
            self.write_rows(source, expanded)
            manifest = parquet.sync(source, root, changed_tasks=set())
            self.assertEqual(manifest["trajectory_count"], 2)
            self.assertEqual({item["task"] for item in parquet.read_active_rows(root)},
                             {"task-a", "task-b"})

    def test_every_active_shard_has_the_exact_same_arrow_schema(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); source = root / "traces.jsonl"
            rows = [row(f"task-{index}") for index in range(21)]
            self.write_rows(source, rows)
            manifest = parquet.sync(source, root,
                                    changed_tasks={r["task"] for r in rows},
                                    trajectories_per_shard=10)
            import pyarrow.parquet as pq
            schemas = [pq.read_schema(root / path)
                       for path in manifest["active_shards"]]
            self.assertTrue(all(schema.equals(schemas[0]) for schema in schemas))

    def test_late_nested_message_fields_are_preserved_losslessly(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); source = root / "traces.jsonl"
            rows = [row("task-a"), row("task-b")]
            rows[0]["messages"][-1]["tool_calls"] = [{
                "id": "call-0", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]
            rows[1]["messages"][-1]["tool_calls"] = [{
                "id": "call-1", "type": "function",
                "function": {"name": "search", "arguments": {"q": "x"}},
            }]
            rows[1]["messages"][-1]["reasoning_details"] = [{
                "type": "text", "text": "inspect", "index": 0,
            }]
            self.write_rows(source, rows)
            parquet.sync(source, root,
                         changed_tasks={item["task"] for item in rows})
            rebuilt = parquet.read_active_rows(root)
            self.assertEqual([item["task"] for item in rebuilt],
                             [item["task"] for item in rows])
            arguments = [item["messages"][-1]["tool_calls"][0]
                         ["function"]["arguments"] for item in rebuilt]
            self.assertEqual([json.loads(item) for item in arguments],
                             [{"q": "x"}, {"q": "x"}])
            self.assertEqual(rebuilt[1]["messages"][-1]["reasoning_details"],
                             rows[1]["messages"][-1]["reasoning_details"])


if __name__ == "__main__":
    unittest.main()
