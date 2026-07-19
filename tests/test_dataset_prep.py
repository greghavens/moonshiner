"""Dataset composition selection and privacy regression tests."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dataset_prep


class Selection(unittest.TestCase):
    def test_exclusions_win_across_name_category_and_tags(self):
        row = {"meta": {"name": "public-1", "category": "code-python",
                        "tags": ["verified", "sensitive"]}}
        filters = (["public-*"], [], ["code-*"], [], ["verified"], ["sensitive"])
        self.assertFalse(dataset_prep._selected(row, filters))

    def test_compose_records_filters_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "rows.jsonl"
            row = {"name": "task-a", "category": "code-python", "tags": ["verified"],
                   "messages": [{"role": "user", "content": "x"},
                                {"role": "assistant", "content": "y"}]}
            source.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
            filters = (["task-*"], [], ["code-*"], [], ["verified"], [])
            output = root / "out.jsonl"
            manifest = dataset_prep.compose([str(source)], [], output, 42, filters)
            self.assertEqual(manifest["rows"], 1)
            self.assertEqual(manifest["filters"]["include_category"], ["code-*"])


class Reproducibility(unittest.TestCase):
    def test_hf_source_requires_revision_before_import(self):
        with self.assertRaisesRegex(ValueError, "pin a revision"):
            dataset_prep.load_source("hf:owner/dataset")


if __name__ == "__main__":
    unittest.main()
