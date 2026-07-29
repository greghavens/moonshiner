#!/usr/bin/env python3
"""Deterministic protected verifier for VcfSessionClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote


PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "docs/contract.json": "9d68c601f99bebce977ce2418334c33404eff1a0aa7c41cbf1204fac662d2b12",
    "docs/official_sources.json": "709c54dba6cdcf0046c173b36a2dea94bba291e69aa2deb0f4da1250fdccb8a6",
    "tests/TestMain.java": "1a116ffa307509a9acdc4571c7761bf351cacdc6e452cb94409f28a078ac94df",
    "tests/mock_server.py": "5fe363fc1f03da8a2840d6e205262957874c5f577c99f6936111cc090e402d4e",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_files() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_contract_metadata() -> None:
    contract = json.loads((PROJECT / "docs/contract.json").read_text(encoding="utf-8"))
    source = json.loads(
        (PROJECT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
    spec_path = "specifications/sddc-manager/sddc-manager-openapi.json"
    expected_operations = {
        (
            "refreshAccessToken",
            "PATCH",
            "/v1/tokens/access-token/refresh",
        ),
        ("getCredentials", "GET", "/v1/credentials"),
    }

    if contract["contract_format"] != "focused-openapi-projection-v1":
        fail("contract projection format changed")
    if (
        contract["derived_from"]["repository_commit_sha"] != sha
        or source["repository"]["commit_sha"] != sha
    ):
        fail("official source commit is not pinned")
    if (
        contract["derived_from"]["spec_path"] != spec_path
        or source["specification"]["path"] != spec_path
    ):
        fail("official specification path changed")
    if contract["derived_from"]["info_version"] != "9.1.0.0":
        fail("contract is not the VCF 9.1 SDDC Manager specification")

    projected = {
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    }
    recorded = {
        (item["operationId"], item["method"], item["path"])
        for item in source["operations"]
    }
    if projected != expected_operations or recorded != expected_operations:
        fail("operationId source record changed")
    for operation in source["operations"]:
        if (
            operation["repository_commit_sha"] != sha
            or operation["spec_path"] != spec_path
        ):
            fail("operation provenance is not recorded at commit granularity")

    refresh, credentials = contract["operations"]
    if refresh["operationId"] != "refreshAccessToken":
        fail("refreshAccessToken must be the first focused operation")
    if refresh["request_body"] != {
        "required": True,
        "media_type": "application/json",
        "schema": {
            "type": "string",
            "description": "ID of the refresh token",
        },
    }:
        fail("refresh token request projection changed")
    if refresh["responses"]["200"]["schema"] != {"type": "string"}:
        fail("refresh access-token response projection changed")

    expected_parameters = [
        "resourceName",
        "resourceIp",
        "resourceType",
        "domainName",
        "pageNumber",
        "pageSize",
        "accountType",
    ]
    if [item["name"] for item in credentials["parameters"]] != expected_parameters:
        fail("getCredentials query parameter projection changed")
    if any(
        item["in"] != "query" or item["required"] is not False
        for item in credentials["parameters"]
    ):
        fail("getCredentials optional query semantics changed")
    if credentials["responses"]["401"]["description"] != "Unauthorized":
        fail("getCredentials 401 contract changed")


def wait_for_server(port_file: Path, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited during startup\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
            return json.loads(port_file.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("mock did not publish its loopback port")


def read_entries(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def assert_get_shape(
    entry: dict,
    *,
    target: str,
    authorization: str,
    response_status: int,
) -> None:
    if (
        entry["operationId"],
        entry["method"],
        entry["target"],
        entry["path"],
        entry["responseStatus"],
    ) != (
        "getCredentials",
        "GET",
        target,
        "/v1/credentials",
        response_status,
    ):
        fail(f"getCredentials request has the wrong wire target: {entry}")
    if entry["body"] != "":
        fail("getCredentials must not send a request body")
    headers = entry["headers"]
    if headers.get("accept") != "application/json":
        fail("getCredentials must send Accept: application/json")
    if headers.get("authorization") != authorization:
        fail("getCredentials used the wrong bearer generation")
    for forbidden in ("content-type", "transfer-encoding"):
        if forbidden in headers:
            fail(f"getCredentials must omit {forbidden}")


def check_wire_log(log_path: Path, server_info: dict) -> None:
    entries = read_entries(log_path)
    if len(entries) != 4:
        fail(
            "expected old GET, refresh, replay, and filtered GET; "
            f"got {len(entries)} requests"
        )
    if [entry["sequence"] for entry in entries] != [1, 2, 3, 4]:
        fail("request sequence is not deterministic")
    if [entry["operationId"] for entry in entries] != [
        "getCredentials",
        "refreshAccessToken",
        "getCredentials",
        "getCredentials",
    ]:
        fail("the in-flight request was not refreshed and replayed in order")

    old_authorization = "Bearer " + server_info["old_access_token"]
    new_authorization = "Bearer " + server_info["new_access_token"]
    assert_get_shape(
        entries[0],
        target="/v1/credentials",
        authorization=old_authorization,
        response_status=401,
    )

    refresh = entries[1]
    if (
        refresh["operationId"],
        refresh["method"],
        refresh["target"],
        refresh["path"],
        refresh["query"],
        refresh["responseStatus"],
    ) != (
        "refreshAccessToken",
        "PATCH",
        "/v1/tokens/access-token/refresh",
        "/v1/tokens/access-token/refresh",
        "",
        200,
    ):
        fail(f"refreshAccessToken has the wrong wire target: {refresh}")
    expected_refresh_body = json.dumps(
        server_info["refresh_token_id"],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if refresh["body"] != expected_refresh_body:
        fail("refreshAccessToken did not send the refresh ID as one JSON string")
    refresh_headers = refresh["headers"]
    if refresh_headers.get("accept") != "application/json":
        fail("refreshAccessToken must send Accept: application/json")
    if refresh_headers.get("content-type") != "application/json":
        fail("refreshAccessToken must send Content-Type: application/json")
    if refresh_headers.get("content-length") != str(
        len(expected_refresh_body.encode("utf-8"))
    ):
        fail("refreshAccessToken Content-Length does not match its UTF-8 body")
    if "authorization" in refresh_headers:
        fail("refreshAccessToken must not leak the superseded bearer")
    if "transfer-encoding" in refresh_headers:
        fail("refreshAccessToken must not use transfer encoding")

    assert_get_shape(
        entries[2],
        target="/v1/credentials",
        authorization=new_authorization,
        response_status=200,
    )

    encoded_name = quote(server_info["resource_name"], safe="-._~")
    expected_target = "/v1/credentials?resourceName=" + encoded_name
    assert_get_shape(
        entries[3],
        target=expected_target,
        authorization=new_authorization,
        response_status=200,
    )
    if parse_qsl(entries[3]["query"], keep_blank_values=True) != [
        ("resourceName", server_info["resource_name"])
    ]:
        fail("unset getCredentials query fields were sent instead of omitted")

    if entries[0]["query"] != "" or entries[2]["query"] != "":
        fail("an all-unset CredentialQuery must omit the query delimiter")
    if [entry["credentialRequestNumber"] for entry in (entries[0], entries[2], entries[3])] != [
        1,
        2,
        3,
    ]:
        fail("credentials request attempt count changed")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0043-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(PROJECT / "VcfSessionClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("Java sources did not compile")

        request_log = temp / "requests.jsonl"
        port_file = temp / "port.json"
        first_request_marker = temp / "first-request-started"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT / "tests/mock_server.py"),
                "--contract",
                str(PROJECT / "docs/contract.json"),
                "--log",
                str(request_log),
                "--port-file",
                str(port_file),
                "--first-request-marker",
                str(first_request_marker),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            server_info = wait_for_server(port_file, mock)
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{server_info['port']}/",
                    server_info["old_access_token"],
                    server_info["refresh_token_id"],
                    server_info["new_access_token"],
                    server_info["resource_name"],
                    str(first_request_marker),
                ],
                text=True,
                capture_output=True,
                timeout=12,
            )
            if run_result.returncode != 0:
                sys.stderr.write(run_result.stdout + run_result.stderr)
                fail("TestMain failed")
            if run_result.stdout.strip() != "SUCCESSFUL":
                fail(f"unexpected TestMain output: {run_result.stdout!r}")
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

        check_wire_log(request_log, server_info)

    print("PASS: cutover-safe SDDC Manager access-token refresh")


if __name__ == "__main__":
    main()
