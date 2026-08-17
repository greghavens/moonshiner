"""Native OpenCode structured-session adapter.

OpenCode runs as an isolated local server inside Moonshiner's existing task
workspace boundary.  Its live event stream is activity evidence only; after
each turn Moonshiner retrieves the completed native session and persists that
session as the authoritative trace.  Provider credentials remain host-side in
the existing loopback proxy, while OpenCode receives only a fixed dummy token.

A provider content-filter block is not a harness failure: it is surfaced as
``safeguard_refusal`` so the caller defers that one seed and keeps going.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from common import scrub_text
from runtimes import availability
from runtimes.auth import load_provider_key
from runtimes.base import (ReviewResult, Runtime, TraceResult,
                           _kill_process_tree, _process_tree_snapshot,
                           run_with_inactivity_timeout,
                           wait_with_inactivity_timeout,
                           workspace_only_command)
from runtimes.credential_proxy import DUMMY_TOKEN, ProxySession


OPENCODE_RUNTIME_VERSION = "1.18.18"
TRACE_FORMAT = "opencode-session-v1"
_ISOLATION_FLAGS = {
    "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
    "OPENCODE_DISABLE_CLAUDE_CODE": "true",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "true",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
    "OPENCODE_PURE": "true",
}
CONTENT_FILTER_NAMES = ("ContentFilterError",)
CONTENT_FILTER_PHRASES = ("content filter", "content_filter")


class ContentFiltered(ValueError):
    """The provider's safety filter blocked the response.

    This is one seed's problem, not the harness's. Left as a generic session
    error it becomes a ``TraceHarnessInfrastructureFailure``, which stops the
    queue and refuses to restart — so a single seed the filter dislikes halts
    the whole corpus. Raised as its own type it reaches ``safeguard_refusal``,
    the deferral path that already exists for exactly this, and the run
    continues with the next seed.

    It subclasses ``ValueError`` because a blocked session fails the evidence
    check *and* the re-parse that follows, and only the first of those has a
    handler that knows this type. Narrowing the base to ``Exception`` lets the
    second raise walk straight out of ``run_trace`` and become the very
    infrastructure failure the first catch just prevented.
    """


def _is_content_filter(error) -> bool:
    """Whether a native OpenCode assistant error is a safety-filter block.

    Prefers the structured ``name`` the provider sends; falls back to the text
    so a renamed error class degrades to a deferral rather than to a
    queue-stopping failure.
    """
    if isinstance(error, dict):
        name = error.get("name")
        if isinstance(name, str) and name in CONTENT_FILTER_NAMES:
            return True
    text = str(error).lower()
    return any(phrase in text for phrase in CONTENT_FILTER_PHRASES)


def _snapshot_excludes(workspace: Path) -> Path:
    """Keep the sandbox HOME out of OpenCode's snapshot, and return the config.

    OpenCode snapshots a turn by running ``git add`` with ``--git-dir`` set to
    ``<data>/snapshot/...`` and ``--work-tree`` set to the workspace. The
    sandbox points every writable cache at ``<workspace>/.sandbox-home``, so
    that object store sits *inside* the tree it snapshots. Git only excludes
    ``.git`` automatically, never a nested git dir at some other path, so each
    snapshot committed the previous snapshot's objects and the next one
    committed those: 2.8 GB and 141,884 objects in eight minutes, with the file
    scanner walking the growth until the job hit its memory ceiling and died.

    Ignoring the sandbox HOME breaks the loop at the source and keeps the
    snapshot to the agent's actual work. It belongs in a global excludes file
    rather than a ``.gitignore`` in the workspace, which would be copied into
    the authored seed. The advice message is silenced because OpenCode reads a
    non-zero ``git add`` as a failed snapshot and logs it every turn.
    """
    home = Path(workspace) / ".sandbox-home" / "git"
    home.mkdir(parents=True, exist_ok=True)
    excludes = home / "ignore"
    excludes.write_text(".sandbox-home/\n")
    config = home / "config"
    config.write_text(
        "[core]\n"
        f"\texcludesFile = {excludes}\n"
        "[advice]\n"
        "\taddIgnoredFile = false\n")
    return config


def prompt_payload(prompt: str, provider: str, model: str) -> dict:
    """Build OpenCode's one-part user message without touching *prompt*."""
    return {
        "model": {"providerID": provider, "modelID": model},
        "agent": "build",
        "parts": [{"type": "text", "text": prompt}],
    }


def validate_tool_schemas(value):
    """Validate and return OpenCode's native experimental tool response.

    The object is deliberately returned unchanged.  Moonshiner neither fills
    missing fields nor reconstructs a schema from tool names.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("OpenCode tool schema response must be a nonempty list")
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("OpenCode tool schema entry must be an object")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("OpenCode tool schema has no nonempty id")
        if identifier in seen:
            raise ValueError(f"duplicate OpenCode tool schema id: {identifier}")
        seen.add(identifier)
        if not isinstance(entry.get("description"), str):
            raise ValueError(f"OpenCode tool schema {identifier!r} has no description")
        if not isinstance(entry.get("parameters"), dict):
            raise ValueError(
                f"OpenCode tool schema {identifier!r} parameters are not JSON schema")
    return value


def _json_request(base_url: str, path: str, *, method: str = "GET",
                  payload=None, query: dict[str, str] | None = None,
                  timeout: float | None = None):
    target = base_url.rstrip("/") + path
    if query:
        target += "?" + urlencode(query)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(target, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[-2000:]
        raise RuntimeError(
            f"OpenCode API {method} {path} returned {error.code}: {detail}") from error
    except (OSError, URLError) as error:
        raise RuntimeError(
            f"OpenCode API {method} {path} failed: {error}") from error
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"OpenCode API {method} {path} returned malformed JSON") from error


def _provider_and_model(runtime: "OpenCodeRuntime") -> tuple[str, str]:
    provider = str(runtime.runtime_config.get("provider") or "").strip()
    model = str(runtime.role.get("model") or "").strip()
    if not provider:
        raise RuntimeError("runtimes.opencode.provider is not configured")
    if not model:
        raise RuntimeError("OpenCode model is not configured")
    if provider not in {"openrouter", "zenmux"}:
        raise RuntimeError(
            "OpenCode provider must be globally configured as openrouter or zenmux")
    return provider, model


def _parse_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                value = json.loads(text[start:end + 1])
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                pass
    return None


def _tool_state(part: dict) -> tuple[str, dict, str]:
    call_id = part.get("callID")
    tool = part.get("tool")
    state = part.get("state")
    if (not isinstance(call_id, str) or not call_id
            or not isinstance(tool, str) or not tool
            or not isinstance(state, dict)):
        raise ValueError("OpenCode tool part lacks native execution evidence")
    status = state.get("status")
    timing = state.get("time")
    if (status not in {"completed", "error"}
            or not isinstance(state.get("input"), dict)
            or not isinstance(timing, dict)
            or timing.get("start") is None or timing.get("end") is None):
        raise ValueError(
            f"OpenCode tool {call_id!r} has no completed native tool evidence")
    if status == "completed":
        if "output" not in state or not isinstance(state["output"], str):
            raise ValueError(
                f"OpenCode tool {call_id!r} has no genuine native result")
        result = state["output"]
    else:
        if not isinstance(state.get("error"), str) or not state["error"]:
            raise ValueError(
                f"OpenCode tool {call_id!r} has no genuine native error")
        result = state["error"]
    return call_id, state, result


def _completed_session_evidence(session, *, expected_prompt: str | None = None,
                                expected_provider: str | None = None,
                                expected_model: str | None = None,
                                model_matches=None) -> dict:
    if not isinstance(session, list) or not session:
        raise ValueError("OpenCode completed session is empty or malformed")
    user_messages = []
    observed_models: list[str] = []
    observed_providers: list[str] = []
    tool_calls = tool_results = 0
    terminal_assistant = False
    errors: list[str] = []
    filtered = False
    for item in session:
        if not isinstance(item, dict):
            raise ValueError("OpenCode completed session message is malformed")
        info, parts = item.get("info"), item.get("parts")
        if not isinstance(info, dict) or not isinstance(parts, list):
            raise ValueError("OpenCode completed session lacks native info or parts")
        role = info.get("role")
        if role == "user":
            user_messages.append(parts)
            continue
        if role != "assistant":
            continue
        provider = info.get("providerID")
        model = info.get("modelID")
        if isinstance(provider, str) and provider not in observed_providers:
            observed_providers.append(provider)
        if isinstance(model, str) and model not in observed_models:
            observed_models.append(model)
        completion = info.get("time") or {}
        if completion.get("completed") is None:
            raise ValueError("OpenCode assistant message is not completed")
        if info.get("error"):
            errors.append(str(info["error"]))
            if _is_content_filter(info["error"]):
                filtered = True
        if info.get("finish"):
            terminal_assistant = True
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("OpenCode session part is malformed")
            if part.get("type") == "tool":
                _tool_state(part)
                tool_calls += 1
                tool_results += 1
    if expected_prompt is not None:
        if not user_messages:
            raise ValueError("OpenCode authoritative session has no user prompt")
        first = user_messages[0]
        if (len(first) != 1 or first[0].get("type") != "text"
                or first[0].get("text") != expected_prompt):
            raise ValueError(
                "OpenCode authoritative session did not preserve the prompt byte-for-byte")
    if expected_provider and expected_provider not in observed_providers:
        raise ValueError(
            f"OpenCode session did not attest provider {expected_provider!r}")
    if expected_model:
        match = model_matches or (lambda value: value == expected_model)
        if not any(match(value) for value in observed_models):
            raise ValueError(
                f"OpenCode session did not attest model {expected_model!r}")
    if errors:
        detail = "OpenCode assistant error: " + "; ".join(errors)
        if filtered:
            raise ContentFiltered(detail)
        raise ValueError(detail)
    if not terminal_assistant:
        raise ValueError("OpenCode completed session has no terminal finish reason")
    return {
        "observed_models": observed_models,
        "observed_providers": observed_providers,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
    }


HEARTBEAT_EVENT_TYPES = ("server.heartbeat",)
QUESTION_EVENT_TYPES = ("question.asked",)


class BlockedOnQuestion(ValueError):
    """The teacher asked the operator a question, and a trace has no operator.

    OpenCode's ``question`` tool suspends the session until someone answers.
    Headless there is no one, and the server keeps heartbeating, so nothing
    downstream can tell the difference between deliberating and stopped: the
    attempt runs until the queue does. Treated as its own condition it becomes
    a deferral — this seed's problem, not the harness's — and the run goes on.

    It subclasses ``ValueError`` for the same reason as ``ContentFiltered``:
    the handlers that know what this is are the ones that already catch
    ``ValueError``, and a broader base walks straight past them.
    """


def _event_type(event) -> str:
    payload = event.get("payload", event) if isinstance(event, dict) else {}
    return str((payload or {}).get("type") or "")


def _is_heartbeat(event) -> bool:
    """Whether an event proves only that the server is alive.

    OpenCode emits ``server.heartbeat`` every few seconds for as long as the
    process lives, entirely independently of the session.
    """
    return _event_type(event) in HEARTBEAT_EVENT_TYPES


class _EventStream:
    """Read OpenCode's native event stream solely as activity evidence."""

    def __init__(self, base_url: str, path: Path):
        self.base_url = base_url
        self.path = path
        self.events: list[dict] = []
        # Counted separately from `events` because the inactivity timeout asks
        # whether the *session* moved, and a heartbeat only says the server is
        # up. Counting heartbeats as progress means an agent that stops for
        # good — one blocked on a question tool with no human to answer it —
        # resets the clock every few seconds and the trace hangs for the life
        # of the queue instead of timing out.
        self.progress = 0
        self.progress_bytes = 0
        self.error: str | None = None
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)

    def start(self) -> "_EventStream":
        self.path.write_text("")
        self._thread.start()
        return self

    def _read(self) -> None:
        request = Request(self.base_url.rstrip("/") + "/event",
                          headers={"Accept": "text/event-stream"})
        try:
            with urlopen(request) as response, self.path.open(
                    "a", encoding="utf-8") as output:
                self._connected.set()
                for raw in response:
                    if self._stop.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    self.events.append(event)
                    record = json.dumps(event, separators=(",", ":"))
                    if not _is_heartbeat(event):
                        self.progress += 1
                        self.progress_bytes += len(record)
                    output.write(record + "\n")
                    output.flush()
        except BaseException as error:
            if not self._stop.is_set():
                self.error = str(error)
        finally:
            self._connected.set()

    def activity(self) -> tuple[int, int]:
        """Session progress, which the file on disk no longer measures.

        The transcript keeps every event, heartbeats included, so its size
        grows on a dead session. Only the progress counters answer the
        question the inactivity timeout is actually asking.
        """
        return self.progress, self.progress_bytes

    def connected(self) -> bool:
        return self._connected.is_set() and self.error is None

    def pending_question(self, session_id: str) -> str | None:
        """The first question this session is waiting on, if any."""
        for raw in self.events:
            event = raw.get("payload", raw) if isinstance(raw, dict) else {}
            if _event_type(raw) not in QUESTION_EVENT_TYPES:
                continue
            properties = event.get("properties") or {}
            if properties.get("sessionID") != session_id:
                continue
            asked = properties.get("questions") or []
            first = asked[0] if isinstance(asked, list) and asked else {}
            text = (first.get("question") or first.get("header")
                    if isinstance(first, dict) else None)
            return str(text or "question text unavailable")
        return None

    def terminal_count(self, session_id: str) -> int:
        count = 0
        for raw in self.events:
            event = raw.get("payload", raw) if isinstance(raw, dict) else {}
            kind = event.get("type")
            properties = event.get("properties") or {}
            event_session = (properties.get("sessionID")
                             or (properties.get("session") or {}).get("id"))
            if event_session != session_id:
                continue
            status = properties.get("status") or {}
            if kind == "session.idle" or (
                    kind == "session.status" and status.get("type") == "idle"):
                count += 1
        return count

    def event_types(self) -> list[str]:
        found: list[str] = []
        for raw in self.events:
            event = raw.get("payload", raw) if isinstance(raw, dict) else {}
            kind = event.get("type")
            if isinstance(kind, str) and kind not in found:
                found.append(kind)
        return found

    def stop(self) -> None:
        self._stop.set()


class OpenCodeRuntime(Runtime):
    name = "opencode"
    trace_formats = (TRACE_FORMAT,)

    def trace_capabilities(self) -> frozenset[str]:
        return frozenset({"workspace_write", "multi_turn",
                          "reasoning_capture", "tool_schema_capture"})

    @staticmethod
    def _managed_cli() -> Path:
        base = Path(os.environ.get(
            "XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return (base / "moonshiner" / "toolchains" / "opencode" /
                "node_modules" / ".bin" / "opencode")

    def _cli_path(self) -> Path:
        configured = str(self.runtime_config.get("cli") or "opencode")
        path = Path(configured)
        if path.is_absolute() or path.parent != Path("."):
            return path.resolve()
        found = shutil.which(configured)
        if found:
            return Path(found).resolve()
        managed = self._managed_cli()
        return managed.resolve() if managed.exists() else path

    def trace_probe_command(self) -> list[str]:
        return [str(self._cli_path()), "--version"]

    def oci_runtime_command(
            self, command: list[str], workspace: Path
            ) -> tuple[list[str], tuple[tuple[Path, Path], ...]]:
        source = self._cli_path()
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = workspace / ".sandbox-home" / "native-runtime" / "opencode"
        return [str(destination), *command[1:]], ((source, destination),)

    def preflight(self, *, require_auth: bool = False) -> None:
        cli = self._cli_path()
        if not cli.is_file():
            raise SystemExit(f"OpenCode CLI not found: {cli}")
        if shutil.which("bwrap") is None:
            raise SystemExit("bwrap (bubblewrap) required for the OpenCode sandbox")
        provider, model = _provider_and_model(self)
        base_url = str(self.runtime_config.get("base_url") or "").strip()
        key_env = str(self.runtime_config.get("key_env") or "").strip()
        if not base_url or not key_env:
            raise SystemExit(
                f"OpenCode provider {provider!r} lacks base_url or key_env")
        try:
            result = run_with_inactivity_timeout(
                [str(cli), "--version"], capture_output=True, text=True,
                inactivity_timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise SystemExit(f"OpenCode CLI unusable: {error}") from error
        observed = (result.stdout or result.stderr or "").strip().lstrip("v")
        pinned = str(self.runtime_config.get("runtime_version")
                     or OPENCODE_RUNTIME_VERSION).lstrip("v")
        if result.returncode != 0 or observed != pinned:
            raise SystemExit(
                f"OpenCode version {observed or 'unknown'} != pinned {pinned}")
        self._observed_runtime_version = observed
        if require_auth:
            real_key = load_provider_key(self.runtime_config)
            signature = (str(cli), observed, provider, model, base_url, key_env)
            if getattr(self, "_interface_signature", None) != signature:
                self._preflight_structured_interface(real_key)
                self._interface_signature = signature
        # Touch both values here so malformed role/provider configuration fails
        # in the existing pre-call resolver, before a queue records an attempt.
        _ = provider, model

    def _preflight_structured_interface(self, real_key: str) -> None:
        """Exercise OpenCode's pinned structured APIs without a model call."""
        from common import WORKSPACES, remove_workspace
        provider, model = _provider_and_model(self)
        workspace = WORKSPACES / f"opencode-preflight-{uuid.uuid4().hex}"
        output = workspace / ".harness-output"
        output.mkdir(parents=True)
        proxy = ProxySession(self.runtime_config["base_url"], real_key).start()
        environment = self._server_environment(workspace, proxy.base_url,
                                               read_only=False)
        port = self._reserve_port()
        base_url = f"http://127.0.0.1:{port}"
        command = workspace_only_command(
            [str(self._cli_path()), "serve", "--hostname", "127.0.0.1",
             "--port", str(port)], workspace)
        stdout_path = output / "preflight.stdout"
        stderr_path = output / "preflight.stderr"
        events = None
        process = None
        try:
            with stdout_path.open("w", encoding="utf-8") as stdout, \
                    stderr_path.open("w", encoding="utf-8") as stderr:
                process = subprocess.Popen(
                    command, cwd=workspace, env=environment,
                    stdout=stdout, stderr=stderr, start_new_session=True)
                health = self._wait_for_health(process, base_url, 30)
                version = str(health.get("version") or "").lstrip("v")
                if version != OPENCODE_RUNTIME_VERSION:
                    raise RuntimeError(
                        f"OpenCode server version {version or 'unknown'} "
                        f"!= pinned {OPENCODE_RUNTIME_VERSION}")
                events = _EventStream(
                    base_url, output / "preflight.events.jsonl").start()
                self._wait_for_event_stream(process, events, 30)
                probe = lambda request: self._send_with_activity(
                    process, request, inactivity_timeout=30,
                    activity_probe=events.activity)
                schemas = validate_tool_schemas(probe(lambda: _json_request(
                    base_url, "/experimental/tool",
                    query={"provider": provider, "model": model})))
                created = probe(lambda: _json_request(
                    base_url, "/session", method="POST",
                    payload={"title": "Moonshiner OpenCode interface preflight"}))
                if not isinstance(created, dict) or not isinstance(
                        created.get("id"), str):
                    raise RuntimeError("OpenCode session interface is incompatible")
                messages = probe(lambda: _json_request(
                    base_url, f"/session/{created['id']}/message"))
                if not isinstance(messages, list):
                    raise RuntimeError(
                        "OpenCode completed-session interface is incompatible")
                probe(lambda: _json_request(
                    base_url, f"/session/{created['id']}", method="DELETE"))
                self._validated_tool_schemas = schemas
        except BaseException as error:
            detail = ""
            try:
                detail = scrub_text(stderr_path.read_text(errors="replace"))[-2000:]
            except OSError:
                pass
            suffix = f"; OpenCode stderr: {detail}" if detail else ""
            raise RuntimeError(f"{error}{suffix}") from error
        finally:
            if events is not None:
                events.stop()
            if process is not None:
                self._stop_server(process, base_url)
            proxy.stop()
            remove_workspace(workspace)

    def _server_environment(self, workspace: Path, proxy_base_url: str,
                            *, read_only: bool,
                            base_environment: dict[str, str] | None = None
                            ) -> dict[str, str]:
        provider, model = _provider_and_model(self)
        environment = dict(base_environment or self.teacher_environment(workspace))
        # Declare the configured model outright. OpenCode otherwise resolves
        # model IDs through the third-party models.dev catalog, which lags the
        # provider's live catalog and rejects a model the provider already
        # serves. The configured model is the authority here, not the catalog.
        config = {
            "$schema": "https://opencode.ai/config.json",
            "share": "disabled",
            "autoupdate": False,
            "plugin": [],
            "instructions": [],
            "provider": {
                provider: {"options": {"baseURL": proxy_base_url},
                           "models": {model: {}}},
            },
            "permission": {"external_directory": "deny"},
        }
        environment.update(_ISOLATION_FLAGS)
        environment.update({
            "USER": "moonshiner-agent",
            "LOGNAME": "moonshiner-agent",
            "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
            str(self.runtime_config["key_env"]): DUMMY_TOKEN,
            "GIT_CONFIG_GLOBAL": str(_snapshot_excludes(workspace)),
        })
        return environment

    @staticmethod
    def _reserve_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    @staticmethod
    def _wait_for_health(process: subprocess.Popen, base_url: str,
                         inactivity_timeout: float) -> dict:
        snapshot = _process_tree_snapshot(process.pid)
        last_activity = time.monotonic()
        interval = max(0.05, min(0.5, inactivity_timeout / 4))
        while process.poll() is None:
            try:
                health = _json_request(base_url, "/global/health", timeout=interval)
                if isinstance(health, dict) and health.get("healthy") is True:
                    return health
            except RuntimeError:
                pass
            current = _process_tree_snapshot(process.pid)
            if current != snapshot:
                snapshot = current
                last_activity = time.monotonic()
            elif time.monotonic() - last_activity >= inactivity_timeout:
                _kill_process_tree(process)
                process.wait()
                raise RuntimeError("OpenCode server became inactive before health")
            time.sleep(interval)
        raise RuntimeError(f"OpenCode server exited {process.returncode} before health")

    @staticmethod
    def _wait_for_event_stream(process: subprocess.Popen, events: _EventStream,
                               inactivity_timeout: float) -> None:
        snapshot = _process_tree_snapshot(process.pid)
        last_activity = time.monotonic()
        interval = max(0.05, min(0.5, inactivity_timeout / 4))
        while not events.connected():
            if events.error:
                raise RuntimeError(f"OpenCode event stream failed: {events.error}")
            if process.poll() is not None:
                raise RuntimeError(
                    f"OpenCode server exited {process.returncode} before event stream")
            current = _process_tree_snapshot(process.pid)
            if current != snapshot:
                snapshot = current
                last_activity = time.monotonic()
            elif time.monotonic() - last_activity >= inactivity_timeout:
                _kill_process_tree(process)
                process.wait()
                raise RuntimeError("OpenCode event stream became inactive during setup")
            time.sleep(interval)

    @staticmethod
    def _wait_for_terminal_event(
            process: subprocess.Popen, events: _EventStream, session_id: str,
            previous_count: int, inactivity_timeout: float) -> None:
        snapshot = _process_tree_snapshot(process.pid)
        external = events.activity()
        last_activity = time.monotonic()
        interval = max(0.05, min(1.0, inactivity_timeout / 4))
        while events.terminal_count(session_id) <= previous_count:
            # Checked before liveness, because the session is not slow — it is
            # finished. Waiting for the inactivity timeout would never work:
            # heartbeats keep both the event stream and the server's CPU and
            # I/O counters moving, so every liveness signal reads as healthy.
            question = events.pending_question(session_id)
            if question is not None:
                raise BlockedOnQuestion(
                    "OpenCode teacher asked the operator a question and a "
                    f"headless trace cannot answer it: {question}")
            if events.error:
                raise RuntimeError(f"OpenCode event stream failed: {events.error}")
            if process.poll() is not None:
                raise RuntimeError(
                    f"OpenCode server exited {process.returncode} before terminal event")
            current = _process_tree_snapshot(process.pid)
            current_external = events.activity()
            if current != snapshot or current_external != external:
                snapshot, external = current, current_external
                last_activity = time.monotonic()
            elif time.monotonic() - last_activity >= inactivity_timeout:
                raise RuntimeError(
                    "OpenCode emitted no terminal session event after becoming inactive")
            time.sleep(interval)

    @staticmethod
    def _send_with_activity(process: subprocess.Popen, request, *,
                            inactivity_timeout: float, activity_probe):
        completed = threading.Event()
        outcome: dict[str, object] = {}

        def send() -> None:
            try:
                outcome["value"] = request()
            except BaseException as error:
                outcome["error"] = error
            finally:
                completed.set()

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        interval = max(0.05, min(1.0, inactivity_timeout / 4))
        snapshot = _process_tree_snapshot(process.pid)
        external = activity_probe()
        last_activity = time.monotonic()
        while not completed.wait(interval):
            if process.poll() is not None:
                raise RuntimeError(
                    f"OpenCode server exited {process.returncode} during request")
            current = _process_tree_snapshot(process.pid)
            current_external = activity_probe()
            if current != snapshot or current_external != external:
                snapshot, external = current, current_external
                last_activity = time.monotonic()
                continue
            if time.monotonic() - last_activity < inactivity_timeout:
                continue
            final = _process_tree_snapshot(process.pid)
            final_external = activity_probe()
            if final != snapshot or final_external != external:
                snapshot, external = final, final_external
                last_activity = time.monotonic()
                continue
            _kill_process_tree(process)
            process.wait()
            raise subprocess.TimeoutExpired(process.args, inactivity_timeout)
        thread.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    @staticmethod
    def _stop_server(process: subprocess.Popen, base_url: str) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            wait_with_inactivity_timeout(process, 5)
        except subprocess.TimeoutExpired:
            pass

    def _run_server_session(
            self, *, seed: dict | None, command_factory, workspace: Path,
            out_dir: Path, artifact_id: str, prompt: str,
            interaction: list[str] | None,
            read_only: bool,
            base_environment: dict[str, str]) -> TraceResult:
        provider, model = _provider_and_model(self)
        real_key = load_provider_key(self.runtime_config)
        proxy = ProxySession(self.runtime_config["base_url"], real_key).start()
        port = self._reserve_port()
        base_url = f"http://127.0.0.1:{port}"
        environment = self._server_environment(
            workspace, proxy.base_url, read_only=read_only,
            base_environment=base_environment)
        command = command_factory(environment)
        command = [*command, "serve", "--hostname", "127.0.0.1",
                   "--port", str(port)]
        stdout_path = out_dir / f"{artifact_id}.opencode.stdout"
        stderr_path = out_dir / f"{artifact_id}.opencode.stderr"
        events_path = out_dir / f"{artifact_id}.opencode.events.jsonl"
        raw_path = out_dir / f"{artifact_id}.session.json"
        for directory in (out_dir,):
            directory.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("")
        stderr_path.write_text("")
        started = time.monotonic()
        process = None
        events = None
        timed_out = False
        error = None
        safeguard_refusal = False
        blocked_on_question = False
        session: list[dict] = []
        session_id = None
        schemas = None
        audit: dict = {}
        health: dict = {}
        evidence: dict = {}
        return_code: int | None = None
        try:
            with stdout_path.open("a", encoding="utf-8") as stdout, \
                    stderr_path.open("a", encoding="utf-8") as stderr:
                process = subprocess.Popen(
                    command, cwd=workspace, env=environment,
                    stdout=stdout, stderr=stderr, start_new_session=True)
                inactivity = float(self.role.get("timeout_s", 3600))
                health = self._wait_for_health(process, base_url, inactivity)
                observed_version = str(health.get("version") or "").lstrip("v")
                if observed_version != OPENCODE_RUNTIME_VERSION:
                    raise RuntimeError(
                        f"OpenCode server version {observed_version or 'unknown'} "
                        f"!= pinned {OPENCODE_RUNTIME_VERSION}")
                events = _EventStream(base_url, events_path).start()
                self._wait_for_event_stream(process, events, inactivity)
                schemas = getattr(self, "_validated_tool_schemas", None)
                if schemas is None:
                    schemas = validate_tool_schemas(self._send_with_activity(
                        process,
                        lambda: _json_request(
                            base_url, "/experimental/tool",
                            query={"provider": provider, "model": model}),
                        inactivity_timeout=inactivity,
                        activity_probe=events.activity))
                created = self._send_with_activity(
                    process,
                    lambda: _json_request(
                        base_url, "/session", method="POST",
                        payload={"title": f"Moonshiner trace {artifact_id}"}),
                    inactivity_timeout=inactivity,
                    activity_probe=events.activity)
                if not isinstance(created, dict) or not isinstance(
                        created.get("id"), str):
                    raise RuntimeError("OpenCode did not return a native session id")
                session_id = created["id"]
                turns = [prompt, *(interaction or [])]
                for turn in turns:
                    before_terminal = events.terminal_count(session_id)
                    self._send_with_activity(
                        process,
                        lambda turn=turn: _json_request(
                            base_url, f"/session/{session_id}/message",
                            method="POST",
                            payload=prompt_payload(turn, provider, model)),
                        inactivity_timeout=inactivity,
                        activity_probe=lambda: (
                            events.activity(),
                            len(proxy.snapshot().get("exchanges") or []),
                            stdout_path.stat().st_size,
                            stderr_path.stat().st_size),
                    )
                    if events.error:
                        raise RuntimeError(
                            f"OpenCode event stream failed: {events.error}")
                    self._wait_for_terminal_event(
                        process, events, session_id, before_terminal, inactivity)
                session = self._send_with_activity(
                    process,
                    lambda: _json_request(
                        base_url, f"/session/{session_id}/message"),
                    inactivity_timeout=inactivity,
                    activity_probe=events.activity)
                evidence = _completed_session_evidence(
                    session, expected_prompt=prompt,
                    expected_provider=provider, expected_model=model,
                    model_matches=self.model_matches)
                audit = proxy.snapshot()
                if not audit.get("had_success"):
                    raise RuntimeError(
                        "OpenCode provider proxy observed no successful model response")
                if not any(self.model_matches(value) for value in
                           audit.get("response_models") or []):
                    raise RuntimeError(
                        "OpenCode provider response did not attest configured model")
                raw_path.write_text(json.dumps(session, ensure_ascii=False, indent=2))
                return_code = 0
        except subprocess.TimeoutExpired:
            timed_out = True
            error = "OpenCode became inactive during a model call"
        except BlockedOnQuestion as asked:
            blocked_on_question = True
            error = scrub_text(str(asked))
        except ContentFiltered as blocked:
            safeguard_refusal = True
            error = scrub_text(str(blocked))
        except BaseException as failure:
            error = scrub_text(str(failure))
        finally:
            if not raw_path.exists():
                raw_path.write_text(json.dumps(session, ensure_ascii=False, indent=2))
            if events is not None:
                events.stop()
            if process is not None:
                self._stop_server(process, base_url)
            if not audit:
                audit = proxy.snapshot()
            proxy.stop()
        duration = time.monotonic() - started
        messages: list[dict] = []
        stats: dict = {}
        if session:
            try:
                messages, stats = self.parse_stream(raw_path, str(workspace))
            except ValueError as parse_error:
                if isinstance(parse_error, ContentFiltered):
                    safeguard_refusal = True
                if isinstance(parse_error, BlockedOnQuestion):
                    blocked_on_question = True
                error = error or scrub_text(str(parse_error))
        observed_models = evidence.get("observed_models") or []
        observed_model = observed_models[0] if observed_models else None
        usage = stats.get("usage") or {}
        event_types = events.event_types() if events is not None else []
        return TraceResult(
            raw_path=raw_path,
            trace_format=TRACE_FORMAT,
            return_code=return_code,
            timed_out=timed_out,
            duration_s=duration,
            stream_success=bool(messages) and error is None and not timed_out,
            observed_model=observed_model,
            observed_models=observed_models,
            model_attested=bool(observed_model and self.model_matches(observed_model)
                                and audit.get("had_success")
                                and not safeguard_refusal
                                and not blocked_on_question),
            usage=usage,
            error=error,
            safeguard_refusal=safeguard_refusal,
            blocked_on_question=blocked_on_question,
            unavailable=availability.find_usage_limit(error),
            provenance={
                "session_id": session_id,
                "provider": provider,
                "runtime": "opencode",
                "runtime_version": health.get("version")
                    or getattr(self, "_observed_runtime_version", None),
                "selected_model": model,
                "observed_providers": evidence.get("observed_providers") or [],
                "tool_schemas": schemas,
                "tool_schema_interface": "/experimental/tool",
                "native_event_types": event_types,
                "native_event_count": len(events.events) if events is not None else 0,
                "upstream_response_models": audit.get("response_models") or [],
                "upstream_audit": audit,
                "cost": stats.get("cost"),
                "finish_reasons": stats.get("finish_reasons") or [],
                "credential_boundary":
                    "host-loopback-proxy; child receives dummy token only",
            },
        )

    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        workspace = self.require_persistent_workspace(workspace)
        environment = self.teacher_environment(workspace)
        inner = [str(self._cli_path())]
        return self._run_server_session(
            seed=seed,
            command_factory=lambda environment: self.prepare_trace_command(
                seed, inner, workspace, environment=environment),
            workspace=workspace,
            out_dir=out_dir, artifact_id=seed["id"], prompt=prompt,
            interaction=interaction, read_only=False,
            base_environment=environment)

    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        workspace = self.require_persistent_workspace(workspace)
        environment = self.teacher_environment(workspace)
        result = self._run_server_session(
            seed=None,
            command_factory=lambda environment: workspace_only_command(
                [str(self._cli_path())], workspace,
                workspace_writable=not read_only),
            workspace=workspace,
            out_dir=out_dir, artifact_id="judge", prompt=instruction,
            interaction=None, read_only=read_only,
            base_environment=environment)
        messages, _ = self.parse_stream(result.raw_path, str(workspace))
        last = next((str(message.get("content") or "")
                     for message in reversed(messages)
                     if message.get("role") == "assistant"), "")
        return ReviewResult(
            raw_text=last,
            verdict=_parse_json_object(last),
            return_code=result.return_code,
            timed_out=result.timed_out,
            duration_s=result.duration_s,
            observed_model=result.observed_model,
            model_attested=result.model_attested,
            error=result.error,
        )

    @staticmethod
    def parse_stream(path: Path, workspace: str | None) -> tuple[list[dict], dict]:
        try:
            session = json.loads(path.read_text(errors="replace"))
        except json.JSONDecodeError as error:
            raise ValueError("OpenCode authoritative session is malformed") from error
        _completed_session_evidence(session)
        messages: list[dict] = []
        usage: dict[str, int | float] = {}
        finish_reasons: list[str] = []
        stats = {"reasoning_blocks": 0, "tool_calls": 0,
                 "tool_results": 0, "finish_reasons": finish_reasons,
                 "cost": 0.0, "usage": usage}
        for item in session:
            info = item["info"]
            parts = item["parts"]
            role = info.get("role")
            if role == "user":
                content = "".join(str(part.get("text") or "") for part in parts
                                  if part.get("type") == "text")
                messages.append({"role": "user", "content": content})
                continue
            if role != "assistant":
                continue
            assistant: dict = {"role": "assistant", "content": ""}
            tool_results: list[dict] = []
            for part in parts:
                kind = part.get("type")
                if kind == "text":
                    assistant["content"] += scrub_text(
                        str(part.get("text") or ""), workspace)
                elif kind == "reasoning":
                    reasoning = scrub_text(str(part.get("text") or ""), workspace)
                    if reasoning:
                        assistant["reasoning_content"] = (
                            assistant.get("reasoning_content", "") + reasoning)
                        stats["reasoning_blocks"] += 1
                elif kind == "tool":
                    call_id, state, result = _tool_state(part)
                    arguments = json.dumps(
                        state["input"], separators=(",", ":"), sort_keys=True)
                    assistant.setdefault("tool_calls", []).append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": part["tool"],
                            "arguments": scrub_text(arguments, workspace),
                        },
                    })
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": scrub_text(result, workspace),
                    })
                    stats["tool_calls"] += 1
                    stats["tool_results"] += 1
                elif kind == "step-finish":
                    reason = part.get("reason")
                    if isinstance(reason, str) and reason:
                        finish_reasons.append(reason)
            if not any(part.get("type") == "step-finish" for part in parts):
                reason = info.get("finish")
                if isinstance(reason, str) and reason:
                    finish_reasons.append(reason)
            cost = info.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                stats["cost"] += cost
            tokens = info.get("tokens") or {}
            if isinstance(tokens, dict):
                for key, value in tokens.items():
                    if key == "cache" and isinstance(value, dict):
                        cache = usage.setdefault("cache", {})
                        for cache_key, cache_value in value.items():
                            if isinstance(cache_value, (int, float)):
                                cache[cache_key] = cache.get(cache_key, 0) + cache_value
                    elif isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
            if assistant.get("content") or assistant.get("reasoning_content") \
                    or assistant.get("tool_calls"):
                messages.append(assistant)
            messages.extend(tool_results)
        return messages, stats
