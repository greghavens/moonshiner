#!/usr/bin/env python3
"""Exercise the reference solution through the genuine library executable."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DELIVERABLE_PATH = ROOT / "invocation.txt"
DELIVERABLE_DIGEST = "af481348fd499fae2398bfaa9db7f5f07131498a4bd35036427a4717115384c7"
RUNTIME_FILES = (
    ROOT / ".library" / "runtime" / "catalog.sqlite3",
    ROOT / ".library" / "runtime" / "catalog.sqlite3-shm",
    ROOT / ".library" / "runtime" / "catalog.sqlite3-wal",
    ROOT / ".library" / "runtime" / "initialize.lock",
    ROOT / ".harness" / "runtime" / "library-audit.jsonl",
)


def main() -> None:
    if (
        not DELIVERABLE_PATH.is_file()
        or hashlib.sha256(DELIVERABLE_PATH.read_bytes()).hexdigest()
        != DELIVERABLE_DIGEST
    ):
        return
    for path in RUNTIME_FILES:
        path.unlink(missing_ok=True)
    help_result = subprocess.run(
        ["./libraryctl", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if help_result.returncode != 0:
        raise SystemExit(
            "reference library help failed: " + help_result.stderr.strip()
        )
    result = subprocess.run(
        ["./libraryctl", "get", "--id", "lib-113"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "reference library retrieval failed: " + result.stderr.strip()
        )


if __name__ == "__main__":
    main()
