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


def row(source="trajectory-a", step=1, content="answer"):
    return {"source_trajectory_id": source, "assistant_step": step,
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


class AppendOnlyExport(unittest.TestCase):
    def test_appends_new_identity_and_keeps_existing_bytes(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); output = root / "traces.jsonl"; journal = root / "journal.jsonl"
            old = json.dumps(row()) + "\n"; output.write_text(old)
            journal.write_text(json.dumps(row()) + "\n" + json.dumps(row("trajectory-b")) + "\n")
            appended, skipped = export.append_journal(output, journal)
            self.assertEqual((appended, skipped), (1, 1))
            self.assertTrue(output.read_text().startswith(old))
            self.assertEqual(len(output.read_text().splitlines()), 2)

    def test_refuses_to_replace_existing_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name); output = root / "traces.jsonl"; journal = root / "journal.jsonl"
            output.write_text(json.dumps(row(content="old")) + "\n")
            journal.write_text(json.dumps(row(content="changed")) + "\n")
            with self.assertRaisesRegex(ValueError, "refusing to replace"):
                export.append_journal(output, journal)
            self.assertIn('"old"', output.read_text())


if __name__ == "__main__":
    unittest.main()
