#!/usr/bin/env python3
"""Contained Codex runtime used by the security trace collector.

The CLI runs inside an outer Bubblewrap filesystem namespace that hides the
user's real home (both the repositories and the saved auth) and re-binds only a
project-local retained workspace plus isolated Codex state. Authentication is copied into
that state before launch and unlinked as soon as ``thread.started`` is observed,
before any model-generated command can run. Codex keeps the already-loaded
session credential in its parent process, while the command sandbox has no
credential file to steal.

The isolated ``CODEX_HOME`` and teacher workspace live under the project's
``.moonshiner/runs`` tree and are retained for audit. They are relocatable with
``MOONSHINER_SECURITY_RUNTIME_ROOT`` / ``MOONSHINER_SECURITY_WORK_ROOT``.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from common import RUNS

ROOT = Path(__file__).resolve().parent.parent
SECURITY = ROOT / "security"
RUNTIME = SECURITY / "runtime"


def _real_codex_home() -> Path:
    explicit = os.environ.get("MOONSHINER_AUTH_HOME")
    return Path(explicit).expanduser() if explicit else Path.home() / ".codex"


def _find_rollout(codex_home: Path, thread_id: str | None) -> Path | None:
    if not thread_id:
        return None
    matches: list[Path] = []
    for base in (codex_home / "sessions", codex_home / "archived_sessions"):
        if base.exists():
            matches.extend(base.glob(f"**/*-{thread_id}.jsonl"))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def _safe_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    env["HOME"] = str(codex_home)
    env["USER"] = "codex"
    env["LOGNAME"] = "codex"
    env.pop("CODEX_THREAD_ID", None)
    # The CLI authenticates from the short-lived auth.json. No unrelated secret should
    # be inherited by model-generated commands.
    sensitive = (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "GITHUB_TOKEN",
        "GH_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    )
    for name in sensitive:
        env.pop(name, None)
    return env


def _outer_sandbox(inner: list[str], cwd: Path, codex_home: Path) -> list[str]:
    """Hide host-private trees from the entire CLI, not just generated commands."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("bubblewrap is required for security traces; refusing a host run")
    # / is read-only for normal binaries/libraries. Private homes and the user runtime
    # are replaced with empty tmpfs mounts. Only the disposable repo and ephemeral
    # CODEX_HOME are writable/readable host binds.
    hidden_mounts = [str(Path.home())]
    if Path("/root").exists() and Path.home() != Path("/root"):
        hidden_mounts.append("/root")
    user_runtime = Path(f"/run/user/{os.getuid()}")
    if user_runtime.exists():
        hidden_mounts.append(str(user_runtime))
    argv = [
        bwrap,
        "--die-with-parent",
        "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
        "--ro-bind", "/", "/",
    ]
    for mount in hidden_mounts:
        argv += ["--tmpfs", mount]
    argv += [
        "--tmpfs", "/tmp",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--bind", str(cwd), str(cwd),
        "--bind", str(codex_home), str(codex_home),
        "--setenv", "HOME", str(codex_home),
        "--setenv", "CODEX_HOME", str(codex_home),
        "--setenv", "USER", "codex",
        "--setenv", "LOGNAME", "codex",
        "--chdir", str(cwd),
        "--",
        *inner,
    ]
    return argv


def extract_last_message(events_path: Path) -> str:
    last = ""
    if not events_path.exists():
        return last
    for line in events_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                last = item["text"]
    return last.strip()


def run_codex(
    *,
    prompt: str,
    cwd: Path,
    events_path: Path,
    stderr_path: Path,
    rollout_path: Path | None,
    model: str,
    effort: str,
    timeout_s: int,
    sandbox: str = "workspace-write",
    output_schema: dict | None = None,
) -> dict:
    """Run one isolated Codex turn and preserve its event stream/rollout."""
    cwd = cwd.resolve()
    cwd.mkdir(parents=True, exist_ok=True)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    if rollout_path:
        rollout_path.parent.mkdir(parents=True, exist_ok=True)

    # This must live outside the hidden real home so it can be rebound into bwrap.
    homes = Path(os.environ.get(
        "MOONSHINER_SECURITY_RUNTIME_ROOT", str(RUNS / "security-runtime")
    )) / "codex-homes"
    homes.mkdir(parents=True, exist_ok=True)
    codex_home = Path(tempfile.mkdtemp(prefix="run-", dir=homes))
    auth_src = _real_codex_home() / "auth.json"
    if not auth_src.exists():
        raise FileNotFoundError(f"Codex auth is missing: {auth_src}")
    auth_dst = codex_home / "auth.json"
    shutil.copy2(auth_src, auth_dst)
    auth_dst.chmod(0o600)

    cmd = [
        "codex", "exec", "--json", "--model", model,
        "-c", f'model_reasoning_effort="{effort}"',
        "--ignore-user-config", "--ignore-rules",
        "--sandbox", sandbox,
        "--skip-git-repo-check",
        "-C", str(cwd),
    ]
    if output_schema is not None:
        schema_path = codex_home / "output.schema.json"
        schema_path.write_text(json.dumps(output_schema))
        cmd += ["--output-schema", str(schema_path)]
    cmd.append("-")
    cmd = _outer_sandbox(cmd, cwd, codex_home)

    thread_id: str | None = None
    usage: dict = {}
    event_error = None
    auth_unlinked = False
    pump_error: list[str] = []
    started = time.time()
    timed_out = False

    with events_path.open("w") as events, stderr_path.open("w") as errors:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            cwd=cwd,
            env=_safe_env(codex_home),
            start_new_session=True,
            bufsize=1,
        )

        def pump() -> None:
            nonlocal thread_id, usage, event_error, auth_unlinked
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    events.write(line)
                    events.flush()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "thread.started":
                        thread_id = event.get("thread_id")
                        # The parent has loaded its credential. Remove the filesystem copy
                        # before a model tool can possibly inspect CODEX_HOME.
                        auth_dst.unlink(missing_ok=True)
                        auth_unlinked = True
                    elif event.get("type") == "turn.completed":
                        usage = event.get("usage") or {}
                    elif event.get("type") in {"turn.failed", "error"}:
                        event_error = event.get("error") or event.get("message") or event
            except Exception as exc:  # preserve failure in metadata; do not deadlock wait()
                pump_error.append(f"{type(exc).__name__}: {exc}")

        reader = threading.Thread(target=pump, name="codex-json-pump", daemon=True)
        reader.start()
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            returncode = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            returncode = proc.returncode
        reader.join(timeout=10)

    auth_dst.unlink(missing_ok=True)
    persisted = _find_rollout(codex_home, thread_id)
    trace_format = "codex-rollout"
    if rollout_path is not None:
        if persisted:
            shutil.copy2(persisted, rollout_path)
        else:
            shutil.copy2(events_path, rollout_path)
            trace_format = "codex-exec-events"
    # Preserve the credential-free Codex session directory for audit. The
    # copied auth file was unlinked before tools ran and again above.

    observed_models = []
    for line in events_path.read_text(errors="replace").splitlines():
        try: event = json.loads(line)
        except json.JSONDecodeError: continue
        stack = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"model", "model_id"} and isinstance(child, str):
                        if child not in observed_models: observed_models.append(child)
                    elif isinstance(child, (dict, list)): stack.append(child)
            elif isinstance(value, list): stack.extend(value)

    return {
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_s": round(time.time() - started, 1),
        "thread_id": thread_id,
        "usage": usage,
        "error": event_error,
        "pump_error": pump_error,
        "auth_unlinked_before_tools": auth_unlinked,
        "trace_format": trace_format,
        "observed_models": observed_models,
        "model_attested": model in observed_models,
        "last_message": extract_last_message(events_path),
    }
