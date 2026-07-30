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
    "docs/contract.json": "70d726a633ebe55269a965fac043f11af7d8137129b212b746f248e3531765a9",
    "docs/official_sources.json": "c526f68909072aef477f28c051b45eefc299272aea24a36aa7329b02b352dc0e",
    "tests/MockVcenterServer.java": "efff9a56df8f6256c61c1900550cab8e95655db5d235e4ce78d9e65a469267cc",
    "tests/TestMain.java": "0f817cbd20c1e3549c336a197ab817525a990ab0703f2e1d5aba2e6b969ccc65",
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
