#!/usr/bin/env python3
"""Protected, hermetic acceptance entry point for vcf91-0214."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_SHA256 = {
    "docs/contract.json": "224023d6598c7d9e0dc81450b5d673211b604f811266736b031e3de6c9b431d7",
    "docs/official_sources.json": "5fa4b2d92cfcbb8c953b4214ca94cca1b4e2b3102222a18c670822d3b62273c1",
    "internal/mockvcf/server.go": "bbd27b6259f032deec156b2528fdb6704544dc39de527eeaac8486e6ba8c05a1",
    "internal/verification/acceptance_test.go": "998670dc001cbadbb8c261b0bb4a84773023e329652fc04cc8d52371c9a5e9fc",
}


def verify_protected_files() -> None:
    failures: list[str] = []
    for relative, expected in PROTECTED_SHA256.items():
        path = PROJECT / relative
        if not path.is_file():
            failures.append(f"missing protected file: {relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(
                f"protected file changed: {relative} (sha256 {actual}, expected {expected})"
            )
    if failures:
        raise SystemExit("\n".join(failures))


def main() -> int:
    verify_protected_files()
    with tempfile.TemporaryDirectory(prefix="vcf91-0214-go-") as cache_root:
        environment = os.environ.copy()
        environment["GOTOOLCHAIN"] = "local"
        environment["GOWORK"] = "off"
        environment["GOENV"] = "off"
        environment["GOCACHE"] = str(Path(cache_root) / "build")
        environment["GOMODCACHE"] = str(Path(cache_root) / "modules")
        environment["GOPATH"] = str(Path(cache_root) / "gopath")
        environment["CCACHE_DIR"] = str(Path(cache_root) / "ccache")
        environment["CCACHE_TEMPDIR"] = str(Path(cache_root) / "ccache-tmp")
        completed = subprocess.run(
            ["go", "test", "-race", "-count=1", "./..."],
            cwd=PROJECT,
            env=environment,
            check=False,
        )
        return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
