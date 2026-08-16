"""Execution support for canonical seeds backed by a genuine OCI environment.

The seed remains Moonshiner's ordinary task dictionary.  This module only
interprets the already-existing ``environment`` field when its explicit type is
``oci``; every other seed continues through the existing local-fixture path.
"""
from __future__ import annotations

import hashlib
import io
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from configuration import PROJECT_STATE
from runtimes import TraceHarnessInfrastructureFailure
from runtimes.base import run_with_inactivity_timeout


OCI_ROOT = PROJECT_STATE / "oci"
REQUIRED_ENVIRONMENT_FIELDS = {
    "type", "image", "repository", "base_commit", "workspace",
    "test_patch", "fail_to_pass", "pass_to_pass", "install_config",
}
REQUIRED_INSTALL_FIELDS = {
    "base_image_name", "docker_specs", "install", "log_parser", "test_cmd",
}


def environment_spec(seed: dict) -> dict | None:
    """Return the explicit OCI contract, or ``None`` for every ordinary seed."""
    value = seed.get("environment")
    if not isinstance(value, dict) or value.get("type") != "oci":
        return None
    return value


def validate_environment_spec(seed: dict) -> str | None:
    """Return a precise structural failure for an OCI contract, if any."""
    spec = environment_spec(seed)
    if spec is None:
        return None
    missing = sorted(REQUIRED_ENVIRONMENT_FIELDS - set(spec))
    if missing:
        return f"OCI environment is missing fields: {missing}"
    for field in ("image", "repository", "base_commit", "workspace",
                  "test_patch"):
        if not isinstance(spec[field], str) or not spec[field].strip():
            return f"OCI environment {field} must be a nonempty string"
    if len(spec["base_commit"]) != 40 or any(
            character not in "0123456789abcdefABCDEF"
            for character in spec["base_commit"]):
        return "OCI environment base_commit must be a 40-character hexadecimal SHA"
    if not PurePosixPath(spec["workspace"]).is_absolute():
        return "OCI environment workspace must be an absolute container path"
    for field in ("fail_to_pass", "pass_to_pass"):
        value = spec[field]
        if (not isinstance(value, list)
                or any(not isinstance(item, str) for item in value)):
            return f"OCI environment {field} must be a list of strings"
    install = spec["install_config"]
    if not isinstance(install, dict):
        return "OCI environment install_config must be an object"
    absent = sorted(REQUIRED_INSTALL_FIELDS - set(install))
    if absent:
        return f"OCI environment install_config is missing fields: {absent}"
    if (not isinstance(install["test_cmd"], str)
            or not install["test_cmd"].strip()):
        return "OCI environment install_config.test_cmd must be nonempty"
    if seed.get("verify_cmd") != install["test_cmd"]:
        return "verify_cmd must exactly match OCI install_config.test_cmd"
    directory = seed.get("_dir")
    if directory is not None:
        patch = Path(directory) / spec["test_patch"]
        try:
            patch.relative_to(Path(directory))
        except ValueError:
            return "OCI environment test_patch escapes the seed directory"
        if not patch.is_file() or patch.stat().st_size == 0:
            return "OCI environment test_patch is missing or empty"
    return None


def podman_command(*arguments: str) -> list[str]:
    """Build a Podman command whose complete mutable state is project-owned."""
    executable = shutil.which("podman")
    if executable is None:
        raise TraceHarnessInfrastructureFailure(
            "Podman is required for OCI task environments")
    storage = OCI_ROOT / "storage"
    runroot = OCI_ROOT / "runroot"
    storage.mkdir(parents=True, exist_ok=True)
    runroot.mkdir(parents=True, exist_ok=True)
    return [executable, "--root", str(storage), "--runroot", str(runroot),
            *map(str, arguments)]


def container_memory_ceiling() -> list[str]:
    """Bound a container's memory with the queue's own ceiling.

    Rootless Podman runs each container in its own transient ``libpod-*.scope``
    under ``app.slice`` -- a sibling of the queue unit, not a child -- so the
    unit's ``MemoryMax`` never reaches it. Without this an OCI seed is the one
    path with no ceiling at all, and a runaway there takes the host down
    instead of failing alone.

    ``pipeline.memory_max`` is the single knob, same as the queue unit. Podman
    defaults ``--memory-swap`` to twice ``--memory``, which matches the unit's
    ``MemoryMax`` plus an equal ``MemorySwapMax``. Set the config to "" or
    "infinity" to remove the ceiling.
    """
    from common import CONFIG
    value = str((CONFIG.get("pipeline") or {}).get("memory_max", "8G")).strip()
    if not value or value.lower() == "infinity":
        return []
    return ["--memory", value]


def _completed(*arguments: str, input: str | bytes | None = None,
               inactivity_timeout: int = 600,
               binary: bool = False) -> subprocess.CompletedProcess:
    command = podman_command(*arguments)
    try:
        return run_with_inactivity_timeout(
            command, input=input, capture_output=True,
            text=not binary and not isinstance(input, bytes),
            inactivity_timeout=inactivity_timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise TraceHarnessInfrastructureFailure(
            f"OCI environment command failed: {error}") from error


def _checked(*arguments: str, input: str | bytes | None = None,
             inactivity_timeout: int = 600,
             binary: bool = False) -> subprocess.CompletedProcess:
    result = _completed(*arguments, input=input,
                        inactivity_timeout=inactivity_timeout, binary=binary)
    if result.returncode != 0:
        stdout = result.stdout.decode(errors="replace") \
            if isinstance(result.stdout, bytes) else (result.stdout or "")
        stderr = result.stderr.decode(errors="replace") \
            if isinstance(result.stderr, bytes) else (result.stderr or "")
        detail = (stdout + "\n" + stderr).strip()[-2000:]
        raise TraceHarnessInfrastructureFailure(
            f"OCI environment command exited {result.returncode}: {detail}")
    return result


def _image_present(image: str) -> bool:
    return _completed("image", "exists", image, inactivity_timeout=30).returncode == 0


def prepare_environment(seed: dict, runtime=None) -> dict:
    """Resolve and validate the task image before any paid model attempt."""
    spec = environment_spec(seed)
    if spec is None:
        return {}
    failure = validate_environment_spec(seed)
    if failure:
        raise TraceHarnessInfrastructureFailure(failure)
    image = spec["image"]
    if not _image_present(image):
        _checked("pull", image, inactivity_timeout=1800)
    probe = (
        f"test -d {shlex.quote(spec['workspace'])} && "
        f"test \"$(git -C {shlex.quote(spec['workspace'])} rev-parse HEAD)\" = "
        f"{shlex.quote(spec['base_commit'])}")
    _checked("run", "--rm", "--network", "none",
             *container_memory_ceiling(),
             "--security-opt", "label=disable", "--entrypoint", "/bin/sh",
             image, "-lc", probe)
    return environment_provenance(seed)


def _safe_extract_archive(payload: bytes, workspace: Path) -> None:
    workspace = workspace.resolve()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise TraceHarnessInfrastructureFailure(
                    f"OCI repository archive contains unsafe path: {member.name}")
            if member.ischr() or member.isblk() or member.isfifo():
                raise TraceHarnessInfrastructureFailure(
                    f"OCI repository archive contains special file: {member.name}")
            if member.issym() or member.islnk():
                target = PurePosixPath(member.linkname)
                parts = [] if member.islnk() else list(relative.parent.parts)
                escaped = target.is_absolute()
                for part in target.parts:
                    if part in {"", "."}:
                        continue
                    if part == "..":
                        if not parts:
                            escaped = True
                            break
                        parts.pop()
                    else:
                        parts.append(part)
                if escaped:
                    raise TraceHarnessInfrastructureFailure(
                        f"OCI repository archive contains escaping link: {member.name}")
        archive.extractall(workspace, filter="data")


def materialize_environment(seed: dict, workspace: Path) -> None:
    """Populate *workspace* from the pinned repository commit in the image."""
    spec = environment_spec(seed)
    if spec is None:
        raise ValueError("seed has no OCI environment")
    command = (
        f"git -C {shlex.quote(spec['workspace'])} archive --format=tar "
        f"{shlex.quote(spec['base_commit'])}")
    result = _checked("run", "--rm", "--network", "none",
                      *container_memory_ceiling(),
                      "--security-opt", "label=disable",
                      "--entrypoint", "/bin/sh", spec["image"], "-lc", command,
                      binary=True)
    payload = result.stdout if isinstance(result.stdout, bytes) \
        else result.stdout.encode()
    _safe_extract_archive(payload, workspace)


def _mount(source: Path, destination: str, read_only: bool = True) -> list[str]:
    option = f"{source.resolve()}:{destination}" + (":ro" if read_only else ":rw")
    return ["--volume", option]


def environment_trace_command(seed: dict, runtime, command: list[str],
                              workspace: Path, *, environment: dict[str, str],
                              read_only_binds: tuple[tuple[Path, Path], ...] = ()
                              ) -> list[str]:
    """Run one native adapter inside the task image with the genuine toolchain."""
    spec = environment_spec(seed)
    if spec is None:
        raise ValueError("seed has no OCI environment")
    rewritten, runtime_binds = runtime.oci_runtime_command(command, workspace)
    if not rewritten:
        raise TraceHarnessInfrastructureFailure("native OCI command is empty")
    workspace = workspace.resolve()
    container_workspace = spec["workspace"]
    rewritten = [container_workspace if value == str(workspace) else value
                 for value in rewritten]
    runner = workspace / ".sandbox-home" / "oci-trace-runner.sh"
    exchange = workspace / ".sandbox-home" / "oci-candidate.patch"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        "#!/bin/sh\n"
        f"git config --global --add safe.directory {shlex.quote(container_workspace)}\n"
        "\"$@\"\n"
        "status=$?\n"
        f"git -C {shlex.quote(container_workspace)} add -A -N\n"
        f"git -C {shlex.quote(container_workspace)} diff --binary HEAD -- . > "
        f"{shlex.quote(str(exchange))}\n"
        f"if test -s {shlex.quote(str(exchange))}; then "
        f"git -C {shlex.quote(str(workspace))} apply --whitespace=nowarn "
        f"{shlex.quote(str(exchange))}; fi\n"
        "exit \"$status\"\n")
    arguments = ["run", "--rm", "--interactive", "--network", "host",
                 *container_memory_ceiling(),
                 "--userns", "keep-id:uid=0,gid=0",
                 "--security-opt", "label=disable",
                 *_mount(workspace, str(workspace), read_only=False),
                 "--workdir", container_workspace]
    for source, destination in (*read_only_binds, *runtime_binds):
        source = Path(source).resolve()
        destination = Path(destination)
        if destination == workspace or destination.is_relative_to(workspace):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.touch(exist_ok=True)
        arguments += _mount(source, str(destination), read_only=True)
    for name in sorted(environment):
        arguments += ["--env", name]
    arguments += ["--entrypoint", "/bin/sh", spec["image"],
                  str(runner), *rewritten]
    return podman_command(*arguments)


def probe_runtime(seed: dict, runtime, workspace: Path,
                  environment: dict[str, str]) -> None:
    """Exercise the same containerized native executable before an attempt."""
    if environment_spec(seed) is None or runtime is None:
        return
    command = runtime.trace_probe_command()
    wrapped = environment_trace_command(
        seed, runtime, command, workspace, environment=environment)
    try:
        result = run_with_inactivity_timeout(
            wrapped, cwd=workspace, env=environment, capture_output=True,
            text=True, inactivity_timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise TraceHarnessInfrastructureFailure(
            f"native harness cannot start in OCI task environment: {error}") from error
    if result.returncode != 0:
        raise TraceHarnessInfrastructureFailure(
            "native harness cannot start in OCI task environment: "
            + (result.stdout + "\n" + result.stderr).strip()[-1000:])


def verify_environment(seed: dict, workspace: Path,
                       inactivity_timeout: int = 180) -> tuple[bool, str]:
    """Apply candidate and held-out test patches, then run the genuine tests."""
    spec = environment_spec(seed)
    if spec is None:
        raise ValueError("seed has no OCI environment")
    from common import git_diff
    scratch = workspace / ".sandbox-home" / "oci-verify"
    scratch.mkdir(parents=True, exist_ok=True)
    candidate = scratch / "candidate.patch"
    candidate.write_text(git_diff(workspace))
    test_patch = Path(seed["_dir"]) / spec["test_patch"]
    script = " && ".join((
        f"cd {shlex.quote(spec['workspace'])}",
        f"git reset --hard {shlex.quote(spec['base_commit'])}",
        "git clean -fd",
        "if test -s /opt/moonshiner/candidate.patch; then "
        "git apply --whitespace=nowarn /opt/moonshiner/candidate.patch; fi",
        "git apply --whitespace=nowarn /opt/moonshiner/test.patch",
        spec["install_config"]["test_cmd"],
    ))
    result = _completed(
        "run", "--rm", "--network", "none", *container_memory_ceiling(),
        "--security-opt", "label=disable",
        *_mount(candidate, "/opt/moonshiner/candidate.patch"),
        *_mount(test_patch, "/opt/moonshiner/test.patch"),
        "--entrypoint", "/bin/sh", spec["image"], "-lc", script,
        inactivity_timeout=inactivity_timeout)
    stdout = result.stdout.decode(errors="replace") \
        if isinstance(result.stdout, bytes) else (result.stdout or "")
    stderr = result.stderr.decode(errors="replace") \
        if isinstance(result.stderr, bytes) else (result.stderr or "")
    return result.returncode == 0, (stdout + "\n" + stderr).strip()


def environment_provenance(seed: dict) -> dict:
    """Return actual image identity for trace provenance only."""
    spec = environment_spec(seed)
    if spec is None:
        return {}
    result = _checked("image", "inspect", "--format",
                      "{{.Digest}}|{{.Id}}", spec["image"],
                      inactivity_timeout=30)
    text = str(result.stdout).strip()
    digest, _, image_id = text.partition("|")
    return {
        "type": "oci", "image_digest": digest or image_id,
        "image_id": image_id, "repository": spec["repository"],
        "base_commit": spec["base_commit"],
    }


def environment_control_hashes(seed: dict) -> dict[str, str | None]:
    """Hash held-out environment controls without placing them in the workspace."""
    spec = environment_spec(seed)
    if spec is None or "_dir" not in seed:
        return {}
    relative = spec["test_patch"]
    path = Path(seed["_dir"]) / relative
    return {relative: hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() else None}
