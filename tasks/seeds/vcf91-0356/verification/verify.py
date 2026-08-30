#!/usr/bin/env python3
"""Protected verifier entrypoint for the loopback VCF Automation exercise."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parent

# Filled with the pristine fixture digests by the seed author. The solution may
# change AutomationClient.java only; the contract, mock, and assertions remain fixed.
PROTECTED_SHA256 = {
    "MockVcfAutomationServer.java": "79d34d8b64d42092eaf6b31c3330ebae23795dc9323051c5b2f3bc53b1d63c0f",
    "TestMain.java": "7734ada537103e7c1a9dd5d349f0198973468ceb4a9a4b88efac385d4122272d",
    "docs/contract.json": "c78acb31cb1f122742f27b793b7acffcfb63f51f3b3109a3b366cfae46c70d46",
    "docs/official_sources.json": "aebd486a72b1f91ce617471b1c0322151a95202a4143d1feaf4722ed99b10b1f",
}


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"missing protected fixture: {relative}")
        actual = sha256(path)
        if actual != expected:
            fail(f"protected fixture was modified: {relative}")


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))
    if contract.get("provenance", {}).get("type") != "reference-documentation":
        fail("contract must identify itself as reference-derived documentation")
    operation_ids = {operation.get("id") for operation in contract.get("operations", [])}
    if operation_ids != {"submitDeploymentActionRequest", "getRequest", "refreshAccessToken"}:
        fail("contract operation set changed")
    terminal = set(contract.get("request_status", {}).get("terminal", []))
    if terminal != {"SUCCESSFUL", "FAILED", "ABORTED", "APPROVAL_REJECTED"}:
        fail("terminal status contract changed")
    if len(sources.get("sources", [])) != 3:
        fail("official source ledger must contain one entry per contract operation")


def verify_client_scope() -> None:
    client = (ROOT / "AutomationClient.java").read_text(encoding="utf-8")
    if re.search(r"https?://", client):
        fail("client must use the injected base URI and must not contain a live endpoint")


def run_harness() -> None:
    with tempfile.TemporaryDirectory(prefix="vcf91-0356-") as build_dir:
        compile_result = subprocess.run(
            [
                "javac",
                "--add-modules",
                "jdk.httpserver",
                "-encoding",
                "UTF-8",
                "-d",
                build_dir,
                str(ROOT / "AutomationClient.java"),
                str(ROOT / "MockVcfAutomationServer.java"),
                str(ROOT / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if compile_result.returncode != 0:
            fail("Java compilation failed:\n" + compile_result.stdout)

        run_result = subprocess.run(
            ["java", "--add-modules", "jdk.httpserver", "-cp", build_dir, "TestMain"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if run_result.returncode != 0:
            fail("contract harness failed:\n" + run_result.stdout)
        if not run_result.stdout.startswith("PASS: OperationResult"):
            fail("contract harness did not report its pass marker")
        print(run_result.stdout.strip())


def main() -> None:
    verify_protected_files()
    verify_contract()
    verify_client_scope()
    run_harness()


if __name__ == "__main__":
    main()
