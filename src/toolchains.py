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
MISSING_EXECUTABLES = (
    re.compile(r"(?:bwrap:\s*)?execvp\s+([^:\s]+):\s+No such file or directory",
               re.IGNORECASE),
    re.compile(r"\[Errno 2\]\s+No such file or directory:\s*['\"]([^'\"]+)['\"]",
               re.IGNORECASE),
    re.compile(r"(?:^|\n)[^\n:]*:\s*([^\s:]+):\s*(?:command )?not found(?:\n|$)",
               re.IGNORECASE),
)


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
