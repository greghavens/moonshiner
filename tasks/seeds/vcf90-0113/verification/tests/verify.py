#!/usr/bin/env python3
"""Protected deterministic verification for vcf90-0113."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "VcfInstallerClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
MOCK = ROOT / "tests" / "mock_vcf_installer.py"
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"

TAG = "9.0.0.0"
COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
OPERATION_IDS = ["startBundleDownloadByID", "getTask"]
START_TARGET = "/v1/bundles/bundle%20alpha%2F9.0%3F%23%25"
TASK_TARGET = "/v1/tasks/task%20alpha%2F9.0"
ALTERNATE_TASK_TARGET = "/v1/tasks/alternate%20task%2Faccepted%3Fyes%23100%25"


def fail(message: str) -> None:
    print(f"VERIFY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def check(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def validate_sources() -> None:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    expected_scalars = {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "license": "Apache-2.0",
        "specificationPath": SPEC_PATH,
        "tag": TAG,
        "commit": COMMIT,
        "specificationUrl": (
            "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
            + COMMIT
            + "/"
            + SPEC_PATH
        ),
    }
    for key, expected in expected_scalars.items():
        check(sources.get(key) == expected, f"official source {key} changed")
    check(sources.get("operationIds") == OPERATION_IDS, "operationIds are not exact")
    operations = sources.get("operations")
    check(isinstance(operations, list) and len(operations) == 2, "per-operation provenance missing")
    for entry, operation_id in zip(operations, OPERATION_IDS):
        check(
            entry
            == {
                "operationId": operation_id,
                "specificationPath": SPEC_PATH,
                "tag": TAG,
                "commit": COMMIT,
            },
            f"provenance for {operation_id} changed",
        )


def validate_contract() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    check(contract.get("openapi") == "3.0.1", "wrong OpenAPI version")
    check(
        contract.get("title") == "VMware Cloud Foundation Installer API Reference Guide",
        "wrong API title",
    )
    check(contract.get("version") == TAG, "wrong API version")
    check(
        contract.get("source")
        == {
            "repository": "https://github.com/vmware/vcf-api-specs",
            "tag": TAG,
            "commit": COMMIT,
            "specificationPath": SPEC_PATH,
        },
        "contract source changed",
    )

    operations = contract.get("operations")
    check(isinstance(operations, dict), "contract operations must be an object")
    check(list(operations) == OPERATION_IDS, "contract must name exactly the requested operations")
    start = operations["startBundleDownloadByID"]
    poll = operations["getTask"]
    check(
        start["operationId"] == "startBundleDownloadByID"
        and start["method"] == "PATCH"
        and start["path"] == "/v1/bundles/{id}",
        "startBundleDownloadByID route changed",
    )
    check(
        start["parameters"]
        == [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "startBundleDownloadByID path parameter changed",
    )
    check(
        start["requestBody"]
        == {"required": True, "mediaType": "application/json", "schema": "BundleUpdateSpec"},
        "startBundleDownloadByID request body changed",
    )
    check(
        start["responses"]["202"]
        == {"description": "Accepted", "mediaType": "application/json", "schema": "Task"},
        "startBundleDownloadByID 202 response changed",
    )
    check(
        poll["operationId"] == "getTask"
        and poll["method"] == "GET"
        and poll["path"] == "/v1/tasks/{id}",
        "getTask route changed",
    )
    check(
        poll["parameters"]
        == [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "getTask path parameter changed",
    )
    check(
        poll["responses"]["200"]
        == {"description": "A task object.", "mediaType": "application/json", "schema": "Task"},
        "getTask 200 response changed",
    )

    schemas = contract.get("schemas")
    check(set(schemas) == {"BundleUpdateSpec", "BundleDownloadSpec", "Task"}, "contract schemas changed")
    check(
        schemas["BundleUpdateSpec"]
        == {
            "type": "object",
            "required": [],
            "properties": {"bundleDownloadSpec": {"$ref": "BundleDownloadSpec"}},
        },
        "BundleUpdateSpec changed",
    )
    check(
        schemas["BundleDownloadSpec"]
        == {
            "type": "object",
            "required": [],
            "properties": {
                "scheduledTimestamp": {"type": "string"},
                "downloadNow": {"type": "boolean"},
                "cancelNow": {"type": "boolean"},
            },
        },
        "BundleDownloadSpec changed",
    )
    task = schemas["Task"]
    check(
        task["required"] == ["creationTimestamp", "id", "name", "status"],
        "Task required fields changed",
    )
    check(
        task["statusValues"]
        == [
            "PENDING",
            "Pending",
            "IN_PROGRESS",
            "In Progress",
            "SUCCESSFUL",
            "Successful",
            "FAILED",
            "Failed",
            "CANCELLED",
            "Cancelled",
            "COMPLETED_WITH_WARNING",
            "SKIPPED",
        ],
        "Task status values changed",
    )
    check("9.1" not in json.dumps(contract), "9.1 material is not allowed")
    return contract


def read_requests(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def verify_start(request: dict[str, object]) -> None:
    check(request["operationId"] == "startBundleDownloadByID", "start used an operation outside the contract")
    check(request["method"] == "PATCH", "start method is not PATCH")
    check(request["target"] == START_TARGET, f"wrong escaped bundle target: {request['target']}")
    check(urllib.parse.urlsplit(request["target"]).query == "", "start sent an unexpected query")
    headers = request["headers"]
    check(headers.get("accept") == "application/json", "start Accept header changed")
    check(headers.get("content-type") == "application/json", "start Content-Type header changed")
    try:
        body = json.loads(request["body"])
    except json.JSONDecodeError as error:
        fail(f"start body is not JSON: {error}")
    check(
        body == {"bundleDownloadSpec": {"downloadNow": True}},
        f"start body contains missing, empty, null, or extra optional fields: {body!r}",
    )
    nested = body["bundleDownloadSpec"]
    check("scheduledTimestamp" not in nested and "cancelNow" not in nested, "unset optional fields were serialized")


def verify_poll(request: dict[str, object], index: int, task_target: str) -> None:
    check(request["operationId"] == "getTask", f"poll {index} used an operation outside the contract")
    check(request["method"] == "GET", f"poll {index} method is not GET")
    check(request["target"] == task_target, f"poll {index} used the wrong escaped task target")
    check(request["body"] == "", f"poll {index} unexpectedly sent a body")
    headers = request["headers"]
    check(headers.get("accept") == "application/json", f"poll {index} Accept header changed")
    check("content-type" not in headers, f"poll {index} unexpectedly sent Content-Type")


def verify_wire(requests: list[dict[str, object]], expected_polls: int, task_target: str) -> None:
    check(len(requests) == 1 + expected_polls, f"expected {expected_polls} polls, got {len(requests) - 1}")
    verify_start(requests[0])
    for index, request in enumerate(requests[1:], start=1):
        verify_poll(request, index, task_target)


def run_scenario(classes: Path, temporary: Path, scenario: str, expected_polls: int) -> None:
    request_log = temporary / f"requests-{scenario}.jsonl"
    request_log.touch()
    mock = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(MOCK),
            "--contract",
            str(CONTRACT),
            "--log",
            str(request_log),
            "--scenario",
            scenario,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port_line = mock.stdout.readline().strip()
        if not port_line.isdigit():
            fail(f"mock did not start for {scenario}: {port_line!r} {mock.stderr.read()}")
        run = subprocess.run(
            ["java", "-cp", str(classes), "TestMain", f"http://127.0.0.1:{port_line}/", scenario],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=8,
        )
        if run.returncode != 0:
            fail(f"TestMain failed for {scenario}:\n{run.stdout}{run.stderr}")
        check(run.stdout.strip() == "TEST_MAIN_OK", f"unexpected TestMain output for {scenario}")
        task_target = ALTERNATE_TASK_TARGET if scenario == "accepted-terminal" else TASK_TARGET
        verify_wire(read_requests(request_log), expected_polls, task_target)
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=2)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.wait(timeout=2)


def main() -> None:
    validate_sources()
    validate_contract()
    if shutil.which("javac") is None or shutil.which("java") is None:
        fail("Java compiler/runtime not found")

    with tempfile.TemporaryDirectory(prefix="vcf90-0113-") as temporary_name:
        temporary = Path(temporary_name)
        classes = temporary / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                str(CLIENT),
                str(TEST_MAIN),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        for scenario, expected_polls in {
            "success": 3,
            "accepted-terminal": 1,
            "mixed-case": 3,
            "failed-upper": 1,
            "failed": 2,
            "cancelled-upper": 1,
            "cancelled": 1,
            "warning": 1,
            "skipped": 1,
            "unknown-status": 1,
            "accepted-unknown-status": 0,
            "start-http-error": 0,
            "start-malformed-json": 0,
            "start-non-object": 0,
            "start-missing-field": 0,
            "start-wrong-type": 0,
            "poll-http-error": 1,
            "poll-malformed-json": 1,
            "poll-non-object": 1,
            "poll-missing-field": 1,
            "poll-wrong-type": 1,
        }.items():
            run_scenario(classes, temporary, scenario, expected_polls)

    print("VERIFY_OK: VCF Installer bundle download was polled with the exact wire contract")


if __name__ == "__main__":
    main()
