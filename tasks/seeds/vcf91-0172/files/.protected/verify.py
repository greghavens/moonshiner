#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0172."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfOpsLogDiagnosis" / "VcfOpsLogDiagnosis.psd1"
MODULE_PATH = ROOT / "VcfOpsLogDiagnosis" / "VcfOpsLogDiagnosis.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_ID = "executeLogSearchQuery_1"
METHOD = "POST"
ROUTE = "/api/v2/logs/search"
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"
UNSET_QUERY_REQUEST = {
    "aggregations",
    "from",
    "indices",
    "scroll",
    "scrollSize",
}


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
    require(source["title"] == "Log Management API", "API title changed")
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
    require(len(operations) == 1, "focused contract must name one operation")
    operation = operations[0]
    require(
        operation["operationId"] == OPERATION_ID
        and operation["method"] == METHOD
        and operation["path"] == ROUTE,
        "focused operation projection changed",
    )
    require(
        operation["security"] == ["OPSTokenAuthorization"],
        "effective operation security changed",
    )
    require(
        operation["requestBody"]
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "QueryRequest",
        },
        "search request projection changed",
    )
    require(
        {
            code: {
                "contentType": item["contentType"],
                "schema": item["schema"],
            }
            for code, item in operation["responses"].items()
        }
        == {
            "200": {
                "contentType": "application/json",
                "schema": "QueryResponse",
            },
            "400": {
                "contentType": "application/json",
                "schema": "ErrorBody",
            },
            "403": {
                "contentType": "application/json",
                "schema": "ErrorBody",
            },
        },
        "search response projection changed",
    )

    schemas = contract["schemas"]
    require(
        list(schemas["QueryRequest"]["properties"].keys())
        == [
            "aggregations",
            "from",
            "indices",
            "query",
            "scroll",
            "scrollSize",
            "size",
            "sort",
            "trackTotalHits",
        ]
        and schemas["QueryRequest"]["required"] == []
        and schemas["QueryRequest"]["properties"]["from"]["maximum"] == 20000
        and schemas["QueryRequest"]["properties"]["size"]["maximum"] == 2000,
        "QueryRequest projection changed",
    )
    require(
        list(schemas["Query"]["properties"].keys())
        == [
            "bool",
            "exists",
            "match_all",
            "match_phrase",
            "prefix",
            "range",
            "regexp",
            "term",
            "terms",
        ]
        and schemas["Query"]["properties"]["match_phrase"][
            "additionalProperties"
        ]
        == {"type": "string"}
        and schemas["Query"]["properties"]["range"]["additionalProperties"]
        == {"$ref": "RangeQueryValue"},
        "Query projection changed",
    )
    require(
        list(schemas["BoolQuery"]["properties"].keys())
        == ["filter", "must", "must_not", "should"],
        "BoolQuery projection changed",
    )
    require(
        list(schemas["RangeQueryValue"]["properties"].keys())
        == ["gt", "gte", "lt", "lte"],
        "RangeQueryValue projection changed",
    )
    require(
        schemas["SortOptions"]["additionalProperties"]
        == {"$ref": "SortOrder"}
        and schemas["SortOrder"]["properties"]["order"]["enum"]
        == ["asc", "desc"],
        "sort projection changed",
    )
    require(
        list(schemas["QueryResponse"]["properties"].keys())
        == [
            "aggregations",
            "events",
            "failureMessage",
            "failureReason",
            "timeTakenMillis",
            "timedOut",
        ]
        and schemas["QueryResponse"]["properties"]["failureReason"]["enum"]
        == ["SYSTEM", "QUERY", "DATA_AVAILABILITY", "OTHER"],
        "QueryResponse projection changed",
    )
    require(
        schemas["EventsResult"]["properties"]["hits"]["items"]
        == {"$ref": "LogMessage"}
        and schemas["LogMessage"]["properties"]["msgContent"]
        == {"$ref": "MessageContent"}
        and list(schemas["MessageContent"]["properties"].keys())
        == [
            "fields",
            "incomingAddress",
            "ingestTimestamp",
            "logTimestamp",
            "originalText",
        ],
        "events response projection changed",
    )
    require(
        list(schemas["Field"]["properties"].keys())
        == [
            "displayName",
            "fieldCategory",
            "fieldType",
            "internalName",
            "length",
            "startPosition",
            "value",
            "valueType",
        ]
        and schemas["Field"]["properties"]["value"] == {},
        "Field projection changed",
    )
    require(
        list(schemas["ErrorBody"]["properties"].keys())
        == ["errorCode", "errorDetails", "errorMessage"]
        and "SEARCH_ERROR"
        in schemas["ErrorBody"]["properties"]["errorCode"]["enumIncludes"],
        "ErrorBody projection changed",
    )

    workflow = contract["focusedWorkflow"]
    require(
        workflow["operationOrder"] == [OPERATION_ID, OPERATION_ID],
        "focused operation order changed",
    )
    first = workflow["requestBodies"]["failureLogSearch"]
    second = workflow["requestBodies"]["correlatedEventSearch"]
    require(
        first["topLevelPropertyOrder"]
        == ["query", "size", "sort", "trackTotalHits"]
        and first["filterOrder"]
        == [
            "match_phrase.request_id",
            "match_phrase.event_type",
            "range.timestamp",
        ]
        and first["eventType"] == "DEPLOYMENT_FAILED",
        "failure-log request projection changed",
    )
    require(
        second["topLevelPropertyOrder"]
        == ["query", "size", "sort", "trackTotalHits"]
        and second["filterOrder"]
        == [
            "match_phrase.correlation_id",
            "match_phrase.event_type",
            "range.timestamp",
        ]
        and second["eventType"] == "CERTIFICATE_VALIDATION_FAILED",
        "correlated-event request projection changed",
    )
    for item in (first, second):
        require(
            item["size"] == 25
            and item["sort"]
            == [{"timestamp": {"order": "asc"}}]
            and item["trackTotalHits"] is False,
            "focused search options changed",
        )
    require(
        set(workflow["unsetQueryRequestProperties"])
        == UNSET_QUERY_REQUEST
        and workflow["unsetBehavior"] == "omit",
        "optional-field omission contract changed",
    )
    require(
        "come from validated QueryResponse" in workflow["evidenceBoundary"]
        and "pinned OpenAPI JSON specification"
        in workflow["specificationBoundary"],
        "evidence or specification boundary is unclear",
    )

    require(
        sources["repositoryCommitSha"] == COMMIT,
        "official source commit changed",
    )
    require(sources["specPath"] == SPEC_PATH, "official spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "official blob changed")
    require(sources["license"] == "Apache-2.0", "official license changed")
    require(
        sources["operationIds"] == [OPERATION_ID],
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
                "operationId": OPERATION_ID,
                "method": METHOD,
                "path": ROUTE,
                "specLine": 1201,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            }
        ],
        "operation must carry its exact pinned source",
    )
    require(
        len(sources["operations"][0]["usedFor"]) == 2,
        "both evidence pulls must be attributed to the operation",
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
        + "if ($d.RootModule -cne 'VcfOpsLogDiagnosis.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne "
        + "'Get-VcfOpsIncidentDiagnosis') { exit 5 }; "
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
    require(result.returncode == 0, "protected PowerShell manifest is invalid")

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    require(
        "system.net.http.httpclient" in folded
        or "net.http.httpclient" in folded,
        "implementation must use System.Net.Http.HttpClient",
    )
    require(
        folded.splitlines().count(
            "function get-vcfopsincidentdiagnosis {"
        )
        == 1,
        "public function declaration changed",
    )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.net.sockets",
        "tcpclient",
        "/api/v2/search",
    ):
        require(forbidden not in folded, f"forbidden transport found: {forbidden}")

    vendored_suffixes = {".dll", ".nupkg", ".nuspec"}
    vendored_names = {"VMware.Sdk.Vcf.Ops.psm1", "VMware.Sdk.Vcf.Ops.psd1"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        require(
            path.suffix.casefold() not in vendored_suffixes
            and path.name not in vendored_names,
            f"vendored VMware dependency or binary found: {path.relative_to(ROOT)}",
        )


def wait_for_ready(
    process: subprocess.Popen[str],
    ready_path: Path,
    timeout: float = 10.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_path.exists() and ready_path.stat().st_size:
            return load_json(ready_path)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before ready: " + stdout + stderr
            )
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def run_diagnosis_case() -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
]:
    start_time = 1_790_000_000_000 + secrets.randbelow(50_000_000)
    config = {
        "log_token": "log-token-" + secrets.token_urlsafe(24),
        "request_id": "request-" + secrets.token_hex(10),
        "correlation_id": "correlation-" + secrets.token_hex(12),
        "component": "vcenter-" + secrets.token_hex(6),
        "start_time": start_time,
        "end_time": start_time + 600_000,
        "failure_timestamp": start_time + 80_000,
        "cause_timestamp": start_time + 120_000,
        "failure_message": (
            "deployment failed; evidence " + secrets.token_hex(12)
        ),
        "cause_message": (
            "certificate validation event; evidence "
            + secrets.token_hex(12)
        ),
        "first_time_taken": 1 + secrets.randbelow(20),
        "second_time_taken": 1 + secrets.randbelow(20),
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0172-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        result_path = temp / "result.json"
        config_path.write_text(
            json.dumps(config, separators=(",", ":")),
            encoding="utf-8",
        )

        mock = subprocess.Popen(
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
            ready = wait_for_ready(mock, ready_path)
            require(ready["host"] == "127.0.0.1", "mock is not loopback-only")
            invocation = subprocess.run(
                [
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
                    str(result_path),
                ],
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
            require(
                invocation.returncode == 0,
                "PowerShell scenario failed: "
                + invocation.stdout
                + invocation.stderr,
            )
            require(result_path.exists(), "PowerShell emitted no result")
            result_text = result_path.read_text(encoding="utf-8")
            require(
                config["log_token"] not in result_text
                and config["log_token"] not in invocation.stdout
                and config["log_token"] not in invocation.stderr,
                "log token leaked outside the request",
            )
            result = json.loads(result_text)

            deadline = time.monotonic() + 2.0
            requests = read_jsonl(log_path)
            while len(requests) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
                requests = read_jsonl(log_path)
            return result, requests, config
        finally:
            if mock.poll() is None:
                mock.terminate()
                try:
                    mock.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.wait(timeout=5)
            if mock.stdout is not None:
                mock.stdout.close()
            if mock.stderr is not None:
                mock.stderr.close()


def expected_body(
    identity_field: str,
    identity_value: str,
    event_type: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "query": {
            "bool": {
                "filter": [
                    {
                        "match_phrase": {
                            identity_field: identity_value,
                        }
                    },
                    {
                        "match_phrase": {
                            "event_type": event_type,
                        }
                    },
                    {
                        "range": {
                            "timestamp": {
                                "gte": str(config["start_time"]),
                                "lte": str(config["end_time"]),
                            }
                        }
                    },
                ]
            }
        },
        "size": 25,
        "sort": [{"timestamp": {"order": "asc"}}],
        "trackTotalHits": False,
    }


def verify_result(result: dict[str, Any], config: dict[str, Any]) -> None:
    require(
        list(result.keys())
        == [
            "Status",
            "RequestId",
            "CorrelationId",
            "Cause",
            "Component",
            "Evidence",
        ],
        "result property order or member set changed",
    )
    require(
        result["Status"] == "Diagnosed"
        and result["RequestId"] == config["request_id"]
        and result["CorrelationId"] == config["correlation_id"]
        and result["Cause"] == "CertificateExpired"
        and result["Component"] == config["component"],
        "diagnosis is not tied to the returned evidence",
    )
    evidence = result["Evidence"]
    require(len(evidence) == 2, "result must contain both evidence records")
    expected = [
        {
            "EventType": "DEPLOYMENT_FAILED",
            "Timestamp": config["failure_timestamp"],
            "Message": config["failure_message"],
        },
        {
            "EventType": "CERTIFICATE_VALIDATION_FAILED",
            "Timestamp": config["cause_timestamp"],
            "Message": config["cause_message"],
        },
    ]
    for index, item in enumerate(evidence):
        require(
            list(item.keys()) == ["EventType", "Timestamp", "Message"],
            f"evidence {index + 1} property order or member set changed",
        )
        require(
            item == expected[index],
            f"evidence {index + 1} was not preserved accurately",
        )
    require(
        config["log_token"] not in json.dumps(result, separators=(",", ":")),
        "result leaked the log token",
    )


def verify_wire(
    requests: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    require(len(requests) == 2, "workflow sent an extra or missing request")
    expected_bodies = [
        expected_body(
            "request_id",
            config["request_id"],
            "DEPLOYMENT_FAILED",
            config,
        ),
        expected_body(
            "correlation_id",
            config["correlation_id"],
            "CERTIFICATE_VALIDATION_FAILED",
            config,
        ),
    ]

    for index, request in enumerate(requests):
        require(
            request["operationId"] == OPERATION_ID,
            f"request {index + 1} was not contract-routed",
        )
        require(
            request["method"] == METHOD,
            f"request {index + 1} method changed",
        )
        require(
            request["raw_target"] == ROUTE
            and request["path"] == ROUTE
            and request["query"] == "",
            f"request {index + 1} target is not exact",
        )
        expected_raw = json.dumps(
            expected_bodies[index],
            separators=(",", ":"),
        )
        require(
            request["body_raw"] == expected_raw
            and request["body_bytes"] == len(expected_raw.encode("utf-8")),
            f"request {index + 1} compact body bytes or order changed",
        )
        require(
            request["body"] == expected_bodies[index],
            f"request {index + 1} JSON value changed",
        )
        require(
            request["response_status"] == 200
            and request["sequence"] == index,
            f"request {index + 1} fixture sequence changed",
        )
        headers = request["headers"]
        require(
            headers.get("x-jwt-token") == [config["log_token"]],
            f"request {index + 1} must send exactly one log token",
        )
        require(
            headers.get("accept") == ["application/json"],
            f"request {index + 1} Accept header changed",
        )
        require(
            headers.get("content-type") == ["application/json"],
            f"request {index + 1} Content-Type header changed",
        )

        body = request["body"]
        require(
            list(body.keys())
            == ["query", "size", "sort", "trackTotalHits"],
            f"request {index + 1} top-level property order changed",
        )
        require(
            UNSET_QUERY_REQUEST.isdisjoint(body),
            f"request {index + 1} sent an unset QueryRequest field",
        )
        require(
            body["trackTotalHits"] is False,
            f"request {index + 1} omitted or changed explicit false",
        )
        filters = body["query"]["bool"]["filter"]
        require(
            list(body["query"].keys()) == ["bool"]
            and list(body["query"]["bool"].keys()) == ["filter"]
            and [list(item.keys()) for item in filters]
            == [["match_phrase"], ["match_phrase"], ["range"]],
            f"request {index + 1} query shape or filter order changed",
        )
        require(
            list(filters[1]["match_phrase"].keys()) == ["event_type"]
            and list(filters[2]["range"].keys()) == ["timestamp"]
            and list(filters[2]["range"]["timestamp"].keys())
            == ["gte", "lte"],
            f"request {index + 1} nested property order changed",
        )

    require(
        list(
            requests[0]["body"]["query"]["bool"]["filter"][0][
                "match_phrase"
            ].keys()
        )
        == ["request_id"],
        "first query did not lead with request_id",
    )
    require(
        list(
            requests[1]["body"]["query"]["bool"]["filter"][0][
                "match_phrase"
            ].keys()
        )
        == ["correlation_id"],
        "second query did not lead with the response correlation_id",
    )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        result, requests, config = run_diagnosis_case()
        verify_result(result, config)
        verify_wire(requests, config)
    except (
        VerificationError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print("all protected checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
