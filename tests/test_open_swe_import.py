"""Executable contracts for the approved NVIDIA Open-SWE manifest import."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import unittest

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import import_seeds  # noqa: E402


MANIFEST = (ROOT / "corpus-manifests" /
            "nvidia-open-swe-qwen35-nonthinking-10000-v1.json")
EXPECTED_DIGEST = "cc6025dfe583cffcc1e3909a2be611997c2b25db4036b143926bed2ad4fcecf4"
TEST_TMP = pathlib.Path(os.environ.get(
    "TMPDIR", ROOT / ".moonshiner" / "test-tmp")).resolve()
TEST_TMP.mkdir(parents=True, exist_ok=True)


class ManifestIdentity(unittest.TestCase):
    def test_exact_count_unique_sorted_ids_and_digest(self):
        manifest = json.loads(MANIFEST.read_text())
        ids = [task["instance_id"] for task in manifest["tasks"]]
        self.assertEqual(manifest["manifest_id"],
                         "nvidia-open-swe-qwen35-nonthinking-10000-v1")
        self.assertEqual(len(ids), 10_000)
        self.assertEqual(len(set(ids)), 10_000)
        self.assertEqual(ids, sorted(ids))
        digest = hashlib.sha256(("\n".join(ids) + "\n").encode()).hexdigest()
        self.assertEqual(digest, EXPECTED_DIGEST)
        self.assertEqual(manifest["selected_instance_ids_sha256"], digest)
        self.assertEqual(
            manifest["sources"]["trajectory_dataset"]["revision"],
            "ad4805a5aa7de70d99cab0bb8f99b15304c76de0")
        self.assertEqual(
            manifest["sources"]["task_dataset"]["revision"],
            "475dd5e8703bb5fb22dd3c60b5d038b019eba1e0")


class SourceResolution(unittest.TestCase):
    def setUp(self):
        self.root = TEST_TMP / f"open-swe-import-{os.getpid()}"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def row(instance_id: str, *, forbidden: str) -> dict:
        return {
            "instance_id": instance_id,
            "repo": "owner/repository",
            "base_commit": "a" * 40,
            "problem_statement": "Keep CRLF exactly.\r\nUnicode: \u2028\n",
            "image_name": "localhost/open-swe-contract:latest",
            "language": "python",
            "license": "MIT",
            "test_patch": "diff --git a/test_task.py b/test_task.py\n",
            "FAIL_TO_PASS": ["test_fix"],
            "PASS_TO_PASS": ["test_regression"],
            "install_config": {
                "base_image_name": "python_3_12",
                "docker_specs": None,
                "install": ["python3 --version"],
                "log_parser": "parse_log_pytest",
                "test_cmd": "python3 -m unittest -q",
            },
            "patch": forbidden,
            "pr_description": forbidden,
            "interface": forbidden,
            "meta": {"resolved": forbidden, "model_patch": forbidden},
        }

    def test_only_selected_rows_resolve_exactly_once(self):
        parquet = self.root / "source.parquet"
        pq.write_table(pa.Table.from_pylist([
            self.row("selected-a", forbidden="FORBIDDEN-A"),
            self.row("outside", forbidden="FORBIDDEN-OUTSIDE"),
            self.row("selected-b", forbidden="FORBIDDEN-B"),
        ]), parquet)
        rows = import_seeds.resolve_source_rows(
            ["selected-a", "selected-b"], [parquet])
        self.assertEqual(list(rows), ["selected-a", "selected-b"])
        self.assertNotIn("outside", rows)

    def test_duplicate_source_id_fails_closed(self):
        parquet = self.root / "duplicates.parquet"
        pq.write_table(pa.Table.from_pylist([
            self.row("selected-a", forbidden="one"),
            self.row("selected-a", forbidden="two"),
        ]), parquet)
        with self.assertRaisesRegex(ValueError, "exactly once"):
            import_seeds.resolve_source_rows(["selected-a"], [parquet])

    def test_canonical_mapping_excludes_every_answer_derived_field(self):
        manifest = json.loads(MANIFEST.read_text())
        selection = {
            "instance_id": "selected-a", "category": "bug-fix",
            "language": "python", "license": "MIT",
            "repo": "owner/repository",
        }
        forbidden = "DO-NOT-IMPORT-THIS-GOLD-ANSWER"
        task, test_patch = import_seeds.canonical_manifest_seed(
            manifest, selection, self.row("selected-a", forbidden=forbidden))
        self.assertEqual(task["id"], "selected-a")
        self.assertEqual(task["prompt"].encode(),
                         self.row("selected-a", forbidden=forbidden)[
                             "problem_statement"].encode())
        self.assertEqual(task["program"], "NVIDIA Open-SWE 10K")
        self.assertEqual(test_patch,
                         self.row("selected-a", forbidden=forbidden)["test_patch"])
        serialized = json.dumps(task, ensure_ascii=False)
        self.assertNotIn(forbidden, serialized)
        for field in ("patch", "pr_description", "interface", "meta",
                      "trajectory", "resolved", "reward", "model_patch",
                      "reference_patch"):
            self.assertNotIn(field, task)
        self.assertNotIn("required_harness_capabilities", task)
        self.assertNotIn("preferred_harness_capabilities", task)

    def test_only_the_two_observed_language_aliases_are_accepted(self):
        manifest = json.loads(MANIFEST.read_text())
        for source, declared in (("ts", "typescript"), ("js", "javascript")):
            selection = {
                "instance_id": f"selected-{source}", "category": "bug-fix",
                "language": declared, "license": "MIT",
                "repo": "owner/repository",
            }
            row = self.row(selection["instance_id"], forbidden="answer")
            row["language"] = source
            task, _ = import_seeds.canonical_manifest_seed(
                manifest, selection, row)
            self.assertEqual(task["lang"], source)

        selection["language"] = "typescript"
        row["language"] = "javascript"
        with self.assertRaisesRegex(ValueError, "language"):
            import_seeds.canonical_manifest_seed(manifest, selection, row)

    def test_import_column_projection_never_reads_answer_fields(self):
        forbidden = {"patch", "pr_description", "interface", "meta",
                     "resolved", "trajectory", "reward"}
        self.assertFalse(forbidden & set(import_seeds.MANIFEST_SOURCE_COLUMNS))


if __name__ == "__main__":
    unittest.main()
