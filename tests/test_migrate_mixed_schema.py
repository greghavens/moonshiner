import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import migrate_canonical_dataset as migration  # noqa: E402

_generation = migration._generation
_legacy_enriched = migration._legacy_enriched


class MixedSchemaMigrationTest(unittest.TestCase):
    def test_prior_canonical_revision_is_normalized_as_current(self):
        row = {
            "task": "example",
            "source_trajectory_id": "example",
            "messages": [{"role": "user", "content": "Do the task"}],
            "tools": "[]",
            # Deliberately omit columns added by the current published schema.
            "assistant_step": 0,
        }
        self.assertEqual(_generation(row), "current")

    def test_whole_session_source_record_is_not_mistaken_for_published_row(self):
        row = {
            "task": "example",
            "messages": [{"role": "user", "content": "Do the task"}],
            "tools": [],
            "teacher_runtime": "pi",
        }
        self.assertIsNone(_generation(row))

    def test_pre_source_identity_enriched_row_preserves_trace(self):
        row = {
            "task": "example",
            "source_trajectory_sha256": "a" * 64,
            "lang": "en",
            "category": "Building",
            "domain": "coding",
            "verifier": "published-baseline",
            "split": "train",
            "provider": "zai",
            "teacher_model": "glm-5.2",
            "observed_models": ["glm-5.2"],
            "reasoning_effort": "xhigh",
            "runtime": "pi-zai",
            "trace_format": "pi-jsonl",
            "tools_used": ["read"],
            "derivation": "cumulative-next-assistant-v1",
            "assistant_step": 0,
            "assistant_steps": 1,
            "target_message_index": 1,
            "n_messages": 2,
            "messages": [
                {"role": "user", "content": "Inspect it"},
                {"role": "assistant", "reasoning": "I should inspect it",
                 "tool_calls": [{"id": "1", "type": "function",
                                 "function": {"name": "read",
                                              "arguments": {"path": "a.txt"}}}]},
            ],
            "tools": [],
        }

        self.assertEqual(_generation(row), "enriched")
        normalized = _legacy_enriched(row, 2)
        self.assertEqual(normalized["source_trajectory_id"], "example")
        self.assertEqual(normalized["teacher_runtime"], "pi-zai")
        self.assertEqual(normalized["original_n_messages"], 2)
        self.assertEqual(
            normalized["messages"][1]["reasoning_content"],
            "I should inspect it")
        self.assertEqual(
            normalized["messages"][1]["tool_calls"][0]["function"]["arguments"],
            '{"path":"a.txt"}')

    def test_current_row_gets_missing_project_provenance_not_attestation(self):
        row = {
            key: None for key in migration.PUBLISH_KEY_ORDER
        }
        row.update({
            "task": "example",
            "source_trajectory_id": "example",
            "source_trajectory_sha256": "a" * 64,
            "split": "train",
            "assistant_step": 0,
            "assistant_steps": 1,
            "target_message_index": 1,
            "original_n_messages": 2,
            "messages": [{"role": "assistant", "content": "done"}],
            "tools": "[]",
        })
        config = {
            "teacher": {
                "runtime": "pi-openrouter",
                "model": "moonshotai/kimi-k3",
                "reasoning": "xhigh",
            },
            "runtimes": {
                "pi-openrouter": {"provider": "openrouter"},
            },
        }

        with mock.patch.object(migration, "CONFIG", config):
            normalized = migration._current_canonical(row)

        self.assertEqual(normalized["teacher_runtime"], "pi-openrouter")
        self.assertEqual(normalized["teacher_model"], "moonshotai/kimi-k3")
        self.assertEqual(normalized["reasoning_effort"], "xhigh")
        self.assertEqual(normalized["provider"], "openrouter")
        self.assertEqual(normalized["observed_models"], ["moonshotai/kimi-k3"])
        self.assertFalse(normalized["model_attested"])
