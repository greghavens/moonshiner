"""Native OpenCode session, reasoning, tool, and schema contracts."""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runtimes.opencode import (  # noqa: E402
    OPENCODE_RUNTIME_VERSION,
    OpenCodeRuntime,
    prompt_payload,
    validate_tool_schemas,
)


def _completed_session(*, reasoning_text: str = "Inspect the file first.") -> list[dict]:
    return [
        {
            "info": {
                "id": "msg_user", "sessionID": "ses_native", "role": "user",
                "time": {"created": 10}, "agent": "build",
                "model": {"providerID": "zenmux", "modelID": "model-a"},
            },
            "parts": [{
                "id": "part_user", "sessionID": "ses_native",
                "messageID": "msg_user", "type": "text",
                "text": "\nExact authored prompt.\n",
            }],
        },
        {
            "info": {
                "id": "msg_assistant", "sessionID": "ses_native",
                "role": "assistant", "time": {"created": 20, "completed": 40},
                "parentID": "msg_user", "modelID": "model-a",
                "providerID": "zenmux", "mode": "build",
                "path": {"cwd": "/workspace", "root": "/workspace"},
                "cost": 0.0042,
                "tokens": {"input": 11, "output": 7, "reasoning": 3,
                           "cache": {"read": 2, "write": 0}},
                "finish": "tool-calls",
            },
            "parts": [
                {"id": "step_1", "sessionID": "ses_native",
                 "messageID": "msg_assistant", "type": "step-start"},
                {"id": "reason_1", "sessionID": "ses_native",
                 "messageID": "msg_assistant", "type": "reasoning",
                 "text": reasoning_text, "time": {"start": 21, "end": 22},
                 "metadata": {"provider": {"native": True}}},
                {"id": "tool_part", "sessionID": "ses_native",
                 "messageID": "msg_assistant", "type": "tool",
                 "callID": "call_native_1", "tool": "write",
                 "state": {"status": "completed",
                           "input": {"filePath": "answer.txt", "content": "done\n"},
                           "output": "Wrote answer.txt", "title": "write answer.txt",
                           "metadata": {"bytes": 5},
                           "time": {"start": 23, "end": 24}}},
                {"id": "step_finish", "sessionID": "ses_native",
                 "messageID": "msg_assistant", "type": "step-finish",
                 "reason": "tool-calls", "cost": 0.0042,
                 "tokens": {"input": 11, "output": 7, "reasoning": 3,
                            "cache": {"read": 2, "write": 0}}},
            ],
        },
        {
            "info": {
                "id": "msg_final", "sessionID": "ses_native",
                "role": "assistant", "time": {"created": 41, "completed": 45},
                "parentID": "msg_user", "modelID": "model-a",
                "providerID": "zenmux", "mode": "build",
                "path": {"cwd": "/workspace", "root": "/workspace"},
                "cost": 0.001,
                "tokens": {"input": 4, "output": 2, "reasoning": 0,
                           "cache": {"read": 0, "write": 0}},
                "finish": "stop",
            },
            "parts": [{"id": "text_final", "sessionID": "ses_native",
                       "messageID": "msg_final", "type": "text",
                       "text": "Done.", "time": {"start": 42, "end": 44}}],
        },
    ]


class OpenCodeStructuredSession(unittest.TestCase):
    def _parse(self, session: list[dict]) -> tuple[list[dict], dict]:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = pathlib.Path(directory) / "session.json"
            path.write_text(json.dumps(session))
            return OpenCodeRuntime.parse_stream(path, None)

    def test_pinned_runtime_and_genuine_capabilities_are_explicit(self):
        self.assertEqual(OPENCODE_RUNTIME_VERSION, "1.18.18")
        runtime = OpenCodeRuntime(
            {"runtimes": {"opencode": {"runtime_version": "1.18.18"}}},
            {"model": "model-a"})
        self.assertEqual(
            runtime.trace_capabilities(),
            frozenset({"workspace_write", "multi_turn",
                       "reasoning_capture", "tool_schema_capture"}))

    def test_prompt_is_one_byte_identical_text_part(self):
        prompt = "\ufeffLine one\r\nLine two\n\x00tail"
        payload = prompt_payload(prompt, "zenmux", "model-a")
        self.assertEqual(payload["model"], {
            "providerID": "zenmux", "modelID": "model-a"})
        self.assertNotIn("system", payload)
        self.assertEqual(payload["parts"], [{"type": "text", "text": prompt}])

    def test_completed_session_preserves_reasoning_calls_results_and_usage(self):
        messages, stats = self._parse(_completed_session())
        self.assertEqual(messages[0], {
            "role": "user", "content": "\nExact authored prompt.\n"})
        self.assertEqual(messages[1]["reasoning_content"],
                         "Inspect the file first.")
        self.assertEqual(messages[1]["tool_calls"], [{
            "id": "call_native_1", "type": "function",
            "function": {"name": "write", "arguments": json.dumps(
                {"filePath": "answer.txt", "content": "done\n"},
                separators=(",", ":"), sort_keys=True)},
        }])
        self.assertEqual(messages[2], {
            "role": "tool", "tool_call_id": "call_native_1",
            "content": "Wrote answer.txt"})
        self.assertEqual(messages[-1], {"role": "assistant", "content": "Done."})
        self.assertEqual(stats["reasoning_blocks"], 1)
        self.assertEqual(stats["tool_calls"], 1)
        self.assertEqual(stats["tool_results"], 1)
        self.assertEqual(stats["finish_reasons"], ["tool-calls", "stop"])
        self.assertEqual(stats["cost"], 0.0052)
        self.assertEqual(stats["usage"]["reasoning"], 3)

    def test_missing_native_tool_result_fails_closed(self):
        session = _completed_session()
        tool = session[1]["parts"][2]
        tool["state"] = {"status": "running", "input": {},
                         "time": {"start": 23}}
        with self.assertRaisesRegex(ValueError, "completed native tool evidence"):
            self._parse(session)

    def test_encrypted_metadata_never_becomes_reasoning_text(self):
        session = _completed_session(reasoning_text="")
        session[1]["parts"][1]["metadata"] = {
            "provider": {"reasoningEncryptedContent": "opaque-secret"}}
        messages, stats = self._parse(session)
        self.assertNotIn("reasoning_content", messages[1])
        self.assertEqual(stats["reasoning_blocks"], 0)
        self.assertNotIn("opaque-secret", json.dumps(messages))

    def test_experimental_tool_response_is_preserved_not_reconstructed(self):
        tools = [
            {"id": "read", "description": "Read a file",
             "parameters": {"type": "object", "properties": {
                 "filePath": {"type": "string"}}, "required": ["filePath"]}},
            {"id": "write", "description": "Write a file",
             "parameters": {"type": "object", "properties": {
                 "filePath": {"type": "string"},
                 "content": {"type": "string"}}}},
        ]
        self.assertIs(validate_tool_schemas(tools), tools)
        with self.assertRaisesRegex(ValueError, "tool schema"):
            validate_tool_schemas([{"id": "read", "description": "Read",
                                    "parameters": "invented"}])


if __name__ == "__main__":
    unittest.main()
