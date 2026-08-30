#!/usr/bin/env python3
"""Deterministic protected verifier for the single-file Java client."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from mock_vcf_automation import ContractMock


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAMES = ["alpha", "bravo", "mike", "zulu"]
MARKER = "vcf91-result:"


def fail(message: str) -> None:
    raise AssertionError(message)


def validate_documentation() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    statement = contract["source"]["statement"].lower()
    if "reference documentation" not in statement or "not from a published api specification" not in statement:
        fail("contract must plainly state that it is reference-derived rather than a published specification")
    operations = {entry["operationId"] for entry in contract["operations"]}
    source_operations = {entry["operation"].split(":", 1)[0] for entry in sources["sources"]}
    if operations != source_operations:
        fail("official_sources.json must map every contracted operation to its page")
    for source in sources["sources"]:
        if not source["url"].startswith("https://developer.broadcom.com/xapis/"):
            fail("all contract sources must be authoritative Broadcom xAPIs pages")
        if source["fetchedOn"] != "2026-08-15":
            fail("source fetch dates are missing or inconsistent")


def compile_client(output_dir: Path) -> None:
    command = [
        "javac",
        "-encoding", "UTF-8",
        "-d", str(output_dir),
        str(ROOT / "VcfAutomationClient.java"),
        str(ROOT / "tests" / "TestMain.java"),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=30)
    if result.returncode != 0:
        fail(f"Java compilation failed:\n{result.stdout}{result.stderr}")


def run_harness(output_dir: Path) -> None:
    with ContractMock(ROOT / "docs" / "contract.json") as mock:
        result = subprocess.run(
            [
                "java", "-cp", str(output_dir), "TestMain",
                mock.base_uri, "fixture tenant", "fixture-refresh-token", MARKER,
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            fail(f"TestMain failed:\n{result.stdout}{result.stderr}")

        marked = [line[len(MARKER):] for line in result.stdout.splitlines() if line.startswith(MARKER)]
        if not marked:
            fail("TestMain did not emit a result")
        try:
            count = int(marked[0])
        except ValueError as error:
            raise AssertionError("TestMain emitted an invalid result count") from error
        names = marked[1:]
        if count != len(names):
            fail("reported result size does not match the returned names")
        if names != EXPECTED_NAMES:
            fail(f"project names must be complete and client-sorted; got {names!r}")

        log = mock.request_log
        token_requests = [entry for entry in log if entry["method"] == "POST"]
        project_requests = [entry for entry in log if entry["method"] == "GET"]
        if len(token_requests) < 2:
            fail("the expired access token was not refreshed")
        if not any(entry["status"] == 401 for entry in project_requests):
            fail("the client did not encounter and recover from the mid-run token expiry")

        first_page_success = next(
            (index for index, entry in enumerate(log)
             if entry["method"] == "GET"
             and entry["query"].get("page", ["0"])[0] == "0"
             and entry["status"] == 200),
            None,
        )
        second_page_success = next(
            (index for index, entry in enumerate(log)
             if entry["method"] == "GET"
             and entry["query"].get("page", ["0"])[0] == "1"
             and entry["status"] == 200),
            None,
        )
        refresh_after_expiry = next(
            (index for index, entry in enumerate(log)
             if entry["method"] == "POST" and index > (first_page_success or -1)),
            None,
        )
        if first_page_success is None or second_page_success is None or refresh_after_expiry is None:
            fail("request log is missing pagination or refresh behavior")
        if not first_page_success < refresh_after_expiry < second_page_success:
            fail("refresh must happen after work began and before the interrupted page succeeds")
        if any(
            entry["method"] == "GET"
            and entry["query"].get("page", ["0"])[0] == "0"
            for entry in log[first_page_success + 1:]
        ):
            fail("the client re-fetched the completed first page instead of retaining its work")
        if any(entry["status"] == 404 for entry in log):
            fail("client called an operation outside the supplied contract")


def main() -> int:
    try:
        validate_documentation()
        with tempfile.TemporaryDirectory(prefix="vcf91-0340-") as directory:
            output_dir = Path(directory)
            compile_client(output_dir)
            run_harness(output_dir)
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF Automation token refresh, pagination, retained work, and sorting verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
