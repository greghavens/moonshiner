#!/usr/bin/env python3
"""Protected verifier for the single-file vCenter Java client."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_SHA256 = {
    "docs/contract.json": "ac369273655f6075829d9d16709c99bd94dbd316046b5fcf2a229f5ec83b69be",
    "docs/official_sources.json": "c526f68909072aef477f28c051b45eefc299272aea24a36aa7329b02b352dc0e",
    "tests/MockVcenterServer.java": "c5bc001a612be62787b7a69c4b261ce029f2aade3a13fa5e3e1c9393d6925bd2",
    "tests/TestMain.java": "025c24557ee22261ce3978a4b612f2e23b919d70fe840cb36666f462ad2428f8",
}


def fail(message: str) -> "NoReturn":
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file was modified: {relative}")


def run_checked(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        fail(f"required command is unavailable: {command[0]}")
    except subprocess.TimeoutExpired:
        fail(f"command timed out: {command[0]}")
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="", file=sys.stderr)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        fail(f"command exited with status {completed.returncode}: {command[0]}")
    return completed


def main() -> None:
    verify_protected_files()
    client = ROOT / "VcenterInventoryClient.java"
    if not client.is_file():
        fail("editable client is missing: VcenterInventoryClient.java")

    with tempfile.TemporaryDirectory(prefix="vcf91-0128-") as classes:
        run_checked(
            [
                "javac",
                "--release",
                "17",
                "--add-modules",
                "jdk.httpserver",
                "-encoding",
                "UTF-8",
                "-d",
                classes,
                str(client),
                str(ROOT / "tests/MockVcenterServer.java"),
                str(ROOT / "tests/TestMain.java"),
            ],
            timeout=20,
        )
        completed = run_checked(
            [
                "java",
                "--add-modules",
                "jdk.httpserver",
                "-cp",
                classes,
                "TestMain",
            ],
            timeout=20,
        )
    expected = "PASS: contract-pinned access-token resume and ordering"
    if expected not in completed.stdout:
        fail("TestMain did not report its completion sentinel")
    print(expected)


if __name__ == "__main__":
    main()
