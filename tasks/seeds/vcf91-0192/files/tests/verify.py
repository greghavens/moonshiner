#!/usr/bin/env python3
"""Protected deterministic acceptance verifier for the Java client."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from mock_server import start_contract_mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
EXPECTED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
EXPECTED_OPERATION_ID = "updateLogForwarder"
EXPECTED_TEMPLATE = "/api/v2/logs/forwarders/{id}"
EXPECTED_TARGET = "/api/v2/logs/forwarders/forwarder%2001%2Fblue"
EXPECTED_BODY = (
    '{"enabled":true,"forwardComplementaryFields":false,'
    '"host":"logs.example.test","name":"Primary \\"audit\\" relay",'
    '"port":6514,"protocol":"SYSLOG","sslEnabled":true,'
    '"transportProtocol":"TCP"}'
)
EXPECTED_PROPERTY_ORDER = [
    "certificate",
    "connectionRefreshInterval",
    "constraints",
    "enabled",
    "forwardComplementaryFields",
    "host",
    "id",
    "name",
    "port",
    "protocol",
    "sslEnabled",
    "tags",
    "transportProtocol",
    "workerCount",
]
UNSET_PROPERTIES = {
    "certificate",
    "connectionRefreshInterval",
    "constraints",
    "id",
    "tags",
    "workerCount",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_source_and_contract() -> None:
    sources = load_json(SOURCES_PATH)
    require(sources["repository"] == "https://github.com/vmware/vcf-api-specs", "wrong repository")
    require(sources["repositoryCommitSha"] == EXPECTED_SHA, "source commit is not pinned")
    require(sources["specPath"] == EXPECTED_SPEC_PATH, "wrong source spec path")
    require(sources["license"] == "Apache-2.0", "wrong source license")
    require(
        sources["pinnedSpecUrl"]
        == f"https://raw.githubusercontent.com/vmware/vcf-api-specs/{EXPECTED_SHA}/{EXPECTED_SPEC_PATH}",
        "source URL must use the immutable commit",
    )
    require(
        sources["operations"]
        == [
            {
                "operationId": EXPECTED_OPERATION_ID,
                "method": "PUT",
                "path": EXPECTED_TEMPLATE,
                "jsonPointer": "#/paths/~1api~1v2~1logs~1forwarders~1{id}/put",
            }
        ],
        "official source operations do not match the extracted contract",
    )

    contract = load_json(CONTRACT_PATH)
    require(contract["openapi"] == "3.0.1", "wrong OpenAPI version")
    require(contract["info"]["version"] == "9.1.0.0", "wrong product contract version")
    operations: list[tuple[str, str, str]] = []
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method.lower() in {"delete", "get", "head", "options", "patch", "post", "put"}:
                operations.append((operation["operationId"], method.upper(), path))
    require(
        operations == [(EXPECTED_OPERATION_ID, "PUT", EXPECTED_TEMPLATE)],
        "contract must expose exactly updateLogForwarder",
    )

    operation = contract["paths"][EXPECTED_TEMPLATE]["put"]
    require(operation["requestBody"]["required"] is True, "request body must be required")
    require(
        operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/LogForwarder",
        "wrong request schema",
    )
    require(list(operation["responses"]) == ["200", "400", "403", "404", "500", "502"], "wrong responses")
    schema = contract["components"]["schemas"]["LogForwarder"]
    require(list(schema["properties"]) == EXPECTED_PROPERTY_ORDER, "LogForwarder projection drifted")
    require(schema["properties"]["id"].get("readOnly") is True, "id must remain read-only")
    require(schema["properties"]["protocol"]["enum"] == ["SYSLOG", "RAW", "RAWPLUS"], "protocol drift")
    require(schema["properties"]["transportProtocol"]["enum"] == ["TCP", "UDP"], "transport drift")
    security = contract["components"]["securitySchemes"]["OPSTokenAuthorization"]
    require(security["type"] == "apiKey" and security["in"] == "header", "wrong security scheme")
    require(security["name"] == "X-JWT-Token", "wrong token header")


def compile_client(classes: Path) -> None:
    result = subprocess.run(
        [
            "javac",
            "--release",
            "17",
            "-encoding",
            "UTF-8",
            "-d",
            str(classes),
            str(ROOT / "VcfLogForwarderClient.java"),
            str(ROOT / "TestMain.java"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )
    require(result.returncode == 0, "javac failed:\n" + result.stdout + result.stderr)


def read_request_log(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def verify_wire(records: list[dict[str, Any]], port: int) -> None:
    require(len(records) == 2, f"expected exactly two PUT attempts, got {len(records)}")
    require([item["responseStatus"] for item in records] == [500, 200], "retry sequence must be 500 then 200")
    require([item["effectApplied"] for item in records] == [True, False], "retry duplicated the mutation effect")

    expected_headers = {
        "accept": ["application/json"],
        "content-length": [str(len(EXPECTED_BODY.encode("utf-8")))],
        "content-type": ["application/json; charset=utf-8"],
        "host": [f"127.0.0.1:{port}"],
        "user-agent": ["vcf91-log-forwarder-client/1.0"],
        "x-jwt-token": ["test-jwt-token"],
    }
    for index, record in enumerate(records, start=1):
        require(record["operationId"] == EXPECTED_OPERATION_ID, f"attempt {index}: wrong operation")
        require(record["method"] == "PUT", f"attempt {index}: method is not PUT")
        require(record["target"] == EXPECTED_TARGET, f"attempt {index}: wrong encoded request target")
        require(record["requestVersion"] == "HTTP/1.1", f"attempt {index}: wrong HTTP version")
        require(record["headers"] == expected_headers, f"attempt {index}: headers differ: {record['headers']!r}")
        require(record["rawBody"] == EXPECTED_BODY, f"attempt {index}: request bytes differ")
        require(record["body"].get("forwardComplementaryFields") is False, "explicit false was lost")
        require(UNSET_PROPERTIES.isdisjoint(record["body"]), f"attempt {index}: unset property was serialized")
        require(not any(value == "" for value in record["body"].values()), "empty-string stand-in was serialized")
    require(records[0]["rawBody"] == records[1]["rawBody"], "retry body is not byte-identical")
    require(records[0]["headers"] == records[1]["headers"], "retry headers are not identical")


def main() -> int:
    verify_source_and_contract()
    with tempfile.TemporaryDirectory(prefix="vcf91-0192-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_client(classes)
        request_log = temp / "requests.jsonl"
        server = start_contract_mock(CONTRACT_PATH, request_log)
        require(server.server_address[0] == "127.0.0.1", "mock is not loopback-bound")
        require(
            [(route["operationId"], route["method"], route["path"]) for route in server.operation_routes]
            == [(EXPECTED_OPERATION_ID, "PUT", EXPECTED_TEMPLATE)],
            "mock serves an operation not named by the contract",
        )
        thread = threading.Thread(target=server.serve_forever, name="vcf-contract-mock", daemon=True)
        thread.start()
        port = int(server.server_address[1])
        try:
            result = subprocess.run(
                ["java", "-cp", str(classes), "TestMain", f"http://127.0.0.1:{port}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        require(
            result.returncode == 0,
            "TestMain failed:\n" + result.stdout + result.stderr,
        )
        require(result.stdout.strip() == "TEST_MAIN_OK", "unexpected TestMain output")
        records = read_request_log(request_log)
        verify_wire(records, port)
        require(server.effect_count == 1, f"expected one mutation effect, got {server.effect_count}")
        require(
            list(server.resources) == ["forwarder 01/blue"],
            "mock addressed a different resource",
        )

    print("PASS: updateLogForwarder is contract-exact and retry-safe")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
