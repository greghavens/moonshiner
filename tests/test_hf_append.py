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
import migrate_canonical_dataset  # noqa: E402
import validate_hf_export  # noqa: E402


def row(source="trajectory-a", step=1, content="answer"):
    return {"task": source, "source_trajectory_id": source, "assistant_step": step,
            "messages": [{"role": "assistant", "content": content}]}


def published_row(task="trajectory-a", step=1, total=1):
    messages = [{"role": "user", "content": "do it"},
                {"role": "assistant", "content": "done"}]
    return {"task": task, "lang": "en", "category": "Tool calling",
            "split": "train", "assistant_step": step,
            "assistant_steps": total, "target_message_index": 1,
            "n_messages": 2, "messages": messages, "tools": "[]"}


class LocalFirstBootstrap(unittest.TestCase):
    def test_dataset_file_detection_uses_remote_siblings(self):
        info = {"siblings": [{"rfilename": "README.md"},
                              {"rfilename": "traces.jsonl"}]}
        with mock.patch.object(hf_sync, "_dataset_info", return_value=info):
            self.assertTrue(hf_sync.dataset_has_file("owner/data", "traces.jsonl"))
            self.assertFalse(hf_sync.dataset_has_file("owner/data", "missing.jsonl"))

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
    def test_export_entrypoint_accepts_dispatcher_argv(self):
        with mock.patch.object(export, "validate_manifest"):
            with self.assertRaises(SystemExit):
                export.main(["--replace", "--task", "task-a"])

    def test_legacy_rows_must_be_normalized_before_append(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name)
            output = root / "traces.jsonl"
            journal = root / "journal.jsonl"
            legacy = published_row("legacy-task")
            output.write_text(json.dumps(legacy) + "\n")
            with self.assertRaisesRegex(ValueError, "non-canonical row fields"):
                export.validate_export(output)
            with mock.patch.object(migrate_canonical_dataset, "DATA", root), \
                 mock.patch.object(migrate_canonical_dataset, "CONFIG", {
                     "teacher": {"runtime": "pi", "model": "model", "reasoning": "max"},
                     "runtimes": {"pi": {"provider": "provider"}}}):
                migrate_canonical_dataset.migrate(output)
            self.assertEqual(validate_hf_export.validate(output), 1)
            current = json.loads(output.read_text())
            current.update({
                "task": "new-task", "source_trajectory_id": "new-task",
                "source_trajectory_sha256": "b" * 64,
            })
            journal.write_text(json.dumps(current) + "\n")
            export.upsert_journal(output, journal)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertTrue(all(list(item) == export.PUBLISH_KEY_ORDER for item in rows))
            self.assertEqual(export.validate_export(output)["trajectories"], 2)

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

    def test_explicit_replace_recovers_from_malformed_existing_mirror(self):
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name)
            output = root / "traces.jsonl"
            journal = root / "journal.jsonl"
            output.write_text("\nnot-json\n")
            journal.write_text(json.dumps(row("replacement")) + "\n")

            written = export.replace_from_journal(output, journal)

            self.assertEqual(written, 1)
            self.assertEqual(json.loads(output.read_text())["task"], "replacement")


class PublishedDatasetValidation(unittest.TestCase):
    def test_preserved_historical_control_text_is_allowed_until_replacement(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "traces.jsonl"
            item = published_row()
            item["lang"] = None
            item["messages"][0]["content"] = (
                "=== MOONSHINER TASK BOUNDARY ===")
            path.write_text(json.dumps(item) + "\n")
            with mock.patch.object(migrate_canonical_dataset, "DATA",
                                   pathlib.Path(name)), \
                 mock.patch.object(migrate_canonical_dataset, "CONFIG", {
                     "teacher": {"runtime": "pi", "model": "model",
                                 "reasoning": "xhigh"},
                     "runtimes": {"pi": {"provider": "provider"}}}):
                migrate_canonical_dataset.migrate(
                    path, preserve_contaminated=True)
            self.assertEqual(validate_hf_export.validate(path), 1)

            replacement = json.loads(path.read_text())
            replacement.update({
                "lang": "python",
                "verifier": "acceptance-tests+quality-review",
                "teacher_model": "model",
                "provider": "provider",
                "observed_models": ["model"],
                "model_attested": True,
            })
            path.write_text(json.dumps(replacement) + "\n")
            with self.assertRaisesRegex(ValueError, "control text"):
                validate_hf_export.validate(path)

    def test_rejects_email_in_a_canonical_field_value(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "traces.jsonl"
            path.write_text(json.dumps(published_row()) + "\n")
            with mock.patch.object(migrate_canonical_dataset, "DATA",
                                   pathlib.Path(name)), \
                 mock.patch.object(migrate_canonical_dataset, "CONFIG", {
                     "teacher": {"runtime": "pi", "model": "model",
                                 "reasoning": "xhigh"},
                     "runtimes": {"pi": {"provider": "provider"}}}):
                migrate_canonical_dataset.migrate(path)
            item = json.loads(path.read_text())
            item["messages"][0]["content"] = "Contact person@example.com"
            path.write_text(json.dumps(item) + "\n")
            with self.assertRaisesRegex(ValueError, "email address"):
                validate_hf_export.validate(path)

    def test_rejects_legacy_public_schema_until_normalized(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "traces.jsonl"
            path.write_text(json.dumps(published_row()) + "\n")
            with self.assertRaisesRegex(ValueError, "unexpected schema"):
                validate_hf_export.validate(path)

    def test_rejects_an_unrecognized_schema(self):
        with tempfile.TemporaryDirectory() as name:
            path = pathlib.Path(name) / "traces.jsonl"
            item = published_row(); item["invented"] = True
            path.write_text(json.dumps(item) + "\n")
            with self.assertRaisesRegex(ValueError, "unexpected schema"):
                validate_hf_export.validate(path)


if __name__ == "__main__":
    unittest.main()
