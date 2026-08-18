"""Local OpenAI-compatible chat-completions backend with logprobs capture.

Every other adapter here delegates the agent loop to an installed CLI and
normalizes whatever that CLI persisted. vLLM serves chat completions, not an
agent, so this adapter owns the loop: it holds the message list, calls the
server, executes the tool calls, and appends the results. That ownership is
the point rather than a cost. Token distributions have to be filed against the
exact assistant turn that produced them, and a turn boundary this adapter
constructed is a fact, where a turn boundary inferred from a CLI's outbound
HTTP would be a guess -- a CLI issues requests for titles, summaries and
subagents against the same endpoint, none of which are turns of the trace.

Tool execution reuses the one containment boundary the repo already has:
every tool -- including the file tools -- runs as a command through
``prepare_trace_command``, so a path a model invents cannot reach outside the
workspace by any route that ``bash`` could not already reach. There is no
second, host-side path check to drift away from the first.

Logprobs capture is opt-in and, once on, fails loudly. See
``logprobs_sidecar`` for why a degraded capture is worse than none.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common import scrub_text
from logprobs_sidecar import (GeneratedToken, LogprobsUnavailable,
                              TurnLogprobs, write_sidecar)
from runtimes import availability
from runtimes.auth import load_provider_key
from runtimes.base import (ReviewResult, Runtime, TraceResult,
                           parse_json_verdict, run_with_inactivity_timeout,
                           with_verdict_schema)

TRACE_FORMAT = "moonshiner-vllm-openai-v1"

#: vLLM renders a token as this prefix followed by its integer vocabulary id
#: when ``return_tokens_as_token_ids`` is honored.
TOKEN_ID_PREFIX = "token_id:"

DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_TOP_K = 100
DEFAULT_MAX_TOOL_ITERATIONS = 60
DEFAULT_REQUEST_TIMEOUT_S = 1800.0
DEFAULT_TOOL_TIMEOUT_S = 600.0
TOOL_OUTPUT_LIMIT = 60000

SYSTEM_PROMPT = """You are a software engineer working in a checked-out repository.

The working directory is your workspace and is the only place you can write.
Use the tools to inspect and change files, and to run commands. Make the change
the user asked for, then verify it the way the repository verifies itself.

Work until the task is done, then reply with a short summary of what you
changed. Do not ask questions; nobody is available to answer them."""

TOOL_SPECS = {
    "bash": {
        "description": ("Run a shell command in the workspace and return its "
                        "combined stdout and stderr."),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "The shell command to run."},
            },
            "required": ["command"],
        },
    },
    "read_file": {
        "description": "Read a file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Path to the file, relative to the "
                                        "workspace."},
            },
            "required": ["path"],
        },
    },
    "write_file": {
        "description": ("Write a file in the workspace, creating or replacing "
                        "it."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "edit_file": {
        "description": ("Replace one exact occurrence of old_string with "
                        "new_string in a workspace file."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    "list_dir": {
        "description": "List a directory in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Directory path; defaults to the "
                                        "workspace root."},
            },
            "required": [],
        },
    },
}

_LOGPROB_LIMIT_PHRASES = (
    "max_logprobs", "max-logprobs", "top_logprobs", "logprob")


class ToolFailure(RuntimeError):
    """A tool could not run at all, as distinct from running and failing."""


class VLLMRuntime(Runtime):
    """An OpenAI-compatible chat-completions server driven as an agent."""

    name = "vllm"
    trace_formats = (TRACE_FORMAT,)

    # -- configuration ------------------------------------------------------ #
    def _setting(self, key: str, default=None):
        """Role config wins over runtime config; both are explicit in files."""
        if key in self.role:
            return self.role[key]
        return self.runtime_config.get(key, default)

    @property
    def base_url(self) -> str:
        return str(self._setting("base_url", DEFAULT_BASE_URL)).rstrip("/")

    @property
    def model(self) -> str:
        model = str(self.role.get("model") or "").strip()
        if not model:
            raise RuntimeError(
                "vllm runtime has no model configured; set it with "
                "'moonshiner config role trace-author vllm <model>'")
        return model

    def _logprobs_settings(self) -> tuple[bool, int]:
        settings = self._setting("logprobs", {}) or {}
        if not isinstance(settings, dict):
            raise RuntimeError("runtimes.vllm.logprobs must be an object")
        enabled = bool(settings.get("enabled", False))
        top_k = int(settings.get("top_k", DEFAULT_TOP_K))
        if enabled and top_k < 1:
            raise RuntimeError(
                f"runtimes.vllm.logprobs.top_k must be at least 1; got {top_k}")
        return enabled, top_k

    def _sampling(self) -> dict:
        sampling = dict(self._setting("sampling", {}) or {})
        allowed = {"temperature", "top_p", "top_k", "max_tokens", "seed",
                   "presence_penalty", "frequency_penalty", "repetition_penalty",
                   "min_p", "stop"}
        unknown = sorted(set(sampling) - allowed)
        if unknown:
            raise RuntimeError(
                f"runtimes.vllm.sampling has unsupported keys: {unknown}; "
                f"supported: {sorted(allowed)}")
        return sampling

    def _api_key(self) -> str | None:
        """Keyless by default; a key is loaded only when one is required.

        A local vLLM usually serves without auth. Setting ``requires_key``
        makes the credential mandatory, so a server that is actually gated
        fails at credential load with the existing message rather than at the
        first request with a bare 401.
        """
        if not self.runtime_config.get("requires_key", False):
            return None
        return load_provider_key(self.runtime_config)

    def trace_capabilities(self) -> frozenset[str]:
        return frozenset({"workspace_write", "multi_turn"})

    def trace_probe_command(self) -> list[str]:
        # There is no CLI to probe; reachability is checked in preflight
        # against the server itself.
        return []

    # -- HTTP --------------------------------------------------------------- #
    def _request(self, path: str, payload: dict | None = None,
                 *, method: str = "POST", stream: bool = False,
                 timeout: float | None = None):
        url = f"{self.base_url}{path}"
        headers = {"Accept": "text/event-stream" if stream
                   else "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = Request(url, data=body, headers=headers, method=method)
        timeout = timeout if timeout is not None else float(
            self._setting("request_timeout_s", DEFAULT_REQUEST_TIMEOUT_S))
        try:
            return urlopen(request, timeout=timeout)
        except HTTPError as error:
            detail = error.read().decode(errors="replace")[:4000]
            raise self._http_error(error.code, detail) from error
        except URLError as error:
            raise RuntimeError(
                f"vLLM server unreachable at {self.base_url}: {error.reason}. "
                f"Start it with 'vllm serve {self.model}' or point "
                f"runtimes.vllm.base_url at the running server.") from error

    def _http_error(self, status: int, detail: str) -> Exception:
        enabled, top_k = self._logprobs_settings()
        lowered = detail.lower()
        # vLLM rejects an over-limit top_logprobs at request validation, before
        # generating anything. Degrading to whatever K the server allows would
        # quietly change the training target, so this is fatal and says exactly
        # which flag to change.
        if enabled and status == 400 and any(
                phrase in lowered for phrase in _LOGPROB_LIMIT_PHRASES):
            return LogprobsUnavailable(
                f"the vLLM server refused top_logprobs={top_k}: {detail.strip()}. "
                f"Restart vLLM with --max-logprobs {top_k} (its default is 20), "
                f"or lower runtimes.vllm.logprobs.top_k to the server's limit. "
                f"Capture is not degraded automatically: fewer alternatives "
                f"than requested silently changes the distillation target.")
        limit = availability.find_usage_limit(detail)
        if limit:
            return availability.ModelUnavailable(f"vllm: {limit}")
        return RuntimeError(
            f"vLLM request failed with HTTP {status}: {detail.strip()}")

    def _chat(self, messages: list[dict], tools: list[dict],
              *, logprobs: bool, top_k: int) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            **self._sampling(),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if logprobs:
            payload["logprobs"] = True
            payload["top_logprobs"] = top_k
            # Without this vLLM returns decoded strings, and a decoded string
            # has to be re-tokenized to become a target again.
            payload["return_tokens_as_token_ids"] = True
        if bool(self._setting("stream", False)):
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
            with self._request("/chat/completions", payload, stream=True) as response:
                return _collect_stream(response)
        with self._request("/chat/completions", payload) as response:
            return _normalize_completion(json.loads(response.read().decode()))

    # -- lifecycle ---------------------------------------------------------- #
    def preflight(self, *, require_auth: bool = False) -> None:
        try:
            with self._request("/models", method="GET", timeout=30) as response:
                served = json.loads(response.read().decode())
        except LogprobsUnavailable as error:
            raise SystemExit(str(error)) from error
        except Exception as error:
            raise SystemExit(f"vllm preflight failed: {error}") from error
        ids = [str(entry.get("id")) for entry in (served.get("data") or [])]
        if ids and not any(self.model_matches(identifier) for identifier in ids):
            raise SystemExit(
                f"vLLM at {self.base_url} serves {ids}, not "
                f"{self.model!r}; attestation would fail on every trace")

        enabled, top_k = self._logprobs_settings()
        if not enabled:
            return
        # Probe with a single token rather than discovering mid-trajectory
        # that the server caps K or will not emit ids -- either one wastes the
        # whole trajectory, and the second one wastes it invisibly.
        probe = {
            "model": self.model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1, "temperature": 0.0,
            "logprobs": True, "top_logprobs": top_k,
            "return_tokens_as_token_ids": True,
        }
        try:
            with self._request("/chat/completions", probe, timeout=120) as response:
                completion = _normalize_completion(
                    json.loads(response.read().decode()))
        except LogprobsUnavailable as error:
            raise SystemExit(str(error)) from error
        except Exception as error:
            raise SystemExit(f"vllm logprobs probe failed: {error}") from error
        try:
            _extract_tokens(completion, top_k)
        except LogprobsUnavailable as error:
            raise SystemExit(str(error)) from error

    # -- teacher ------------------------------------------------------------ #
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        workspace = self.require_persistent_workspace(workspace)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"{seed['id']}.json"
        logprobs_enabled, top_k = self._logprobs_settings()
        environment = self.teacher_environment(workspace)
        names = [name for name in TOOL_SPECS
                 if tools is None or name in tools]
        tool_schema = [{"type": "function",
                        "function": {"name": name, **TOOL_SPECS[name]}}
                       for name in names]

        system = system_prompt.strip() or SYSTEM_PROMPT
        # The seed prompt becomes the user message byte for byte. The system
        # message describes only this harness's own tools and is recorded in
        # the artifact, never folded into the seed's text.
        conversation: list[dict] = [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}]
        pending_turns = list(interaction or [])

        turns: list[dict] = []
        captured: list[TurnLogprobs] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        observed: list[str] = []
        finish_reasons: list[str] = []
        error: str | None = None
        unavailable: str | None = None
        assistant_index = 0
        max_iterations = int(self._setting(
            "max_tool_iterations", DEFAULT_MAX_TOOL_ITERATIONS))
        started = time.monotonic()

        try:
            for _ in range(max_iterations):
                completion = self._chat(conversation, tool_schema,
                                        logprobs=logprobs_enabled, top_k=top_k)
                assistant_index += 1
                message = completion["message"]
                served_model = completion.get("model")
                if served_model and served_model not in observed:
                    observed.append(served_model)
                for key, value in (completion.get("usage") or {}).items():
                    if key in usage and isinstance(value, int):
                        usage[key] += value
                if completion.get("finish_reason"):
                    finish_reasons.append(completion["finish_reason"])

                if logprobs_enabled:
                    captured.append(TurnLogprobs(
                        assistant_turn_index=assistant_index,
                        tokens=_extract_tokens(completion, top_k),
                        model=served_model,
                        finish_reason=completion.get("finish_reason"),
                        prompt_token_count=(completion.get("usage") or {}
                                            ).get("prompt_tokens"),
                    ))

                conversation.append(_assistant_message(message))
                calls = message.get("tool_calls") or []
                results = []
                for call in calls:
                    output = self._run_tool(seed, call, workspace, environment)
                    results.append({"role": "tool",
                                    "tool_call_id": call["id"],
                                    "content": output})
                conversation.extend(results)
                turns.append({
                    "assistant_turn_index": assistant_index,
                    "model": served_model,
                    "finish_reason": completion.get("finish_reason"),
                    "usage": completion.get("usage") or {},
                    "message": message,
                    "tool_results": results,
                })
                if calls:
                    continue
                if pending_turns:
                    follow_up = pending_turns.pop(0)
                    conversation.append({"role": "user", "content": follow_up})
                    # Recorded against the turn it follows, so the canonical
                    # message list keeps every operator turn of a multi-turn
                    # seed instead of only the first.
                    turns[-1]["user_follow_up"] = [follow_up]
                    continue
                break
            else:
                error = (f"vllm agent loop hit max_tool_iterations="
                         f"{max_iterations} without finishing")
        except availability.ModelUnavailable as limit:
            unavailable = str(limit)
        except LogprobsUnavailable:
            # Never softened into a trace-level error string: a capture that
            # cannot be trusted must stop the attempt, not produce a trace that
            # looks complete.
            _write_raw(raw_path, seed, self, system, conversation, turns,
                       usage, finish_reasons, logprobs_enabled, top_k, None)
            raise

        sidecar = None
        if logprobs_enabled and captured:
            sidecar = write_sidecar(
                out_dir.parent / "logprobs" / f"{seed['id']}.parquet",
                trajectory_id=str(seed["id"]), turns=captured, top_k=top_k,
                system_prompt=system,
                extra_metadata={"model": self.model, "base_url": self.base_url})

        _write_raw(raw_path, seed, self, system, conversation, turns, usage,
                   finish_reasons, logprobs_enabled, top_k, sidecar)

        attested = bool(observed) and all(
            self.model_matches(name) for name in observed)
        provenance: dict = {"base_url": self.base_url,
                            "assistant_turns": assistant_index,
                            "finish_reasons": finish_reasons}
        if sidecar:
            provenance["logprobs"] = {
                "enabled": True, "top_k": top_k,
                "path": str(Path(sidecar["path"]).relative_to(out_dir.parent.parent)),
                "sha256": sidecar["sha256"], "tokens": sidecar["tokens"],
                "bytes": sidecar["bytes"],
                "assistant_turns": sidecar["assistant_turns"],
                "renormalized": False,
            }
        elif logprobs_enabled:
            provenance["logprobs"] = {"enabled": True, "top_k": top_k,
                                      "tokens": 0}
        return TraceResult(
            raw_path=raw_path, trace_format=TRACE_FORMAT,
            return_code=0 if not (error or unavailable) else 1,
            timed_out=False, duration_s=time.monotonic() - started,
            stream_success=bool(turns) and not error and not unavailable,
            observed_model=observed[0] if observed else None,
            observed_models=observed, model_attested=attested,
            usage=usage, error=error, unavailable=unavailable,
            provenance=provenance)

    # -- tools -------------------------------------------------------------- #
    def _run_tool(self, seed: dict, call: dict, workspace: Path,
                  environment: dict[str, str]) -> str:
        name = call.get("function", {}).get("name") or ""
        raw_arguments = call.get("function", {}).get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as error:
            return f"error: could not parse arguments for {name}: {error}"
        try:
            if name == "bash":
                return self._shell(seed, workspace, environment, 'eval "$1"',
                                   [arguments["command"]])
            if name == "read_file":
                return self._shell(seed, workspace, environment,
                                   'cat -- "$1"', [arguments["path"]])
            if name == "list_dir":
                return self._shell(seed, workspace, environment,
                                   'ls -la -- "$1"',
                                   [arguments.get("path") or "."])
            if name == "write_file":
                code, output = self._run(
                    seed, workspace, environment,
                    'mkdir -p -- "$(dirname -- "$1")" && cat > "$1"',
                    [arguments["path"]], stdin=arguments["content"])
                if code != 0:
                    return f"error: could not write {arguments['path']}: {output.strip()}"
                return f"wrote {arguments['path']}"
            if name == "edit_file":
                return self._edit(seed, workspace, environment, arguments)
        except KeyError as error:
            return f"error: {name} is missing required argument {error}"
        except ToolFailure as error:
            return f"error: {error}"
        return f"error: unknown tool {name!r}"

    def _run(self, seed: dict, workspace: Path, environment: dict[str, str],
             script: str, arguments: list[str], *,
             stdin: str | None = None) -> tuple[int, str]:
        """Run one short script through the workspace-only boundary.

        Reads and writes alike go through here, so the containment the sandbox
        provides is the containment the file tools have -- there is no second
        implementation of "inside the workspace" to disagree with the first.

        The exit status is returned rather than folded into the text: callers
        that need to branch on failure must not have to recognize it by
        reading a message.
        """
        command = self.prepare_trace_command(
            seed, ["bash", "-c", script, "bash", *arguments], workspace,
            environment=environment)
        try:
            completed = run_with_inactivity_timeout(
                command, inactivity_timeout=float(self._setting(
                    "tool_timeout_s", DEFAULT_TOOL_TIMEOUT_S)),
                cwd=str(workspace), input=stdin, capture_output=True,
                text=True, env=environment)
        except subprocess.TimeoutExpired as error:
            raise ToolFailure(
                "command produced no activity for the tool timeout") from error
        output = scrub_text(
            (completed.stdout or "") + (completed.stderr or ""), str(workspace))
        if len(output) > TOOL_OUTPUT_LIMIT:
            output = (output[:TOOL_OUTPUT_LIMIT]
                      + f"\n[truncated at {TOOL_OUTPUT_LIMIT} characters]")
        return completed.returncode, output

    def _shell(self, seed: dict, workspace: Path, environment: dict[str, str],
               script: str, arguments: list[str], *,
               stdin: str | None = None) -> str:
        """The model-facing form: output with a failing exit status appended."""
        code, output = self._run(seed, workspace, environment, script,
                                 arguments, stdin=stdin)
        return output + (f"\n[exit {code}]" if code != 0 else "")

    def _edit(self, seed: dict, workspace: Path, environment: dict[str, str],
              arguments: dict) -> str:
        path = arguments["path"]
        old, new = arguments["old_string"], arguments["new_string"]
        code, current = self._run(seed, workspace, environment,
                                  'cat -- "$1"', [path])
        if code != 0:
            return f"error: could not read {path}: {current.strip()}"
        occurrences = current.count(old)
        if occurrences == 0:
            return f"error: old_string not found in {path}"
        if occurrences > 1:
            return (f"error: old_string appears {occurrences} times in {path}; "
                    f"it must match exactly once")
        code, output = self._run(seed, workspace, environment, 'cat > "$1"',
                                 [path], stdin=current.replace(old, new))
        if code != 0:
            return f"error: could not write {path}: {output.strip()}"
        return f"edited {path}"

    # -- judge -------------------------------------------------------------- #
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        """Review without tools: a judge here reads only what it is given.

        This backend exists to record a teacher's distributions. It is usable
        as a judge for a self-contained instruction, but it does not inspect
        the workspace, so the configured judge should normally stay on a CLI
        harness that can.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            completion = self._chat(
                [{"role": "user",
                  "content": with_verdict_schema(instruction, schema)}],
                [], logprobs=False, top_k=0)
        except availability.ModelUnavailable:
            raise
        # Only the server's own failures become a judge-execution error, which
        # `screen` already budgets. A broader catch would turn a bug in this
        # adapter into a plausible-looking judge fault and re-review it three
        # times before anyone saw the real exception.
        except RuntimeError as error:
            return ReviewResult(raw_text="", verdict=None, return_code=1,
                                duration_s=time.monotonic() - started,
                                error=f"{type(error).__name__}: {error}")
        text = str(completion["message"].get("content") or "")
        (out_dir / f"review-{uuid.uuid4().hex[:8]}.txt").write_text(text)
        return ReviewResult(
            raw_text=text, verdict=parse_json_verdict(text), return_code=0,
            duration_s=time.monotonic() - started,
            observed_model=completion.get("model"),
            model_attested=self.model_matches(completion.get("model")))

    # -- normalization ------------------------------------------------------ #
    @staticmethod
    def parse_stream(path: Path, workspace: str | None
                     ) -> tuple[list[dict], dict]:
        try:
            document = json.loads(Path(path).read_text(errors="replace"))
        except json.JSONDecodeError as error:
            raise ValueError("vllm trace artifact is malformed") from error
        turns = document.get("turns") or []
        if not turns:
            raise ValueError("vllm trace artifact has no assistant turns")

        messages: list[dict] = []
        prompt = document.get("prompt")
        if isinstance(prompt, str):
            messages.append({"role": "user", "content": prompt})
        stats = {"reasoning_blocks": 0, "tool_calls": 0, "tool_results": 0,
                 "finish_reasons": [], "usage": document.get("usage") or {},
                 "cost": 0.0}
        for turn in turns:
            message = turn.get("message") or {}
            assistant: dict = {"role": "assistant",
                               "content": scrub_text(
                                   str(message.get("content") or ""), workspace)}
            reasoning = scrub_text(
                str(message.get("reasoning_content") or ""), workspace)
            if reasoning:
                assistant["reasoning_content"] = reasoning
                stats["reasoning_blocks"] += 1
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                assistant.setdefault("tool_calls", []).append({
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": function.get("name"),
                        "arguments": scrub_text(
                            str(function.get("arguments") or ""), workspace),
                    },
                })
                stats["tool_calls"] += 1
            messages.append(assistant)
            for result in turn.get("tool_results") or []:
                messages.append({"role": "tool",
                                 "tool_call_id": result.get("tool_call_id"),
                                 "content": scrub_text(
                                     str(result.get("content") or ""),
                                     workspace)})
                stats["tool_results"] += 1
            for follow_up in turn.get("user_follow_up") or []:
                messages.append({"role": "user", "content": follow_up})
            if turn.get("finish_reason"):
                stats["finish_reasons"].append(turn["finish_reason"])
        return messages, stats


# -- response handling ------------------------------------------------------ #
def _assistant_message(message: dict) -> dict:
    """The assistant message as it goes back to the server, nothing added."""
    reply: dict = {"role": "assistant",
                   "content": message.get("content") or ""}
    if message.get("reasoning_content"):
        reply["reasoning_content"] = message["reasoning_content"]
    if message.get("tool_calls"):
        reply["tool_calls"] = message["tool_calls"]
    return reply


def _normalize_completion(payload: dict) -> dict:
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(
            f"vLLM returned no choices: {json.dumps(payload)[:500]}")
    choice = choices[0]
    return {
        "message": choice.get("message") or {},
        "logprobs": choice.get("logprobs") or {},
        "finish_reason": choice.get("finish_reason"),
        "model": payload.get("model"),
        "usage": payload.get("usage") or {},
    }


def _collect_stream(response) -> dict:
    """Reassemble one completion from a server-sent-event stream.

    Deltas arrive per token, and the logprob entries arrive with them, so the
    stream is folded back into exactly the shape the non-streaming path
    returns. Everything downstream then has one response format to handle.
    """
    message: dict = {"role": "assistant", "content": ""}
    tool_calls: dict[int, dict] = {}
    content_logprobs: list[dict] = []
    finish_reason = None
    model = None
    usage: dict = {}
    for line in response:
        line = line.decode(errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"vLLM stream chunk is not JSON: {data[:200]}") from error
        model = chunk.get("model") or model
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                message["content"] += delta["content"]
            if delta.get("reasoning_content"):
                message["reasoning_content"] = (
                    message.get("reasoning_content", "")
                    + delta["reasoning_content"])
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index", 0))
                slot = tool_calls.setdefault(
                    index, {"id": None, "type": "function",
                            "function": {"name": "", "arguments": ""}})
                if call.get("id"):
                    slot["id"] = call["id"]
                function = call.get("function") or {}
                if function.get("name"):
                    slot["function"]["name"] += function["name"]
                if function.get("arguments"):
                    slot["function"]["arguments"] += function["arguments"]
            entries = (choice.get("logprobs") or {}).get("content") or []
            content_logprobs.extend(entries)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
    if tool_calls:
        message["tool_calls"] = [tool_calls[index]
                                 for index in sorted(tool_calls)]
    return {"message": message, "logprobs": {"content": content_logprobs},
            "finish_reason": finish_reason, "model": model, "usage": usage}


def _token_id(token: object, where: str) -> int:
    """Read the integer vocabulary id, or refuse to guess one."""
    text = str(token or "")
    if not text.startswith(TOKEN_ID_PREFIX):
        raise LogprobsUnavailable(
            f"the vLLM server ignored return_tokens_as_token_ids and returned "
            f"the decoded token {text!r} at {where}. Moonshiner will not "
            f"re-tokenize it: a tokenizer that disagrees with the server by "
            f"one token misaligns every target after it, and nothing reports "
            f"that. Use a vLLM new enough to support "
            f"return_tokens_as_token_ids, or turn "
            f"runtimes.vllm.logprobs.enabled off.")
    try:
        return int(text[len(TOKEN_ID_PREFIX):])
    except ValueError as error:
        raise LogprobsUnavailable(
            f"malformed token id {text!r} at {where}") from error


def _extract_tokens(completion: dict, top_k: int) -> list[GeneratedToken]:
    """Turn one response's logprobs into per-token records, or fail."""
    entries = (completion.get("logprobs") or {}).get("content")
    if not entries:
        raise LogprobsUnavailable(
            "logprobs capture is enabled but the vLLM response carried no "
            "logprobs. The server must be started with --max-logprobs set and "
            "must support the OpenAI logprobs fields on chat completions.")
    tokens: list[GeneratedToken] = []
    for index, entry in enumerate(entries):
        where = f"token {index}"
        alternatives = entry.get("top_logprobs") or []
        tokens.append(GeneratedToken(
            token_id=_token_id(entry.get("token"), where),
            logprob=float(entry.get("logprob")),
            top_token_ids=[_token_id(item.get("token"), f"{where} alternative")
                           for item in alternatives],
            # Left exactly as the server reported them: the top-K is a
            # truncated head whose missing mass the KL objective needs.
            top_logprobs=[float(item.get("logprob")) for item in alternatives],
        ))
    generated = (completion.get("usage") or {}).get("completion_tokens")
    if isinstance(generated, int) and generated != len(tokens):
        raise LogprobsUnavailable(
            f"the server reported {generated} generated tokens but returned "
            f"logprobs for {len(tokens)}; the sidecar would be misaligned "
            f"against the assistant turn")
    return tokens


def _write_raw(path: Path, seed: dict, runtime: VLLMRuntime, system: str,
               conversation: list[dict], turns: list[dict], usage: dict,
               finish_reasons: list[str], logprobs_enabled: bool,
               top_k: int, sidecar: dict | None) -> None:
    prompt = next((message["content"] for message in conversation
                   if message.get("role") == "user"), "")
    document = {
        "format": TRACE_FORMAT,
        "seed": seed["id"],
        "model": runtime.model,
        "base_url": runtime.base_url,
        "sampling": runtime._sampling(),
        # Recorded, not published: canonical trace messages omit a
        # harness-owned system prompt, and a distillation consumer still needs
        # the exact context the distributions were produced under.
        "system_prompt": system,
        "prompt": prompt,
        "turns": turns,
        "usage": usage,
        "finish_reasons": finish_reasons,
        "logprobs": {
            "enabled": logprobs_enabled,
            "top_k": top_k if logprobs_enabled else None,
            "renormalized": False,
            "sidecar": sidecar,
        },
    }
    path.write_text(json.dumps(document, indent=2))
