"""Canonical HF export compatibility."""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import export_hf  # noqa: E402
import export_hf_next_steps  # noqa: E402


class ImportedCanonicalCompatibility(unittest.TestCase):
    def test_export_does_not_invent_a_tool_schema_field(self):
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
        self.assertNotIn("tools", row)
        for key in ("teacher_runtime", "teacher_model", "provider",
                    "reasoning_effort", "model_attested", "observed_models",
                    "trace_format"):
            self.assertEqual(row[key], record[key])

        record["meta"].update({
            "source_trajectory_id": "imported:1", "source_sha256": "a" * 64,
            "derivation": "cumulative-next-assistant-v1", "assistant_step": 1,
            "assistant_steps": 1, "target_message_index": 1,
            "original_n_messages": 1,
        })
        next_row = export_hf_next_steps.build_row(record, "train")
        self.assertNotIn("tools", next_row)
        for key in ("teacher_runtime", "teacher_model", "provider",
                    "reasoning_effort", "model_attested", "observed_models",
                    "trace_format"):
            self.assertEqual(next_row[key], record[key])


if __name__ == "__main__":
    unittest.main()
