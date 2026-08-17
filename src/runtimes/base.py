"""Runtime-agnostic interfaces for teacher trace generation and judging.

A *runtime* is one agentic CLI (Claude Code, Codex, or Pi/GLM) that moonshiner
can drive either as the **teacher** (generating a coding trace to distill) or as
the **judge** (independently reviewing a candidate trace read-only). Each concrete
adapter in this package implements :class:`Runtime`; the pipeline selects one by
name from ``config.json`` so a full distill can be run against any model.
"""
from __future__ import annotations

import abc
import os
import signal
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


def _peer_workspace_mask(workspace: Path, masks: Path) -> list[str]:
    """Hide every other workspace from this one.

    ``--ro-bind / /`` leaves the whole host readable, and every workspace this
    project has ever created sits under one root. A seed-authoring agent used
    that to enumerate its neighbours and read their ``task.json``, then copied a
    sibling's ``verify.py`` into its scratch and ran it. The result is a seed
    shaped by whatever else happened to be in flight -- including candidates
    still awaiting judgment, and ones that will be rejected -- rather than by
    its own brief. Only writes were ever bounded here; reads were not.

    The replacement carries a skeleton of this workspace's own path, because
    the bind that restores the workspace needs a mount point to land on: a
    directory cannot be *created* inside a read-only mount, but mounting over
    one that already exists is fine. Replacing the whole root this way also
    covers workspaces that appear after the sandbox starts, which masking each
    neighbour in turn would miss -- jobs run concurrently, so they do.

    Emitted before the workspace is bound so that bind lands on top. That
    ordering is why this cannot join the private-path loop below, which runs
    after the bind and so has to skip anything the workspace lives inside.
    """
    from common import WORKSPACES
    root = Path(WORKSPACES).resolve()
    if root == workspace or not workspace.is_relative_to(root):
        return []
    skeleton = masks / "workspace-root"
    (skeleton / workspace.relative_to(root)).mkdir(parents=True, exist_ok=True)
    return ["--ro-bind", str(skeleton), str(root)]


def workspace_only_command(
        command: list[str], workspace: Path, *,
        read_only_binds: tuple[tuple[Path, Path], ...] = (),
        unshare_network: bool = False,
        workspace_writable: bool = True) -> list[str]:
    """Wrap a command so its only persistent writable storage is *workspace*.

    The root filesystem remains visible but read-only.  Both conventional
    temporary locations are aliases of one real directory below the workspace;
    this preserves tools which insist on ``/tmp`` without allowing a byte to be
    created in the host temporary filesystem.  Additional credential or
    toolchain mounts are always read-only.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError(
            "bubblewrap is required to enforce workspace-only writes")
    workspace = Path(workspace).resolve()
    sandbox_home = workspace / ".sandbox-home"
    scratch = sandbox_home / "tmp"
    shared_memory = sandbox_home / "shm"
    masks = sandbox_home / "masks"
    empty_directory = masks / "empty-directory"
    empty_file = masks / "empty-file"
    scratch.mkdir(parents=True, exist_ok=True)
    shared_memory.mkdir(parents=True, exist_ok=True)
    empty_directory.mkdir(parents=True, exist_ok=True)
    empty_file.parent.mkdir(parents=True, exist_ok=True)
    empty_file.touch(exist_ok=True)
    argv = [
        bwrap, "--die-with-parent", "--unshare-pid", "--unshare-ipc",
        "--unshare-uts", "--unshare-cgroup-try",
    ]
    if unshare_network:
        argv.append("--unshare-net")
    argv += [
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        *_peer_workspace_mask(workspace, masks),
        "--bind" if workspace_writable else "--ro-bind",
        str(workspace), str(workspace),
        # A read-only review still needs isolated CLI/session/cache state. This
        # is runtime-owned and excluded from every candidate diff.
        "--bind", str(sandbox_home), str(sandbox_home),
        "--bind", str(scratch), "/tmp",
        "--bind", str(scratch), "/var/tmp",
        "--bind", str(shared_memory), "/dev/shm",
    ]
    for source, destination in read_only_binds:
        source = Path(source).resolve()
        destination = Path(destination).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        if not destination.is_relative_to(workspace):
            raise RuntimeError(
                f"read-only mount target is outside workspace: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.touch(exist_ok=True)
        argv += ["--ro-bind", str(source), str(destination)]
    from configuration import PROJECT_ROOT
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    private_paths = (
        Path.home() / ".codex", Path.home() / ".claude",
        Path.home() / ".ssh", Path.home() / ".gnupg",
        Path.home() / ".aws", Path.home() / ".config" / "gh",
        Path.home() / ".config" / "moonshiner",
        Path.home() / ".netrc", Path.home() / ".npmrc",
        runtime, PROJECT_ROOT.resolve(),
    )
    for target in dict.fromkeys(path for path in private_paths if path.exists()):
        resolved = target.resolve()
        if resolved == workspace or resolved in workspace.parents:
            continue
        source = empty_directory if target.is_dir() else empty_file
        argv += ["--ro-bind", str(source), str(target)]
    return argv + ["--chdir", str(workspace), "--", *command]


def _process_tree_snapshot(root_pid: int) -> tuple[tuple[int, ...], int, int]:
    """Return process membership, CPU ticks, and I/O for one child tree."""
    processes: dict[int, tuple[int, int, int]] = {}
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return (), 0, 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            fields = stat[stat.rfind(")") + 2:].split()
            pid = int(entry.name)
            ppid = int(fields[1])
            cpu = int(fields[11]) + int(fields[12])
            io_total = 0
            try:
                for line in (entry / "io").read_text().splitlines():
                    key, value = line.split(":", 1)
                    if key in {"rchar", "wchar", "read_bytes", "write_bytes"}:
                        io_total += int(value)
            except (OSError, ValueError):
                pass
            processes[pid] = (ppid, cpu, io_total)
        except (OSError, ValueError, IndexError):
            continue

    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _, _) in processes.items():
            if ppid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    live = tuple(sorted(pid for pid in descendants if pid in processes))
    return (live,
            sum(processes[pid][1] for pid in live),
            sum(processes[pid][2] for pid in live))


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Kill the process session plus descendants that escaped its group."""
    descendants = _process_tree_snapshot(process.pid)[0]
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    for pid in reversed(descendants):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def wait_with_inactivity_timeout(
        process: subprocess.Popen, inactivity_timeout: float, *,
        activity_probe=None) -> int:
    """Wait for an existing process, killing only a continuously idle tree."""
    if inactivity_timeout <= 0:
        raise ValueError("inactivity_timeout must be positive")
    interval = max(0.01, min(1.0, inactivity_timeout / 4))
    last_activity = time.monotonic()
    snapshot = _process_tree_snapshot(process.pid)
    external = activity_probe() if activity_probe else None
    while True:
        try:
            return process.wait(timeout=interval)
        except subprocess.TimeoutExpired:
            current = _process_tree_snapshot(process.pid)
            current_external = activity_probe() if activity_probe else None
            if current != snapshot or current_external != external:
                snapshot, external = current, current_external
                last_activity = time.monotonic()
                continue
            if time.monotonic() - last_activity < inactivity_timeout:
                continue

            # Re-sample immediately before the destructive action. Activity
            # racing the timeout cancels the kill and resets the clock.
            final = _process_tree_snapshot(process.pid)
            final_external = activity_probe() if activity_probe else None
            if final != snapshot or final_external != external:
                snapshot, external = final, final_external
                last_activity = time.monotonic()
                continue
            _kill_process_tree(process)
            process.wait()
            raise subprocess.TimeoutExpired(process.args, inactivity_timeout)


def run_with_inactivity_timeout(
        command: list[str], *, inactivity_timeout: float,
        cwd: str | Path | None = None, input: str | bytes | None = None,
        capture_output: bool = False, text: bool = False,
        stdin=None, stdout=None, stderr=None, env: dict[str, str] | None = None,
        activity_probe=None,
        **popen_options) -> subprocess.CompletedProcess:
    """Run until completion or sustained whole-process-tree inactivity.

    There is deliberately no total-runtime deadline. Output, CPU, disk I/O,
    or child-process changes reset the inactivity clock. Only a tree whose
    complete observable state remains unchanged for ``inactivity_timeout`` is
    killed.
    """
    if inactivity_timeout <= 0:
        raise ValueError("inactivity_timeout must be positive")
    if capture_output and (stdout is not None or stderr is not None):
        raise ValueError("stdout/stderr may not be used with capture_output")
    process = subprocess.Popen(
        command, cwd=cwd,
        stdin=subprocess.PIPE if input is not None else stdin,
        stdout=subprocess.PIPE if capture_output else stdout,
        stderr=subprocess.PIPE if capture_output else stderr,
        text=text, env=env, start_new_session=True, **popen_options)
    interval = max(0.01, min(1.0, inactivity_timeout / 4))
    last_activity = time.monotonic()
    snapshot = _process_tree_snapshot(process.pid)
    external = activity_probe() if activity_probe else None
    completed = threading.Event()
    communication: dict[str, object] = {}

    def communicate() -> None:
        try:
            communication["result"] = process.communicate(input=input)
        except BaseException as error:
            communication["error"] = error
        finally:
            completed.set()

    reader = threading.Thread(target=communicate, daemon=True)
    reader.start()
    timed_out = False
    while not completed.wait(interval):
        current = _process_tree_snapshot(process.pid)
        current_external = activity_probe() if activity_probe else None
        if current != snapshot or current_external != external:
            snapshot, external = current, current_external
            last_activity = time.monotonic()
            continue
        if time.monotonic() - last_activity < inactivity_timeout:
            continue

        # Re-sample immediately before the destructive action. Activity
        # racing the timeout cancels the kill and resets the clock.
        final = _process_tree_snapshot(process.pid)
        final_external = activity_probe() if activity_probe else None
        if final != snapshot or final_external != external:
            snapshot, external = final, final_external
            last_activity = time.monotonic()
            continue
        timed_out = True
        _kill_process_tree(process)
        break

    reader.join()
    if "error" in communication:
        raise communication["error"]
    output, errors = communication.get("result", (None, None))
    if timed_out:
        raise subprocess.TimeoutExpired(
            command, inactivity_timeout, output=output, stderr=errors)
    return subprocess.CompletedProcess(command, process.returncode, output, errors)


@dataclass
class TraceResult:
    """Everything the pipeline needs after a teacher runs on one seed.

    ``raw_path`` is the primary buildable artifact (a persisted rollout or the
    finalized event stream) that ``build_dataset`` later normalizes. Attestation
    fields let the caller decide acceptance without knowing runtime internals.
    """
    raw_path: Path
    trace_format: str
    return_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    stream_success: bool = False
    observed_model: str | None = None
    observed_models: list[str] = field(default_factory=list)
    model_attested: bool = True
    model_fallback: bool = False
    safeguard_refusal: bool = False
    # Set when the teacher asked the operator a question. A trace runs
    # headless, so the reply never comes and the session is over; the caller
    # defers this seed rather than treating a stopped agent as a broken harness.
    blocked_on_question: bool = False
    usage: dict = field(default_factory=dict)
    error: str | None = None
    # Set (to a human-readable reason) when a metered account hit a usage limit
    # before the attempt could complete; the caller must fail closed and defer.
    unavailable: str | None = None
    # Runtime-specific provenance merged into the trace's ``teacher`` meta block
    # (e.g. thread_id, session_id, upstream_audit, runtime_version).
    provenance: dict = field(default_factory=dict)


@dataclass
class ReviewResult:
    """Outcome of an independent read-only judge pass over a candidate trace."""
    raw_text: str
    verdict: dict | None
    return_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    observed_model: str | None = None
    model_attested: bool = True
    error: str | None = None


class Runtime(abc.ABC):
    """One agentic CLI usable as teacher and/or judge."""

    #: Stable identifier used in config.json (``teacher.runtime`` / ``judge.runtime``).
    name: str = "base"
    #: ``trace_format`` strings this runtime's normalizer can parse.
    trace_formats: tuple[str, ...] = ()

    def __init__(self, config: dict, role_config: dict):
        self.config = config
        self.role = role_config
        self.runtime_config = config.get("runtimes", {}).get(self.name, {})

    def model_matches(self, observed: str | None) -> bool:
        from model_profile import matches
        profile = self.config.get("model_profile") or {}
        aliases = ((profile.get("attestation_aliases") or [])
                   if profile.get("id") == self.role.get("model") else [])
        return matches(str(self.role.get("model") or ""), observed, aliases)

    def trace_capabilities(self) -> frozenset[str]:
        """Capabilities genuinely provided by this native trace adapter."""
        return frozenset()

    def trace_probe_command(self) -> list[str]:
        """Return the native executable probe used before an OCI paid call."""
        return [str(self.runtime_config.get("cli") or self.name), "--version"]

    def oci_runtime_command(
            self, command: list[str], workspace: Path
            ) -> tuple[list[str], tuple[tuple[Path, Path], ...]]:
        """Expose a native command inside an OCI image at the adapter boundary.

        Test and third-party runtimes whose command is already supplied by the
        task image need no mounts. Native Moonshiner adapters override this to
        mount their genuine installed CLI read-only.
        """
        return list(command), ()

    def prepare_trace_command(
            self, seed: dict, command: list[str], workspace: Path, *,
            environment: dict[str, str],
            read_only_binds: tuple[tuple[Path, Path], ...] = ()) -> list[str]:
        """Use the one local boundary or the explicit OCI environment boundary."""
        from task_environment import environment_spec, environment_trace_command
        if environment_spec(seed) is not None:
            return environment_trace_command(
                seed, self, command, workspace, environment=environment,
                read_only_binds=read_only_binds)
        return workspace_only_command(
            command, workspace, read_only_binds=read_only_binds)

    @staticmethod
    def require_persistent_workspace(workspace: Path) -> Path:
        """Refuse model contexts that are ephemeral or inherit AGENTS.md."""
        resolved = Path(workspace).resolve()
        from configuration import PROJECT_ROOT
        project = PROJECT_ROOT.resolve()
        if resolved == project or project in resolved.parents:
            raise RuntimeError(
                f"model workspace must be outside the project repository: {resolved}")
        for directory in (resolved, *resolved.parents):
            if (directory / "AGENTS.md").is_file():
                raise RuntimeError(
                    f"model workspace ancestry contains AGENTS.md: {directory}")
        for temporary_root in (Path("/tmp"), Path("/var/tmp")):
            root = temporary_root.resolve()
            if resolved == root or root in resolved.parents:
                raise RuntimeError(
                    f"model workspace must be persistent; temporary path prohibited: {resolved}")
        return resolved

    @staticmethod
    def teacher_environment(workspace: Path) -> dict[str, str]:
        """Confine every teacher-owned writable cache to its workspace."""
        home = Path(workspace) / ".sandbox-home"
        home.mkdir(parents=True, exist_ok=True)
        environment = {key: os.environ[key]
                       for key in ("PATH", "LANG", "LC_ALL", "TERM")
                       if key in os.environ}
        environment.update({
            key: str(home / suffix) for key, suffix in {
                "HOME": "", "XDG_CACHE_HOME": ".cache",
                "XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share",
                "DOTNET_CLI_HOME": ".dotnet", "NUGET_PACKAGES": ".nuget/packages",
                "GOCACHE": ".cache/go-build", "GOMODCACHE": "go/pkg/mod",
                "GOPATH": "go", "TMPDIR": "tmp", "TMP": "tmp",
                "TEMP": "tmp", "CODEX_HOME": "codex",
                "CLAUDE_CONFIG_DIR": "claude"}.items()})
        for value in environment.values():
            path = Path(value)
            if path.is_relative_to(home):
                path.mkdir(parents=True, exist_ok=True)
        return environment

    # -- lifecycle ---------------------------------------------------------- #
    @abc.abstractmethod
    def preflight(self, *, require_auth: bool = False) -> None:
        """Verify the CLI/toolchain is present and correctly pinned.

        Raises ``SystemExit`` with a human message when unusable.
        """

    # -- teacher ------------------------------------------------------------ #
    @abc.abstractmethod
    def run_trace(self, seed: dict, workspace: Path, *, out_dir: Path,
                  system_prompt: str, prompt: str,
                  interaction: list[str] | None = None,
                  security: bool = False,
                  tools: list[str] | None = None) -> TraceResult:
        """Run the teacher on ``seed`` inside the prepared ``workspace``.

        Implementations write raw artifacts under ``out_dir`` and return a
        :class:`TraceResult`. They must not run verification (the caller does).
        """

    # -- judge -------------------------------------------------------------- #
    @abc.abstractmethod
    def run_review(self, instruction: str, workspace: Path, *, out_dir: Path,
                   schema: dict | None = None,
                   read_only: bool = True) -> ReviewResult:
        """Run this runtime as an independent, read-only reviewer/judge."""

    # -- normalization (build_dataset) ------------------------------------- #
    @staticmethod
    @abc.abstractmethod
    def parse_stream(path: Path, workspace: str | None
                     ) -> tuple[list[dict], dict]:
        """Convert a raw trace into OpenAI-style messages and parse stats."""
