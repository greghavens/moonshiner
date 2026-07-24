"""Claude Code (``claude -p`` headless) teacher and judge adapter.

The teacher streams ``stream-json`` events; the ``system/init`` event carries the
model actually loaded, which we compare against the requested model for
attestation. A model safeguard refusal (``model_refusal_no_fallback``) is not a
generation we want to distill and not a failure to retry blindly, so it is
surfaced as ``safeguard_refusal`` for the caller to defer. Multi-turn seeds use
``--input-format stream-json --replay-user-messages``; seed-scoped MCP tools use
``--mcp-config`` + ``--strict-mcp-config``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from common import PAID_RUN_UNLOCK, scrub_text
from runtimes import availability
from runtimes.base import ReviewResult, Runtime, TraceResult

REFUSAL_MARKERS = ("model_refusal_no_fallback", "model_refusal")
READ_ONLY_DISALLOW = "Edit Write NotebookEdit Bash MultiEdit"

class ClaudeCodeRuntime(Runtime):
    name = "claude-code"
    trace_formats = ("claude-stream-json",)

    # -- lifecycle ---------------------------------------------------------- #
    def preflight(self, *, require_auth: bool = False) -> None:
        cli = self.runtime_config.get("cli", "claude")
        if shutil.which(cli) is None:
            raise SystemExit(f"claude CLI not found on PATH: {cli!r}")
        try:
            subprocess.run([cli, "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise SystemExit(f"claude CLI unusable: {error}") from error

    def _require_unlock(self) -> None:
        if not self.runtime_config.get("paid_unlock_required"):
            return
        unlock = os.environ.get("MOONSHINER_CREDITS_UNLOCK")
        if unlock != PAID_RUN_UNLOCK:
            raise SystemExit(
                "claude-code is a paid runtime; export "
                f"MOONSHINER_CREDITS_UNLOCK={PAID_RUN_UNLOCK} to authorize spend")

    # -- command construction ---------------------------------------------- #
    def _base_cmd(self, *, disallowed: str | None = None) -> list[str]:
        cli = self.runtime_config.get("cli", "claude")
        cmd = [cli, "-p", "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions", "--model", self.role["model"]]
        disallowed = disallowed if disallowed is not None else \
            self.runtime_config.get("disallowed_tools")
        if disallowed:
            cmd += ["--disallowedTools", disallowed]
        return cmd

    # -- teacher ------------------------------------------------------------ #
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        workspace = self.require_persistent_workspace(workspace)
        if not self.runtime_config.get("unsafe_host_access", False):
            raise RuntimeError("claude-code teacher disabled until a contained runtime is configured")
        self._require_unlock()
        availability.require_available(self.name)
        cmd = self._base_cmd()
        cmd += ["--append-system-prompt", system_prompt]

        mcp_config = (seed.get("tool_harness") or {}).get("mcp_config")
        if mcp_config:
            mcp_path = seed["_dir"] / mcp_config if "_dir" in seed else Path(mcp_config)
            cmd += ["--mcp-config", str(mcp_path), "--strict-mcp-config"]

        stdin_text, streaming = self._teacher_input(prompt, interaction)
        if streaming:
            cmd += ["--input-format", "stream-json", "--replay-user-messages"]

        events_path = out_dir / f"{seed['id']}.jsonl"
        stderr_path = out_dir / f"{seed['id']}.stderr"
        timeout = int(self.role.get("timeout_s", 3600))

        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(cmd, cwd=workspace, input=stdin_text,
                                  capture_output=True, text=True, timeout=timeout,
                                  env=self.teacher_environment(workspace))
            return_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out, return_code = True, None
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.monotonic() - started

        events_path.write_text(stdout)
        stderr_path.write_text(stderr)
        meta = self._result_meta(stdout, stderr)
        block = availability.record_block(self.name, stderr, "claude-teacher-stderr")

        model_fallback = bool(meta["observed_model"]
                              and not self.model_matches(meta["observed_model"]))
        return TraceResult(
            raw_path=events_path,
            trace_format="claude-stream-json",
            return_code=return_code,
            timed_out=timed_out,
            duration_s=duration,
            stream_success=meta["success"] and not timed_out,
            observed_model=meta["observed_model"],
            observed_models=[meta["observed_model"]] if meta["observed_model"] else [],
            model_attested=not model_fallback and not meta["safeguard_refusal"],
            model_fallback=model_fallback,
            safeguard_refusal=meta["safeguard_refusal"],
            usage=meta["usage"],
            error=meta["error"],
            unavailable=(f"claude usage limit until {block['retry_at']}"
                         if block else None),
            provenance={"session_id": meta["session_id"],
                        "init_tools": meta["init_tools"]},
        )

    def _teacher_input(self, prompt: str,
                       interaction: list[str] | None) -> tuple[str, bool]:
        if not interaction:
            return prompt, False
        lines = []
        for turn in [prompt, *interaction]:
            lines.append(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": turn},
            }))
        return "\n".join(lines) + "\n", True

    def _result_meta(self, stdout: str, stderr: str) -> dict:
        observed_model = None
        session_id = None
        init_tools: list[str] = []
        usage: dict = {}
        error = None
        success = False
        safeguard = any(marker in stdout or marker in stderr
                        for marker in REFUSAL_MARKERS)
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                observed_model = event.get("model") or observed_model
                session_id = event.get("session_id") or session_id
                init_tools = event.get("tools") or init_tools
            elif event.get("type") == "assistant":
                observed_model = (event.get("message") or {}).get(
                    "model") or observed_model
            elif event.get("type") == "result":
                usage = event.get("usage") or usage
                subtype = event.get("subtype", "")
                success = subtype == "success"
                if "refusal" in subtype:
                    safeguard = True
                if event.get("is_error") or "error" in subtype:
                    error = subtype or "result error"
        return {"observed_model": observed_model, "session_id": session_id,
                "init_tools": init_tools, "usage": usage, "error": error,
                "success": success, "safeguard_refusal": safeguard}

    # -- judge -------------------------------------------------------------- #
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        workspace = self.require_persistent_workspace(workspace)
        self._require_unlock()
        availability.require_available(self.name)
        cmd = self._base_cmd(disallowed=READ_ONLY_DISALLOW if read_only else None)
        prompt = instruction
        if schema is not None:
            prompt += ("\n\nReturn ONLY a single JSON object matching this schema, "
                       "with no prose:\n" + json.dumps(schema))
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(cmd, cwd=workspace, input=prompt,
                                  capture_output=True, text=True,
                                  timeout=int(self.role.get("timeout_s", 1800)),
                                  env={k: os.environ[k]
                                       for k in ("PATH", "HOME", "LANG",
                                                 "LC_ALL", "TERM")
                                       if k in os.environ})
            return_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out, return_code = True, None
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.monotonic() - started

        # Per-workspace names: a later review must not overwrite the stream
        # needed to adjudicate this one.
        (out_dir / f"{workspace.name}.judge.jsonl").write_text(stdout)
        (out_dir / f"{workspace.name}.judge.stderr").write_text(stderr)
        meta = self._result_meta(stdout, stderr)
        last = self._final_text(stdout)
        return ReviewResult(
            raw_text=last,
            verdict=_parse_json(last),
            return_code=return_code,
            timed_out=timed_out,
            duration_s=duration,
            observed_model=meta["observed_model"],
            model_attested=self.model_matches(meta["observed_model"]),
        )

    def _final_text(self, stdout: str) -> str:
        last = ""
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in (event.get("message") or {}).get("content", []):
                    if block.get("type") == "text":
                        last = block.get("text", last)
            elif event.get("type") == "result" and event.get("result"):
                last = event["result"]
        return last

    # -- normalization ------------------------------------------------------ #
    @staticmethod
    def parse_stream(path: Path, workspace: str | None) -> tuple[list[dict], dict]:
        messages: list[dict] = []
        stats = {"reasoning_blocks": 0, "tool_calls": 0}
        pending_tools: dict[str, str] = {}
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "assistant":
                assistant: dict = {"role": "assistant", "content": ""}
                for block in (event.get("message") or {}).get("content", []):
                    btype = block.get("type")
                    if btype == "text":
                        assistant["content"] += scrub_text(block.get("text", ""), workspace)
                    elif btype == "thinking":
                        reasoning = scrub_text(block.get("thinking", ""), workspace)
                        if reasoning:
                            assistant["reasoning_content"] = (
                                assistant.get("reasoning_content", "") + reasoning)
                            stats["reasoning_blocks"] += 1
                    elif btype == "tool_use":
                        assistant.setdefault("tool_calls", []).append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", "tool"),
                                "arguments": scrub_text(
                                    json.dumps(block.get("input") or {}), workspace),
                            },
                        })
                        stats["tool_calls"] += 1
                if assistant.get("content") or assistant.get("tool_calls"):
                    messages.append(assistant)
            elif kind == "user":
                for block in (event.get("message") or {}).get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        content = block.get("content")
                        if isinstance(content, list):
                            content = "".join(chunk.get("text", "")
                                              for chunk in content
                                              if isinstance(chunk, dict))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": scrub_text(str(content or ""), workspace),
                        })
                    elif isinstance(block, dict) and block.get("type") == "text":
                        messages.append({"role": "user",
                                         "content": scrub_text(block.get("text", ""), workspace)})
                    elif isinstance(block, str):
                        messages.append({"role": "user",
                                         "content": scrub_text(block, workspace)})
        return messages, stats

def _parse_json(text: str) -> dict | None:
    """Extract the judge's JSON verdict from its final text.

    Judges wrap the verdict in prose or code fences, and sometimes emit a
    preliminary object before the definitive one — a first-{-to-last-}
    substring spans both and parses as nothing, rejecting a good trace.
    Scan top-level objects with ``raw_decode`` (string-aware) and keep the
    last one that parses: the final answer.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    result = None
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
            if isinstance(value, dict):
                result = value
            index = text.find("{", max(end, index + 1))
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
    return result
