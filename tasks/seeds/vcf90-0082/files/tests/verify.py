#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tests" / "mock_vcf_logs.py"
CLIENT = ROOT / "tests" / "run-client.ps1"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_pins():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    operation_ids = [item["operationId"] for item in contract["operations"]]
    expected = ["POST_sessions", "POST_log-forwarder", "GET_log-forwarder"]
    require(operation_ids == expected, "contract operation IDs changed")
    require(sources["operationIds"] == expected, "official source operation IDs changed")
    require(sources["tag"] == "9.0.0.0", "contract is not pinned to VCF 9.0")
    require(
        sources["commitSha"] == "d81412d7bdd9642a488f897b35c2b09acae60db3",
        "official source commit changed",
    )
    require(
        sources["specPath"]
        == "specifications/vcf-operations/vcf-operations-for-logs-openapi.json",
        "official specification path changed",
    )


def wait_for_port(path, process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("loopback fixture exited before becoming ready")
        if path.exists() and path.stat().st_size:
            return int(path.read_text(encoding="ascii"))
        time.sleep(0.01)
    raise AssertionError("loopback fixture did not publish its port")


def check_wire_log(entries, show_details, provider, failure_status, expire_after):
    require(len(entries) == 6, f"expected 6 requests, received {len(entries)}")
    list_target = (
        "/api/v2/log-forwarder?showDetails=true"
        if show_details
        else "/api/v2/log-forwarder"
    )
    if expire_after == 1:
        expected_operations = [
            "POST_sessions",
            "POST_log-forwarder",
            "POST_log-forwarder",
            "POST_sessions",
            "POST_log-forwarder",
            "GET_log-forwarder",
        ]
        expected_statuses = [200, 201, failure_status, 200, 201, 200]
        expected_targets = [
            "/api/v2/sessions",
            "/api/v2/log-forwarder",
            "/api/v2/log-forwarder",
            "/api/v2/sessions",
            "/api/v2/log-forwarder",
            list_target,
        ]
        expected_methods = ["POST", "POST", "POST", "POST", "POST", "GET"]
        expected_authorization = [
            None,
            "Bearer session-1",
            "Bearer session-1",
            None,
            "Bearer session-2",
            "Bearer session-2",
        ]
        session_indexes = (0, 3)
        post_indexes = (1, 2, 4)
        get_indexes = (5,)
    else:
        expected_operations = [
            "POST_sessions",
            "POST_log-forwarder",
            "POST_log-forwarder",
            "GET_log-forwarder",
            "POST_sessions",
            "GET_log-forwarder",
        ]
        expected_statuses = [200, 201, 201, failure_status, 200, 200]
        expected_targets = [
            "/api/v2/sessions",
            "/api/v2/log-forwarder",
            "/api/v2/log-forwarder",
            list_target,
            "/api/v2/sessions",
            list_target,
        ]
        expected_methods = ["POST", "POST", "POST", "GET", "POST", "GET"]
        expected_authorization = [
            None,
            "Bearer session-1",
            "Bearer session-1",
            "Bearer session-1",
            None,
            "Bearer session-2",
        ]
        session_indexes = (0, 4)
        post_indexes = (1, 2)
        get_indexes = (3, 5)

    require(
        [entry["operationId"] for entry in entries] == expected_operations,
        "request operation order does not preserve completed work during refresh",
    )
    require(
        [entry["responseStatus"] for entry in entries] == expected_statuses,
        "fixture did not observe the expected expiration and same-operation recovery",
    )
    require(
        [entry["target"] for entry in entries] == expected_targets,
        "method target or showDetails query is incorrect",
    )
    require(
        [entry["method"] for entry in entries] == expected_methods,
        "HTTP methods are incorrect",
    )

    session_body = {
        "username": "ops-admin",
        "password": "fixture-password",
        "provider": provider,
    }
    for index in session_indexes:
        require(entries[index]["body"] == session_body, "session JSON is incorrect")
        require(
            entries[index]["authorization"] is None,
            "session request must not carry a bearer token",
        )

    first = {
        "name": "edge-syslog",
        "host": "192.0.2.10",
        "port": 514,
        "protocol": "SYSLOG",
        "sslEnabled": False,
    }
    second = {
        "name": "cfapi-archive",
        "host": "192.0.2.20",
        "port": 9000,
        "protocol": "CFAPI",
        "sslEnabled": True,
        "acceptCert": False,
        "workerCount": 6,
        "connectionRefreshInterval": 30,
        "diskCacheSize": 0,
        "tags": {"site": "central"},
        "filter": "severity:error",
        "transportProtocol": "TCP_OCTET",
        "forwardComplementaryFields": False,
        "testConnection": True,
    }
    expected_post_bodies = [first, second, second] if expire_after == 1 else [first, second]
    require(
        [entries[index]["body"] for index in post_indexes] == expected_post_bodies,
        "forwarder JSON, omission behavior, input order, or retry body is incorrect",
    )
    expected_query = {"showDetails": ["true"]} if show_details else {}
    for index in get_indexes:
        require(entries[index]["body"] is None, "GET request unexpectedly has a body")
        require(entries[index]["bodyText"] == "", "GET request body bytes are not empty")
        require(
            entries[index]["query"] == expected_query,
            "showDetails query wire shape is incorrect",
        )

    require(
        [entry["authorization"] for entry in entries] == expected_authorization,
        "Bearer token was absent, stale after refresh, or sent on a session request",
    )
    for index, entry in enumerate(entries):
        if entry["operationId"] == "GET_log-forwarder":
            continue
        content_type = (entries[index]["contentType"] or "").split(";", 1)[0].lower()
        require(content_type == "application/json", f"request {index + 1} is not JSON")


def run_acceptance(show_details, provider, failure_status, expire_after, pipeline):
    with tempfile.TemporaryDirectory(prefix="vcf-logs-") as directory:
        temp = Path(directory)
        request_log = temp / "requests.ndjson"
        port_file = temp / "port"
        result_file = temp / "result.json"
        server = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--request-log",
                str(request_log),
                "--port-file",
                str(port_file),
                "--failure-status",
                str(failure_status),
                "--expire-after",
                str(expire_after),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_port(port_file, server)
            command = [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(CLIENT),
                "-Server",
                f"http://127.0.0.1:{port}",
                "-ResultPath",
                str(result_file),
            ]
            if provider is not None:
                command.extend(("-Provider", provider))
            if show_details:
                command.append("-ShowDetails")
            if pipeline:
                command.append("-Pipeline")
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
            )
            require(
                completed.returncode == 0,
                "PowerShell client failed:\n" + completed.stdout + completed.stderr,
            )
            result = json.loads(result_file.read_text(encoding="utf-8-sig"))
            require(isinstance(result, list) and len(result) == 2, "work was lost or duplicated")
            require(
                [item["name"] for item in result] == ["edge-syslog", "cfapi-archive"],
                "returned forwarder list is incorrect",
            )
            require(
                result[0]
                == {
                    "name": "edge-syslog",
                    "host": "192.0.2.10",
                    "port": 514,
                    "protocol": "SYSLOG",
                    "sslEnabled": False,
                    "workerCount": 4,
                    "diskCacheSize": 1000000000,
                    "tags": {},
                    "filter": "",
                    "forwardComplementaryFields": False,
                    "id": "forwarder-001",
                },
                "first returned forwarder does not match the current service state",
            )
            require(
                result[1]
                == {
                    "name": "cfapi-archive",
                    "host": "192.0.2.20",
                    "port": 9000,
                    "protocol": "CFAPI",
                    "sslEnabled": True,
                    "workerCount": 6,
                    "connectionRefreshInterval": 30,
                    "diskCacheSize": 0,
                    "tags": {"site": "central"},
                    "filter": "severity:error",
                    "transportProtocol": "TCP_OCTET",
                    "forwardComplementaryFields": False,
                    "id": "forwarder-002",
                },
                "second returned forwarder does not match the current service state",
            )
            entries = [
                json.loads(line)
                for line in request_log.read_text(encoding="utf-8").splitlines()
                if line
            ]
            check_wire_log(
                entries,
                show_details,
                provider if provider is not None else "Local",
                failure_status,
                expire_after,
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)


if __name__ == "__main__":
    try:
        check_pins()
        run_acceptance(
            show_details=False,
            provider=None,
            failure_status=440,
            expire_after=1,
            pipeline=False,
        )
        run_acceptance(
            show_details=True,
            provider="vIDM",
            failure_status=401,
            expire_after=2,
            pipeline=True,
        )
    except (AssertionError, FileNotFoundError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: VCF Operations for Logs contract, refresh flow, and wire shape verified")
