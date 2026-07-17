"""Pi coding-agent (Z.ai GLM) teacher and judge adapter.

Two isolation layers protect the metered provider key. First, a host-side
loopback proxy (:mod:`runtimes.zai_proxy`) holds the real key and hands the
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

from common import ROOT, fn, schemas_for, scrub_text, stub
from runtimes.auth import load_provider_key
from runtimes.base import ReviewResult, Runtime, TraceResult
from runtimes.zai_proxy import DUMMY_TOKEN, ProxySession

RUNTIME_ROOT = Path("/var/tmp/moonshiner-pi-runtime")
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

    # -- lifecycle ---------------------------------------------------------- #
    def preflight(self, *, require_auth: bool = False) -> None:
        cli = ROOT / self.runtime_config.get("cli", "node_modules/.bin/pi")
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

    # -- runtime dir + sandbox --------------------------------------------- #
    def _prepare_runtime(self, runtime_dir: Path, proxy_base_url: str) -> None:
        (runtime_dir / "home").mkdir(parents=True, exist_ok=True)
        (runtime_dir / "run").mkdir(parents=True, exist_ok=True)
        config = runtime_dir / "config"
        config.mkdir(parents=True, exist_ok=True)
        models = {"providers": {self.runtime_config.get("provider", "zai"): {
            "baseUrl": proxy_base_url,
            "apiKey": DUMMY_TOKEN,
            "models": [self.role["model"]],
        }}}
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
                "--ro-bind", str(ROOT / "node_modules"),
                str(runtime_dir / "node_modules"),
                "--setenv", "HOME", str(runtime_dir / "home"),
                "--setenv", "USER", "moonshiner-agent",
                "--setenv", "LOGNAME", "moonshiner-agent",
                "--setenv", "XDG_RUNTIME_DIR", str(runtime_dir / "run"),
                "--chdir", str(workspace), "--"]
        return cmd + inner

    def _pi_cmd(self, runtime_dir: Path, *, system_prompt: str,
                tools: list[str] | None, schema_path: Path | None,
                read_only: bool) -> list[str]:
        cli = str(runtime_dir / "node_modules" / ".bin" / "pi")
        cmd = [cli, "--mode", "json",
               "--provider", self.runtime_config.get("provider", "zai"),
               "--model", self.role["model"],
               "--thinking", self.role.get("reasoning", "max"),
               "--system-prompt", system_prompt,
               "--config-dir", str(runtime_dir / "config"),
               "--session-dir", str(runtime_dir / "home" / "session"),
               "--offline", "--no-skills", "--no-context-files", "--no-approve"]
        if tools is not None:
            cmd += ["--tools", ",".join(tools)]
        if not read_only:
            cmd += ["--extension", str(runtime_dir / "config" / "bash-timeout-guard.js")]
        if schema_path is not None:
            cmd += ["--output-schema", str(schema_path)]
        return cmd

    def _child_env(self) -> dict:
        """Strip every real credential; the child only reaches the proxy."""
        env = dict(os.environ)
        for name in ("ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                     "CLAUDE_CODE_OAUTH_TOKEN", "HF_TOKEN"):
            env.pop(name, None)
        return env

    # -- teacher ------------------------------------------------------------ #
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
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
        proxy = ProxySession(self.runtime_config["base_url"], real_key).start()
        started = time.monotonic()
        timed_out = False
        try:
            self._prepare_runtime(runtime_dir, proxy.base_url)
            schema_path = None
            if schema is not None:
                schema_path = runtime_dir / "config" / "schema.json"
                schema_path.write_text(json.dumps(schema))
            inner = self._pi_cmd(runtime_dir, system_prompt=system_prompt,
                                  tools=tools, schema_path=schema_path,
                                  read_only=read_only)
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
        stderr_path.write_text(stderr)
        meta = _parse_stream_meta(stdout)
        attested = _model_attested(self.role["model"], meta, audit)
        shutil.rmtree(runtime_dir, ignore_errors=True)

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
                "provider": self.runtime_config.get("display_provider", "z.ai"),
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
        result = self._run(prompt=instruction, workspace=workspace, out_dir=out_dir,
                           system_prompt="You are an independent read-only reviewer.",
                           tools=["read", "grep", "find", "ls"], schema=schema,
                           read_only=True, artifact_id="judge")
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
        names: list[str] = []
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
