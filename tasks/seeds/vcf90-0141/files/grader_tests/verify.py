#!/usr/bin/env python3
"""Protected deterministic verifier for the single-file Java client."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from mock_server import ContractMock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SHA256 = "2301c4868a52a32c8db490700ef8f80fab53d983020849f2f87abdda28971bd7"
SOURCES_SHA256 = "57d5708920ce2926955ca78f571ccfb21ef6535c4eb2d7dc43daeb86addca93c"
HARNESS_SHA256 = "0911811c1a1ab5e8ec5a2230f34a4a5ad482ba60ecebc936d8500754369a3b75"
BEHAVIOR_HARNESS_SHA256 = "3718e81d7178e625d35df23548cade7a29ef47678af5c1991cb667dfbb116d36"
MOCK_SHA256 = "b7b97561916b5bc6b479c8326fa365295decee12287e36568d938c2a6f5843ca"

EXPECTED_CERTIFICATE = "-----BEGIN CERTIFICATE-----\nfixture-cert\n-----END CERTIFICATE-----"
EXPECTED_PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nfixture-key\n-----END PRIVATE KEY-----"
BEHAVIOR_CERTIFICATE = 'cert "quoted"\\line\n雪'
BEHAVIOR_PRIVATE_KEY = "key\tvalue\r\n\\end"
BEHAVIOR_CHAIN = "chain\nvalue"

EXPECTED_PUT_TARGET = "/api/ni/settings/certificates/platform%20cert%2Fprimary"
EXPECTED_GET_TARGET = "/api/ni/settings/certificates/status/update-42"
CHAIN_PUT_TARGET = "/api/ni/settings/certificates/node%2F%CE%B2%20%3F%23%25"
CHAIN_GET_TARGET = (
    "/api/ni/settings/certificates/status/update%20%2F%E2%9C%93%3F%23%25"
)

BEHAVIOR_CASES = [
    "chain_success",
    "failed",
    "submit_http",
    "missing_submit_id",
    "nested_submit_id",
    "missing_submit_status",
    "unknown_submit_status",
    "poll_http",
    "missing_poll_id",
    "missing_poll_status",
    "unknown_poll_status",
    "interrupt",
]

POLL_IDS = {
    "poll_http": "poll-http",
    "missing_poll_id": "missing-poll-id",
    "missing_poll_status": "missing-poll-status",
    "unknown_poll_status": "unknown-poll",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_fixtures() -> None:
    expected_hashes = {
        ROOT / "docs" / "contract.json": CONTRACT_SHA256,
        ROOT / "docs" / "official_sources.json": SOURCES_SHA256,
        ROOT / "TestMain.java": HARNESS_SHA256,
        ROOT / "grader_tests" / "BehaviorTestMain.java": BEHAVIOR_HARNESS_SHA256,
        ROOT / "grader_tests" / "mock_server.py": MOCK_SHA256,
    }
    for path, expected in expected_hashes.items():
        actual = sha256(path)
        if actual != expected:
            fail(f"protected fixture changed: {path.relative_to(ROOT)}")

    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    operations = {
        operation["operationId"]
        for path_item in contract["paths"].values()
        for operation in path_item.values()
        if "operationId" in operation
    }
    if operations != {
        "updateCertificate",
        "fetchCertificateUpdateStatusForUpdateId",
    }:
        fail(f"unexpected contract operations: {sorted(operations)}")


def read_log(log_path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]


def required_header(entry: dict[str, object], name: str) -> list[str]:
    headers = entry["headers"]
    assert isinstance(headers, dict)
    values = headers.get(name)
    if not isinstance(values, list):
        fail(f"{entry['method']} {entry['target']} is missing {name}")
    return values


def verify_common_request(entry: dict[str, object]) -> None:
    if required_header(entry, "authorization") != ["NetworkInsight fixture-token"]:
        fail(f"wrong Authorization header on {entry['method']} {entry['target']}")
    if required_header(entry, "accept") != ["application/json"]:
        fail(f"wrong Accept header on {entry['method']} {entry['target']}")


def verify_submission(
    entry: dict[str, object],
    expected_target: str,
    expected_json: dict[str, str],
) -> None:
    if entry["method"] != "PUT" or entry["target"] != expected_target:
        fail(f"wrong submission request: {entry['method']} {entry['target']}")
    if entry["operationId"] != "updateCertificate":
        fail("submission did not use updateCertificate")
    verify_common_request(entry)
    if required_header(entry, "content-type") != ["application/json"]:
        fail("submission Content-Type must be exactly application/json")
    try:
        request_json = json.loads(entry["body"])
    except (json.JSONDecodeError, TypeError) as error:
        fail(f"submission body is not JSON: {error}")
    if request_json != expected_json:
        fail(f"submission JSON has the wrong wire fields or values: {request_json!r}")


def verify_poll(entry: dict[str, object], expected_target: str) -> None:
    if entry["method"] != "GET" or entry["target"] != expected_target:
        fail(f"wrong status request: {entry['method']} {entry['target']}")
    if entry["operationId"] != "fetchCertificateUpdateStatusForUpdateId":
        fail("poll did not use fetchCertificateUpdateStatusForUpdateId")
    verify_common_request(entry)
    if entry["body"] != "":
        fail("status GET must have an empty body")


def verify_happy_log(log_path: Path) -> None:
    entries = read_log(log_path)
    if len(entries) != 4:
        fail(f"expected one submission and three status polls, got {len(entries)} requests")
    verify_submission(
        entries[0],
        EXPECTED_PUT_TARGET,
        {
            "certificate": EXPECTED_CERTIFICATE,
            "private_key": EXPECTED_PRIVATE_KEY,
        },
    )
    for entry in entries[1:]:
        verify_poll(entry, EXPECTED_GET_TARGET)


def verify_behavior_log(case: str, log_path: Path) -> None:
    entries = read_log(log_path)
    if case == "interrupt":
        # A pre-interrupted call may stop before sending, or HttpClient may observe
        # interruption after beginning the submission. The Java harness verifies
        # the deterministic requirement: InterruptedException is propagated.
        return

    if case == "chain_success":
        expected_count = 4
        put_target = CHAIN_PUT_TARGET
        poll_target = CHAIN_GET_TARGET
        expected_json = {
            "certificate": BEHAVIOR_CERTIFICATE,
            "private_key": BEHAVIOR_PRIVATE_KEY,
            "chain": BEHAVIOR_CHAIN,
        }
    else:
        expected_count = 2 if case == "failed" or case in POLL_IDS else 1
        certificate_id = "failed certificate" if case == "failed" else "error certificate"
        put_target = (
            "/api/ni/settings/certificates/" + certificate_id.replace(" ", "%20")
        )
        update_id = "failed-7" if case == "failed" else POLL_IDS.get(case)
        poll_target = (
            "/api/ni/settings/certificates/status/" + update_id
            if update_id is not None
            else ""
        )
        expected_json = {
            "certificate": BEHAVIOR_CERTIFICATE,
            "private_key": BEHAVIOR_PRIVATE_KEY,
        }

    if len(entries) != expected_count:
        fail(f"{case}: expected {expected_count} requests, got {len(entries)}")
    verify_submission(entries[0], put_target, expected_json)
    for entry in entries[1:]:
        verify_poll(entry, poll_target)


def run_java(
    classes: Path,
    main_class: str,
    arguments: list[str],
    expected_stdout: str,
) -> None:
    result = subprocess.run(
        ["java", "-cp", str(classes), main_class, *arguments],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        fail(f"{main_class} failed:\n{result.stdout}{result.stderr}")
    if result.stdout.strip() != expected_stdout:
        fail(f"unexpected {main_class} output: {result.stdout!r}")


def main() -> int:
    verify_fixtures()
    with tempfile.TemporaryDirectory(prefix="vcf-networks-test-") as temporary:
        temporary_path = Path(temporary)
        classes = temporary_path / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(ROOT / "VcfNetworksClient.java"),
                str(ROOT / "TestMain.java"),
                str(ROOT / "grader_tests" / "BehaviorTestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stderr)

        happy_log = temporary_path / "happy.jsonl"
        server = ContractMock(ROOT / "docs" / "contract.json", happy_log)
        server.start()
        try:
            run_java(
                classes,
                "TestMain",
                [server.api_base_uri],
                "terminal=SUCCESS updateId=update-42",
            )
        finally:
            server.close()
        verify_happy_log(happy_log)

        for case in BEHAVIOR_CASES:
            case_log = temporary_path / f"{case}.jsonl"
            server = ContractMock(
                ROOT / "docs" / "contract.json", case_log, scenario=case
            )
            server.start()
            try:
                run_java(
                    classes,
                    "BehaviorTestMain",
                    [server.api_base_uri, case],
                    f"PASS {case}",
                )
            finally:
                server.close()
            verify_behavior_log(case, case_log)

    print("PASS: contract-pinned async certificate client")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
