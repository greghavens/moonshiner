"""Pi runtime provisioning: the models.json the sandboxed agent runs on.

Offline — exercises only config generation, no pi process and no network.
"""
import json
import inspect
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.credential_proxy import DUMMY_TOKEN  # noqa: E402
from runtimes.pi import (PiRuntime, _parse_pi_stream, compact_events_file,
                         run_streamed)  # noqa: E402


def _provider_entry(runtime_config: dict) -> dict:
    config = {"runtimes": {"pi": dict(runtime_config, provider="openrouter")}}
    runtime = PiRuntime(config, {"model": "moonshotai/kimi-k3"})
    with tempfile.TemporaryDirectory() as tmp:
        runtime._prepare_runtime(pathlib.Path(tmp), "http://127.0.0.1:1")
        models = json.loads(
            (pathlib.Path(tmp) / "config" / "models.json").read_text())
    return models["providers"]["openrouter"]


class ModelsJson(unittest.TestCase):
    def test_trace_command_does_not_modify_native_pi_behavior(self):
        runtime = PiRuntime(
            {"workspace": {"confirmed_root": str(_ROOT)},
             "runtimes": {"pi": {"provider": "openrouter"}}},
            {"model": "anthropic/claude-fable-5", "reasoning": "max"})
        runtime.runtime_config = {"provider": "openrouter", "cli": "pi"}
        with mock.patch.object(runtime, "_cli_path",
                               return_value=pathlib.Path("/usr/bin/pi")):
            command = runtime._pi_cmd(
                pathlib.Path("/runtime"), system_prompt="must not be used",
                tools=["read"], read_only=False)
        prohibited = {
            "--system-prompt", "--tools", "--offline", "--no-skills",
            "--no-context-files", "--no-approve", "--extension",
        }
        self.assertTrue(prohibited.isdisjoint(command))

    def test_coding_guidance_appends_without_replacing_native_prompt(self):
        runtime = PiRuntime(
            {"workspace": {"confirmed_root": str(_ROOT)},
             "runtimes": {"pi": {"provider": "openrouter"}}},
            {"model": "anthropic/claude-fable-5", "reasoning": "max"})
        runtime.runtime_config = {"provider": "openrouter", "cli": "pi"}
        with mock.patch.object(runtime, "_cli_path",
                               return_value=pathlib.Path("/usr/bin/pi")):
            command = runtime._pi_cmd(
                pathlib.Path("/runtime"), system_prompt="ignored",
                tools=None, read_only=False,
                append_system_prompt="coding guidance")
        self.assertNotIn("--system-prompt", command)
        self.assertEqual(
            command[command.index("--append-system-prompt") + 1],
            "coding guidance")

    def test_coding_guidance_selection_uses_catalog_program_data(self):
        runtime = PiRuntime(
            {"pipeline": {"trace": {"coding_system_prompt_append": True}}},
            {"model": "anthropic/claude-fable-5"})
        self.assertTrue(runtime._coding_guidance(
            {"_catalog_program": "Building"}))
        self.assertIsNone(runtime._coding_guidance(
            {"_catalog_program": "Instruction following"}))
        runtime.config["pipeline"]["trace"]["coding_system_prompt_append"] = False
        self.assertIsNone(runtime._coding_guidance(
            {"_catalog_program": "Building"}))

    def test_runtime_does_not_install_a_tool_execution_extension(self):
        runtime = PiRuntime(
            {"runtimes": {"pi": {"provider": "openrouter"}}},
            {"model": "anthropic/claude-fable-5"})
        runtime.runtime_config = {
            "provider": "openrouter", "base_url": "http://127.0.0.1:1",
            "api": "openai-completions",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            runtime._prepare_runtime(root, "http://127.0.0.1:1")
            self.assertFalse((root / "config" / "bash-timeout-guard.js").exists())

    def test_managed_fallback_is_stable_user_data_not_the_versioned_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = PiRuntime(
                {"workspace": {"confirmed_root": directory},
                 "runtimes": {"pi": {"provider": "openrouter"}}},
                {"model": "moonshotai/kimi-k3"})
            runtime.runtime_config = {"provider": "openrouter", "cli": "pi"}
            data = pathlib.Path(directory) / "data"
            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": str(data)}), \
             mock.patch("runtimes.pi.shutil.which", return_value=None):
                path = runtime._cli_path()
        self.assertEqual(path, data / "moonshiner" /
                         "toolchains" / "pi" / "node_modules" / ".bin" / "pi")
        self.assertNotIn("moonshiner_app/bundle", str(path))

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


class NativeToolArguments(unittest.TestCase):
    def test_openai_completions_arguments_are_preserved(self):
        event = {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "toolCall", "id": "read_0",
                             "name": "read",
                             "arguments": {"path": "PROJECTS.md"}}],
            },
        }
        messages, stats = _parse_pi_stream(json.dumps(event), None)
        call = messages[0]["tool_calls"][0]
        self.assertEqual(json.loads(call["function"]["arguments"]),
                         {"path": "PROJECTS.md"})
        self.assertEqual(stats["tool_calls"], 1)


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


class StreamedProcess(unittest.TestCase):
    def test_pi_trace_path_uses_streaming_helper(self):
        source = inspect.getsource(PiRuntime._run)
        self.assertIn("run_streamed(", source)
        self.assertNotIn("capture_output=True", source)

    def test_pi_output_is_never_buffered_in_process_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            stdout_path, stderr_path = root / "events.jsonl", root / "stderr"

            def fake_run(*_args, **kwargs):
                self.assertNotIn("capture_output", kwargs)
                self.assertNotEqual(kwargs.get("stdout"), subprocess.PIPE)
                self.assertNotEqual(kwargs.get("stderr"), subprocess.PIPE)
                kwargs["stdout"].write('{"type":"message_end"}\n')
                kwargs["stderr"].write("diagnostic\n")
                return subprocess.CompletedProcess([], 0)

            with mock.patch("runtimes.pi.subprocess.run", side_effect=fake_run):
                result = run_streamed(["pi"], workspace=root, turn="prompt",
                                      stdout_path=stdout_path,
                                      stderr_path=stderr_path, timeout=30,
                                      environment={})
            self.assertEqual(result.returncode, 0)
            self.assertIn("message_end", stdout_path.read_text())
            self.assertEqual(stderr_path.read_text(), "diagnostic\n")


if __name__ == "__main__":
    unittest.main()
