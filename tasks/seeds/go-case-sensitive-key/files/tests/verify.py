#!/usr/bin/env python3
"""Offline protected verification for the identifier casing task."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_HASHES = {
    "evidence/catalog-export.jsonl": "b9f50dd2f376cd8db05ea3af05ab68ce31e195621b171e641dee874e2fbabb89",
    "evidence/key-protocol.md": "318a174efc3563cdc09996199134ea3d092a3340e91326aa93c7a081596c4386",
    "go.mod": "af4dc3d8e7c198950387ead636a666c1a946c44d12bae40d0bf11bedf0a3b28b",
    "model.go": "3c39914f7cb778bc76c64a678da1e37d9d80625f045cd3f8346413d59f497bea",
    "protected_tests/catalog_test.go": "aeac994a2a28605bb1020bbff4584ce43a1363c1155e10a8f4c6e28fa8258ca0",
}
EXPECTED_FILES = {
    "catalog.go",
    "evidence/catalog-export.jsonl",
    "evidence/key-protocol.md",
    "go.mod",
    "model.go",
    "protected_tests/catalog_test.go",
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
    with tempfile.TemporaryDirectory(prefix="case-key-go-") as temp:
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
    print("PASS protected identifier casing verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
