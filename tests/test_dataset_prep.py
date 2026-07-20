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

    def test_dimension_weights_require_token_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory) / "rows.jsonl"
            source.write_text(json.dumps({"category": "tools", "messages": [
                {"role": "user", "content": "x"},
                {"role": "assistant", "content": "y"}]}) + "\n")
            with self.assertRaisesRegex(ValueError, "require --target-tokens"):
                dataset_prep.compose([str(source)], [], pathlib.Path(directory) / "out.jsonl",
                                     42, category_weights=["tools=2"])

    def test_dry_run_analyzes_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, output = root / "rows.jsonl", root / "out.jsonl"
            source.write_text(json.dumps({"category": "tools", "tags": ["parallel"],
                "messages": [{"role": "user", "content": "x"},
                             {"role": "assistant", "content": "y"}]}) + "\n")
            manifest = dataset_prep.compose([str(source)], [], output, 42,
                                             target_tokens=100, dry_run=True)
            self.assertFalse(output.exists())
            self.assertEqual(manifest["analysis"]["summary"]["trajectories"], 1)
            self.assertIn("tools", manifest["analysis"]["mix"]["category"])


class Readiness(unittest.TestCase):
    def test_reports_quality_risks_without_blocking(self):
        row = {"messages": [
            {"role": "user", "content": "same prompt"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "a", "function": {"name": "lookup", "arguments": "{}"}}]},
            {"role": "tool", "content": "result", "tool_call_id": "wrong"},
            {"role": "assistant", "content": "done"}],
            "tools": [], "meta": {"source": "x", "source_row": 0,
                                      "category": "tools", "tags": []}}
        report = dataset_prep.readiness_rows([row, json.loads(json.dumps(row))],
                                               dataset_prep.TokenCounter(),
                                               context_lengths=[1], packing=True)
        self.assertTrue(report["advisory_only"])
        self.assertEqual(report["issues"]["exact_duplicate_prompts"], 1)
        self.assertEqual(report["issues"]["malformed_tool_sequences"], 2)
        self.assertEqual(report["truncation"]["1"]["rows"], 2)
        self.assertTrue(report["advisories"])


class Reproducibility(unittest.TestCase):
    def test_hf_source_requires_revision_before_import(self):
        with self.assertRaisesRegex(ValueError, "pin a revision"):
            dataset_prep.load_source("hf:owner/dataset")

    def test_assistant_tool_call_may_have_null_content(self):
        row = dataset_prep._normalize({"messages": [
            {"role": "user", "content": "look it up"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "x", "function": {"name": "lookup", "arguments": "{}"}}]}]},
            "local:test", 0)
        self.assertEqual(row["messages"][1]["content"], "")


if __name__ == "__main__":
    unittest.main()
