"""Codex (ChatGPT-backed ``codex exec``) teacher and judge adapter.

Teacher runs stream events to stdout (captured as ``<id>.events.jsonl``) and
also persist a full rollout under ``~/.codex/sessions``; we copy that rollout
next to the trace as the primary buildable artifact (``trace_format`` =
``codex-rollout``), falling back to the event stream (``codex-exec-events``)
when no rollout is found. The judge runs the same CLI read-only with an
``--output-schema`` so the verdict arrives as a validated JSON object.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from common import SECRET_RE, scrub_text
from runtimes import availability
from runtimes.base import ReviewResult, Runtime, TraceResult

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"

def _scrub_env() -> dict:
    """Allowlist non-secret process state; Codex uses its auth file."""
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "LC_ALL", "TERM")
            if k in os.environ}


class CodexRuntime(Runtime):
    name = "codex"
    trace_formats = ("codex-rollout", "codex-exec-events")

    # -- lifecycle ---------------------------------------------------------- #
    def preflight(self, *, require_auth: bool = False) -> None:
        cli = self.runtime_config.get("cli", "codex")
        if shutil.which(cli) is None:
            raise SystemExit(f"codex CLI not found on PATH: {cli!r}")
        try:
            subprocess.run([cli, "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise SystemExit(f"codex CLI unusable: {error}") from error
        if require_auth and not (Path.home() / ".codex" / "auth.json").exists():
            raise SystemExit("codex not authenticated (~/.codex/auth.json missing)")

    # -- command construction ---------------------------------------------- #
    def _base_cmd(self, *, sandbox: str, schema_path: Path | None = None) -> list[str]:
        cli = self.runtime_config.get("cli", "codex")
        cmd = [cli, "exec", "--json", "--model", self.role["model"]]
        reasoning = self.role.get("reasoning")
        if reasoning:
            cmd += ["-c", f'model_reasoning_effort="{reasoning}"']
        if self.runtime_config.get("ignore_user_config", True):
            cmd.append("--ignore-user-config")
        if self.runtime_config.get("ignore_rules", True):
            cmd.append("--ignore-rules")
        if sandbox == "danger-full-access":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd += ["--sandbox", sandbox]
        if schema_path is not None:
            cmd += ["--output-schema", str(schema_path)]
        return cmd

    # -- teacher ------------------------------------------------------------ #
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        workspace = self.require_persistent_workspace(workspace)
        availability.require_available(self.name)
        sandbox = self.runtime_config.get("sandbox", "workspace-write")
        if security:
            sandbox = "workspace-write"
        cmd = self._base_cmd(sandbox=sandbox)
        if self.runtime_config.get("web_search") == "live" and not security:
            # Append configuration as a complete option/value pair. Inserting
            # at index 4 splits ``--model <name>`` and makes Codex report that
            # --model has no value.
            cmd += ["-c", 'web_search="live"']
        cmd += ["-C", str(workspace), "-"]

        full_prompt = f"{system_prompt}\n\n{prompt}"
        events_path = out_dir / f"{seed['id']}.events.jsonl"
        stderr_path = out_dir / f"{seed['id']}.stderr"
        timeout = int(self.role.get("timeout_s", 3600))

        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd, cwd=workspace, input=full_prompt, capture_output=True,
                text=True, timeout=timeout, env=_scrub_env())
            return_code = proc.returncode
            stdout, stderr = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            return_code = None
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.monotonic() - started

        events_path.write_text(stdout)
        stderr_path.write_text(stderr)
        thread_id, usage, error, messages = self._scan_events(stdout)

        block = availability.record_block(self.name, stderr, "codex-teacher-stderr")
        if not block and error:
            block = availability.record_block(self.name, error, "codex-teacher-turn")

        raw_path = out_dir / f"{seed['id']}.jsonl"
        trace_format = "codex-exec-events"
        rollout = self._find_rollout(thread_id) if thread_id else None
        if rollout is not None:
            shutil.copyfile(rollout, raw_path)
            trace_format = "codex-rollout"
        else:
            raw_path.write_text(stdout)
        observed_models = _observed_models(stdout)
        if rollout is not None:
            observed_models += [m for m in _observed_models(rollout.read_text(errors="replace"))
                                if m not in observed_models]
        observed_model = observed_models[0] if observed_models else None

        return TraceResult(
            raw_path=raw_path,
            trace_format=trace_format,
            return_code=return_code,
            timed_out=timed_out,
            duration_s=duration,
            stream_success=bool(messages) and not error and not timed_out,
            observed_model=observed_model,
            observed_models=observed_models,
            model_attested=any(self.model_matches(model) for model in observed_models),
            usage=usage,
            error=error,
            unavailable=(f"codex usage limit until {block['retry_at']}"
                         if block else None),
            provenance={"thread_id": thread_id, "sandbox": sandbox,
                        "reasoning": self.role.get("reasoning")},
        )

    def _scan_events(self, stdout: str) -> tuple[str | None, dict, str | None, list]:
        thread_id = None
        usage: dict = {}
        error = None
        messages = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "thread.started":
                thread_id = event.get("thread_id") or thread_id
            elif kind == "turn.completed":
                usage = event.get("usage") or usage
            elif kind in {"turn.failed", "error"}:
                detail = event.get("error") or event.get("message") or {}
                error = detail.get("message") if isinstance(detail, dict) else str(detail)
            elif kind == "item.completed":
                item = event.get("item") or {}
                if item.get("type") in {"assistant_message", "agent_message"}:
                    messages.append(item.get("text", ""))
        return thread_id, usage, error, messages

    def _find_rollout(self, thread_id: str) -> Path | None:
        if not CODEX_SESSIONS.is_dir():
            return None
        matches = [p for p in CODEX_SESSIONS.rglob("rollout-*.jsonl")
                   if thread_id in p.name]
        if not matches:
            matches = [p for p in CODEX_SESSIONS.rglob("*.jsonl")
                       if thread_id in p.read_text(errors="ignore")[:4000]]
        return max(matches, key=lambda p: p.stat().st_mtime) if matches else None

    # -- judge -------------------------------------------------------------- #
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        workspace = self.require_persistent_workspace(workspace)
        availability.require_available(self.name)
        schema_path = None
        if schema is not None:
            schema_path = out_dir / "review.schema.json"
            schema_path.write_text(json.dumps(schema))
        sandbox = "read-only" if read_only else "workspace-write"
        cmd = self._base_cmd(sandbox=sandbox, schema_path=schema_path)
        cmd += ["-C", str(workspace), "-"]

        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd, cwd=workspace, input=instruction, capture_output=True,
                text=True, timeout=int(self.role.get("timeout_s", 1800)),
                env=_scrub_env())
            return_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out, return_code = True, None
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.monotonic() - started

        (out_dir / "judge.events.jsonl").write_text(stdout)
        (out_dir / "judge.stderr").write_text(stderr)
        availability.record_block(self.name, stderr, "codex-judge-stderr")

        last_message = self._last_message(stdout)
        verdict = _parse_json_object(last_message)
        thread_id, _, event_error, _ = self._scan_events(stdout)
        observed_models = _observed_models(stdout)
        rollout = self._find_rollout(thread_id) if thread_id else None
        if rollout:
            observed_models += [model for model in _observed_models(
                rollout.read_text(errors="replace")) if model not in observed_models]
        observed_model = observed_models[0] if observed_models else None
        return ReviewResult(
            raw_text=last_message,
            verdict=verdict,
            return_code=return_code,
            timed_out=timed_out,
            duration_s=duration,
            observed_model=observed_model,
            model_attested=any(self.model_matches(model) for model in observed_models),
            error=event_error or (stderr.strip() if return_code not in (0, None) else None),
        )

    def _last_message(self, stdout: str) -> str:
        last = ""
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "item.completed":
                item = event.get("item") or {}
                if item.get("type") in {"assistant_message", "agent_message"}:
                    last = item.get("text", last)
        return last

    # -- normalization ------------------------------------------------------ #
    @staticmethod
    def parse_stream(path: Path, workspace: str | None) -> tuple[list[dict], dict]:
        text = path.read_text(errors="replace")
        first = next((json.loads(l) for l in text.splitlines() if l.strip()), {})
        if "payload" in first or first.get("type") in {"response_item",
                                                        "session_meta", "turn_context"}:
            return _parse_rollout(text, workspace)
        return _parse_exec_events(text, workspace)

# --------------------------------------------------------------------------- #
# Rollout / event-stream normalizers                                          #
# --------------------------------------------------------------------------- #
def _parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start, depth = None, 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    start = None
    return None


def _observed_models(text: str) -> list[str]:
    """Extract only explicit model fields from Codex event/rollout JSON."""
    found: list[str] = []
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"model", "model_id"} and isinstance(child, str) and child and child not in found:
                    found.append(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    for line in text.splitlines():
        try: visit(json.loads(line))
        except json.JSONDecodeError: continue
    return found


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for chunk in content or []:
        if isinstance(chunk, dict):
            parts.append(chunk.get("text") or chunk.get("reasoning_text") or "")
        else:
            parts.append(str(chunk))
    return "".join(parts)


def _finish_assistant(messages: list[dict], assistant: dict | None) -> None:
    if assistant and (assistant.get("content") or assistant.get("tool_calls")):
        messages.append(assistant)


def _parse_rollout(text: str, workspace: str | None) -> tuple[list[dict], dict]:
    messages: list[dict] = []
    assistant: dict | None = None
    stats = {"reasoning_blocks": 0, "tool_calls": 0}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload", record)
        kind = payload.get("type")
        if kind == "message":
            role = payload.get("role")
            content = scrub_text(_text_of(payload.get("content")), workspace)
            if role == "user":
                _finish_assistant(messages, assistant)
                assistant = None
                messages.append({"role": "user", "content": content})
            elif role == "assistant":
                if assistant is None:
                    assistant = {"role": "assistant", "content": ""}
                assistant["content"] = (assistant.get("content") or "") + content
        elif kind == "reasoning":
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            reasoning = scrub_text(_text_of(
                payload.get("content") or payload.get("summary")), workspace)
            if reasoning:
                assistant["reasoning_content"] = (
                    assistant.get("reasoning_content", "") + reasoning)
                stats["reasoning_blocks"] += 1
        elif kind in {"function_call", "custom_tool_call", "local_shell_call"}:
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            arguments = payload.get("arguments")
            if arguments is None:
                arguments = payload.get("input")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments or {})
            call_id = payload.get("call_id") or payload.get("id") or f"call_{stats['tool_calls']}"
            assistant.setdefault("tool_calls", []).append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": payload.get("name") or "exec",
                    "arguments": scrub_text(arguments, workspace),
                },
            })
            stats["tool_calls"] += 1
        elif kind in {"function_call_output", "custom_tool_call_output",
                      "local_shell_call_output"}:
            _finish_assistant(messages, assistant)
            assistant = None
            output = payload.get("output")
            if isinstance(output, dict):
                output = output.get("content") or json.dumps(output)
            messages.append({
                "role": "tool",
                "tool_call_id": payload.get("call_id") or payload.get("id") or "",
                "content": scrub_text(str(output), workspace),
            })
    _finish_assistant(messages, assistant)
    return messages, stats


def _parse_exec_events(text: str, workspace: str | None) -> tuple[list[dict], dict]:
    messages: list[dict] = []
    assistant: dict | None = None
    stats = {"reasoning_blocks": 0, "tool_calls": 0}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "item.completed":
            continue
        item = event.get("item") or {}
        kind = item.get("type")
        if kind == "reasoning":
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            reasoning = scrub_text(item.get("text", ""), workspace)
            if reasoning:
                assistant["reasoning_content"] = (
                    assistant.get("reasoning_content", "") + reasoning)
                stats["reasoning_blocks"] += 1
        elif kind in {"assistant_message", "agent_message"}:
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            assistant["content"] = (assistant.get("content") or "") + scrub_text(
                item.get("text", ""), workspace)
            _finish_assistant(messages, assistant)
            assistant = None
        elif kind in {"command_execution", "mcp_tool_call", "web_search"}:
            if assistant is None:
                assistant = {"role": "assistant", "content": ""}
            call_id = item.get("id") or f"call_{stats['tool_calls']}"
            name = ("exec" if kind == "command_execution"
                    else "web_search" if kind == "web_search"
                    else item.get("tool") or "mcp")
            arguments = {"command": item.get("command")} if kind == "command_execution" \
                else {"query": item.get("query")} if kind == "web_search" \
                else item.get("arguments") or {}
            assistant.setdefault("tool_calls", []).append({
                "id": call_id, "type": "function",
                "function": {"name": name,
                             "arguments": scrub_text(json.dumps(arguments), workspace)},
            })
            stats["tool_calls"] += 1
            _finish_assistant(messages, assistant)
            assistant = None
            messages.append({
                "role": "tool", "tool_call_id": call_id,
                "content": scrub_text(
                    str(item.get("aggregated_output") or item.get("output") or ""),
                    workspace),
            })
    _finish_assistant(messages, assistant)
    return messages, stats
