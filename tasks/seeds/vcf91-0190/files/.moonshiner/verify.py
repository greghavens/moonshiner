#!/usr/bin/env python3
"""Protected verifier for the single-file VCF Log Management Java client."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_SHA256 = {
    "docs/contract.json": "c2c1a5c542df3d47db163fd9fd282b738623c32948a8cd11a3c274ddadd99a87",
    "docs/official_sources.json": "c273bf73ce7c0c337fe79d32020c85b2d89dcbc773226b9d20e116c9592d89ca",
    "tests/MockVcfLogServer.java": "306c009d54f59976910309c8cbe6f38fc64bc4b805b459485c84bf5d92fa2984",
    "tests/TestMain.java": "914adfe134e56e0169a67c909a01b7f4b37cf803fa0f72092725af63e1c54f52",
}


def fail(message: str) -> NoReturn:
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
    client = ROOT / "VcfLogManagementClient.java"
    if not client.is_file():
        fail("editable client is missing: VcfLogManagementClient.java")

    with tempfile.TemporaryDirectory(prefix="vcf91-0190-") as classes:
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
                str(ROOT / "tests" / "MockVcfLogServer.java"),
                str(ROOT / "tests" / "TestMain.java"),
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

    expected = "PASS: contract-pinned VCF token refresh without replay"
    if expected not in completed.stdout:
        fail("TestMain did not report its completion sentinel")
    print(expected)


if __name__ == "__main__":
    main()
