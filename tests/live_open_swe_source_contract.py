"""Live, model-free verification of the complete pinned Open-SWE task source."""
from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import import_seeds  # noqa: E402


MANIFEST = (ROOT / "corpus-manifests" /
            "nvidia-open-swe-qwen35-nonthinking-10000-v1.json")


class CompletePinnedSource(unittest.TestCase):
    def test_every_selected_id_resolves_exactly_once(self):
        manifest = import_seeds.load_selection_manifest(MANIFEST)
        parquet = import_seeds.download_manifest_source(manifest)
        selected = [task["instance_id"] for task in manifest["tasks"]]
        rows = import_seeds.resolve_source_rows(selected, [parquet])
        self.assertEqual(list(rows), selected)
        self.assertEqual(len(rows), 10_000)
        for selection in manifest["tasks"]:
            row = rows[selection["instance_id"]]
            self.assertEqual(row["instance_id"], selection["instance_id"])
            self.assertEqual(row["repo"], selection["repo"])
            declared_language = {"ts": "typescript", "js": "javascript"}.get(
                row["language"], row["language"])
            self.assertEqual(declared_language, selection["language"])
            self.assertEqual(row["license"], selection["license"])
            self.assertIsInstance(row["problem_statement"], str)
            task, test_patch = import_seeds.canonical_manifest_seed(
                manifest, selection, row)
            self.assertEqual(task["id"], selection["instance_id"])
            self.assertEqual(task["prompt"].encode(),
                             row["problem_statement"].encode())
            self.assertEqual(test_patch, row["test_patch"])
        result = import_seeds.import_manifest(
            MANIFEST, dry_run=True, source_parquet=parquet)
        self.assertEqual(result["selected"], 10_000)
        self.assertEqual(result["imported"], 10_000)
        self.assertEqual(result["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
