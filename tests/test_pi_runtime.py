"""Pi runtime provisioning: the models.json the sandboxed agent runs on.

Offline — exercises only config generation, no pi process and no network.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.credential_proxy import DUMMY_TOKEN  # noqa: E402
from runtimes.pi import PiRuntime, compact_events_file  # noqa: E402


def _provider_entry(runtime_config: dict) -> dict:
    config = {"runtimes": {"pi": dict(runtime_config, provider="openrouter")}}
    runtime = PiRuntime(config, {"model": "moonshotai/kimi-k3"})
    with tempfile.TemporaryDirectory() as tmp:
        runtime._prepare_runtime(pathlib.Path(tmp), "http://127.0.0.1:1")
        models = json.loads(
            (pathlib.Path(tmp) / "config" / "models.json").read_text())
    return models["providers"]["openrouter"]


class ModelsJson(unittest.TestCase):
    def test_output_budget_defaults_beyond_pi_16k(self):
        # pi fills in maxTokens=16384 when the entry omits it; reasoning-max
        # turns overrun that and get truncated with stopReason "length", so
        # the generated entry must always carry an explicit budget.
        entry = _provider_entry({})["models"][0]
        self.assertEqual(entry["maxTokens"], 131072)
        self.assertTrue(entry["reasoning"])

    def test_output_budget_configurable(self):
        entry = _provider_entry({"max_output_tokens": 65536})["models"][0]
        self.assertEqual(entry["maxTokens"], 65536)

    def test_sandbox_only_ever_sees_dummy_credentials(self):
        provider = _provider_entry({})
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:1")
        self.assertEqual(provider["apiKey"], DUMMY_TOKEN)

    def test_follow_up_turn_resumes_the_same_pi_session(self):
        runtime = PiRuntime(
            {"workspace": {"confirmed_root": str(_ROOT)},
             "runtimes": {"pi": {"provider": "openrouter"}}},
            {"model": "anthropic/claude-fable-5", "reasoning": "max"})
        runtime.runtime_config = {"provider": "openrouter", "cli": "pi"}
        with mock.patch.object(runtime, "_cli_path",
                               return_value=pathlib.Path("/usr/bin/pi")):
            first = runtime._pi_cmd(pathlib.Path("/runtime"),
                                    system_prompt="system", tools=["read"],
                                    read_only=False)
            follow_up = runtime._pi_cmd(pathlib.Path("/runtime"),
                                        system_prompt="system", tools=["read"],
                                        read_only=False, continue_session=True)
        self.assertNotIn("--continue", first)
        self.assertIn("--continue", follow_up)
        self.assertEqual(first[first.index("--session-dir") + 1],
                         follow_up[follow_up.index("--session-dir") + 1])


class CompactEventsFile(unittest.TestCase):
    """Pi streams a full cumulative snapshot on every token, so one reasoning
    block is re-serialized thousands of times -- ~99% of raw bytes that
    parse_stream never reads. Compacting must strip only the ``*_update``
    chatter, before raw_sha256 is taken, and leave every finalized event and
    the file's byte-identity-under-repeat intact."""

    def _write(self, lines: list[str]) -> pathlib.Path:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        path = tmp / "x.events.jsonl"
        path.write_text("".join(line + "\n" for line in lines))
        return path

    def test_drops_updates_keeps_finalized(self):
        path = self._write([
            json.dumps({"type": "message_start"}),
            json.dumps({"type": "message_update", "message": {"x": "a" * 5000}}),
            json.dumps({"type": "message_update", "message": {"x": "a" * 5000}}),
            json.dumps({"type": "tool_execution_update", "n": 1}),
            json.dumps({"type": "tool_execution_end", "n": 1}),
            json.dumps({"type": "message_end", "message": {"role": "assistant"}}),
        ])
        before = path.stat().st_size
        skipped = compact_events_file(path)
        kinds = [json.loads(x)["type"] for x in path.read_text().splitlines()]
        self.assertEqual(kinds, ["message_start", "tool_execution_end",
                                 "message_end"])
        self.assertEqual(skipped, 3)
        self.assertLess(path.stat().st_size, before)

    def test_preserves_unparseable_lines(self):
        path = self._write([
            "not json at all",
            json.dumps({"type": "message_update"}),
            json.dumps({"type": "message_end"}),
        ])
        compact_events_file(path)
        self.assertEqual(path.read_text().splitlines(),
                         ["not json at all", json.dumps({"type": "message_end"})])

    def test_idempotent(self):
        path = self._write([
            json.dumps({"type": "message_update"}),
            json.dumps({"type": "message_end", "message": {"role": "assistant"}}),
        ])
        compact_events_file(path)
        once = path.read_text()
        self.assertEqual(compact_events_file(path), 0)
        self.assertEqual(path.read_text(), once)


if __name__ == "__main__":
    unittest.main()
