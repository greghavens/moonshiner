import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import migrate_canonical_dataset as migration  # noqa: E402
from canonical_dataset import normalize_messages  # noqa: E402
from generate_traces import with_action_boundary  # noqa: E402
from privacy import findings  # noqa: E402

_generation = migration._generation
_legacy_enriched = migration._legacy_enriched


class MixedSchemaMigrationTest(unittest.TestCase):
    def test_current_historical_row_is_scrubbed_during_migration(self):
        messages = normalize_messages([
            {"role": "user", "content": "Use api_key=supersecretvalue"},
            {"role": "assistant", "content": "Contact person@example.com"},
        ])
        row = {
            "task": "credential-fixture",
            "source_trajectory_id": "credential-fixture",
            "source_trajectory_sha256": "a" * 64,
            "lang": "en",
            "category": "Building",
            "domain": "coding",
            "verifier": "published-baseline",
            "split": "train",
            "teacher_runtime": "historical",
            "teacher_model": None,
            "reasoning_effort": None,
            "provider": "historical",
            "observed_models": [],
            "model_attested": False,
            "trace_format": "historical-canonical",
            "tools_used": [],
            "derivation": "cumulative-next-assistant-v1",
            "assistant_step": 1,
            "assistant_steps": 1,
            "target_message_index": 1,
            "original_n_messages": 2,
            "n_messages": 2,
            "messages": messages,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "traces.jsonl"
            path.write_text(json.dumps(row) + "\n")
            path.with_suffix(
                path.suffix + ".canonical.pending").write_text("stale")
            self.assertEqual(migration.migrate(path), (1, 1))
            migrated = json.loads(path.read_text())
        self.assertNotIn("supersecretvalue", json.dumps(migrated))
        self.assertIn("[REDACTED_SECRET]", json.dumps(migrated))
        self.assertEqual(findings(json.dumps(migrated)), [])

    def test_migration_advances_the_canonical_traces_append_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "hf-publish" / "traces.jsonl"
            path.parent.mkdir()
            path.write_text("{}\n")
            marker_name = migration.hashlib.sha256(
                b"owner/dataset:traces.jsonl").hexdigest()[:16]
            marker = root / "hf-sync" / f"{marker_name}.json"
            marker.parent.mkdir()
            marker.write_text("{}")
            with mock.patch.object(migration, "DATA", root), \
                 mock.patch.object(migration, "CONFIG", {
                     "publish": {
                         "hf_dataset": "owner/dataset",
                         "filename": "legacy-name.jsonl",
                     },
                 }):
                migration._advance_baseline(path, {"rows": 1})
            state = json.loads(marker.read_text())
            expected_sha256 = migration.sha256(path)
        self.assertEqual(state["bootstrap_sha256"], expected_sha256)
        self.assertEqual(state["bootstrap_rows"], 1)

    def test_final_serialized_row_is_scrubbed_at_validator_boundary(self):
        row = {"messages": [{
            "content": r'C:\work person@example.com \"quoted\"',
        }]}
        scrubbed = migration._privacy_scrub_row(row)
        self.assertEqual(findings(json.dumps(scrubbed)), [])
        self.assertEqual(
            scrubbed["messages"][0]["content"],
            r'C:\work [REDACTED_EMAIL] \"quoted\"')

    def test_future_trace_prompt_is_exactly_the_authored_seed_prompt(self):
        prompt = "\nUse the available tools to complete this task.\n"
        self.assertEqual(
            with_action_boundary(
                prompt,
                {"research": {"required": True}},
                feedback="The prior attempt failed."),
            prompt)

    def test_historical_control_wrapper_marks_entire_trajectory_for_removal(self):
        prompt = "Use the available tools to complete this task."
        wrapped = (
            "TRACE EXECUTION INTEGRITY REMINDER: This task requires consulting "
            "official documentation. Use WebSearch and WebFetch to read the "
            "official source before the first source-code mutation, and keep "
            "every action inside the provided task workspace.\n\n"
            "=== MOONSHINER TASK BOUNDARY ===\n\n"
            f"{prompt}\n\n"
            "PRIOR ATTEMPT FEEDBACK (address before finishing):\nfailed")
        row = {"messages": [
            {"role": "user", "content": wrapped},
            {"role": "assistant", "content": "Done",
             "reasoning": "Native reasoning"},
        ]}
        self.assertTrue(migration._contains_internal_control(row))
        messages = normalize_messages(row["messages"])
        self.assertEqual(messages[0]["content"], wrapped)

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
