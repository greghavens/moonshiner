#!/usr/bin/env python3
"""Deterministic offline verification for the generated drift gate."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_HASHES = {
    "api/events.json": "4835bd91a0d93853c4b0e26029e103b2da27f50478780e0d6f82dab542901340",
    "cmd/eventgen/main.go": "6e60809273630e60905251417f2e47a38e82a640bae07c61db464906217b84b4",
    "go.mod": "5bcbc252a27855b947d0ff10810a9f4d6632778d2965c6ae2c85f63b518dfa26",
    "internal/eventwire/codec.go": "7775748ff1bd36e3cd4c6557a4db97021ce72c7a3b6b98d9a97cc9d2af54d358",
    "protected_tests/drift_gate_test.py": "093fe28c64178f1f3ac654f0480f0876937f7f8254cc1a06501750263ba1438c",
    "protected_tests/eventwire_test.go": "5827d33aa98653e7a69a55a9134cae0be7c884a774c5bb639eaaf30871779949",
}
EXPECTED_FILES = {
    "Makefile",
    "api/events.json",
    "cmd/eventgen/main.go",
    "docs/events.md",
    "go.mod",
    "internal/eventwire/codec.go",
    "internal/eventwire/types.gen.go",
    "protected_tests/drift_gate_test.py",
    "protected_tests/eventwire_test.go",
    "tests/verify.py",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture_integrity() -> None:
    actual_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
        and ".sandbox-home" not in path.parts
    }
    unexpected = sorted(actual_files - EXPECTED_FILES)
    missing = sorted(EXPECTED_FILES - actual_files)
    if unexpected or missing:
        raise AssertionError(
            f"fixture layout changed; unexpected={unexpected}, missing={missing}"
        )
    for relative, expected in IMMUTABLE_HASHES.items():
        actual = digest(ROOT / relative)
        if actual != expected:
            raise AssertionError(f"protected fixture changed: {relative}")


def offline_environment(base: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(base / "home"),
            "GOCACHE": str(base / "go-build"),
            "GOMODCACHE": str(base / "go-mod"),
            "GOPATH": str(base / "gopath"),
            "GOENV": "off",
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
        }
    )
    for name in ("HOME", "GOCACHE", "GOMODCACHE", "GOPATH"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    return environment


def run(command: list[str], environment: dict[str, str], timeout: int = 35) -> None:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{' '.join(command)} failed with {completed.returncode}:\n"
            + completed.stdout[-16000:]
        )
    print(completed.stdout, end="")


def main() -> int:
    try:
        verify_fixture_integrity()
        with tempfile.TemporaryDirectory(prefix="eventwire-verify-") as temporary:
            environment = offline_environment(Path(temporary))
            run(
                [
                    "python3",
                    "-B",
                    "protected_tests/drift_gate_test.py",
                ],
                environment,
            )
            run(
                [
                    "go",
                    "test",
                    "-count=1",
                    "./...",
                ],
                environment,
            )
    except (AssertionError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL verification: {error}", file=sys.stderr)
        return 1
    print("PASS protected generated drift gate verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
