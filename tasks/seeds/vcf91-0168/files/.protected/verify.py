#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0168."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
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
OPERATION_IDS = ["getAllLogForwarders", "createLogForwarder"]
ROUTE = "/api/v2/logs/forwarders"
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"
RESULT_KEYS = [
    "Status",
    "Id",
    "Name",
    "Host",
    "Port",
    "Protocol",
    "TransportProtocol",
    "SslEnabled",
    "Enabled",
]
FOCUSED_BODY_KEYS = [
    "enabled",
    "host",
    "name",
    "port",
    "protocol",
    "sslEnabled",
    "transportProtocol",
]
UNSET_BODY_KEYS = [
    "certificate",
    "connectionRefreshInterval",
    "constraints",
    "forwardComplementaryFields",
    "tags",
    "workerCount",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract blob changed")
    require(source["license"] == "Apache-2.0", "source license changed")
    require(source["openapi"] == "3.0.1", "OpenAPI version changed")
    require(source["apiVersion"] == "9.1.0.0", "API version changed")
    require(
        source["serverUrlInSpecification"] == "http://localhost:8787",
        "specification server projection changed",
    )
    require(
        contract["securitySchemes"]["OPSTokenAuthorization"]
        == {
            "type": "apiKey",
            "in": "header",
            "name": "X-JWT-Token",
        },
        "log token security projection changed",
    )

    operations = contract["operations"]
    require(
        [item["operationId"] for item in operations] == OPERATION_IDS,
        "focused operationId order changed",
    )
    require(
        [(item["method"], item["path"]) for item in operations]
        == [("GET", ROUTE), ("POST", ROUTE)],
        "focused routes changed",
    )

    listing, create = operations
    require(listing["parameters"] == [], "list parameters changed")
    require(listing["requestBody"] is False, "list request must be bodyless")
    require(
        listing["responses"]["200"]
        == {
            "contentType": "application/json",
            "type": "array",
            "itemsSchema": "LogForwarder",
            "description": "Log-forwarders retrieved successfully",
        },
        "list response projection changed",
    )
    body = create["requestBody"]
    require(
        body
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "LogForwarder",
            "focusedPropertyOrder": FOCUSED_BODY_KEYS,
            "readOnlyProperties": ["id"],
            "unsetProperties": UNSET_BODY_KEYS,
            "unsetBehavior": "omit",
        },
        "create body projection or omission semantics changed",
    )
    require(
        create["responses"]["201"]["schema"] == "LogForwarder",
        "create response projection changed",
    )

    schema = contract["schemas"]["LogForwarder"]
    property_names = list(schema["properties"].keys())
    require(schema["type"] == "object", "LogForwarder type changed")
    require(schema["required"] == [], "LogForwarder required set changed")
    require(
        property_names
        == [
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
        ],
        "LogForwarder property projection changed",
    )
    require(
        schema["properties"]["id"]["readOnly"] is True,
        "LogForwarder id must remain read-only",
    )
    require(
        schema["properties"]["protocol"]["enum"]
        == ["SYSLOG", "RAW", "RAWPLUS"],
        "LogForwarder protocol enum changed",
    )
    require(
        schema["properties"]["transportProtocol"]["enum"] == ["TCP", "UDP"],
        "LogForwarder transport enum changed",
    )

    profile = contract["idempotentEnsureProfile"]
    require(
        profile["lookupOperation"] == "logForwarders.list"
        and profile["mutationOperation"] == "logForwarders.create"
        and profile["identityField"] == "name"
        and profile["comparisonFields"]
        == [
            "host",
            "port",
            "protocol",
            "transportProtocol",
            "sslEnabled",
            "enabled",
        ],
        "retry-safe ensure profile changed",
    )
    require(
        "focused retry-safe application scenario"
        in profile["specificationBoundary"],
        "specification/scenario boundary is unclear",
    )

    require(
        sources["repositoryCommitSha"] == COMMIT,
        "official source commit changed",
    )
    require(sources["specPath"] == SPEC_PATH, "official spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "official blob changed")
    require(sources["license"] == "Apache-2.0", "official license changed")
    require(
        sources["operationIds"] == OPERATION_IDS,
        "official operationIds changed",
    )
    require(
        COMMIT in sources["specUrl"]
        and sources["specUrl"].endswith(SPEC_PATH),
        "official specification URL is not immutable",
    )
    require(
        [
            {
                "operationId": item["operationId"],
                "method": item["method"],
                "path": item["path"],
                "specLine": item["specLine"],
                "repositoryCommitSha": item["repositoryCommitSha"],
                "specPath": item["specPath"],
            }
            for item in sources["operations"]
        ]
        == [
            {
                "operationId": "getAllLogForwarders",
                "method": "GET",
                "path": ROUTE,
                "specLine": 765,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
            {
                "operationId": "createLogForwarder",
                "method": "POST",
                "path": ROUTE,
                "specLine": 805,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
        ],
        "each operation must carry its exact pinned source",
    )
    require(
        sources["schemaLines"]
        == [
            {"name": "LogForwarder", "specLine": 2038},
            {"name": "OPSTokenAuthorization", "specLine": 3256},
        ],
        "schema source locations changed",
    )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"]
        is False,
        "a documentation page must not be the contract source",
    )
    return contract


def verify_manifest_and_shape() -> None:
    command = (
        "$d = Import-PowerShellDataFile -Path '"
        + str(MANIFEST_PATH).replace("'", "''")
        + "'; "
        + "if ($d.RootModule -cne 'VcfOpsLogForwarder.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne "
        + "'Ensure-VcfOpsLogForwarder') { exit 5 }; "
        + "$r = $d.RequiredModules[0]; "
        + "if ($r.ModuleName -cne '"
        + MODULE_NAME
        + "' -or [string]$r.RequiredVersion -cne '"
        + MODULE_VERSION
        + "') { exit 6 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
        env={
            **os.environ,
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        },
    )
    require(result.returncode == 0, "protected manifest is invalid")

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    require(
        "system.net.http.httpclient" in folded
        or "net.http.httpclient" in folded,
        "implementation must use System.Net.Http.HttpClient",
    )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.diagnostics.process",
        "tcpclient",
        "curl",
        "wget",
    ):
        require(forbidden not in folded, f"forbidden implementation: {forbidden}")
    require(
        folded.count("export-modulemember") == 1
        and "export-modulemember -function ensure-vcfopslogforwarder"
        in folded,
        "module must export only the requested function",
    )

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path != MANIFEST_PATH
        and (
            path.name.casefold().startswith("vmware.sdk")
            or path.suffix.casefold() in {".dll", ".nupkg"}
        )
    ]
    require(not vendored, "VMware SDK content must not be vendored")


def runtime_config(*, drift: bool) -> dict[str, Any]:
    nonce = secrets.token_hex(8)
    desired = {
        "enabled": True,
        "host": f"collector-{nonce}.internal.example",
        "name": f"retry-safe-{nonce}",
        "port": 6514,
        "protocol": "SYSLOG",
        "sslEnabled": True,
        "transportProtocol": "TCP",
    }
    unrelated = {
        "id": str(uuid.uuid4()),
        "enabled": False,
        "host": f"legacy-{nonce}.internal.example",
        "name": f"legacy-{nonce}",
        "port": 514,
        "protocol": "RAW",
        "sslEnabled": False,
        "transportProtocol": "UDP",
    }
    initial = [unrelated]
    if drift:
        initial.append(
            {
                "id": str(uuid.uuid4()),
                **desired,
                "port": desired["port"] - 1,
            }
        )
    return {
        "log_token": f"jwt-{secrets.token_urlsafe(30)}",
        "created_id": str(uuid.uuid4()),
        "desired": desired,
        "initial_forwarders": initial,
    }


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return int(load_json(path)["port"])
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(
                "mock exited before readiness: " + stdout + stderr
            )
        time.sleep(0.02)
    raise VerificationError("mock readiness timed out")


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def run_case(
    mode: str,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0168-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        output_path = temp / "output.json"
        config_path.write_text(
            json.dumps(config, separators=(",", ":")),
            encoding="utf-8",
        )

        mock = subprocess.Popen(
            [
                "python3",
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
            port = wait_for_ready(ready_path, mock)
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-Port",
                    str(port),
                    "-ConfigPath",
                    str(config_path),
                    "-OutputPath",
                    str(output_path),
                    "-Mode",
                    mode,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
            require(
                result.returncode == 0,
                "PowerShell case failed without exposing captured output",
            )
            require(output_path.exists(), "PowerShell case produced no result")
            output = load_json(output_path)
            requests = read_log(log_path)
        finally:
            if mock.poll() is None:
                mock.terminate()
                try:
                    mock.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.wait(timeout=3)
        return output, requests


def require_common_headers(
    request: dict[str, Any],
    token: str,
) -> None:
    headers = request["headers"]
    require(
        headers.get("accept") == ["application/json"],
        "Accept header must appear exactly once",
    )
    require(
        headers.get("x-jwt-token") == [token],
        "X-JWT-Token must appear exactly once",
    )
    require("authorization" not in headers, "unexpected Authorization header")
    require(request["query"] == "", "focused operations have no query")


def expected_result(
    status: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    desired = config["desired"]
    return {
        "Status": status,
        "Id": config["created_id"],
        "Name": desired["name"],
        "Host": desired["host"],
        "Port": desired["port"],
        "Protocol": desired["protocol"],
        "TransportProtocol": desired["transportProtocol"],
        "SslEnabled": desired["sslEnabled"],
        "Enabled": desired["enabled"],
    }


def verify_retry_case() -> None:
    config = runtime_config(drift=False)
    output, requests = run_case("Retry", config)
    require(
        [item["method"] for item in requests] == ["GET", "POST", "GET"],
        "retry must produce exactly GET, POST, GET",
    )
    require(
        [item["operationId"] for item in requests]
        == [
            "getAllLogForwarders",
            "createLogForwarder",
            "getAllLogForwarders",
        ],
        "request sequence left the focused operation set",
    )

    for index in (0, 2):
        request = requests[index]
        require(request["raw_target"] == ROUTE, "GET raw target changed")
        require(request["body_bytes"] == 0, "GET must be bodyless")
        require(request["body_raw"] == "", "GET body must be empty")
        require("content-type" not in request["headers"], "GET has Content-Type")
        require_common_headers(request, config["log_token"])

    post = requests[1]
    require(post["raw_target"] == ROUTE, "POST raw target changed")
    require_common_headers(post, config["log_token"])
    require(
        post["headers"].get("content-type") == ["application/json"],
        "POST Content-Type must appear exactly once",
    )
    expected_raw = json.dumps(config["desired"], separators=(",", ":"))
    require(post["body_raw"] == expected_raw, "POST body bytes changed")
    require(post["body_bytes"] == len(expected_raw), "POST byte count changed")
    require(list(post["body"].keys()) == FOCUSED_BODY_KEYS, "body order changed")
    require(post["body"] == config["desired"], "body values changed")
    for name in ["id", *UNSET_BODY_KEYS]:
        require(name not in post["body"], f"unset property was sent: {name}")
    require(
        not any(
            value is None or value == "" or value == [] or value == {}
            for value in post["body"].values()
        ),
        "POST contains an empty optional value",
    )

    require(list(output.keys()) == ["first", "second"], "case output changed")
    first = output["first"]
    second = output["second"]
    require(list(first.keys()) == RESULT_KEYS, "Created result shape changed")
    require(list(second.keys()) == RESULT_KEYS, "Unchanged result shape changed")
    require(first == expected_result("Created", config), "Created result changed")
    require(
        second == expected_result("Unchanged", config),
        "retry result changed",
    )
    require(
        config["log_token"] not in json.dumps(output),
        "result leaked the log token",
    )


def verify_drift_case() -> None:
    config = runtime_config(drift=True)
    output, requests = run_case("Drift", config)
    require(output["threw"] is True, "drifted identity must fail")
    require(isinstance(output["error"], str), "drift error is missing")
    require(
        config["log_token"] not in output["error"],
        "drift error leaked the log token",
    )
    require(
        len(requests) == 1
        and requests[0]["method"] == "GET"
        and requests[0]["operationId"] == "getAllLogForwarders",
        "drift must stop after the collection lookup",
    )
    request = requests[0]
    require(request["raw_target"] == ROUTE, "drift GET target changed")
    require(request["body_bytes"] == 0, "drift GET must be bodyless")
    require("content-type" not in request["headers"], "drift GET has Content-Type")
    require_common_headers(request, config["log_token"])


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        verify_retry_case()
        verify_drift_case()
    except (
        VerificationError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(f"verification failed: {error}", file=os.sys.stderr)
        return 1
    print("verification passed: contract, exact wire shape, and retry safety")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
