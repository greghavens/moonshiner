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

from common import scrub_text
from runtimes import availability
from runtimes.base import (ReviewResult, Runtime, TraceResult,
                           parse_json_verdict, run_with_inactivity_timeout,
                           with_verdict_schema,
                           workspace_only_command)

REFUSAL_MARKERS = ("model_refusal_no_fallback", "model_refusal")
READ_ONLY_DISALLOW = "Edit Write NotebookEdit Bash MultiEdit"
CREDENTIAL_NAME = ".credentials.json"


def credential_home() -> Path:
    """The directory Claude Code reads its login from on this host."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def account_credential() -> Path | None:
    """The login file Claude Code will actually read, if there is one.

    Shared by ``preflight`` and ``_auth_bindings`` on purpose: a check that
    resolves a different path than the bind is worse than no check at all,
    because it reports health for a file the sandbox never receives.
    """
    candidate = credential_home() / CREDENTIAL_NAME
    return candidate if candidate.is_file() else None


def displaced_credentials() -> list[Path]:
    """Logins sitting beside the config directory rather than inside it.

    Renaming ``~/.claude`` (to ``.claude-broken``, ``.claude.bak``, …) leaves a
    perfectly good login intact but invisible: the CLI answers "Not logged in",
    and the trace queue only finds out mid-run, as an infrastructure failure
    that stops everything. Naming the file that does exist turns an archaeology
    session into a one-line repair.
    """
    home, active = Path.home(), credential_home().resolve()
    found = []
    for root in sorted(home.glob(".claude*")):
        if not root.is_dir() or root.resolve() == active:
            continue
        candidate = root / CREDENTIAL_NAME
        if candidate.is_file():
            found.append(candidate)
    return found


def adopt_credential(source: Path) -> Path:
    """Install a login where both the CLI and the sandbox bind will find it."""
    destination = credential_home() / CREDENTIAL_NAME
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.write_bytes(source.read_bytes())
    destination.chmod(0o600)
    return destination

class ClaudeCodeRuntime(Runtime):
    name = "claude-code"
    trace_formats = ("claude-stream-json",)

    def trace_capabilities(self) -> frozenset[str]:
        capabilities = {"workspace_write", "multi_turn", "seed_scoped_mcp"}
        disallowed = self.runtime_config.get("disallowed_tools") or ""
        if isinstance(disallowed, str):
            disallowed_names = set(disallowed.split())
        else:
            disallowed_names = {str(name) for name in disallowed}
        if not {"WebSearch", "WebFetch"} & disallowed_names:
            capabilities.add("live_web_research")
        return frozenset(capabilities)

    # -- lifecycle ---------------------------------------------------------- #
    def preflight(self, *, require_auth: bool = False) -> None:
        cli = self.runtime_config.get("cli", "claude")
        if shutil.which(cli) is None:
            raise SystemExit(f"claude CLI not found on PATH: {cli!r}")
        try:
            subprocess.run([cli, "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            raise SystemExit(f"claude CLI unusable: {error}") from error
        # Every other adapter fails preflight when its credential is absent;
        # this one used to accept require_auth and ignore it, so `doctor`
        # reported a ready harness and the queue discovered the truth only
        # after claiming a seed — where it counts as an infrastructure failure
        # and stops the run.
        if require_auth and account_credential() is None \
                and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            lines = ["claude-code not authenticated "
                     f"({credential_home() / CREDENTIAL_NAME} missing)"]
            lines += [f"  a login is sitting at {path}"
                      for path in displaced_credentials()]
            lines.append("  repair with: moonshiner auth set claude-code")
            raise SystemExit("\n".join(lines))

    def trace_probe_command(self) -> list[str]:
        return [str(self.runtime_config.get("cli", "claude")), "--version"]

    def oci_runtime_command(
            self, command: list[str], workspace: Path
            ) -> tuple[list[str], tuple[tuple[Path, Path], ...]]:
        cli = shutil.which(command[0])
        if cli is None:
            raise FileNotFoundError(command[0])
        source = Path(cli).resolve()
        destination = workspace / ".sandbox-home" / "native-runtime" / "claude"
        return [str(destination), *command[1:]], ((source, destination),)

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
        cmd = self._base_cmd()
        cmd += ["--append-system-prompt", system_prompt]

        mcp_config = (seed.get("tool_harness") or {}).get("mcp_config")
        if mcp_config:
            mcp_path = seed["_dir"] / mcp_config if "_dir" in seed else Path(mcp_config)
            cmd += ["--mcp-config", str(mcp_path), "--strict-mcp-config"]

        stdin_text, streaming = self._teacher_input(prompt, interaction)
        if streaming:
            cmd += ["--input-format", "stream-json", "--replay-user-messages"]
        environment = self.teacher_environment(workspace)
        self._add_host_auth(environment)
        cmd = self.prepare_trace_command(
            seed, cmd, workspace, environment=environment,
            read_only_binds=self._auth_bindings(environment))

        events_path = out_dir / f"{seed['id']}.jsonl"
        stderr_path = out_dir / f"{seed['id']}.stderr"
        timeout = int(self.role.get("timeout_s", 3600))

        started = time.monotonic()
        timed_out = False
        try:
            proc = run_with_inactivity_timeout(
                                  cmd, cwd=workspace, input=stdin_text,
                                  capture_output=True, text=True,
                                  inactivity_timeout=timeout,
                                  env=environment)
            return_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out, return_code = True, None
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        duration = time.monotonic() - started

        events_path.write_text(stdout)
        stderr_path.write_text(stderr)
        meta = self._result_meta(stdout, stderr)
        limit = availability.find_usage_limit(stderr, meta["error"])

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
            unavailable=limit,
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

    @staticmethod
    def _auth_bindings(environment: dict[str, str]
                       ) -> tuple[tuple[Path, Path], ...]:
        source = account_credential()
        if source is None:
            return ()
        # The source is resolved against the host; the destination against the
        # sandbox's own CLAUDE_CONFIG_DIR, which points inside the workspace.
        destination = Path(environment["CLAUDE_CONFIG_DIR"]) / CREDENTIAL_NAME
        return ((source, destination),)

    @staticmethod
    def _add_host_auth(environment: dict[str, str]) -> None:
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if token:
            environment["CLAUDE_CODE_OAUTH_TOKEN"] = token

    def _result_meta(self, stdout: str, stderr: str) -> dict:
        observed_model = None
        session_id = None
        init_tools: list[str] = []
        usage: dict = {}
        error = None
        success = False
        safeguard = any(marker in stdout or marker in stderr
                        for marker in REFUSAL_MARKERS)
        for line in stdout.split("\n"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                observed_model = event.get("model") or observed_model
                session_id = event.get("session_id") or session_id
                init_tools = event.get("tools") or init_tools
            elif event.get("type") == "assistant":
                message = event.get("message") or {}
                observed_model = message.get("model") or observed_model
                if event.get("is_api_error_message") or event.get("error"):
                    content = message.get("content") or []
                    text = " ".join(
                        str(item.get("text") or "") for item in content
                        if isinstance(item, dict)).strip()
                    error = text or str(event.get("error") or "API error")
            elif event.get("type") == "result":
                usage = event.get("usage") or usage
                subtype = event.get("subtype", "")
                success = subtype == "success"
                if "refusal" in subtype:
                    safeguard = True
                if event.get("is_error") or "error" in subtype:
                    error = str(event.get("result") or error or subtype
                                or "result error")
        return {"observed_model": observed_model, "session_id": session_id,
                "init_tools": init_tools, "usage": usage, "error": error,
                "success": success, "safeguard_refusal": safeguard}

    # -- judge -------------------------------------------------------------- #
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        workspace = self.require_persistent_workspace(workspace)
        cmd = self._base_cmd(disallowed=READ_ONLY_DISALLOW if read_only else None)
        prompt = with_verdict_schema(instruction, schema)
        environment = self.teacher_environment(workspace)
        self._add_host_auth(environment)
        cmd = workspace_only_command(
            cmd, workspace,
            read_only_binds=self._auth_bindings(environment))
        started = time.monotonic()
        timed_out = False
        try:
            proc = run_with_inactivity_timeout(cmd, cwd=workspace, input=prompt,
                                  capture_output=True, text=True,
                                  inactivity_timeout=int(self.role.get("timeout_s", 1800)),
                                  env=environment)
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
        limit = availability.find_usage_limit(stderr, meta["error"])
        if limit:
            raise availability.ModelUnavailable(f"{self.name}: {limit}")
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
        for line in stdout.split("\n"):
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
        for line in path.read_text(errors="replace").split("\n"):
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

_parse_json = parse_json_verdict
