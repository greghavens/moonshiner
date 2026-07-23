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
            "meta": {"task": "imported"},
            "teacher_runtime": "pi",
            "teacher_model": "moonshotai/kimi-k3",
            "provider": "openrouter",
            "reasoning_effort": "max",
            "model_attested": True,
            "observed_models": ["moonshotai/kimi-k3"],
            "trace_format": "pi-coding-agent-json-v3",
            "messages": [{"role": "user", "content": "hello"}],
        }
        row = export_hf.build_row(record, "train")
        self.assertEqual(json.loads(row["tools"]), [])
        for key in ("teacher_runtime", "teacher_model", "provider",
                    "reasoning_effort", "model_attested", "observed_models",
                    "trace_format"):
            self.assertEqual(row[key], record[key])


if __name__ == "__main__":
    unittest.main()
