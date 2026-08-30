#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 Java credential-rotation task."""

from __future__ import annotations

import base64
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
        "a938d5e3c9092f3db8b84207aac0e3c28d044b8afef1a198fabe47ea1876fa7d"
    ),
    "docs/official_sources.json": (
        "fe5678f5f452ca6239b45b7fa4864ea17aa69eef591b34fc937e0789f9287a1e"
    ),
    "tests/TestMain.java": (
        "7ca072f5bbdce20f8340a4ccb12711939217899f3e9cd53341ad76619797edba"
    ),
    "tests/mock_nsx_policy.py": (
        "cc69f9f4345b705d69e841d303e81274f2fa78b2efe5a0cd781313dc0e0590d8"
    ),
}
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"


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
    require(set(contract["operations"]) == {"ListTier1"}, "contract operation set")
    operation = contract["operations"]["ListTier1"]
    require(operation["operationId"] == "ListTier1", "contract operationId")
    require(operation["method"] == "GET", "ListTier1 method")
    require(operation["path"] == "/infra/tier-1s", "ListTier1 path")
    parameters = operation["parameters"]
    require(
        [parameter["name"] for parameter in parameters]
        == [
            "cursor",
            "include_mark_for_delete_objects",
            "included_fields",
            "page_size",
            "sort_ascending",
            "sort_by",
        ],
        "ListTier1 parameter order",
    )
    require(
        all(
            parameter["in"] == "query" and not parameter["required"]
            for parameter in parameters
        ),
        "ListTier1 optional query projection",
    )
    page_size = next(
        parameter for parameter in parameters if parameter["name"] == "page_size"
    )
    require(
        page_size["minimum"] == 0
        and page_size["maximum"] == 1000
        and page_size["default"] == 1000,
        "page_size bounds/default projection",
    )
    include_deleted = next(
        parameter
        for parameter in parameters
        if parameter["name"] == "include_mark_for_delete_objects"
    )
    require(include_deleted["default"] is False, "boolean default projection")
    require(
        contract["source"]["repository_commit_sha"] == COMMIT
        and contract["source"]["spec_path"] == SPEC_PATH,
        "contract source provenance",
    )
    require(
        sources["repository_commit_sha"] == COMMIT
        and sources["spec_path"] == SPEC_PATH
        and sources["operationIds"] == ["ListTier1"],
        "official source provenance",
    )
    require(len(sources["operations"]) == 1, "official operation count")
    source_operation = sources["operations"][0]
    require(
        source_operation["operationId"] == "ListTier1"
        and source_operation["repository_commit_sha"] == COMMIT
        and source_operation["spec_path"] == SPEC_PATH,
        "per-operation provenance",
    )
    return contract


def runtime_environment() -> dict[str, str]:
    marker = secrets.token_hex(7)
    environment = os.environ.copy()
    environment.update(
        {
            "NSX_OLD_USERNAME": "old-" + marker,
            "NSX_OLD_PASSWORD": "old:" + secrets.token_urlsafe(13),
            "NSX_NEW_USERNAME": "new-" + marker,
            "NSX_NEW_PASSWORD": "new:" + secrets.token_urlsafe(13),
            "NSX_TIMEOUT_OLD_USERNAME": "held-" + marker,
            "NSX_TIMEOUT_OLD_PASSWORD": "held:" + secrets.token_urlsafe(13),
            "NSX_TIMEOUT_NEW_USERNAME": "next-" + marker,
            "NSX_TIMEOUT_NEW_PASSWORD": "next:" + secrets.token_urlsafe(13),
            "NSX_CENTRAL_CURSOR": "after snow \u96ea & + / " + marker,
            "NSX_TIMEOUT_CURSOR": "timeout \u96ea & " + marker,
            "NSX_RELEASE_CURSOR": "release + / " + marker,
            "NSX_ERROR_CURSOR": "error once ? " + marker,
        }
    )
    return environment


def wait_for_ready(process: subprocess.Popen[str], ready: Path) -> dict[str, Any]:
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
        retired: Path,
        environment: dict[str, str],
) -> None:
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
        "--retired",
        str(retired),
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
            str(retired),
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


def authorization(username: str, password: str) -> str:
    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return "Basic " + token


def encoded(value: str) -> str:
    return urllib.parse.quote(value, safe="-._~", encoding="utf-8")


def verify_request(
    event: dict[str, Any],
    *,
    raw_target: str,
    expected_authorization: str,
    expected_query: list[tuple[str, str]],
) -> None:
    require(event["event"] == "request", "request event kind")
    require(event["operationId"] == "ListTier1", "request operationId")
    require(event["method"] == "GET", "request method")
    require(event["raw_target"] == raw_target, "raw request target")
    require(event["body_hex"] == "", "GET request body bytes")
    headers = event["headers"]
    require(
        headers.get("authorization") == [expected_authorization],
        "Basic Authorization wire value",
    )
    require(
        headers.get("accept") == ["application/json"],
        "Accept media type",
    )
    require("content-type" not in headers, "GET sent Content-Type")
    require("transfer-encoding" not in headers, "GET sent transfer encoding")

    parsed = urllib.parse.urlsplit(event["raw_target"])
    pairs = urllib.parse.parse_qsl(
        parsed.query, keep_blank_values=True, strict_parsing=True
    )
    require(pairs == expected_query, "query names, order, or decoded values")
    names = {name for name, _ in pairs}
    require(
        names.isdisjoint(
            {
                "include_mark_for_delete_objects",
                "included_fields",
                "sort_ascending",
                "sort_by",
            }
        ),
        "unset optional query field was synthesized",
    )


def verify_wire_log(
        request_log: Path,
        retired: Path,
        environment: dict[str, str],
) -> None:
    events = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    require(
        [event["event"] for event in events]
        == [
            "request",
            "request",
            "response",
            "response",
            "retired",
            "request",
            "request",
            "response",
            "response",
            "request",
            "response",
        ],
        "credential drain event ordering",
    )
    requests = [event for event in events if event["event"] == "request"]
    responses = [event for event in events if event["event"] == "response"]
    require(len(requests) == 5, "exact ListTier1 request count")
    require(
        [event["status"] for event in responses] == [200, 200, 200, 200, 503],
        "response status sequence",
    )
    path = "/policy/api/v1/infra/tier-1s"

    old_auth = authorization(
        environment["NSX_OLD_USERNAME"], environment["NSX_OLD_PASSWORD"]
    )
    new_auth = authorization(
        environment["NSX_NEW_USERNAME"], environment["NSX_NEW_PASSWORD"]
    )
    timeout_old_auth = authorization(
        environment["NSX_TIMEOUT_OLD_USERNAME"],
        environment["NSX_TIMEOUT_OLD_PASSWORD"],
    )
    timeout_new_auth = authorization(
        environment["NSX_TIMEOUT_NEW_USERNAME"],
        environment["NSX_TIMEOUT_NEW_PASSWORD"],
    )

    verify_request(
        requests[0],
        raw_target=path,
        expected_authorization=old_auth,
        expected_query=[],
    )
    require("?" not in requests[0]["raw_target"], "unset query left trailing ?")

    central_cursor = environment["NSX_CENTRAL_CURSOR"]
    verify_request(
        requests[1],
        raw_target=(
            path
            + "?cursor="
            + encoded(central_cursor)
            + "&page_size=0"
        ),
        expected_authorization=new_auth,
        expected_query=[("cursor", central_cursor), ("page_size", "0")],
    )
    timeout_cursor = environment["NSX_TIMEOUT_CURSOR"]
    verify_request(
        requests[2],
        raw_target=path + "?cursor=" + encoded(timeout_cursor),
        expected_authorization=timeout_old_auth,
        expected_query=[("cursor", timeout_cursor)],
    )
    release_cursor = environment["NSX_RELEASE_CURSOR"]
    verify_request(
        requests[3],
        raw_target=path + "?cursor=" + encoded(release_cursor),
        expected_authorization=timeout_new_auth,
        expected_query=[("cursor", release_cursor)],
    )
    error_cursor = environment["NSX_ERROR_CURSOR"]
    verify_request(
        requests[4],
        raw_target=path + "?cursor=" + encoded(error_cursor),
        expected_authorization=timeout_new_auth,
        expected_query=[("cursor", error_cursor)],
    )
    require(
        retired.read_text(encoding="utf-8") == "retired\n",
        "retirement callback marker",
    )


def main() -> int:
    try:
        verify_protected_inputs()
        with tempfile.TemporaryDirectory(prefix="vcf91-0085-") as temporary:
            work = Path(temporary)
            classes = work / "classes"
            classes.mkdir()
            compile_client(classes)
            environment = runtime_environment()
            request_log = work / "requests.jsonl"
            ready = work / "ready.json"
            retired = work / "retired.marker"
            run_harness(
                classes, request_log, ready, retired, environment
            )
            verify_wire_log(request_log, retired, environment)
    except (VerificationFailure, OSError, subprocess.SubprocessError) as error:
        print(f"VERIFY_FAIL: {error}", file=sys.stderr)
        return 1
    print("VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
