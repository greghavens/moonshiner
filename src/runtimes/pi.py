"""Pi coding-agent teacher and judge adapter (any OpenAI-compatible provider).

Two isolation layers protect the metered provider key. First, a host-side
loopback proxy (:mod:`runtimes.credential_proxy`) holds the real key and hands the
child a dummy bearer token. Second, the ``pi`` process runs under ``bwrap`` with
the host home, ``/root``, and the user runtime directory replaced by tmpfs, so a
generated command cannot reach any host credential even if one existed on disk.

A trace is only attested when *both* the Pi event stream reports the expected
provider+model **and** the proxy observed a successful upstream response
carrying that model — a sandbox cannot self-certify its identity.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from common import ROOT, RUNS, fn, schemas_for, scrub_text, stub
from runtimes.auth import load_provider_key
from runtimes.base import ReviewResult, Runtime, TraceResult
from runtimes.credential_proxy import DUMMY_TOKEN, ProxySession

RUNTIME_ROOT = RUNS / "pi-runtime"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 600

TOOL_REGISTRY = {
    "read": fn("read", "Read a file from the workspace.",
               {"path": {"type": "string"}}, ["path"]),
    "write": fn("write", "Write a file in the workspace.",
                {"path": {"type": "string"}, "content": {"type": "string"}},
                ["path", "content"]),
    "edit": fn("edit", "Edit a file in the workspace.",
               {"path": {"type": "string"}, "old": {"type": "string"},
                "new": {"type": "string"}}, ["path", "old", "new"]),
    "bash": fn("bash", "Run a shell command in the workspace.",
               {"command": {"type": "string"}}, ["command"]),
    "grep": fn("grep", "Search file contents in the workspace.",
               {"pattern": {"type": "string"}}, ["pattern"]),
    "find": fn("find", "Find files by name in the workspace.",
               {"pattern": {"type": "string"}}, ["pattern"]),
    "ls": fn("ls", "List a directory in the workspace.",
             {"path": {"type": "string"}}, ["path"]),
}

# The full Pi coding-agent tool surface. Declared so every exported row lists the
# complete action space the teacher had, not just the tools a trace happened to
# call.
OFFERED_TOOLS = ("read", "write", "edit", "bash", "grep", "find", "ls")

# A tiny extension that fails any bash call still running past the ceiling, so a
# hung command cannot consume the whole turn budget.
BASH_TIMEOUT_GUARD = """export default function (pi) {
  const CEIL = %d * 1000;
  pi.on("tool_call", (event) => {
    if (event.toolName === "bash" && !event.input?.timeout) {
      event.input = { ...(event.input || {}), timeout: %d };
    }
  });
}
""" % (MAX_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS * 1000)


def _hidden_mounts() -> list[str]:
    hidden = [str(Path.home())]
    if Path("/root").exists():
        hidden.append("/root")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if Path(runtime).exists():
        hidden.append(runtime)
    return hidden


class PiRuntime(Runtime):
    name = "pi"
    trace_formats = ("pi-coding-agent-json-v3",)

    def _cli_path(self) -> Path:
        configured = Path(self.runtime_config.get("cli", "pi"))
        if configured.is_absolute():
            return configured
        native = shutil.which(configured.name)
        if native:
            return Path(native)
        # Preserve Moonshiner's existing managed-install option. Native PATH
        # resolution always wins; this is only the explicit setup fallback.
        return ROOT / "node_modules" / ".bin" / "pi"

    # -- lifecycle ---------------------------------------------------------- #
    def preflight(self, *, require_auth: bool = False) -> None:
        cli = self._cli_path()
        if not cli.exists():
            raise SystemExit(f"pi CLI not found: {cli} (run npm install)")
        if shutil.which("bwrap") is None:
            raise SystemExit("bwrap (bubblewrap) required for the pi sandbox")
        version = self._runtime_version(cli)
        pinned = self.runtime_config.get("runtime_version")
        if pinned and version and version != pinned:
            raise SystemExit(f"pi version {version} != pinned {pinned}")
        if require_auth:
            load_provider_key(self.runtime_config)

    def _runtime_version(self, cli: Path) -> str | None:
        try:
            out = subprocess.run([str(cli), "--version"], capture_output=True,
                                 text=True, timeout=30)
            return out.stdout.strip() or None
        except (subprocess.SubprocessError, FileNotFoundError):
            return None

    def _provider(self) -> str:
        provider = self.runtime_config.get("provider")
        if not provider:
            raise RuntimeError("runtimes.pi.provider is not configured")
        return provider

    # -- runtime dir + sandbox --------------------------------------------- #
    def _prepare_runtime(self, runtime_dir: Path, proxy_base_url: str) -> None:
        (runtime_dir / "home").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "run").mkdir(parents=True, exist_ok=True)
        config = runtime_dir / "config"
        config.mkdir(parents=True, exist_ok=True)
        # pi 0.80.7 models.json schema: models is a list of OBJECTS with a
        # required "id" (a bare-string list fails validation and pi silently
        # falls back to the built-in provider, bypassing the proxy). api and
        # apiKey are declared explicitly rather than inherited so the entry is
        # self-contained even for a built-in provider name.
        # pi defaults a model's maxTokens to 16384 when the entry omits it;
        # reasoning-max turns overrun that and get truncated with
        # stopReason "length", failing the seed. Provision an output budget
        # sized for max reasoning instead of pi's chat-sized default.
        provider_entry: dict = {
            "baseUrl": proxy_base_url,
            "api": self.runtime_config.get("api", "openai-completions"),
            "apiKey": DUMMY_TOKEN,
            "models": [{"id": self.role["model"], "reasoning": True,
                        "maxTokens": int(self.runtime_config.get(
                            "max_output_tokens", 131072))}],
        }
        thinking_format = self.runtime_config.get("thinking_format")
        if thinking_format:
            provider_entry["compat"] = {"thinkingFormat": thinking_format}
        models = {"providers": {self._provider(): provider_entry}}
        (config / "models.json").write_text(json.dumps(models, indent=2))
        (config / "settings.json").write_text(json.dumps({
            "compaction": {"enabled": False},
            "defaultProjectTrust": "never",
        }, indent=2))
        (config / "bash-timeout-guard.js").write_text(BASH_TIMEOUT_GUARD)

    def _sandbox_cmd(self, inner: list[str], workspace: Path,
                     runtime_dir: Path) -> list[str]:
        cmd = ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc",
               "--unshare-uts", "--unshare-cgroup-try", "--ro-bind", "/", "/"]
        for mount in _hidden_mounts():
            cmd += ["--tmpfs", mount]
        cmd += ["--tmpfs", "/tmp", "--dev-bind", "/dev", "/dev", "--proc", "/proc",
                "--bind", str(workspace), str(workspace),
                "--bind", str(runtime_dir), str(runtime_dir),
                "--setenv", "HOME", str(runtime_dir / "home"),
                "--setenv", "USER", "moonshiner-agent",
                "--setenv", "LOGNAME", "moonshiner-agent",
                "--setenv", "XDG_RUNTIME_DIR", str(runtime_dir / "run"),
                "--setenv", "PI_CODING_AGENT_DIR", str(runtime_dir / "config"),
                "--chdir", str(workspace), "--"]
        return cmd + inner

    def _pi_cmd(self, runtime_dir: Path, *, system_prompt: str,
                tools: list[str] | None, read_only: bool) -> list[str]:
        # pi 0.80.7: the agent config dir (models.json/settings.json) is
        # selected via the PI_CODING_AGENT_DIR env var set in _sandbox_cmd —
        # there is no --config-dir flag, and no --output-schema (a judge
        # verdict is parsed from the last assistant message instead).
        # --print is required for non-interactive mode; the prompt arrives on
        # stdin and is folded into the initial message. --api-key pins the
        # runtime credential to the proxy's dummy token as a second path
        # alongside the models.json apiKey.
        cli = str(self._cli_path())
        cmd = [cli, "--print", "--mode", "json",
               "--provider", self._provider(),
               "--model", self.role["model"],
               "--api-key", DUMMY_TOKEN,
               "--thinking", self.role.get("reasoning", "max"),
               "--system-prompt", system_prompt,
               "--session-dir", str(runtime_dir / "home" / "session"),
               "--offline", "--no-skills", "--no-context-files", "--no-approve"]
        if tools is not None:
            cmd += ["--tools", ",".join(tools)]
        if not read_only:
            cmd += ["--extension", str(runtime_dir / "config" / "bash-timeout-guard.js")]
        return cmd

    def _child_env(self) -> dict:
        """Strip every real credential; the child only reaches the proxy.

        The configured provider key (``key_env``) is stripped generically, so a
        new provider (OpenRouter, Fireworks, …) is covered without editing this
        list — the sandbox only ever sees the loopback proxy's dummy token.
        """
        return {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TERM")
                if k in os.environ}

    # -- teacher ------------------------------------------------------------ #
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        workspace = self.require_persistent_workspace(workspace)
        return self._run(prompt=f"{prompt}", workspace=workspace, out_dir=out_dir,
                         system_prompt=system_prompt, tools=tools,
                         schema=None, read_only=False, artifact_id=seed["id"])

    def _run(self, *, prompt: str, workspace: Path, out_dir: Path,
             system_prompt: str, tools: list[str] | None, schema: dict | None,
             read_only: bool, artifact_id: str) -> TraceResult:
        real_key = load_provider_key(self.runtime_config)
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        runtime_dir = Path(tempfile.mkdtemp(prefix="run-", dir=RUNTIME_ROOT))
        events_path = out_dir / f"{artifact_id}.events.jsonl"
        stderr_path = out_dir / f"{artifact_id}.stderr"
        auth_style = ("x-api-key" if self.runtime_config.get("api") == "anthropic-messages"
                      else "bearer")
        proxy = ProxySession(self.runtime_config["base_url"], real_key,
                             auth_style=auth_style).start()
        started = time.monotonic()
        timed_out = False
        try:
            self._prepare_runtime(runtime_dir, proxy.base_url)
            # `schema` has no CLI channel in pi 0.80.7; judge instructions
            # embed the expected JSON shape and run_review parses the last
            # assistant message.
            inner = self._pi_cmd(runtime_dir, system_prompt=system_prompt,
                                  tools=tools, read_only=read_only)
            cmd = self._sandbox_cmd(inner, workspace, runtime_dir)
            try:
                proc = subprocess.run(
                    cmd, cwd=workspace, input=prompt, capture_output=True,
                    text=True, timeout=int(self.role.get("timeout_s", 3600)),
                    env=self._child_env())
                return_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out, return_code = True, None
                stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            audit = proxy.snapshot()
        finally:
            proxy.stop()
        duration = time.monotonic() - started

        events_path.write_text(stdout)
        # Drop the ~99% of raw bytes that are cumulative *_update snapshots
        # before anything hashes the file, so raw_sha256 matches the stored
        # (compacted) trace and multi-GB captures never hit disk long-term.
        compact_events_file(events_path)
        stderr_path.write_text(stderr)
        meta = _parse_stream_meta(stdout)
        attested = _model_attested(self.role["model"], meta, audit)
        # Retain the project-local runtime/session directory for audit and
        # reproducibility. It contains only the proxy dummy credential; the
        # real provider key never enters the sandbox.

        return TraceResult(
            raw_path=events_path,
            trace_format="pi-coding-agent-json-v3",
            return_code=return_code,
            timed_out=timed_out,
            duration_s=duration,
            stream_success=meta["stream_success"] and not timed_out,
            observed_model=(meta["observed_models"] or [None])[0],
            observed_models=meta["observed_models"],
            model_attested=attested,
            usage=meta["usage"],
            error=meta["error"],
            provenance={
                "session_id": meta["session_id"],
                "provider": self.runtime_config.get("display_provider")
                    or self._provider(),
                "runtime": self.runtime_config.get("runtime", "pi-coding-agent"),
                "runtime_version": self.runtime_config.get("runtime_version"),
                "pi_observed_models": meta["observed_models"],
                "upstream_response_models": audit["response_models"],
                "upstream_audit": audit,
                "credential_boundary":
                    "host-loopback-proxy; child receives dummy token only",
            },
        )

    # -- judge -------------------------------------------------------------- #
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        workspace = self.require_persistent_workspace(workspace)
        review_tools = ["read", "grep", "find", "ls"] if read_only else list(OFFERED_TOOLS)
        result = self._run(prompt=instruction, workspace=workspace, out_dir=out_dir,
                           system_prompt=("You are an independent read-only reviewer."
                                          if read_only else
                                          "You are an independent reviewer allowed to repair this workspace."),
                           tools=review_tools, schema=schema,
                           read_only=read_only, artifact_id="judge")
        last = _last_message(result.raw_path.read_text())
        verdict = _structured_output(result.raw_path.read_text()) or _parse_json(last)
        return ReviewResult(
            raw_text=last,
            verdict=verdict,
            return_code=result.return_code,
            timed_out=result.timed_out,
            duration_s=result.duration_s,
            observed_model=result.observed_model,
            model_attested=result.model_attested,
        )

    # -- normalization ------------------------------------------------------ #
    @staticmethod
    def parse_stream(path: Path, workspace: str | None) -> tuple[list[dict], dict]:
        return _parse_pi_stream(path.read_text(errors="replace"), workspace)

    @staticmethod
    def tool_schemas(messages: list[dict]) -> list[dict]:
        # Start from the full offered surface, then fold in any other tool
        # actually observed in the stream, so the row always carries the
        # complete tool list — not only what this trace happened to call.
        names: list[str] = list(OFFERED_TOOLS)
        for message in messages:
            for call in message.get("tool_calls") or []:
                name = call.get("function", {}).get("name")
                if name and name not in names:
                    names.append(name)
        return schemas_for(names, TOOL_REGISTRY)


# --------------------------------------------------------------------------- #
# Pi event helpers                                                            #
# --------------------------------------------------------------------------- #
def _iter_events(text: str):
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def compact_event_stream(text: str) -> list[dict]:
    """Keep finalized events, drop the *_update snapshot chatter."""
    return [event for event in _iter_events(text)
            if not str(event.get("type", "")).endswith("_update")]


def compact_events_file(path: Path) -> int:
    """Rewrite a raw events file in place, dropping the ``*_update`` snapshot
    chatter that :func:`compact_event_stream` already filters at parse time.

    Pi streams a full *cumulative* ``message_update`` on every token, so one
    large reasoning block is re-serialized thousands of times -- up to ~99% of
    raw bytes that nothing downstream reads (``parse_stream`` consumes
    ``message_end`` only). Compacting before ``raw_sha256`` is computed keeps
    the integrity hash (``screen_traces``) consistent with the stored file.
    Streamed line-by-line so a multi-GB capture never loads whole into memory.
    Returns the number of snapshot events dropped.
    """
    skipped = 0
    tmp = path.with_suffix(path.suffix + ".compact.tmp")
    with path.open(errors="replace") as src, tmp.open("w") as out:
        for line in src:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                out.write(line)
                continue
            if str(event.get("type", "")).endswith("_update"):
                skipped += 1
                continue
            out.write(line)
    os.replace(tmp, path)
    return skipped


def _parse_stream_meta(text: str) -> dict:
    observed: list[str] = []
    session_id = None
    usage: dict = {}
    error = None
    stream_success = False
    for event in _iter_events(text):
        kind = event.get("type")
        if kind == "session":
            session_id = event.get("sessionId") or event.get("session_id")
        message = event.get("message") or {}
        model = message.get("model") or event.get("model")
        if model and model not in observed:
            observed.append(model)
        if kind == "result":
            usage = event.get("usage") or usage
            if event.get("error"):
                error = str(event.get("error"))
        if kind == "message_end" and message.get("role") == "assistant":
            if message.get("stopReason") in {"stop", "end_turn", None}:
                stream_success = True
    return {"observed_models": observed, "session_id": session_id,
            "usage": usage, "error": error, "stream_success": stream_success}


def _model_attested(expected: str, meta: dict, audit: dict) -> bool:
    pi_ok = expected in meta.get("observed_models", [])
    upstream_ok = audit.get("had_success") and expected in audit.get(
        "response_models", [])
    return bool(pi_ok and upstream_ok)


def _last_message(text: str) -> str:
    last = ""
    for event in _iter_events(text):
        message = event.get("message") or {}
        if event.get("type") == "message_end" and message.get("role") == "assistant":
            last = "".join(block.get("text", "")
                           for block in message.get("content", [])
                           if block.get("type") == "text") or last
    return last


def _structured_output(text: str) -> dict | None:
    for event in _iter_events(text):
        if event.get("type") == "result" and isinstance(
                event.get("structuredOutput"), dict):
            return event["structuredOutput"]
    return None


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _parse_pi_stream(text: str, workspace: str | None) -> tuple[list[dict], dict]:
    messages: list[dict] = []
    stats = {"reasoning_blocks": 0, "tool_calls": 0}
    for event in compact_event_stream(text):
        if event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        role = message.get("role")
        content_blocks = message.get("content") or []
        if role == "user":
            text_content = "".join(scrub_text(block.get("text", ""), workspace)
                                   for block in content_blocks
                                   if block.get("type") == "text")
            messages.append({"role": "user", "content": text_content})
        elif role == "assistant":
            assistant: dict = {"role": "assistant", "content": ""}
            for block in content_blocks:
                btype = block.get("type")
                if btype == "text":
                    assistant["content"] += scrub_text(block.get("text", ""), workspace)
                elif btype == "thinking":
                    reasoning = scrub_text(block.get("thinking", ""), workspace)
                    if reasoning:
                        assistant["reasoning_content"] = (
                            assistant.get("reasoning_content", "") + reasoning)
                        stats["reasoning_blocks"] += 1
                elif btype == "toolCall":
                    assistant.setdefault("tool_calls", []).append({
                        "id": block.get("id") or f"call_{stats['tool_calls']}",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "bash",
                            "arguments": scrub_text(
                                json.dumps(block.get("input") or {}), workspace),
                        },
                    })
                    stats["tool_calls"] += 1
            if assistant.get("content") or assistant.get("tool_calls"):
                messages.append(assistant)
        elif role == "tool":
            for block in content_blocks:
                if block.get("type") == "toolResult":
                    messages.append({
                        "role": "tool",
                        "tool_call_id": block.get("toolCallId") or block.get("id") or "",
                        "content": scrub_text(str(block.get("output", "")), workspace),
                    })
    return messages, stats
