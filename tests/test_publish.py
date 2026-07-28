import tempfile
import unittest
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publish import (_verify_remote_card, _verify_trusted_prefix, build_viewer_shards,
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
    def test_task_keyed_replacements_do_not_require_byte_prefix_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            traces = Path(directory) / "traces.jsonl"
            traces.write_bytes(b"replacement\n")
            state = {
                "bootstrap_size": len(b"original\n"),
                "bootstrap_sha256": __import__("hashlib").sha256(
                    b"original\n").hexdigest(),
            }
            _verify_trusted_prefix(traces, state, allow_task_replacements=True)
            with self.assertRaisesRegex(RuntimeError, "prefix differs"):
                _verify_trusted_prefix(
                    traces, state, allow_task_replacements=False)

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
            "parquet-shards", remote,
            {"traces.jsonl", "data/train-00001.parquet"}),
            ["data/train-00000.parquet", "viewer/train-00000.jsonl"])
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

    def test_parquet_mode_can_omit_jsonl_and_remove_remote_monolith(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("card")
            (root / "traces.jsonl").write_text("{}\n")
            shard = root / "data" / "train-00000.parquet"
            shard.parent.mkdir(); shard.write_bytes(b"parquet")
            (root / "dataset-manifest.json").write_text(json.dumps({
                "active_shards": ["data/train-00000.parquet"]}))
            self.assertEqual(
                {path.relative_to(root).as_posix()
                 for path in publication_files(root, "parquet-shards",
                                                include_jsonl=False)},
                {"README.md", "dataset-manifest.json",
                 "data/train-00000.parquet"})
            remote = {"README.md", "traces.jsonl", "dataset-manifest.json",
                      "data/train-00000.parquet"}
            self.assertEqual(inactive_remote_paths(
                "parquet-shards", remote,
                {"README.md", "dataset-manifest.json", "data/train-00000.parquet"}),
                ["traces.jsonl"])

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


class PublishingNeverShrinksTheCorpus(unittest.TestCase):
    """The published dataset is the product; a local mirror is not.

    Fable's mirror was destroyed and rebuilt from nothing. Publishing it
    replaced 11,487 rows on the Hub with the 239 the rebuilt mirror happened
    to hold, deleting the Parquet shards that held the rest.
    """

    def test_publishing_fewer_rows_than_are_published_is_a_shrink(self):
        from publish import shrinks_the_dataset
        self.assertTrue(shrinks_the_dataset(239, 11487))

    def test_growing_or_holding_steady_is_not(self):
        from publish import shrinks_the_dataset
        self.assertFalse(shrinks_the_dataset(11726, 11487))
        self.assertFalse(shrinks_the_dataset(11487, 11487))

    def test_replacing_a_task_is_never_blocked(self):
        """Retracing a poisoned trajectory usually yields fewer rows.

        That is the retrace working, not a corpus being lost, and blocking it
        would make the poisoned traces unreplaceable.
        """
        from publish import shrinks_the_dataset
        self.assertFalse(shrinks_the_dataset(11475, 11487, replacing_tasks=True))
        self.assertFalse(shrinks_the_dataset(1, 11487, replacing_tasks=True))
        self.assertTrue(shrinks_the_dataset(11475, 11487, replacing_tasks=False),
                        "the same shrink without --task is still refused")

    def test_the_first_publication_of_a_dataset_is_not_a_shrink(self):
        from publish import shrinks_the_dataset
        self.assertFalse(shrinks_the_dataset(50, 0))

    def test_the_row_count_comes_from_the_manifest_then_the_jsonl(self):
        import json as _json, pathlib, tempfile
        from publish import publishing_row_count
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            (directory / "traces.jsonl").write_text('{"a":1}\n{"a":2}\n')
            self.assertEqual(2, publishing_row_count(directory))
            (directory / "dataset-manifest.json").write_text(
                _json.dumps({"row_count": 11726}))
            self.assertEqual(11726, publishing_row_count(directory),
                             "the manifest is authoritative when present")

    def test_the_guard_runs_before_the_commit_and_can_be_overridden(self):
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "publish.py").read_text()
        self.assertLess(source.index("shrinks_the_dataset(publishing, published,"),
                        source.index("api.create_commit("),
                        "the check must precede the commit that would delete rows")
        self.assertIn("--allow-shrink", source)
