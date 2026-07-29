#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 token-refresh collection task."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "docs/contract.json": (
        "de66e1f1e0be310144566781e70e60b6b44d4e868bda0b82b617a552c6c78ced"
    ),
    "docs/official_sources.json": (
        "8b8f2c88ec72e894a5685a8fd12db8b994848ffffe1c7037bdae2a251e7df8ca"
    ),
    "tests/TestMain.java": (
        "014bda0897c6ef3c122be5e6e056fd7ee9b298deba3f74ecf68bd6bf464e04fb"
    ),
    "tests/mock_nsx_policy.py": (
        "fc214f2bfed538f1884627cdd715170f538a2336f7576ce549eb60aaada403c4"
    ),
}
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
OPERATION_ID = "ListAllInfraSegments"


class VerificationFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> dict[str, Any]:
    for relative, expected in PROTECTED_HASHES.items():
        actual = sha256(ROOT / relative)
        require(actual == expected, f"protected fixture changed: {relative}")

    contract = json.loads(
        (ROOT / "docs/contract.json").read_text(encoding="utf-8")
    )
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    require(contract["swagger"] == "2.0", "contract swagger version")
    require(contract["info"]["version"] == "9.1.0.0", "contract product version")
    require(contract["basePath"] == "/policy/api/v1", "contract base path")
    require(set(contract["operations"]) == {OPERATION_ID}, "operation set")
    operation = contract["operations"][OPERATION_ID]
    require(operation["operationId"] == OPERATION_ID, "operationId")
    require(operation["method"] == "GET", "operation method")
    require(operation["path"] == "/infra/segments", "operation path")
    parameters = operation["parameters"]
    require(
        [parameter["name"] for parameter in parameters]
        == [
            "cursor",
            "include_mark_for_delete_objects",
            "included_fields",
            "page_size",
            "segment_type",
            "sort_ascending",
            "sort_by",
        ],
        "specification parameter order",
    )
    require(
        all(
            parameter["in"] == "query" and not parameter["required"]
            for parameter in parameters
        ),
        "optional query projection",
    )
    page_size = next(
        parameter for parameter in parameters if parameter["name"] == "page_size"
    )
    require(
        page_size["minimum"] == 0
        and page_size["maximum"] == 1000
        and page_size["default"] == 1000,
        "page_size bounds and default",
    )
    segment_type = next(
        parameter
        for parameter in parameters
        if parameter["name"] == "segment_type"
    )
    require(
        segment_type["enum"] == ["DVPortgroup", "ALL"],
        "segment_type enum",
    )
    require(
        operation["responses"]["200"]["schema_ref"]
        == "#/definitions/SegmentListResult",
        "success response schema",
    )
    require(
        contract["source"]["repository_commit_sha"] == COMMIT
        and contract["source"]["spec_path"] == SPEC_PATH,
        "contract source provenance",
    )
    require(
        sources["repository_commit_sha"] == COMMIT
        and sources["spec_path"] == SPEC_PATH
        and sources["operationIds"] == [OPERATION_ID],
        "official source provenance",
    )
    require(len(sources["operations"]) == 1, "official operation count")
    source_operation = sources["operations"][0]
    require(
        source_operation["operationId"] == OPERATION_ID
        and source_operation["method"] == "GET"
        and source_operation["path"] == "/infra/segments"
        and source_operation["repository_commit_sha"] == COMMIT
        and source_operation["spec_path"] == SPEC_PATH,
        "per-operation provenance",
    )
    return contract


def runtime_environment() -> dict[str, str]:
    marker = secrets.token_hex(8)
    environment = os.environ.copy()
    environment.update(
        {
            "NSX_INITIAL_TOKEN": "initial." + secrets.token_urlsafe(18),
            "NSX_REFRESHED_TOKEN": "refreshed." + secrets.token_urlsafe(18),
            "NSX_CURSOR": "next page/\u96ea + & ? " + marker,
            "NSX_SEGMENT_1_ID": "z-id-" + marker,
            "NSX_SEGMENT_1_NAME": "Zulu " + marker,
            "NSX_SEGMENT_2_ID": "a-id-" + marker,
            "NSX_SEGMENT_2_NAME": "Alpha " + marker,
            "NSX_SEGMENT_3_ID": "m-id-" + marker,
            "NSX_SEGMENT_3_NAME": "Mike " + marker,
            "NSX_SEGMENT_4_ID": "b-id-" + marker,
            "NSX_SEGMENT_4_NAME": "Bravo " + marker,
        }
    )
    return environment


def wait_for_ready(
        process: subprocess.Popen[str],
        ready: Path) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready.exists() and ready.stat().st_size > 0:
            return json.loads(ready.read_text(encoding="utf-8"))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationFailure(
                "loopback mock exited before readiness\n" + stdout + stderr
            )
        time.sleep(0.02)
    raise VerificationFailure("loopback mock did not become ready")


def compile_client(classes: Path) -> None:
    command = [
        "javac",
        "--release",
        "17",
        "-d",
        str(classes),
        str(ROOT / "NsxPolicyClient.java"),
        str(ROOT / "tests/TestMain.java"),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    require(
        result.returncode == 0,
        "javac failed\n" + result.stdout + result.stderr,
    )


def run_harness(
        classes: Path,
        request_log: Path,
        ready: Path,
        environment: dict[str, str]) -> None:
    mock_command = [
        sys.executable,
        "-B",
        str(ROOT / "tests/mock_nsx_policy.py"),
        "--contract",
        str(ROOT / "docs/contract.json"),
        "--log",
        str(request_log),
        "--ready",
        str(ready),
    ]
    mock = subprocess.Popen(
        mock_command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        endpoint = wait_for_ready(mock, ready)
        require(endpoint["host"] == "127.0.0.1", "mock is not IPv4 loopback")
        port = endpoint["port"]
        require(isinstance(port, int) and 0 < port < 65536, "mock port")
        base_url = f"http://127.0.0.1:{port}"
        command = [
            "java",
            "-cp",
            str(classes),
            "TestMain",
            base_url,
            str(request_log),
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=24,
            check=False,
        )
        require(
            result.returncode == 0,
            "TestMain failed\n" + result.stdout + result.stderr,
        )
        require(
            result.stdout.strip() == "TEST_MAIN_OK",
            "TestMain success sentinel",
        )
    finally:
        if mock.poll() is None:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)


def bearer(token: str) -> str:
    return "Bearer " + token


def encoded(value: str) -> str:
    return urllib.parse.quote(value, safe="-._~", encoding="utf-8")


def verify_request(
        event: dict[str, Any],
        *,
        raw_target: str,
        authorization: str,
        status: int,
        expected_query: list[tuple[str, str]]) -> None:
    require(event["event"] == "request", "request event kind")
    require(event["operationId"] == OPERATION_ID, "request operationId")
    require(event["method"] == "GET", "request method")
    require(event["raw_target"] == raw_target, "raw request target")
    require(event["body_hex"] == "", "GET body bytes")
    require(event["response_status"] == status, "response status trace")
    headers = event["headers"]
    require(
        headers.get("authorization") == [authorization],
        "Bearer Authorization wire value",
    )
    require(
        headers.get("accept") == ["application/json"],
        "Accept media type",
    )
    require("content-type" not in headers, "GET sent Content-Type")
    require("transfer-encoding" not in headers, "GET sent transfer encoding")

    parsed = urllib.parse.urlsplit(event["raw_target"])
    pairs = urllib.parse.parse_qsl(
        parsed.query,
        keep_blank_values=True,
        strict_parsing=True,
    )
    require(pairs == expected_query, "query names, order, or values")
    names = {name for name, _ in pairs}
    require(
        names.isdisjoint(
            {
                "include_mark_for_delete_objects",
                "included_fields",
                "segment_type",
                "sort_ascending",
                "sort_by",
            }
        ),
        "unset optional query field was synthesized",
    )


def verify_wire_log(
        request_log: Path,
        environment: dict[str, str]) -> None:
    events = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    require(len(events) == 5, "wire request count")

    first_target = "/policy/api/v1/infra/segments?page_size=2"
    second_target = (
        "/policy/api/v1/infra/segments?cursor="
        + encoded(environment["NSX_CURSOR"])
        + "&page_size=2"
    )
    first_query = [("page_size", "2")]
    second_query = [
        ("cursor", environment["NSX_CURSOR"]),
        ("page_size", "2"),
    ]
    initial = bearer(environment["NSX_INITIAL_TOKEN"])
    refreshed = bearer(environment["NSX_REFRESHED_TOKEN"])
    expected = [
        (first_target, initial, 200, first_query),
        (second_target, initial, 401, second_query),
        (second_target, refreshed, 200, second_query),
        (first_target, refreshed, 200, first_query),
        (second_target, refreshed, 200, second_query),
    ]
    for event, (target, auth, status, query) in zip(
            events, expected, strict=True):
        verify_request(
            event,
            raw_target=target,
            authorization=auth,
            status=status,
            expected_query=query,
        )

    successful = [
        event for event in events if event["response_status"] == 200
    ]
    require(
        [event["successful_response_ordinal"] for event in successful]
        == [1, 2, 3, 4],
        "successful response ordinals",
    )
    page_one = [
        environment["NSX_SEGMENT_1_ID"],
        environment["NSX_SEGMENT_2_ID"],
    ]
    page_two = [
        environment["NSX_SEGMENT_3_ID"],
        environment["NSX_SEGMENT_4_ID"],
    ]
    require(
        [event["response_result_ids"] for event in successful]
        == [
            list(reversed(page_one)),
            page_two,
            list(reversed(page_one)),
            page_two,
        ],
        "mock did not flip element order on every response",
    )


def main() -> int:
    try:
        verify_protected_inputs()
        with tempfile.TemporaryDirectory(prefix="vcf91-0088-") as temporary:
            temp = Path(temporary)
            classes = temp / "classes"
            classes.mkdir()
            request_log = temp / "requests.jsonl"
            ready = temp / "ready.json"
            environment = runtime_environment()
            compile_client(classes)
            run_harness(
                classes,
                request_log,
                ready,
                environment,
            )
            verify_wire_log(request_log, environment)
        print("verification passed")
        return 0
    except (
            VerificationFailure,
            KeyError,
            json.JSONDecodeError,
            OSError,
            subprocess.TimeoutExpired) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
