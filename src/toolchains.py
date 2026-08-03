"""Resolve and provision seed toolchains before any paid trace call."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import fcntl
from functools import lru_cache
from pathlib import Path


FORMULAS = {
    "go": "go",
    "dotnet": "dotnet",
    "ruby": "ruby",
    "java": "openjdk",
    "javac": "openjdk",
    "pwsh": "powershell",
}
POWERSHELL_RUNTIME_MOUNT = "/tmp/moonshiner-powershell"
POWERSHELL_MODULES_MOUNT = "/tmp/moonshiner-powershell-modules"
MISSING_EXECUTABLES = (
    re.compile(r"(?:bwrap:\s*)?execvp\s+([^:\s]+):\s+No such file or directory",
               re.IGNORECASE),
    re.compile(r"\[Errno 2\]\s+No such file or directory:\s*['\"]([^'\"]+)['\"]",
               re.IGNORECASE),
    re.compile(r"(?:^|\n)[^\n:]*:\s*([^\s:]+):\s*(?:command )?not found(?:\n|$)",
               re.IGNORECASE),
)
ENVIRONMENT_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)


@lru_cache(maxsize=1)
def path_entries() -> tuple[str, ...]:
    """Return installed package-manager toolchain bins, including keg-only bins."""
    entries: list[str] = []
    brew = shutil.which("brew")
    if not brew:
        return tuple(entries)
    for formula in sorted(set(FORMULAS.values())):
        result = subprocess.run([brew, "--prefix", formula], capture_output=True,
                                text=True)
        if result.returncode == 0:
            candidate = Path(result.stdout.strip()) / "bin"
            if candidate.is_dir():
                entries.append(str(candidate))
    return tuple(entries)


def effective_path() -> str:
    values = list(path_entries()) + os.environ.get("PATH", "/usr/bin:/bin").split(":")
    return ":".join(dict.fromkeys(value for value in values if value))


def powershell_runtime() -> Path | None:
    """Resolve the complete PowerShell runtime directory on the host."""
    executable = shutil.which("pwsh", path=effective_path())
    return Path(executable).resolve() if executable else None


def powershell_module_root() -> Path:
    """Project-managed modules exposed read-only to verifier sandboxes."""
    from configuration import PROJECT_STATE
    return PROJECT_STATE / "toolchains" / "powershell" / "Modules"


def declared_commands(seed: dict) -> list[str]:
    """Return executable prerequisites declared by one seed."""
    prerequisites = seed.get("prerequisites")
    commands: list[str] = []
    if isinstance(prerequisites, dict):
        for value in prerequisites.get("commands", []):
            if not isinstance(value, str) or not value.strip():
                continue
            command = value.strip()
            # Environment assignments configure a command; they are not
            # executables and therefore have nothing to provision.
            if ENVIRONMENT_ASSIGNMENT.fullmatch(command):
                continue
            commands.append(command)
    if declared_powershell_modules(seed) and "pwsh" not in commands:
        commands.append("pwsh")
    return list(dict.fromkeys(commands))


def declared_powershell_modules(seed: dict) -> list[tuple[str, str]]:
    """Normalize the PowerShell module prerequisite shapes used by seeds."""
    prerequisites = seed.get("prerequisites")
    records: list[object] = []
    if isinstance(prerequisites, dict):
        records.extend(prerequisites.get("powershell_modules") or [])
        commands = prerequisites.get("commands") or []
        if ("pwsh" in commands or "powershell" in prerequisites):
            records.extend(prerequisites.get("modules") or [])
    elif isinstance(prerequisites, list):
        records.extend(prerequisites)

    modules: list[tuple[str, str]] = []
    text_requirement = re.compile(
        r"^\s*([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+)\s+"
        r"(\d+(?:\.\d+)+)")
    for record in records:
        if isinstance(record, dict):
            if (record.get("kind") not in (None, "powershell-module")):
                continue
            name = record.get("name")
            version = (record.get("version") or record.get("required_version")
                       or record.get("minimum_version"))
        elif isinstance(record, str):
            match = text_requirement.match(record)
            if not match:
                continue
            name, version = match.groups()
        else:
            continue
        if isinstance(name, str) and isinstance(version, str) and name and version:
            modules.append((name, version))
    return list(dict.fromkeys(modules))


def missing_executables(detail: str) -> list[str]:
    found: list[str] = []
    for pattern in MISSING_EXECUTABLES:
        found.extend(Path(value).name for value in pattern.findall(detail or ""))
    return list(dict.fromkeys(found))


def provision(tools: list[str]) -> tuple[bool, str]:
    """Install known missing toolchains at user scope and verify PATH resolution."""
    unresolved = [tool for tool in tools if shutil.which(tool, path=effective_path()) is None]
    if not unresolved:
        return True, "required toolchain already available"
    unknown = [tool for tool in unresolved if tool not in FORMULAS]
    if unknown:
        return False, "no Moonshiner toolchain package mapping for: " + ", ".join(unknown)
    brew = shutil.which("brew")
    if not brew:
        return False, "automatic user-level toolchain deployment requires Homebrew"
    from configuration import PROJECT_STATE
    PROJECT_STATE.mkdir(parents=True, exist_ok=True)
    with (PROJECT_STATE / "toolchain-deployment.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path_entries.cache_clear()
        unresolved = [tool for tool in tools
                      if shutil.which(tool, path=effective_path()) is None]
        formulas = list(dict.fromkeys(FORMULAS[tool] for tool in unresolved))
        if formulas:
            result = subprocess.run([brew, "install", *formulas],
                                    stdin=subprocess.DEVNULL,
                                    text=True, capture_output=True)
            if result.returncode:
                detail = (result.stdout + "\n" + result.stderr).strip()[-4000:]
                return False, f"Homebrew toolchain deployment failed: {detail}"
            path_entries.cache_clear()
    still_missing = [tool for tool in unresolved
                     if shutil.which(tool, path=effective_path()) is None]
    if still_missing:
        return False, "toolchain installed but executable unresolved: " + ", ".join(still_missing)
    return True, "deployed and resolved: " + ", ".join(unresolved)


def _module_available(executable: Path, root: Path,
                      name: str, version: str) -> bool:
    script = """
$module = Get-Module -ListAvailable -Name $env:MOONSHINER_MODULE_NAME |
  Where-Object {
    $_.Version.ToString() -eq $env:MOONSHINER_MODULE_VERSION -and
    $_.ModuleBase.StartsWith($env:MOONSHINER_MODULE_PATH,
      [System.StringComparison]::Ordinal)
  } | Select-Object -First 1
if ($null -eq $module) { exit 1 }
"""
    env = os.environ.copy()
    env.update({
        "MOONSHINER_MODULE_NAME": name,
        "MOONSHINER_MODULE_VERSION": version,
        "MOONSHINER_MODULE_PATH": str(root),
        "PSModulePath": str(root) + os.pathsep + env.get("PSModulePath", ""),
    })
    try:
        result = subprocess.run(
            [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-Command", script], stdin=subprocess.DEVNULL, text=True,
            capture_output=True, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def provision_powershell_modules(
        modules: list[tuple[str, str]]) -> tuple[bool, str]:
    """Save exact declared PowerShell modules and dependencies at project scope."""
    if not modules:
        return True, "no PowerShell modules declared"
    executable = powershell_runtime()
    if executable is None:
        return False, "PowerShell module deployment requires pwsh"
    root = powershell_module_root()
    root.mkdir(parents=True, exist_ok=True)
    from configuration import PROJECT_STATE
    PROJECT_STATE.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    with (PROJECT_STATE / "toolchain-deployment.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for name, version in modules:
            if _module_available(executable, root, name, version):
                continue
            script = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Save-Module -Name $env:MOONSHINER_MODULE_NAME `
  -RequiredVersion $env:MOONSHINER_MODULE_VERSION `
  -Path $env:MOONSHINER_MODULE_PATH -Repository PSGallery `
  -AcceptLicense -Force
"""
            env = os.environ.copy()
            env.update({
                "MOONSHINER_MODULE_NAME": name,
                "MOONSHINER_MODULE_VERSION": version,
                "MOONSHINER_MODULE_PATH": str(root),
            })
            try:
                result = subprocess.run(
                    [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive",
                     "-Command", script], stdin=subprocess.DEVNULL, text=True,
                    capture_output=True, env=env, timeout=900)
            except subprocess.TimeoutExpired:
                return False, ("PowerShell module deployment timed out: "
                               f"{name} {version}")
            except OSError as error:
                return False, ("PowerShell module deployment could not start for "
                               f"{name} {version}: {error}")
            if result.returncode:
                detail = (result.stdout + "\n" + result.stderr).strip()[-4000:]
                return False, ("PowerShell module deployment failed for "
                               f"{name} {version}: {detail}")
            if not _module_available(executable, root, name, version):
                return False, ("PowerShell module deployed but unresolved: "
                               f"{name} {version}")
            installed.append(f"{name} {version}")
    if installed:
        return True, "deployed PowerShell modules: " + ", ".join(installed)
    return True, "declared PowerShell modules already available"
