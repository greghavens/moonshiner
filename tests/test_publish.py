import tempfile
import unittest
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publish import (_verify_remote_card, build_viewer_shards,
                     configure_viewer_card, inactive_remote_paths, publication_files,
                     privacy_scan_files, publication_format,
                     viewer_dataset_config)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class RemoteCardVerification(unittest.TestCase):
    def test_all_three_publication_modes_are_explicit_and_model_independent(self):
        for mode in ("jsonl", "jsonl-hf-parquet", "parquet-shards"):
            self.assertEqual(publication_format({
                "teacher": {"model": "anything"},
                "publish": {"hf_dataset": "any/dataset", "format": mode}}), mode)
        with self.assertRaisesRegex(ValueError, "publish.format"):
            publication_format({"publish": {"format": "invented"}})

    def test_switching_formats_removes_only_inactive_current_artifacts(self):
        remote = {"README.md", "traces.jsonl", "dataset-manifest.json",
                  "viewer/train-00000.jsonl", "data/train-00000.parquet",
                  "data/train-00001.parquet"}
        self.assertEqual(inactive_remote_paths(
            "parquet-shards", remote, {"data/train-00001.parquet"}),
            ["data/train-00000.parquet", "traces.jsonl",
             "viewer/train-00000.jsonl"])
        self.assertEqual(inactive_remote_paths("jsonl", remote, {"traces.jsonl"}),
                         ["data/train-00000.parquet", "data/train-00001.parquet",
                          "dataset-manifest.json", "viewer/train-00000.jsonl"])

    def test_viewer_shards_preserve_canonical_rows_and_bound_file_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "traces.jsonl"
            rows = [
                {"task": f"task-{index}", "lang": "en", "category": "test",
                 "split": "train", "assistant_step": 1, "assistant_steps": 1,
                 "target_message_index": 1, "n_messages": 2,
                 "messages": [{"role": "user", "content": "x" * 30},
                              {"role": "assistant", "content": str(index)}],
                 "tools": "[]"}
                for index in range(7)
            ]
            original = b"".join(
                (json.dumps(row, separators=(",", ":")) + "\n").encode()
                for row in rows)
            canonical.write_bytes(original)

            shards = build_viewer_shards(canonical, root / "viewer", max_bytes=500)

            self.assertEqual(canonical.read_bytes(), original)
            self.assertGreater(len(shards), 1)
            self.assertTrue(all(path.stat().st_size <= 500 for path in shards))
            rebuilt = [json.loads(line) for path in shards
                       for line in path.read_text().splitlines() if line]
            self.assertEqual(rebuilt, rows)
            self.assertEqual(
                viewer_dataset_config("viewer/train-*.jsonl"),
                {"configs": [{"config_name": "default", "data_files": [
                    {"split": "train", "path": "viewer/train-*.jsonl"}]}]})

    def test_publication_files_exclude_local_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("traces.jsonl", "README.md", "moonshiner-dataset-banner.png",
                         "traces.jsonl.pre-1661"):
                (root / name).write_bytes(b"fixture")
            self.assertEqual({path.name for path in publication_files(root, "jsonl")},
                             {"traces.jsonl", "README.md",
                              "moonshiner-dataset-banner.png"})

    def test_publication_files_fail_when_required_artifact_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("card")
            with self.assertRaisesRegex(ValueError, "required publication artifact"):
                publication_files(root, "jsonl")

    def test_card_selects_viewer_shards_without_hiding_canonical_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            card = root / "README.md"
            card.write_text("---\nlicense: cc-by-4.0\n---\n\n# Dataset\n")
            (root / "traces.jsonl").write_text("canonical\n")
            viewer = root / "viewer"
            viewer.mkdir()
            shard = viewer / "train-00000.jsonl"
            shard.write_text("viewer\n")

            configure_viewer_card(card, "viewer/train-*.jsonl")

            text = card.read_text()
            self.assertIn("path: viewer/train-*.jsonl", text)
            self.assertIn("# Dataset", text)
            self.assertEqual(
                {path.relative_to(root).as_posix() for path in publication_files(root, "jsonl")},
                {"README.md", "traces.jsonl"})

    def test_generated_viewer_shards_do_not_create_a_second_privacy_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "traces.jsonl").write_text("canonical already validated\n")
            (root / "README.md").write_text("dataset card\n")
            viewer = root / "viewer"
            viewer.mkdir()
            (viewer / "train-00000.jsonl").write_text(
                '{"content":"fixture@example.com credential pattern"}\n')

            self.assertEqual(privacy_scan_files(root), [root / "README.md"])

    def test_accepts_exact_live_card(self):
        with tempfile.TemporaryDirectory() as directory:
            card = Path(directory) / "README.md"
            card.write_bytes(b"current card\n")
            with patch("publish.urllib.request.urlopen",
                       return_value=_Response(card.read_bytes())):
                _verify_remote_card("owner/dataset", card, "token")

    def test_rejects_stale_live_card(self):
        with tempfile.TemporaryDirectory() as directory:
            card = Path(directory) / "README.md"
            card.write_bytes(b"current card\n")
            with patch("publish.urllib.request.urlopen",
                       return_value=_Response(b"old card\n")):
                with self.assertRaisesRegex(RuntimeError, "failed remote verification"):
                    _verify_remote_card("owner/dataset", card, "token")


if __name__ == "__main__":
    unittest.main()
