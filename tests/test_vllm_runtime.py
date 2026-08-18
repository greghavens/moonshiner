"""The local chat-completions backend: its agent loop, tools, and capture.

The HTTP layer is a fake server; everything below it is real. Tool calls run
through the same ``prepare_trace_command`` boundary a production trace uses, so
what these tests exercise is the containment the model actually gets, not a
host-side stand-in for it.

The capture assertions are deliberately unforgiving. Every way this adapter can
degrade -- fewer alternatives than requested, decoded tokens instead of ids, a
turn whose distributions go missing -- produces a sidecar that still loads and
still trains against the wrong thing, so each of them is asserted to stop the
attempt instead.
"""
from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
import uuid
from unittest import mock
from urllib.error import HTTPError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
from logprobs_sidecar import (LogprobsUnavailable, alignment_report,  # noqa: E402
                              file_sha256, read_sidecar)
from runtimes import availability  # noqa: E402
from runtimes.vllm import TRACE_FORMAT, VLLMRuntime  # noqa: E402

#: Sandboxed workspaces live here rather than under TMPDIR. The write boundary
#: aliases /tmp into the workspace's own scratch, so a workspace that itself sat
#: under /tmp would be shadowed by that bind and every tool call would fail.
WORKSPACE_ROOT = ROOT / ".moonshiner" / "test-workspaces"

MODEL = "Qwen3-Coder-30B-A3B-Instruct"
# Byte-for-byte what the seed says, blank lines and all: the adapter must not
# strip, wrap, or annotate it on the way to the server.
PROMPT = "Fix the off-by-one in `total()`.\n\nRun the tests when you're done.\n"


def _entries(token_ids: list[int], top_k: int) -> list[dict]:
    """A server's logprobs block for one turn, ids rendered as vLLM renders."""
    return [{"token": f"token_id:{token_id}", "logprob": -0.3,
             "top_logprobs": [{"token": f"token_id:{token_id + 1000 * rank}",
                               "logprob": -0.3 - rank}
                              for rank in range(top_k)]}
            for token_id in token_ids]


def _completion(*, content: str = "", tool_calls: list[dict] | None = None,
                token_ids: list[int] | None = None, top_k: int = 4,
                finish_reason: str = "stop", model: str = MODEL,
                entries: list[dict] | None = None,
                completion_tokens: int | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    choice: dict = {"index": 0, "message": message,
                    "finish_reason": finish_reason}
    if token_ids is not None or entries is not None:
        choice["logprobs"] = {"content": entries if entries is not None
                              else _entries(token_ids or [], top_k)}
    generated = (completion_tokens if completion_tokens is not None
                 else len(token_ids or []))
    return {"id": "chatcmpl-1", "model": model, "choices": [choice],
            "usage": {"prompt_tokens": 12, "completion_tokens": generated,
                      "total_tokens": 12 + generated}}


def _call(name: str, arguments: dict, identifier: str = "call_1") -> dict:
    return {"id": identifier, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


class _Server:
    """A fake OpenAI-compatible endpoint that records what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request, timeout=None):
        payload = json.loads(request.data.decode()) if request.data else {}
        self.requests.append((request.full_url, payload))
        if not self.answers:
            raise AssertionError(f"unexpected extra request to {request.full_url}")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, bytes):
            return io.BytesIO(answer)
        return io.BytesIO(json.dumps(answer).encode())

    @property
    def chats(self) -> list[dict]:
        return [payload for url, payload in self.requests
                if url.endswith("/chat/completions")]


def _http_error(status: int, body: str) -> HTTPError:
    return HTTPError("http://127.0.0.1:8000/v1/chat/completions", status,
                     "error", {}, io.BytesIO(body.encode()))


def _runtime(*, model: str = MODEL, **runtime_config) -> VLLMRuntime:
    role = {"runtime": "vllm", "model": model}
    config = {"teacher": role,
              "runtimes": {"vllm": {"base_url": "http://127.0.0.1:8000/v1",
                                    "sampling": {"temperature": 0.0,
                                                 "max_tokens": 128},
                                    **runtime_config}}}
    return VLLMRuntime(config, role)


class _TraceCase(unittest.TestCase):
    """A real workspace plus a real output tree; only the server is faked."""

    def setUp(self):
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        self.workspace = WORKSPACE_ROOT / f"vllm-{uuid.uuid4().hex}"
        self.workspace.mkdir()
        self.addCleanup(common.remove_workspace, self.workspace,
                        workspaces=WORKSPACE_ROOT)
        # The adapter refuses any workspace with an AGENTS.md above it, and the
        # offline gate keeps every scrap of test state inside the checkout --
        # where AGENTS.md is by definition an ancestor. That precondition has
        # its own test in ``test_model_context_isolation``; what these tests
        # are here to exercise is the sandbox below it.
        persistent = mock.patch.object(
            VLLMRuntime, "require_persistent_workspace",
            staticmethod(lambda workspace: pathlib.Path(workspace).resolve()))
        persistent.start()
        self.addCleanup(persistent.stop)
        self.storage = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-vllm-"))
        self.addCleanup(shutil.rmtree, self.storage, ignore_errors=True)
        self.out_dir = self.storage / "traces" / "raw"
        self.out_dir.mkdir(parents=True)
        self.seed = {"id": "vllm-seed"}

    def trace(self, runtime: VLLMRuntime, server: _Server, *,
              prompt: str = PROMPT, system_prompt: str = "",
              interaction: list[str] | None = None):
        with mock.patch("runtimes.vllm.urlopen", server):
            return runtime.run_trace(self.seed, self.workspace,
                                     out_dir=self.out_dir,
                                     system_prompt=system_prompt, prompt=prompt,
                                     interaction=interaction)

    def raw(self) -> dict:
        return json.loads((self.out_dir / "vllm-seed.json").read_text())

    def messages(self) -> list[dict]:
        messages, _ = VLLMRuntime.parse_stream(self.out_dir / "vllm-seed.json",
                                               str(self.workspace))
        return messages


class TheSeedReachesTheModelUnchanged(_TraceCase):
    def test_the_prompt_is_the_user_message_byte_for_byte(self):
        server = _Server(_completion(content="Done."))
        result = self.trace(_runtime(), server)
        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.trace_format, TRACE_FORMAT)
        messages = server.chats[0]["messages"]
        self.assertEqual(messages[1], {"role": "user", "content": PROMPT})
        self.assertEqual(len(messages), 2)

    def test_the_harness_system_prompt_is_recorded_and_never_folded_in(self):
        # It describes this harness's own tools, so it is not part of the
        # seed and must not reach the published trace as one of its messages.
        server = _Server(_completion(content="Done."))
        self.trace(_runtime(), server, system_prompt="Be terse.")
        self.assertEqual(server.chats[0]["messages"][0],
                         {"role": "system", "content": "Be terse."})
        self.assertEqual(self.raw()["system_prompt"], "Be terse.")
        self.assertNotIn("system", [message["role"]
                                    for message in self.messages()])

    def test_an_operator_follow_up_stays_in_the_trace(self):
        server = _Server(_completion(content="First."),
                         _completion(content="Second."))
        self.trace(_runtime(), server, interaction=["Now add a test."])
        self.assertEqual([message["role"] for message in self.messages()],
                         ["user", "assistant", "user", "assistant"])
        self.assertEqual(self.messages()[2]["content"], "Now add a test.")

    def test_the_configured_sampling_is_sent_and_nothing_else_is(self):
        server = _Server(_completion(content="Done."))
        self.trace(_runtime(), server)
        payload = server.chats[0]
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_tokens"], 128)
        self.assertEqual(payload["model"], MODEL)
        self.assertNotIn("logprobs", payload)
        self.assertNotIn("top_logprobs", payload)


class ToolsRunInsideTheWorkspace(_TraceCase):
    def test_the_model_changes_the_workspace_through_the_sandbox(self):
        server = _Server(
            _completion(tool_calls=[_call("write_file",
                                          {"path": "total.py",
                                           "content": "def total(x):\n    return x\n"})],
                        finish_reason="tool_calls"),
            _completion(tool_calls=[_call("bash", {"command": "cat total.py"},
                                          "call_2")],
                        finish_reason="tool_calls"),
            _completion(content="Fixed and verified."))
        result = self.trace(_runtime(), server)
        self.assertEqual(result.return_code, 0)
        self.assertTrue(result.stream_success)
        self.assertEqual((self.workspace / "total.py").read_text(),
                         "def total(x):\n    return x\n")
        roles = [message["role"] for message in self.messages()]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant",
                                 "tool", "assistant"])
        self.assertIn("def total(x):", self.messages()[4]["content"])

    def test_a_write_outside_the_workspace_never_lands(self):
        # The file tools go through the same boundary as bash rather than
        # checking paths themselves, so there is no second notion of "inside
        # the workspace" that could disagree with the kernel's. Two escapes
        # the boundary handles differently: the read-only host filesystem
        # refuses the write outright, while /tmp is an alias of the
        # workspace's own scratch, so that one succeeds and stays inside.
        repository = ROOT / f"vllm-escape-{uuid.uuid4().hex}"
        host_tmp = pathlib.Path("/tmp") / f"vllm-escape-{uuid.uuid4().hex}"
        server = _Server(
            _completion(tool_calls=[_call("write_file",
                                          {"path": str(repository),
                                           "content": "escaped"})],
                        finish_reason="tool_calls"),
            _completion(tool_calls=[_call("write_file",
                                          {"path": str(host_tmp),
                                           "content": "escaped"}, "call_2")],
                        finish_reason="tool_calls"),
            _completion(content="Stayed in the workspace."))
        self.trace(_runtime(), server)
        self.assertFalse(repository.exists())
        self.assertFalse(host_tmp.exists())
        refused = self.messages()[2]["content"]
        self.assertTrue(refused.startswith("error:"), refused)
        scratch = self.workspace / ".sandbox-home" / "tmp" / host_tmp.name
        self.assertEqual(scratch.read_text(), "escaped")

    def test_a_malformed_tool_call_is_answered_not_raised(self):
        broken = {"id": "call_1", "type": "function",
                  "function": {"name": "bash", "arguments": "{not json"}}
        server = _Server(_completion(tool_calls=[broken],
                                     finish_reason="tool_calls"),
                         _completion(content="Retrying."))
        result = self.trace(_runtime(), server)
        self.assertEqual(result.return_code, 0)
        self.assertIn("could not parse arguments", self.messages()[2]["content"])

    def test_an_edit_that_matches_nothing_says_so(self):
        (self.workspace / "total.py").write_text("def total(x):\n    return x\n")
        server = _Server(
            _completion(tool_calls=[_call("edit_file",
                                          {"path": "total.py",
                                           "old_string": "return y",
                                           "new_string": "return x + 1"})],
                        finish_reason="tool_calls"),
            _completion(content="Looked again."))
        self.trace(_runtime(), server)
        self.assertIn("old_string not found", self.messages()[2]["content"])
        self.assertEqual((self.workspace / "total.py").read_text(),
                         "def total(x):\n    return x\n")


class CaptureIsFiledAgainstTheTurnThatProducedIt(_TraceCase):
    def _capture(self, top_k: int = 4, **overrides):
        server = _Server(
            _completion(tool_calls=[_call("bash", {"command": "true"})],
                        token_ids=[101, 102, 103], top_k=top_k,
                        finish_reason="tool_calls"),
            _completion(content="Done.", token_ids=[201, 202], top_k=top_k))
        runtime = _runtime(logprobs={"enabled": True, "top_k": top_k},
                           **overrides)
        return server, self.trace(runtime, server)

    @property
    def sidecar(self) -> pathlib.Path:
        return self.storage / "traces" / "logprobs" / "vllm-seed.parquet"

    def test_the_request_asks_for_ids_rather_than_text(self):
        server, _ = self._capture()
        for payload in server.chats:
            self.assertIs(payload["logprobs"], True)
            self.assertEqual(payload["top_logprobs"], 4)
            self.assertIs(payload["return_tokens_as_token_ids"], True)

    def test_each_turn_lands_under_its_own_assistant_index(self):
        self._capture()
        table, metadata = read_sidecar(self.sidecar)
        self.assertEqual(metadata["trajectory_id"], "vllm-seed")
        self.assertEqual(
            list(zip(table.column("assistant_turn_index").to_pylist(),
                     table.column("token_index").to_pylist(),
                     table.column("token_id").to_pylist())),
            [(1, 0, 101), (1, 1, 102), (1, 2, 103), (2, 0, 201), (2, 1, 202)])
        report = alignment_report(self.messages(), self.sidecar)
        self.assertTrue(report["aligned"], report["problems"])
        self.assertEqual(report["assistant_turns_in_sidecar"], 2)

    def test_the_trace_points_at_the_sidecar_by_hash(self):
        # A sidecar whose bytes no longer match this trace describes some
        # other generation, and the export path drops it on exactly this
        # hash -- so the hash has to be the one the file actually has.
        _, result = self._capture()
        recorded = self.raw()["logprobs"]
        self.assertTrue(recorded["enabled"])
        self.assertIs(recorded["renormalized"], False)
        self.assertEqual(recorded["sidecar"]["tokens"], 5)
        self.assertEqual(recorded["sidecar"]["sha256"], file_sha256(self.sidecar))
        self.assertEqual(result.provenance["logprobs"]["sha256"],
                         file_sha256(self.sidecar))

    def test_the_published_path_is_relative_to_project_storage(self):
        # ``build_dataset`` carries this string into the dataset and the
        # exporter resolves it against STORAGE_ROOT, so an absolute path here
        # would publish one machine's filesystem and resolve nowhere else.
        _, result = self._capture()
        self.assertEqual(result.provenance["logprobs"]["path"],
                         "traces/logprobs/vllm-seed.parquet")
        self.assertEqual(result.provenance["logprobs"]["top_k"], 4)
        self.assertEqual(result.provenance["logprobs"]["assistant_turns"], 2)
        self.assertIs(result.provenance["logprobs"]["renormalized"], False)

    def test_capture_stays_off_unless_it_is_asked_for(self):
        server = _Server(_completion(content="Done."))
        result = self.trace(_runtime(), server)
        self.assertNotIn("logprobs", result.provenance)
        self.assertFalse((self.storage / "traces" / "logprobs").exists())
        self.assertEqual(self.raw()["logprobs"],
                         {"enabled": False, "top_k": None,
                          "renormalized": False, "sidecar": None})


class Streaming(_TraceCase):
    """A streamed completion must reassemble into exactly the same capture."""

    @staticmethod
    def _stream(*chunks: dict) -> bytes:
        lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
        return ("".join(lines) + "data: [DONE]\n\n").encode()

    @staticmethod
    def _delta(delta: dict, *, logprobs: list[dict] | None = None,
               finish_reason=None, usage: dict | None = None) -> dict:
        choice: dict = {"index": 0, "delta": delta,
                        "finish_reason": finish_reason}
        if logprobs is not None:
            choice["logprobs"] = {"content": logprobs}
        chunk = {"model": MODEL, "choices": [choice]}
        if usage:
            chunk["usage"] = usage
        return chunk

    def test_token_deltas_and_split_tool_arguments_rejoin(self):
        first = self._stream(
            self._delta({"role": "assistant", "content": ""},
                        logprobs=_entries([101], 3)),
            # Arguments arrive in fragments, as they do on the wire.
            self._delta({"tool_calls": [{"index": 0, "id": "call_1",
                                         "function": {"name": "write_file",
                                                      "arguments": '{"path": "note.txt",'}}]},
                        logprobs=_entries([102], 3)),
            self._delta({"tool_calls": [{"index": 0,
                                         "function": {"arguments": ' "content": "hi\\n"}'}}]},
                        logprobs=_entries([103], 3), finish_reason="tool_calls",
                        usage={"prompt_tokens": 12, "completion_tokens": 3,
                               "total_tokens": 15}))
        second = self._stream(
            self._delta({"content": "Wrote it."}, logprobs=_entries([201], 3)),
            self._delta({}, logprobs=_entries([202], 3), finish_reason="stop",
                        usage={"prompt_tokens": 20, "completion_tokens": 2,
                               "total_tokens": 22}))
        server = _Server(first, second)
        runtime = _runtime(stream=True, logprobs={"enabled": True, "top_k": 3})
        result = self.trace(runtime, server)

        self.assertEqual(result.return_code, 0)
        self.assertTrue(all(payload["stream"] for payload in server.chats))
        self.assertEqual((self.workspace / "note.txt").read_text(), "hi\n")
        self.assertEqual(result.usage["completion_tokens"], 5)
        table, _ = read_sidecar(
            self.storage / "traces" / "logprobs" / "vllm-seed.parquet")
        self.assertEqual(table.column("token_id").to_pylist(),
                         [101, 102, 103, 201, 202])
        self.assertEqual(table.column("assistant_turn_index").to_pylist(),
                         [1, 1, 1, 2, 2])

    def test_a_chunk_that_is_not_json_stops_the_attempt(self):
        server = _Server(b"data: {not json\n\n")
        with self.assertRaisesRegex(RuntimeError, "not JSON"):
            self.trace(_runtime(stream=True), server)


class CaptureFailsLoudlyOrNotAtAll(_TraceCase):
    def test_an_over_limit_top_logprobs_names_the_flag_to_change(self):
        # vLLM caps this server-side at --max-logprobs (default 20) and
        # rejects the request outright. Silently retrying with a smaller K
        # would change the distillation target without saying so.
        body = json.dumps({"object": "error", "message":
                           "top_logprobs must be <= max_logprobs (20)"})
        server = _Server(_http_error(400, body))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 64})
        with self.assertRaises(LogprobsUnavailable) as raised:
            self.trace(runtime, server)
        message = str(raised.exception)
        self.assertIn("--max-logprobs 64", message)
        self.assertIn("runtimes.vllm.logprobs.top_k", message)

    def test_the_probe_catches_the_cap_before_a_trajectory_is_wasted(self):
        models = {"data": [{"id": MODEL}]}
        body = json.dumps({"message": "top_logprobs must be <= max_logprobs"})
        server = _Server(models, _http_error(400, body))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 100})
        with mock.patch("runtimes.vllm.urlopen", server):
            with self.assertRaises(SystemExit) as raised:
                runtime.preflight()
        self.assertIn("--max-logprobs 100", str(raised.exception))

    def test_a_probe_that_returns_decoded_text_is_refused(self):
        models = {"data": [{"id": MODEL}]}
        decoded = [{"token": "def", "logprob": -0.2,
                    "top_logprobs": [{"token": "def", "logprob": -0.2}]}]
        server = _Server(models, _completion(entries=decoded,
                                             completion_tokens=1))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 1})
        with mock.patch("runtimes.vllm.urlopen", server):
            with self.assertRaises(SystemExit) as raised:
                runtime.preflight()
        self.assertIn("return_tokens_as_token_ids", str(raised.exception))

    def test_decoded_tokens_mid_trajectory_stop_the_attempt(self):
        decoded = [{"token": "return", "logprob": -0.2,
                    "top_logprobs": [{"token": "return", "logprob": -0.2}]}]
        server = _Server(_completion(content="Done.", entries=decoded,
                                     completion_tokens=1))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 1})
        with self.assertRaisesRegex(LogprobsUnavailable, "re-tokenize"):
            self.trace(runtime, server)
        # The partial artifact is still written: an operator debugging a
        # capture failure needs to see what the server actually returned.
        self.assertTrue((self.out_dir / "vllm-seed.json").exists())

    def test_a_turn_with_no_logprobs_at_all_stops_the_attempt(self):
        server = _Server(_completion(content="Done."))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 4})
        with self.assertRaisesRegex(LogprobsUnavailable, "carried no logprobs"):
            self.trace(runtime, server)

    def test_fewer_logprobs_than_generated_tokens_stops_the_attempt(self):
        server = _Server(_completion(content="Done.", token_ids=[1, 2],
                                     completion_tokens=5))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 4})
        with self.assertRaisesRegex(LogprobsUnavailable, "misaligned"):
            self.trace(runtime, server)

    def test_no_sidecar_is_left_behind_when_capture_fails(self):
        server = _Server(_completion(content="Done."))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 4})
        with self.assertRaises(LogprobsUnavailable):
            self.trace(runtime, server)
        self.assertFalse((self.storage / "traces" / "logprobs").exists())


class ServerFailures(_TraceCase):
    def test_an_ordinary_failure_is_not_read_as_the_logprob_cap(self):
        server = _Server(_http_error(500, "internal server error"))
        runtime = _runtime(logprobs={"enabled": True, "top_k": 8})
        with self.assertRaises(RuntimeError) as raised:
            self.trace(runtime, server)
        self.assertNotIsInstance(raised.exception, LogprobsUnavailable)
        self.assertIn("HTTP 500", str(raised.exception))

    def test_a_quota_block_is_reported_as_the_live_condition_it_is(self):
        server = _Server(_http_error(402, "insufficient credits for this request"))
        result = self.trace(_runtime(), server)
        self.assertIn("insufficient credits", result.unavailable)
        self.assertEqual(result.return_code, 1)

    def test_an_unreachable_server_says_how_to_start_one(self):
        from urllib.error import URLError
        server = _Server(URLError("Connection refused"))
        with self.assertRaisesRegex(RuntimeError, "vllm serve"):
            self.trace(_runtime(), server)

    def test_a_loop_that_never_finishes_ends_as_a_failed_attempt(self):
        calls = [_call("bash", {"command": "true"})]
        server = _Server(*[_completion(tool_calls=calls,
                                       finish_reason="tool_calls")
                           for _ in range(2)])
        result = self.trace(_runtime(max_tool_iterations=2), server)
        self.assertEqual(result.return_code, 1)
        self.assertIn("max_tool_iterations=2", result.error)
        self.assertFalse(result.stream_success)


class Configuration(unittest.TestCase):
    def test_a_model_must_be_configured_before_anything_is_sent(self):
        with self.assertRaisesRegex(RuntimeError, "config role trace-author"):
            _runtime(model="").model

    def test_sampling_keys_are_a_whitelist(self):
        runtime = _runtime()
        runtime.runtime_config["sampling"] = {"temperature": 0.2, "beam_width": 4}
        with self.assertRaisesRegex(RuntimeError, "beam_width"):
            runtime._sampling()

    def test_role_configuration_wins_over_the_runtime_default(self):
        runtime = _runtime(logprobs={"enabled": False, "top_k": 20})
        runtime.role["logprobs"] = {"enabled": True, "top_k": 5}
        self.assertEqual(runtime._logprobs_settings(), (True, 5))

    def test_a_server_serving_a_different_model_is_refused(self):
        runtime = _runtime()
        server = _Server({"data": [{"id": "some-other-model"}]})
        with mock.patch("runtimes.vllm.urlopen", server):
            with self.assertRaises(SystemExit) as raised:
                runtime.preflight()
        self.assertIn("some-other-model", str(raised.exception))

    def test_a_keyless_local_server_is_asked_for_no_credential(self):
        # The common local deployment has no auth at all; requiring a key
        # would make the backend unusable without inventing one.
        runtime = _runtime()
        self.assertIsNone(runtime._api_key())

    def test_this_backend_offers_no_reasoning_effort_to_step_down(self):
        import reasoning_stepdown
        with self.assertRaisesRegex(ValueError, "step_down_reasoning_on_failure"):
            reasoning_stepdown.runtime_for_stage(_runtime(), "medium")


if __name__ == "__main__":
    unittest.main()
