#!/usr/bin/env python3
"""Deterministic acceptance verifier for the single-file Java client."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
EXPECTED_OPERATION_IDS = ["POST_sessions", "PATCH_log-forwarder-id"]
FIRST_ID = "11111111-1111-4111-8111-111111111111"
SECOND_ID = "22222222-2222-4222-8222-222222222222"
THIRD_ID = "33333333-3333-4333-8333-333333333333"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_provenance_and_contract() -> None:
    official = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    require(official["tag"] == "9.0.0.0", "official source must remain pinned to tag 9.0.0.0")
    require(official["commit"] == EXPECTED_COMMIT, "official source commit changed")
    require(official["specPath"] == EXPECTED_SPEC_PATH, "official spec path changed")
    require(official["operationIds"] == EXPECTED_OPERATION_IDS, "official operationIds changed")
    require(official["license"] == "Apache-2.0", "official license metadata changed")
    require(EXPECTED_COMMIT in official["rawSpec"], "raw spec URL is not commit pinned")

    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    require(contract["title"] == "VCF Operations for Logs", "contract is not the 9.0 component")
    require(contract["basePath"] == "/api/v2", "contract base path changed")
    require(contract["derivedFrom"]["tag"] == "9.0.0.0", "contract tag changed")
    require(contract["derivedFrom"]["commit"] == EXPECTED_COMMIT, "contract commit changed")
    require(contract["derivedFrom"]["specPath"] == EXPECTED_SPEC_PATH, "contract source path changed")

    operations = contract["operations"]
    require([operation["operationId"] for operation in operations] == EXPECTED_OPERATION_IDS,
            "contract operationIds changed")
    session, patch = operations
    require((session["method"], session["path"]) == ("POST", "/sessions"),
            "POST_sessions wire route changed")
    require(session["requestBody"]["contentType"] == "application/json",
            "POST_sessions content type changed")
    require(set(session["requestBody"]["schema"]["required"]) == {"username", "password", "provider"},
            "POST_sessions required request fields changed")
    require(session["requestBody"]["schema"]["properties"]["provider"]["enum"]
            == ["Local", "ActiveDirectory", "vIDM"], "POST_sessions provider enum changed")

    require((patch["method"], patch["path"]) == ("PATCH", "/log-forwarder/{id}"),
            "PATCH_log-forwarder-id wire route changed")
    require(patch["security"] == [{"Bearer": []}], "PATCH bearer security changed")
    patch_schema = patch["requestBody"]["schema"]
    require("required" not in patch_schema, "9.0 patch fields must remain optional")
    require(set(patch_schema["properties"]) == {
        "name", "host", "port", "protocol", "sslEnabled", "workerCount", "diskCacheSize",
        "tags", "filter", "transportProtocol", "forwardComplementaryFields", "testConnection",
    }, "PATCH request properties changed")
    require(patch_schema["properties"]["protocol"]["enum"] == ["SYSLOG", "CFAPI", "RAW", "RAWPlus"],
            "PATCH protocol enum changed")
    require(patch_schema["properties"]["transportProtocol"]["enum"] == ["TCP", "UDP", "TCP_OCTET"],
            "PATCH transportProtocol enum changed")
    require(set(patch["responses"]) == {"200", "400", "401", "404", "440", "500"},
            "PATCH response statuses changed")


def run_harness(temp_dir: Path) -> list[dict[str, Any]]:
    build_dir = temp_dir / "classes"
    build_dir.mkdir()
    compile_result = subprocess.run(
        [
            "javac",
            "--release",
            "17",
            "-d",
            str(build_dir),
            str(ROOT / "src" / "VcfLogsClient.java"),
            str(ROOT / "tests" / "TestMain.java"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    require(
        compile_result.returncode == 0,
        "javac failed:\n" + compile_result.stdout + compile_result.stderr,
    )

    request_log = temp_dir / "requests.jsonl"
    port_file = temp_dir / "port"
    mock_process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "mock" / "mock_vcf_logs.py"),
            "--contract",
            str(ROOT / "docs" / "contract.json"),
            "--request-log",
            str(request_log),
            "--port-file",
            str(port_file),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while not port_file.exists() and time.monotonic() < deadline:
            require(mock_process.poll() is None, "mock exited before publishing its port")
            time.sleep(0.02)
        require(port_file.exists(), "timed out waiting for loopback mock")
        port = int(port_file.read_text(encoding="ascii"))

        result = subprocess.run(
            ["java", "-cp", str(build_dir), "TestMain", f"http://127.0.0.1:{port}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
        require(result.returncode == 0, "TestMain failed:\n" + result.stdout + result.stderr)
        require(result.stdout.strip() == "TEST_MAIN_OK", "TestMain did not complete its report assertions")
    finally:
        mock_process.terminate()
        try:
            mock_stdout, mock_stderr = mock_process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            mock_process.kill()
            mock_stdout, mock_stderr = mock_process.communicate(timeout=5)
        require(mock_process.returncode in (-15, 0),
                f"mock exited unexpectedly ({mock_process.returncode}):\n{mock_stdout}{mock_stderr}")

    return [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]


def verify_wire_log(requests: list[dict[str, Any]]) -> None:
    require(len(requests) == 4, f"expected exactly 4 requests, got {len(requests)}")
    require([request["sequence"] for request in requests] == [1, 2, 3, 4],
            "request sequence changed")
    require([request["operationId"] for request in requests]
            == ["POST_sessions", "PATCH_log-forwarder-id", "PATCH_log-forwarder-id",
                "PATCH_log-forwarder-id"],
            "client called an operation outside the pinned contract")

    session, first, second, third = requests
    require((session["method"], session["rawPath"]) == ("POST", "/api/v2/sessions"),
            "session request method/path is wrong")
    require(session["headers"].get("content-type") == "application/json",
            "session Content-Type must be application/json")
    require(session["headers"].get("accept") == "application/json",
            "session Accept must be application/json")
    require("authorization" not in session["headers"],
            "session request must not send an empty Authorization header")
    require(json.loads(session["body"]) == {
        "username": "automation@example.com",
        "password": "s3cr\"et\\line\nnext",
        "provider": "Local",
    }, "session request JSON wire shape is wrong")

    require((first["method"], first["rawPath"])
            == ("PATCH", f"/api/v2/log-forwarder/{FIRST_ID}"),
            "first PATCH method/path is wrong")
    require(first["headers"].get("authorization") == "Bearer session-token/9.0+mock",
            "first PATCH bearer header is wrong")
    require(first["headers"].get("content-type") == "application/json",
            "first PATCH Content-Type must be application/json")
    require(first["headers"].get("accept") == "application/json",
            "first PATCH Accept must be application/json")
    require(json.loads(first["body"]) == {
        "name": "checkout \"primary\"\nwest",
        "workerCount": 6,
        "diskCacheSize": 0,
        "tags": {},
        "filter": "app=checkout",
        "forwardComplementaryFields": False,
    }, "first PATCH must include false, zero and empty-map values while omitting unset fields")

    require((second["method"], second["rawPath"])
            == ("PATCH", f"/api/v2/log-forwarder/{SECOND_ID}"),
            "second PATCH method/path is wrong")
    require(second["headers"].get("authorization") == "Bearer session-token/9.0+mock",
            "second PATCH bearer header is wrong")
    require(second["headers"].get("content-type") == "application/json",
            "second PATCH Content-Type must be application/json")
    require(second["headers"].get("accept") == "application/json",
            "second PATCH Accept must be application/json")
    require(json.loads(second["body"]) == {
        "host": "logs-dr.example.com",
        "port": 1514,
        "protocol": "SYSLOG",
        "sslEnabled": True,
        "tags": {"site\"code": "dr\\east\nline"},
        "transportProtocol": "TCP",
        "testConnection": False,
    }, "second PATCH escaping, explicit false, or optional-field omission is wrong")

    require((third["method"], third["rawPath"])
            == ("PATCH", f"/api/v2/log-forwarder/{THIRD_ID}"),
            "third PATCH method/path is wrong or processing stopped after a failure")
    require(third["headers"].get("authorization") == "Bearer session-token/9.0+mock",
            "third PATCH bearer header is wrong")
    require(third["headers"].get("content-type") == "application/json",
            "third PATCH Content-Type must be application/json")
    require(third["headers"].get("accept") == "application/json",
            "third PATCH Accept must be application/json")
    require(json.loads(third["body"]) == {},
            "an all-unset patch must serialize as an empty JSON object")


def main() -> None:
    verify_provenance_and_contract()
    with tempfile.TemporaryDirectory(prefix="vcf90-0100-") as temp_name:
        requests = run_harness(Path(temp_name))
    verify_wire_log(requests)
    print("verification passed")


if __name__ == "__main__":
    main()
