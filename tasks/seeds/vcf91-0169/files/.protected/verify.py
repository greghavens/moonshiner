#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0169."""

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
MANIFEST_PATH = ROOT / "VcfOpsLogRouting" / "VcfOpsLogRouting.psd1"
MODULE_PATH = ROOT / "VcfOpsLogRouting" / "VcfOpsLogRouting.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = [
    "patchUpdateAgentGroupConfig",
    "patchLogForwarder",
    "testLogForwarderConnection",
]
ROUTES = [
    ("PATCH", "/api/v2/agent/groups/{id}"),
    ("PATCH", "/api/v2/logs/forwarders/{id}"),
    ("POST", "/api/v2/logs/forwarders/test"),
]
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"


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
        [(item["method"], item["path"]) for item in operations] == ROUTES,
        "focused routes changed",
    )
    require(
        all(
            item["security"] == ["OPSTokenAuthorization"]
            and item["requestBody"]["required"] is True
            and item["requestBody"]["contentType"] == "application/json"
            for item in operations
        ),
        "operation security or request content projection changed",
    )
    require(
        operations[0]["requestBody"]["schema"] == "AgentGroupPatchRequest"
        and operations[0]["responses"]["200"]["schema"]
        == "AgentGroupResponse",
        "agent group patch projection changed",
    )
    require(
        operations[1]["requestBody"]["schema"] == "LogForwarder"
        and operations[1]["responses"]["200"]["schema"] == "LogForwarder",
        "forwarder patch projection changed",
    )
    require(
        operations[2]["requestBody"]["schema"] == "LogForwarder"
        and operations[2]["responses"]["200"]["schema"] is None
        and operations[2]["responses"]["502"]["schema"] == "ErrorBody",
        "forwarder test projection changed",
    )
    require(
        operations[0]["parameters"]
        == [
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
        and operations[1]["parameters"]
        == [
            {
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        "path parameter projection changed",
    )

    schemas = contract["schemas"]
    require(
        list(schemas["AgentGroupPatchRequest"]["properties"].keys())
        == [
            "agentConfig",
            "autoUpdate",
            "constraints",
            "id",
            "info",
            "mpId",
            "name",
        ]
        and schemas["AgentGroupPatchRequest"]["required"] == [],
        "AgentGroupPatchRequest projection changed",
    )
    log_properties = [
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
    require(
        list(schemas["LogForwarder"]["properties"].keys()) == log_properties
        and schemas["LogForwarder"]["required"] == []
        and schemas["LogForwarder"]["properties"]["protocol"]["enum"]
        == ["SYSLOG", "RAW", "RAWPLUS"]
        and schemas["LogForwarder"]["properties"]["transportProtocol"]["enum"]
        == ["TCP", "UDP"],
        "LogForwarder projection changed",
    )
    require(
        list(schemas["ErrorBody"]["properties"].keys())
        == ["errorCode", "errorDetails", "errorMessage"]
        and "SSL_ERROR"
        in schemas["ErrorBody"]["properties"]["errorCode"]["enumIncludes"],
        "ErrorBody projection changed",
    )

    workflow = contract["focusedWorkflow"]
    require(
        workflow["operationOrder"] == OPERATION_IDS,
        "focused workflow order changed",
    )
    bodies = workflow["requestBodies"]
    require(
        bodies["patchUpdateAgentGroupConfig"]["propertyOrder"]
        == ["autoUpdate"]
        and bodies["patchUpdateAgentGroupConfig"]["unsetProperties"]
        == ["agentConfig", "constraints", "id", "info", "mpId", "name"],
        "agent-group focused body changed",
    )
    require(
        bodies["patchLogForwarder"]["propertyOrder"] == ["enabled"]
        and bodies["patchLogForwarder"]["unsetBehavior"] == "omit",
        "forwarder-patch focused body changed",
    )
    require(
        bodies["testLogForwarderConnection"]["propertyOrder"]
        == [
            "host",
            "port",
            "protocol",
            "sslEnabled",
            "transportProtocol",
        ]
        and bodies["testLogForwarderConnection"]["unsetProperties"]
        == [
            "certificate",
            "connectionRefreshInterval",
            "constraints",
            "enabled",
            "forwardComplementaryFields",
            "id",
            "name",
            "tags",
            "workerCount",
        ]
        and bodies["testLogForwarderConnection"]["unsetBehavior"] == "omit",
        "forwarder-test focused body or omission semantics changed",
    )
    require(
        "Do not compensate" in workflow["failureRule"]
        and "HTTP 502 ErrorBody" in workflow["scenario"]
        and "come from the pinned OpenAPI specification"
        in workflow["specificationBoundary"],
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
                "operationId": "patchUpdateAgentGroupConfig",
                "method": "PATCH",
                "path": "/api/v2/agent/groups/{id}",
                "specLine": 248,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
            {
                "operationId": "patchLogForwarder",
                "method": "PATCH",
                "path": "/api/v2/logs/forwarders/{id}",
                "specLine": 1029,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
            {
                "operationId": "testLogForwarderConnection",
                "method": "POST",
                "path": "/api/v2/logs/forwarders/test",
                "specLine": 884,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
        ],
        "each operation must carry its exact pinned source",
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
        + "if ($d.RootModule -cne 'VcfOpsLogRouting.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne "
        + "'Invoke-VcfOpsLogRoutingChange') { exit 5 }; "
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
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.net.sockets",
        "tcpclient",
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


def run_partial_failure_case() -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
]:
    first_flag = bool(secrets.randbits(1))
    config = {
        "log_token": "log-token-" + secrets.token_urlsafe(24),
        "agent_group_id": "agent-" + secrets.token_hex(8),
        "agent_auto_update": first_flag,
        "forwarder_id": "forwarder-" + secrets.token_hex(8),
        "forwarder_enabled": not first_flag,
        "test_host": "relay-" + secrets.token_hex(6) + ".corp.example",
        "test_port": 1024 + secrets.randbelow(64511),
        "test_protocol": secrets.choice(["SYSLOG", "RAW", "RAWPLUS"]),
        "test_ssl_enabled": False,
        "test_transport_protocol": secrets.choice(["TCP", "UDP"]),
        "error_message": (
            "certificate trust failed, trace " + secrets.token_hex(10)
        ),
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0169-") as temp_name:
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
            while len(requests) < 3 and time.monotonic() < deadline:
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


def verify_result(result: dict[str, Any], config: dict[str, Any]) -> None:
    require(
        list(result.keys()) == ["Status", "Succeeded", "Steps"],
        "result property order or member set changed",
    )
    require(
        result["Status"] == "PartiallyApplied"
        and result["Succeeded"] is False,
        "later failure was not reported as partially applied",
    )
    steps = result["Steps"]
    require(len(steps) == 3, "result must retain all three attempted steps")
    expected_outcomes = ["Applied", "Applied", "Failed"]
    expected_statuses = [200, 200, 502]
    for index, step in enumerate(steps):
        require(
            list(step.keys())
            == [
                "OperationId",
                "Outcome",
                "StatusCode",
                "ErrorCode",
                "ErrorMessage",
            ],
            f"step {index + 1} property order or member set changed",
        )
        require(
            step["OperationId"] == OPERATION_IDS[index]
            and step["Outcome"] == expected_outcomes[index]
            and step["StatusCode"] == expected_statuses[index],
            f"step {index + 1} outcome is inaccurate",
        )
    require(
        steps[0]["ErrorCode"] is None
        and steps[0]["ErrorMessage"] is None
        and steps[1]["ErrorCode"] is None
        and steps[1]["ErrorMessage"] is None,
        "successful earlier steps must have null error fields",
    )
    require(
        steps[2]["ErrorCode"] == "SSL_ERROR"
        and steps[2]["ErrorMessage"] == config["error_message"],
        "later ErrorBody was not decoded accurately",
    )
    require(
        config["log_token"] not in json.dumps(result, separators=(",", ":")),
        "result leaked the log token",
    )


def verify_wire(
    requests: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    require(len(requests) == 3, "workflow sent an extra or missing request")
    expected_targets = [
        f"/api/v2/agent/groups/{config['agent_group_id']}",
        f"/api/v2/logs/forwarders/{config['forwarder_id']}",
        "/api/v2/logs/forwarders/test",
    ]
    expected_methods = ["PATCH", "PATCH", "POST"]
    expected_bodies = [
        json.dumps(
            {"autoUpdate": config["agent_auto_update"]},
            separators=(",", ":"),
        ),
        json.dumps(
            {"enabled": config["forwarder_enabled"]},
            separators=(",", ":"),
        ),
        json.dumps(
            {
                "host": config["test_host"],
                "port": config["test_port"],
                "protocol": config["test_protocol"],
                "sslEnabled": config["test_ssl_enabled"],
                "transportProtocol": config["test_transport_protocol"],
            },
            separators=(",", ":"),
        ),
    ]
    expected_statuses = [200, 200, 502]
    expected_effects = [True, True, False]

    for index, request in enumerate(requests):
        require(
            request["operationId"] == OPERATION_IDS[index],
            f"request {index + 1} was not contract-routed",
        )
        require(
            request["method"] == expected_methods[index],
            f"request {index + 1} method changed",
        )
        require(
            request["raw_target"] == expected_targets[index]
            and request["path"] == expected_targets[index]
            and request["query"] == "",
            f"request {index + 1} target is not exact",
        )
        require(
            request["body_raw"] == expected_bodies[index]
            and request["body_bytes"]
            == len(expected_bodies[index].encode("utf-8")),
            f"request {index + 1} compact body bytes or property order changed",
        )
        require(
            request["response_status"] == expected_statuses[index]
            and request["effect_committed"] is expected_effects[index],
            f"request {index + 1} fixture effect changed",
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

    require(
        list(requests[0]["body"].keys()) == ["autoUpdate"]
        and requests[0]["body"]["autoUpdate"]
        is config["agent_auto_update"],
        "agent-group patch synthesized an unset optional field",
    )
    require(
        list(requests[1]["body"].keys()) == ["enabled"]
        and requests[1]["body"]["enabled"] is config["forwarder_enabled"],
        "forwarder patch synthesized an unset optional field",
    )
    test_body = requests[2]["body"]
    require(
        list(test_body.keys())
        == [
            "host",
            "port",
            "protocol",
            "sslEnabled",
            "transportProtocol",
        ],
        "forwarder test member set or order changed",
    )
    unset_test = {
        "certificate",
        "connectionRefreshInterval",
        "constraints",
        "enabled",
        "forwardComplementaryFields",
        "id",
        "name",
        "tags",
        "workerCount",
    }
    require(
        unset_test.isdisjoint(test_body),
        "forwarder test sent an unset optional field",
    )
    require(
        test_body["sslEnabled"] is False,
        "explicit false sslEnabled was omitted or changed",
    )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        result, requests, config = run_partial_failure_case()
        verify_result(result, config)
        verify_wire(requests, config)
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print("all protected checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
