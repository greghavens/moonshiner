#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0166."""

from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfOpsLogForwarder" / "VcfOpsLogForwarder.psd1"
MODULE_PATH = ROOT / "VcfOpsLogForwarder" / "VcfOpsLogForwarder.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
ROUTE = "/api/v2/logs/forwarders"
OPERATION_IDS = ["getAllLogForwarders", "createLogForwarder"]
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"
SCHEMA_PROPERTIES = [
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


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_operation_ids(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "operationId":
                result.append(child)
            else:
                result.extend(collect_operation_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(collect_operation_ids(child))
    return result


def verify_contract() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    require(contract["openapi"] == "3.0.1", "OpenAPI version changed")
    require(
        contract["info"]
        == {"title": "Log Management API\n", "version": "9.1.0.0"},
        "Log Management API identity changed",
    )
    require(
        contract["x-source"]
        == {
            "kind": "pinned-openapi-specification",
            "repository": "vmware/vcf-api-specs",
            "commitSha": COMMIT,
            "specPath": SPEC_PATH,
            "specBlobSha": SPEC_BLOB,
            "license": "Apache-2.0",
            "operationIds": OPERATION_IDS,
        },
        "contract provenance changed",
    )
    require(
        set(contract["paths"]) == {ROUTE},
        "contract contains a route outside the focused surface",
    )
    path_item = contract["paths"][ROUTE]
    require(list(path_item) == ["get", "post"], "focused methods changed")
    require(
        [path_item["get"]["operationId"], path_item["post"]["operationId"]]
        == OPERATION_IDS,
        "focused operationIds changed",
    )
    require(
        collect_operation_ids(contract) == OPERATION_IDS,
        "contract contains an unexpected operationId",
    )
    require(
        "200" in path_item["get"]["responses"]
        and "403" in path_item["get"]["responses"]
        and "201" in path_item["post"]["responses"]
        and "403" in path_item["post"]["responses"],
        "success or authentication responses changed",
    )

    security = contract["components"]["securitySchemes"][
        "OPSTokenAuthorization"
    ]
    require(
        (security["type"], security["in"], security["name"])
        == ("apiKey", "header", "X-JWT-Token"),
        "security scheme changed",
    )
    require(
        "POST /suite-api/api/auth/token/exchange" in security["description"]
        and '"serviceKeys": ["ops-li"]' in security["description"],
        "token exchange provenance was lost",
    )
    schema = contract["components"]["schemas"]["LogForwarder"]
    require(schema["type"] == "object", "LogForwarder type changed")
    require(
        list(schema["properties"]) == SCHEMA_PROPERTIES,
        "LogForwarder property order changed",
    )
    require(
        schema["properties"]["id"].get("readOnly") is True,
        "LogForwarder.id is no longer read-only",
    )
    require(
        schema["properties"]["protocol"]["enum"]
        == ["SYSLOG", "RAW", "RAWPLUS"]
        and schema["properties"]["transportProtocol"]["enum"]
        == ["TCP", "UDP"],
        "forwarder protocol enums changed",
    )

    require(sources["repositoryCommitSha"] == COMMIT, "source commit changed")
    require(sources["specPath"] == SPEC_PATH, "source path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "source blob changed")
    require(sources["license"] == "Apache-2.0", "source license changed")
    require(sources["operationIds"] == OPERATION_IDS, "source operations changed")
    operations = sources["operations"]
    require(
        [item["operationId"] for item in operations] == OPERATION_IDS,
        "official_sources must record each exact operationId",
    )
    for item, expected in zip(
        operations,
        [("GET", ROUTE, 763), ("POST", ROUTE, 803)],
    ):
        require(
            (item["method"], item["path"], item["specLine"]) == expected,
            "operation source location changed",
        )
        require(
            item["repositoryCommitSha"] == COMMIT
            and item["specPath"] == SPEC_PATH,
            "each operation must record its commit and spec path",
        )
        require(
            item["sourceUrl"].startswith(
                f"https://github.com/vmware/vcf-api-specs/blob/{COMMIT}/{SPEC_PATH}"
            ),
            "operation URL is not commit-pinned",
        )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"] is False,
        "contract must not come from a documentation page",
    )
    return contract


def verify_manifest_and_shape() -> None:
    manifest_literal = str(MANIFEST_PATH).replace("'", "''")
    command = (
        f"$d=Import-PowerShellDataFile -Path '{manifest_literal}';"
        "if($d.RootModule -cne 'VcfOpsLogForwarder.psm1'){exit 3};"
        "if($d.PowerShellVersion -cne '7.4'){exit 4};"
        "if($d.FunctionsToExport.Count -ne 1 -or "
        "$d.FunctionsToExport[0] -cne 'Sync-VcfOpsLogForwarder'){exit 5};"
        "$r=$d.RequiredModules[0];"
        f"if($r.ModuleName -cne '{MODULE_NAME}' -or "
        f"[string]$r.RequiredVersion -cne '{MODULE_VERSION}'){{exit 6}}"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(result.returncode == 0, "protected module manifest is invalid")

    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    require(
        "system.net.http.httpclient" in source or "net.http.httpclient" in source,
        "implementation must use System.Net.Http.HttpClient",
    )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.net.sockets",
        "tcpclient",
    ):
        require(forbidden not in source, f"forbidden transport found: {forbidden}")

    forbidden_suffixes = {".dll", ".nupkg", ".nuspec"}
    forbidden_names = {"VMware.Sdk.Vcf.Ops.psd1", "VMware.Sdk.Vcf.Ops.psm1"}
    for path in ROOT.rglob("*"):
        if path.is_file():
            require(
                path.suffix.casefold() not in forbidden_suffixes
                and path.name not in forbidden_names,
                f"vendored VMware dependency found: {path.relative_to(ROOT)}",
            )


def meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (dict, list, tuple)):
        return len(value) != 0
    return True


def projected_body(contract: dict[str, Any], desired: dict[str, Any]) -> bytes:
    properties = contract["components"]["schemas"]["LogForwarder"]["properties"]
    projected: dict[str, Any] = {}
    for name, definition in properties.items():
        if definition.get("readOnly") is True:
            continue
        if name in desired and meaningful(desired[name]):
            projected[name] = desired[name]
    return json.dumps(
        projected, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def runtime_config() -> dict[str, Any]:
    nonce = secrets.token_hex(7)
    existing_name = f"existing-{nonce}"
    first_name = f"edge-a-{nonce}"
    second_name = f"edge-b-{nonce}"
    initial = {
        "id": f"initial-{secrets.token_hex(8)}",
        "name": existing_name,
        "host": f"203.0.113.{1 + int(nonce[:2], 16) % 200}",
        "port": 514,
        "protocol": "SYSLOG",
        "transportProtocol": "UDP",
        "enabled": True,
    }
    desired = [
        {
            "transportProtocol": initial["transportProtocol"],
            "protocol": initial["protocol"],
            "port": initial["port"],
            "name": existing_name,
            "host": initial["host"],
        },
        {
            "clientOnly": f"omit-{nonce}",
            "transportProtocol": "UDP",
            "protocol": "SYSLOG",
            "port": 514,
            "name": first_name,
            "host": f"192.0.2.{1 + int(nonce[2:4], 16) % 200}",
            "enabled": False,
            "certificate": "",
            "sslEnabled": None,
            "tags": {},
            "workerCount": [],
            "id": f"read-only-{nonce}",
        },
        {
            "forwardComplementaryFields": "",
            "constraints": None,
            "transportProtocol": "TCP",
            "tags": {"site": f"dc-{nonce}"},
            "sslEnabled": True,
            "protocol": "RAW",
            "port": 6514,
            "name": second_name,
            "host": f"198.51.100.{1 + int(nonce[4:6], 16) % 200}",
            "enabled": True,
            "connectionRefreshInterval": 0,
        },
    ]
    return {
        "old_token": f"old-{secrets.token_urlsafe(30)}",
        "new_token": f"new-{secrets.token_urlsafe(30)}",
        "initial_forwarders": [initial],
        "desired": desired,
        "created_ids": {
            first_name: f"created-a-{secrets.token_hex(9)}",
            second_name: f"created-b-{secrets.token_hex(9)}",
        },
    }


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return load_json(path)
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(
                "mock exited before ready: " + (stderr or stdout).strip()
            )
        time.sleep(0.025)
    raise VerificationError("mock did not become ready")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_header(entry: dict[str, Any], name: str) -> str:
    values = entry["headers"].get(name.casefold(), [])
    require(len(values) == 1, f"{name} must appear exactly once")
    return values[0]


def assert_uncontracted_rejected(ready: dict[str, Any], log_path: Path) -> None:
    if ready["mode"] == "inprocess":
        return
    if ready["mode"] == "tcp":
        connection = http.client.HTTPConnection(
            "127.0.0.1", ready["port"], timeout=5
        )
        try:
            connection.request("POST", f"{ROUTE}/test", body=b"{}")
            response = connection.getresponse()
            response.read()
            require(response.status == 404, "mock served an unnamed operation")
        finally:
            connection.close()
    else:
        request = (
            f"POST {ROUTE}/test HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Length: 2\r\n"
            "Connection: close\r\n\r\n{}"
        ).encode("ascii")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(5)
            client.connect(ready["socket"])
            client.sendall(request)
            response_bytes = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response_bytes += chunk
        finally:
            client.close()
        require(
            response_bytes.startswith(b"HTTP/1.1 404"),
            "local fallback served an unnamed operation",
        )
    entries = read_log(log_path)
    require(
        len(entries) == 1 and entries[0]["operationId"] is None,
        "unnamed operation mapped to an operationId",
    )
    log_path.write_text("", encoding="utf-8")


def verify_wire(
    entries: list[dict[str, Any]],
    config: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    body_a = projected_body(contract, config["desired"][1])
    body_b = projected_body(contract, config["desired"][2])
    expected = [
        ("getAllLogForwarders", "GET", config["old_token"], b"", 200),
        ("createLogForwarder", "POST", config["old_token"], body_a, 201),
        ("createLogForwarder", "POST", config["old_token"], body_b, 403),
        ("createLogForwarder", "POST", config["new_token"], body_b, 201),
    ]
    require(
        len(entries) == len(expected),
        "reconciliation restarted, lost its place, or made an extra request",
    )
    for index, (entry, wanted) in enumerate(zip(entries, expected), start=1):
        operation, method, token, body, status = wanted
        require(entry["operationId"] == operation, f"request {index} operation changed")
        require(entry["method"] == method, f"request {index} method changed")
        require(entry["raw_target"] == ROUTE, f"request {index} target changed")
        require(entry["query"] == "", f"request {index} sent a query delimiter")
        require(one_header(entry, "accept") == "application/json", "Accept changed")
        require(one_header(entry, "x-jwt-token") == token, "token transition changed")
        require("authorization" not in entry["headers"], "Authorization was sent")
        require(entry["body_raw"].encode("utf-8") == body, f"request {index} body bytes changed")
        require(entry["status"] == status, f"request {index} scenario status changed")
        if method == "GET":
            require(entry["body_bytes"] == 0, "GET carried a body")
            require("content-type" not in entry["headers"], "GET sent Content-Type")
            require("content-length" not in entry["headers"], "GET sent Content-Length")
        else:
            require(
                one_header(entry, "content-type") == "application/json",
                "POST Content-Type changed",
            )
            require(
                one_header(entry, "content-length") == str(len(body)),
                "POST Content-Length changed",
            )

    require(entries[2]["body_raw"] == entries[3]["body_raw"], "retry body changed")
    parsed_a = json.loads(entries[1]["body_raw"])
    require(parsed_a.get("enabled") is False, "explicit false was dropped")
    require(
        not {
            "certificate",
            "constraints",
            "id",
            "sslEnabled",
            "tags",
            "workerCount",
            "clientOnly",
        }.intersection(parsed_a),
        "unset, empty, read-only, or unknown fields crossed the wire",
    )
    parsed_b = json.loads(entries[2]["body_raw"])
    require(
        parsed_b.get("connectionRefreshInterval") == 0,
        "explicit numeric zero was dropped",
    )
    require(
        "constraints" not in parsed_b and "forwardComplementaryFields" not in parsed_b,
        "empty optional fields crossed the wire",
    )


def run_success_case(contract: dict[str, Any]) -> None:
    config = runtime_config()
    with tempfile.TemporaryDirectory(prefix="vcf91-0166-") as temp_text:
        temp = Path(temp_text)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        output_path = temp / "output.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--config",
                str(config_path),
                "--log",
                str(log_path),
                "--ready",
                str(ready_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready = wait_for_ready(ready_path, process)
            require(ready["host"] == "127.0.0.1", "mock is not loopback")
            assert_uncontracted_rejected(ready, log_path)
            invocation = [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-Port",
                    str(ready["port"]),
                    "-ConfigPath",
                    str(config_path),
                    "-OutputPath",
                    str(output_path),
                ]
            if ready["mode"] == "unix":
                invocation.extend(["-SocketPath", ready["socket"]])
            elif ready["mode"] == "inprocess":
                invocation.extend(
                    [
                        "-FallbackContractPath",
                        str(CONTRACT_PATH),
                        "-FallbackLogPath",
                        str(log_path),
                    ]
                )
            result = subprocess.run(
                invocation,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        combined = result.stdout + result.stderr
        for token in (config["old_token"], config["new_token"]):
            require(token not in combined, "access token leaked to console output")
        require(
            result.returncode == 0,
            "PowerShell case failed without exposing protected diagnostics: "
            + combined.strip(),
        )
        output = load_json(output_path)
        require(output["ProviderCalls"] == [False, True], "provider call sequence changed")
        value = output["Result"]
        require(
            list(value) == ["Status", "Forwarders", "CreatedNames", "TokenRefreshes"],
            "result property order changed",
        )
        require(value["Status"] == "Reconciled", "result status changed")
        require(value["TokenRefreshes"] == 1, "refresh count changed")
        expected_names = [
            config["initial_forwarders"][0]["name"],
            config["desired"][1]["name"],
            config["desired"][2]["name"],
        ]
        require(
            [item["name"] for item in value["Forwarders"]] == expected_names,
            "existing-then-created output order changed",
        )
        require(
            value["CreatedNames"] == expected_names[1:],
            "created-name order changed",
        )
        require(
            [item["id"] for item in value["Forwarders"]]
            == [
                config["initial_forwarders"][0]["id"],
                config["created_ids"][expected_names[1]],
                config["created_ids"][expected_names[2]],
            ],
            "service-issued identities were not preserved",
        )
        verify_wire(read_log(log_path), config, contract)


def main() -> int:
    try:
        contract = verify_contract()
        verify_manifest_and_shape()
        run_success_case(contract)
    except (
        VerificationError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: pinned contract, token refresh, exact wire shape, and preserved work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
