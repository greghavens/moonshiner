"""Dataset assembly: secret redaction, path scrub, token estimate, next-step
expansion into cumulative prefixes. All transforms are pure and model-free."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import build_dataset as bd  # noqa: E402
import expand_next_steps as ex  # noqa: E402


class Redaction(unittest.TestCase):
    def test_internal_moonshiner_prompt_content_is_rejected(self):
        session = [
            {"role": "user",
             "content": "=== MOONSHINER TASK BOUNDARY ===\ndo the task"},
            {"role": "assistant", "content": "done"},
        ]
        self.assertTrue(bd.has_internal_content(session))

    def test_redact_secret_matches_counts_and_strips(self):
        redacted, count = bd.redact_secret_matches(
            {"log": "key AKIAIOSFODNN7EXAMPLE end"})
        self.assertGreaterEqual(count, 1)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", json.dumps(redacted))
        self.assertIn("[REDACTED_SECRET]", json.dumps(redacted))

    def test_scrub_session_strips_runtime_path(self):
        session = [{"role": "user",
                    "content": "at /var/tmp/moonshiner-pi-runtime/run-x/y"}]
        out = bd.scrub_session(session)
        self.assertNotIn("moonshiner-pi-runtime", json.dumps(out))
        self.assertEqual(out[0]["role"], "user")

    def test_a_quoted_credential_survives_redaction_as_json(self):
        # A credential pattern ends at the first quote. Scrubbed over the
        # serialized session, the quote it stopped at was an escaped one, so
        # the redaction ate the backslash and left a quote that closed the
        # string early — an unreadable document that stopped the whole build.
        session = [{"role": "user",
                    "content": 'Token string // sent as '
                               '"Authorization: Bearer abc123def456xyz"'}]
        out = bd.scrub_session(session)
        content = out[0]["content"]
        self.assertNotIn("abc123def456xyz", content)
        self.assertIn("[REDACTED_SECRET]", content)
        self.assertEqual(json.loads(json.dumps(out)), out)
        self.assertTrue(content.endswith('"'))

    def test_json_escaping_does_not_invent_a_credential(self):
        # `seed[\"` is six characters only because the serialization escaped
        # the quote, and six characters is what the password pattern wants.
        # Scrubbing the real string redacts nothing, which is correct: this
        # is code, not a credential.
        session = [{"role": "assistant",
                    "content": 'self.password = seed["credentials"]["password"]'}]
        self.assertEqual(bd.scrub_session(session), session)

    def test_scrubbing_leaves_message_whitespace_alone(self):
        # Message content is the trace, not a captured block to tidy: the
        # trailing newline an assistant wrote is part of what it wrote.
        session = [{"role": "assistant", "content": "done\n"}]
        self.assertEqual(bd.scrub_session(session)[0]["content"], "done\n")


class DeclaredLanguage(unittest.TestCase):
    def test_the_source_manifest_spelling_counts_as_declared(self):
        # `language` is the key the source manifests use and `lang` is the one
        # Moonshiner reads; import_seeds maps one to the other, so a seed that
        # arrived with the source spelling has declared its language already.
        self.assertEqual(bd.seed_language({"language": "powershell"}),
                         "powershell")
        self.assertEqual(bd.seed_language({"lang": "go", "language": "java"}),
                         "go")

    def test_a_behavior_seed_without_one_is_english(self):
        self.assertEqual(bd.seed_language({"kind": "tool_behavior"}), "English")

    def test_a_language_among_the_seeds_own_tags_counts(self):
        self.assertEqual(
            bd.seed_language({"training_tags": ["vcf-9-0", "java", "spec"]}),
            "java")
        self.assertIsNone(
            bd.seed_language({"training_tags": ["multi-turn", "planning"]}))

    def test_files_are_never_read_for_a_language(self):
        # An instruction-following task ships a Python verifier and is not a
        # Python task. Guessing from what is on disk would publish it
        # mislabelled, which is worse than publishing nothing.
        self.assertIsNone(bd.seed_language(
            {"verify_cmd": "python3 -B .protected/verify.py",
             "test_files": [".protected/verify.py"]}))

    def _build(self, seed):
        """Run build_row over a trace that is fine, so only the seed is judged."""
        turns = [{"role": "user", "content": "do it"},
                 {"role": "assistant", "content": "done"}]
        with tempfile.TemporaryDirectory() as directory:
            raw = pathlib.Path(directory) / "raw.jsonl"
            raw.write_text("{}\n")
            with mock.patch.object(bd, "raw_trace_path", return_value=raw), \
                 mock.patch.object(bd, "parse_trace", return_value=(turns, {})), \
                 mock.patch.object(bd, "_review", return_value={}):
                return bd.build_row(seed, {"trace_format": "codex-jsonl-v1"})

    def test_a_row_the_contract_would_refuse_is_never_built(self):
        # The export contract rejects an empty lang or category by raising, and
        # one such row stopped `publish` outright — systemd restarted the queue
        # into the same batch and nothing at all reached the dataset. The build
        # refuses first, with a reason, so every other trace still ships.
        row, reason = self._build({"id": "undeclared", "category": "Building"})
        self.assertIsNone(row)
        self.assertIn("lang", reason)
        row, reason = self._build({"id": "undeclared", "lang": "go"})
        self.assertIsNone(row)
        self.assertIn("category", reason)

    def test_a_declared_language_reaches_the_row(self):
        row, reason = self._build({"id": "declared", "language": "powershell",
                                   "category": "project-integration"})
        self.assertIsNone(reason)
        self.assertEqual(row["meta"]["lang"], "powershell")


class BuildLoopResilience(unittest.TestCase):
    def test_one_unbuildable_trace_does_not_stop_the_build(self):
        # Every other unusable row is a reason string the build walks past. A
        # raise instead took the build down, and with it the publish queue,
        # which systemd restarted straight back into the same trace: one bad
        # row and nothing at all reached the dataset.
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            meta = root / "meta"
            meta.mkdir()
            for task in ("bad-task", "good-task"):
                (meta / f"{task}.json").write_text(json.dumps({"id": task}))

            def build_row(seed, info, traces_root=None):
                if info["id"] == "bad-task":
                    raise json.JSONDecodeError("Expecting ',' delimiter", "{}", 1)
                return {"messages": [{"role": "assistant", "content": "hi"}],
                        "meta": {"task": info["id"]}}, None

            with mock.patch.object(bd, "META", meta), \
                 mock.patch.object(bd, "DATA", root / "data"), \
                 mock.patch.object(bd, "load_seeds", return_value=[
                     {"id": "bad-task"}, {"id": "good-task"}]), \
                 mock.patch.object(bd, "screening_acceptance",
                                   return_value=(True, None)), \
                 mock.patch.object(bd, "build_row", side_effect=build_row), \
                 mock.patch.object(bd, "accepted_author_rows",
                                   return_value=([], [])), \
                 mock.patch.object(sys, "argv", ["build_dataset.py", "--quiet"]), \
                 mock.patch("builtins.print") as printed:
                bd.main()
            written = [json.loads(line)
                       for path in sorted((root / "data" / "full").glob("*.jsonl"))
                       for line in path.read_text().splitlines() if line.strip()]
            self.assertEqual([row["meta"]["task"] for row in written],
                             ["good-task"])
        reported = " ".join(str(call) for call in printed.call_args_list)
        self.assertIn("bad-task", reported)
        self.assertIn("JSONDecodeError", reported)


class Tokens(unittest.TestCase):
    def test_empty_message_has_floor(self):
        self.assertGreaterEqual(bd.est_tokens({"content": ""}), 8)

    def test_longer_content_costs_more(self):
        base = bd.est_tokens({"content": ""})
        self.assertGreater(bd.est_tokens({"content": "x" * 330}), base)

    def test_tool_calls_add_cost(self):
        plain = bd.est_tokens({"content": "hi"})
        with_call = bd.est_tokens({"content": "hi", "tool_calls": [
            {"function": {"name": "bash", "arguments": {"command": "ls"}}}]})
        self.assertGreater(with_call, plain)


class Expand(unittest.TestCase):
    RECORD = {
        "messages": [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "step 1"},
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": "step 2"},
        ],
        "tools": [],
        "meta": {"task": "demo"},
    }

    def test_one_prefix_per_assistant_message(self):
        out = ex.expand_record(self.RECORD)
        self.assertEqual(len(out), 2)
        self.assertEqual(len(out[0]["messages"]), 2)
        self.assertEqual(len(out[1]["messages"]), 4)
        self.assertEqual(out[0]["messages"][-1]["role"], "assistant")
        self.assertEqual(out[1]["messages"][-1]["role"], "assistant")

    def test_step_metadata(self):
        out = ex.expand_record(self.RECORD)
        self.assertEqual(out[0]["meta"]["assistant_step"], 1)
        self.assertEqual(out[1]["meta"]["assistant_step"], 2)
        self.assertEqual(out[1]["meta"]["assistant_steps"], 2)
        self.assertEqual(out[0]["meta"]["derivation"], out[1]["meta"]["derivation"])

    def test_no_assistant_message_raises(self):
        with self.assertRaises(ValueError):
            ex.expand_record({"messages": [{"role": "user", "content": "x"}]})

    def test_source_fingerprint_is_stable_and_distinct(self):
        other = json.loads(json.dumps(self.RECORD))
        other["meta"]["task"] = "different"
        self.assertEqual(ex.source_fingerprint(self.RECORD),
                         ex.source_fingerprint(json.loads(json.dumps(self.RECORD))))
        self.assertNotEqual(ex.source_fingerprint(self.RECORD),
                            ex.source_fingerprint(other))


class BehavioralTags(unittest.TestCase):
    def test_parallel_multiturn_and_iterative_tags_are_derived(self):
        turns = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "read", "arguments": {}}},
                {"function": {"name": "grep", "arguments": {}}}]},
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ]
        tags = bd.training_tags({"training_tags": ["debugging"]}, turns,
                                {"feedback_used": True})
        self.assertTrue({"debugging", "tool-use", "parallel-tool-calls",
                         "multi-turn", "iterative-repair", "tool:read",
                         "tool:grep"}.issubset(tags))

    def test_observed_reasoning_and_format_tags(self):
        turns = [{"role": "assistant", "content": "", "reasoning_content": "plan " * 220,
                  "tool_calls": [{"function": {"name": "validate_order", "arguments": {}}}]},
                 {"role": "tool", "content": "ok"},
                 {"role": "assistant", "content": '{"status":"done"}'}]
        tags = bd.training_tags({}, turns, {})
        self.assertTrue({"reasoning:planning", "reasoning:extended",
                         "reasoning:verification", "format:strict-json",
                         "interaction:multi-turn"}.issubset(tags))

    def test_direct_response_is_observed(self):
        tags = bd.training_tags({}, [{"role": "assistant", "content": "hello"}], {})
        self.assertIn("response:direct", tags)


if __name__ == "__main__":
    unittest.main()
