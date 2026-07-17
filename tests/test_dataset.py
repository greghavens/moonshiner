"""Dataset assembly: secret redaction, path scrub, token estimate, next-step
expansion into cumulative prefixes. All transforms are pure and model-free."""
import json
import pathlib
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import build_dataset as bd  # noqa: E402
import expand_next_steps as ex  # noqa: E402


class Redaction(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
