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
    "docs/contract.json": "63fd9e4a29b9b7a7f14a4f3ec903a9749cb5f450a8d1788adcb079ed6ec0471c",
    "docs/official_sources.json": "bbf8a78f9199d1c4b70812708a4ccf3f026721b293177c0ea7139c2913d78c3b",
    "tests/TestJson.java": "320a7e032d275d80c136b2bfeae943528d84742ac06a1c4f6cc428ddef52fd97",
    "tests/MockVcfLogServer.java": "0331b216861a78d23c381f5e7492cc0c088e26085003d404b958d374c7838615",
    "tests/TestMain.java": "2a31b39bb9be5bc5f1139d9000e553dc5ec7b5de7010907d3d856737c52574b3",
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

    with tempfile.TemporaryDirectory(prefix="vcf91-0193-") as classes:
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
                str(ROOT / "tests" / "TestJson.java"),
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

    expected = "PASS: contract-pinned truthful partial VCF log-routing report"
    if expected not in completed.stdout:
        fail("TestMain did not report its completion sentinel")
    print(expected)


if __name__ == "__main__":
    main()
