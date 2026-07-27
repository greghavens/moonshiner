#!/usr/bin/env python3
"""Offline protected verification for the blob metadata rollout."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_HASHES = {
    "fake_store.go": "2d76b4f998ebf146cf491ad40e7c4700375237c0a6a70105767e60e784e6532e",
    "go.mod": "0a717ac6655772906888468bc976577c2aea61ab5cb94b076f98f66d4b5ea1ad",
    "model.go": "fa7b2f2776b27ecfb6ae084b03e91328821cf0e266a9b55d7efe905f7dfd61a8",
    "protected_tests/backfill_test.go": "a9453a953ee72503eabe2a13603a580b258d17dd90d769531b098c730ef16e55",
}
EXPECTED_FILES = {
    "fake_store.go",
    "go.mod",
    "migration.go",
    "model.go",
    "protected_tests/backfill_test.go",
    "tests/verify.py",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixture_integrity() -> None:
    actual_files = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts
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


def run_go_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="blob-rollout-go-") as temp:
        temp_root = Path(temp)
        environment = os.environ.copy()
        environment.update(
            {
                "GOCACHE": str(temp_root / "cache"),
                "GOMODCACHE": str(temp_root / "modules"),
                "GOPATH": str(temp_root / "gopath"),
                "GOPROXY": "off",
                "GOSUMDB": "off",
                "GOTELEMETRY": "off",
                "HOME": str(temp_root / "home"),
                "XDG_CONFIG_HOME": str(temp_root / "config"),
            }
        )
        completed = subprocess.run(
            ["go", "test", "-count=1", "./..."],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=25,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "protected Go tests failed:\n" + completed.stdout[-12000:]
            )
        print(completed.stdout, end="")


def main() -> int:
    try:
        verify_fixture_integrity()
        run_go_tests()
    except (AssertionError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL verification: {error}", file=sys.stderr)
        return 1
    print("PASS protected blob metadata rollout verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
