import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import export_hf_next_steps as export  # noqa: E402
import hf_sync  # noqa: E402
import publish_queue  # noqa: E402
import validate_hf_export  # noqa: E402


def row(source="trajectory-a", step=1, content="answer"):
    return {"task": source, "source_trajectory_id": source, "assistant_step": step,
            "messages": [{"role": "assistant", "content": content}]}


class LocalFirstBootstrap(unittest.TestCase):
    def test_existing_local_file_is_kept_and_later_runs_do_not_check_remote(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name)
            target = root / "publish" / "traces.jsonl"
            target.parent.mkdir(); target.write_text('{"existing":true}\n')
            config = {"publish": {"hf_dataset": "owner/data",
                                  "filename": "traces.jsonl",
                                  "check_before_append": False}}
            with (mock.patch.object(hf_sync, "CONFIG", config),
                  mock.patch.object(hf_sync, "DATA", root),
                  mock.patch.object(hf_sync, "_dataset_info",
                                    return_value={"sha": "abc", "siblings": [{"rfilename": "traces.jsonl"}]})):
                first = hf_sync.ensure_local_dataset(target=target)
                self.assertEqual(first["origin"], "existing_local")
                original = target.read_bytes()
                with mock.patch.object(hf_sync, "_dataset_info",
                                       side_effect=AssertionError("remote checked twice")):
                    second = hf_sync.ensure_local_dataset(target=target)
                self.assertEqual(second["status"], "local_append")
                self.assertEqual(target.read_bytes(), original)

    def test_missing_local_file_downloads_remote_once(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); target = root / "publish" / "traces.jsonl"
            config = {"publish": {"hf_dataset": "owner/data", "filename": "traces.jsonl"}}
            def download(dataset, revision, filename, destination):
                destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text("remote\n")
            with (mock.patch.object(hf_sync, "CONFIG", config),
                  mock.patch.object(hf_sync, "DATA", root),
                  mock.patch.object(hf_sync, "RUNS", root / "runs"),
                  mock.patch.object(hf_sync, "_dataset_info",
                                    return_value={"sha": "abc", "siblings": [{"rfilename": "traces.jsonl"}]}),
                  mock.patch.object(hf_sync, "_download", side_effect=download) as fetch):
                result = hf_sync.ensure_local_dataset(target=target)
                self.assertEqual(result["origin"], "downloaded_remote")
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(target.read_text(), "remote\n")


class TaskKeyedExport(unittest.TestCase):
    def test_appends_new_identity_and_keeps_existing_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); output = root / "traces.jsonl"; journal = root / "journal.jsonl"
            old = json.dumps(row()) + "\n"; output.write_text(old)
            journal.write_text(json.dumps(row()) + "\n" + json.dumps(row("trajectory-b")) + "\n")
            written, replaced = export.upsert_journal(output, journal)
            self.assertEqual((written, replaced), (2, 1))
            self.assertEqual(len(output.read_text().splitlines()), 2)

    def test_replaces_only_rows_for_the_same_task(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); output = root / "traces.jsonl"; journal = root / "journal.jsonl"
            output.write_text(json.dumps(row(content="old")) + "\n" +
                              json.dumps(row("trajectory-b", content="keep")) + "\n")
            replacement = row(content="changed")
            journal.write_text(json.dumps(replacement) + "\n")
            written, replaced = export.upsert_journal(output, journal)
            self.assertEqual((written, replaced), (1, 1))
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual({item["source_trajectory_id"] for item in rows},
                             {"trajectory-a", "trajectory-b"})
            self.assertIn("changed", {item["messages"][0]["content"] for item in rows})
            self.assertIn("keep", {item["messages"][0]["content"] for item in rows})

    def test_existing_whole_trajectory_schema_is_preserved(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name)
            output = root / "traces.jsonl"
            output.write_text(json.dumps({
                "task": "old", "lang": "en", "category": "building",
                "teacher_runtime": "pi", "teacher_model": "acme/old",
                "provider": "openrouter", "reasoning_effort": "max",
                "model_attested": True, "observed_models": ["acme/old"],
                "trace_format": "native", "n_messages": 1,
                "messages": [{"role": "assistant", "content": "old"}],
                "tools": "[]"}) + "\n")
            full = root / "full"; full.mkdir()
            record = {"meta": {"task": "new", "lang": "en",
                "category": "tool-calling", "teacher_runtime": "pi",
                "teacher_model": "acme/new", "provider": "openrouter",
                "reasoning_effort": "max", "model_attested": True,
                "observed_models": ["acme/new"], "trace_format": "native"},
                "messages": [{"role": "assistant", "content": "new"}],
                "tools": []}
            (full / "train.jsonl").write_text(json.dumps(record) + "\n")
            (full / "val.jsonl").write_text("")
            with mock.patch.object(publish_queue, "DATA", root):
                self.assertEqual(publish_queue.publication_shape(output), "whole")
                publish_queue.export_whole_trajectories(output, ["new"])
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(list(rows[0]), list(rows[1]))
            self.assertEqual(rows[1]["task"], "new")
            self.assertNotIn("assistant_step", rows[1])
            with mock.patch.object(validate_hf_export, "ROOT", root):
                self.assertEqual(validate_hf_export.validate(output), 2)


if __name__ == "__main__":
    unittest.main()
