"""Canonical HF export compatibility."""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import export_hf  # noqa: E402


class ImportedCanonicalCompatibility(unittest.TestCase):
    def test_missing_tool_schema_field_exports_as_empty_list(self):
        record = {
            "meta": {"task": "imported", "trace_format": "native"},
            "messages": [{"role": "user", "content": "hello"}],
        }
        row = export_hf.build_row(record, "train")
        self.assertEqual(json.loads(row["tools"]), [])


if __name__ == "__main__":
    unittest.main()
