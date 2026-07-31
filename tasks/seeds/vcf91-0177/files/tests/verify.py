#!/usr/bin/env python3
"""Deterministic verifier for the VCF log-forwarder integration task."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTRACT_SHA256 = "ed60bdfb33f293e9bdac1cf406f6ff97f6d3ae0e7f8182e0b4b3cd5ceaa147f7"
SOURCES_SHA256 = "4411b2519068e26a1e6eac0f02bd421f3b2a9b812ccb05bb2bf64c942bb6329a"
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_OPERATIONS = {
    "createLogForwarder": ("POST", "/api/v2/logs/forwarders"),
    "patchLogForwarder": ("PATCH", "/api/v2/logs/forwarders/{id}"),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_provenance() -> None:
    contract_path = ROOT / "docs" / "contract.json"
    sources_path = ROOT / "docs" / "official_sources.json"
    if file_sha256(contract_path) != CONTRACT_SHA256:
        fail("docs/contract.json was modified or is not the protected fixture")
    if file_sha256(sources_path) != SOURCES_SHA256:
        fail("docs/official_sources.json was modified or is not the protected fixture")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    if contract["derivedFrom"] != {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "commit": PINNED_COMMIT,
        "specPath": "specifications/vcf-operations/log-management-openapi.json",
        "license": "Apache-2.0",
    }:
        fail("contract provenance is not the pinned official specification")
    actual = {
        name: (value["method"], value["path"])
        for name, value in contract["operations"].items()
    }
    if actual != EXPECTED_OPERATIONS:
        fail("contract operationIds or routes changed")
    source_ops = {
        item["operationId"]: (item["method"], item["path"])
        for item in sources["operations"]
    }
    if source_ops != EXPECTED_OPERATIONS:
        fail("official_sources.json does not record every exact operationId")
    if sources["commit"] != PINNED_COMMIT:
        fail("official_sources.json commit pin changed")


def assert_stdlib_only() -> None:
    allowed_roots = set(sys.stdlib_module_names)
    allowed_roots.add("vcf_log_forwarder")
    for path in sorted((ROOT / "vcf_log_forwarder").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root not in allowed_roots:
                        fail(f"non-stdlib import {alias.name!r} in {path.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                module = node.module or ""
                root = module.split(".", 1)[0]
                if root not in allowed_roots:
                    fail(f"non-stdlib import {module!r} in {path.name}")


def expect_raises(
    exception: type[BaseException], function: Callable[..., Any], *args: Any
) -> None:
    try:
        function(*args)
    except exception:
        return
    except Exception as caught:
        fail(
            f"expected {exception.__name__}, got {type(caught).__name__}: {caught}"
        )
    fail(f"expected {exception.__name__}")


def assert_model_and_validation() -> None:
    from vcf_log_forwarder import ForwarderSpec, VcfLogClient

    empty = ForwarderSpec()
    if empty.to_payload() != {}:
        fail("to_payload must omit every field whose value is None")
    if "id" in ForwarderSpec.__dataclass_fields__:
        fail("the read-only id field must not be part of ForwarderSpec")
    if any(
        getattr(empty, name) is not None
        for name in ForwarderSpec.__dataclass_fields__
    ):
        fail("every ForwarderSpec field must default to None")

    payload = ForwarderSpec(
        certificate="pem",
        connection_refresh_interval=0,
        constraints={},
        enabled=False,
        forward_complementary_fields=False,
        host="collector.local",
        name="siem",
        port=0,
        protocol="SYSLOG",
        ssl_enabled=False,
        tags={},
        transport_protocol="TCP",
        worker_count=0,
    ).to_payload()
    if payload != {
        "certificate": "pem",
        "connectionRefreshInterval": 0,
        "constraints": {},
        "enabled": False,
        "forwardComplementaryFields": False,
        "host": "collector.local",
        "name": "siem",
        "port": 0,
        "protocol": "SYSLOG",
        "sslEnabled": False,
        "tags": {},
        "transportProtocol": "TCP",
        "workerCount": 0,
    }:
        fail("to_payload field mapping or explicit falsy-value handling is wrong")
    for field in (
        "certificate",
        "host",
        "name",
        "protocol",
        "transport_protocol",
    ):
        expect_raises(ValueError, ForwarderSpec(**{field: ""}).to_payload)
    expect_raises(ValueError, VcfLogClient, "http://127.0.0.1:1", "")


def header_map(request: dict[str, Any]) -> dict[str, str]:
    return {name.lower(): value for name, value in request["headers"]}


def assert_request(
    request: dict[str, Any],
    *,
    method: str,
    path: str,
    body: dict[str, Any],
    token: str,
) -> None:
    if request["method"] != method:
        fail(f"expected method {method}, got {request['method']}")
    if request["rawPath"] != path or "?" in request["rawPath"]:
        fail(f"expected raw path without query {path!r}, got {request['rawPath']!r}")
    try:
        decoded_body = json.loads(request["body"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"request body is not UTF-8 JSON: {error}")
    if decoded_body != body:
        fail(f"wire body mismatch: expected {body!r}, got {decoded_body!r}")
    compact_body = json.dumps(
        decoded_body, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if request["body"] != compact_body:
        fail("request body must be compact UTF-8 JSON")
    headers = header_map(request)
    expected = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-jwt-token": token,
        "content-length": str(len(request["body"])),
    }
    for name, value in expected.items():
        if headers.get(name) != value:
            fail(f"expected header {name}: {value!r}, got {headers.get(name)!r}")
    if "authorization" in headers:
        fail("Authorization header must not be sent")


def assert_rollout_and_wire() -> None:
    from mock.contract_server import ContractMock
    from vcf_log_forwarder import (
        ForwarderSpec,
        VcfLogClient,
        rollout_forwarder_pair,
    )

    primary = ForwarderSpec(
        name="primary-siem",
        host="collector-a.local",
        port=6514,
        protocol="SYSLOG",
        transport_protocol="TCP",
        enabled=False,
        ssl_enabled=True,
    )
    secondary = ForwarderSpec(
        name="secondary-siem",
        host="collector-b.local",
        port=6514,
        protocol="SYSLOG",
        transport_protocol="TCP",
        enabled=False,
        ssl_enabled=True,
    )
    token = "fixture-jwt-token"

    with ContractMock() as service, service.route_client_requests():
        client = VcfLogClient(service.base_url + "/", token, timeout=2)
        report = rollout_forwarder_pair(client, primary, secondary)
        requests = list(service.requests)

    expected_report = {
        "outcome": "partial_failure",
        "steps": [
            {
                "operationId": "createLogForwarder",
                "target": "primary-siem",
                "status": "succeeded",
                "httpStatus": 201,
                "resourceId": "fw-001",
            },
            {
                "operationId": "patchLogForwarder",
                "target": "primary-siem",
                "status": "succeeded",
                "httpStatus": 200,
                "resourceId": "fw-001",
            },
            {
                "operationId": "createLogForwarder",
                "target": "secondary-siem",
                "status": "failed",
                "httpStatus": 502,
                "error": {
                    "errorCode": "SSL_ERROR",
                    "errorDetails": {"destination": "secondary-siem"},
                    "errorMessage": "certificate is not trusted",
                },
            },
        ],
    }
    if report != expected_report:
        fail(
            "partial-failure report mismatch:\n"
            + json.dumps(report, indent=2, sort_keys=True)
        )
    if len(requests) != 3:
        fail(f"rollout must stop after the failed third request, got {len(requests)}")

    optional_names = {
        "certificate",
        "connectionRefreshInterval",
        "constraints",
        "forwardComplementaryFields",
        "id",
        "tags",
        "workerCount",
    }
    bodies = [
        {
            "name": "primary-siem",
            "host": "collector-a.local",
            "port": 6514,
            "protocol": "SYSLOG",
            "transportProtocol": "TCP",
            "enabled": False,
            "sslEnabled": True,
        },
        {"enabled": True},
        {
            "name": "secondary-siem",
            "host": "collector-b.local",
            "port": 6514,
            "protocol": "SYSLOG",
            "transportProtocol": "TCP",
            "enabled": False,
            "sslEnabled": True,
        },
    ]
    for decoded in (bodies[0], bodies[2]):
        leaked = optional_names.intersection(decoded)
        if leaked:
            fail(f"unset optional request fields were emitted: {sorted(leaked)}")

    assert_request(
        requests[0],
        method="POST",
        path="/api/v2/logs/forwarders",
        body=bodies[0],
        token=token,
    )
    assert_request(
        requests[1],
        method="PATCH",
        path="/api/v2/logs/forwarders/fw-001",
        body=bodies[1],
        token=token,
    )
    assert_request(
        requests[2],
        method="POST",
        path="/api/v2/logs/forwarders",
        body=bodies[2],
        token=token,
    )


def assert_rollout_outcomes_and_stopping() -> None:
    from vcf_log_forwarder import ApiResponse, ForwarderSpec, rollout_forwarder_pair

    primary = ForwarderSpec(name="primary")
    secondary = ForwarderSpec(name="secondary")
    operations = (
        ("createLogForwarder", "primary-id"),
        ("patchLogForwarder", "primary-id"),
        ("createLogForwarder", "secondary-id"),
        ("patchLogForwarder", "secondary-id"),
    )
    expected_calls = (
        ("create", "primary"),
        ("patch", "primary-id", {"enabled": True}),
        ("create", "secondary"),
        ("patch", "secondary-id", {"enabled": True}),
    )

    class ScriptedClient:
        def __init__(self, responses: list[ApiResponse]):
            self.responses = responses
            self.calls: list[tuple[Any, ...]] = []

        def create_log_forwarder(self, spec: ForwarderSpec) -> ApiResponse:
            self.calls.append(("create", spec.name))
            return self.responses.pop(0)

        def patch_log_forwarder(
            self, forwarder_id: str, changes: dict[str, Any]
        ) -> ApiResponse:
            self.calls.append(("patch", forwarder_id, changes))
            return self.responses.pop(0)

    def responses(failure_at: int | None) -> list[ApiResponse]:
        scripted: list[ApiResponse] = []
        for index, (operation_id, resource_id) in enumerate(operations):
            if index == failure_at:
                scripted.append(ApiResponse(operation_id, 502, {"at": index}))
                break
            scripted.append(ApiResponse(operation_id, 200, {"id": resource_id}))
        return scripted

    for failure_at in range(4):
        client = ScriptedClient(responses(failure_at))
        report = rollout_forwarder_pair(client, primary, secondary)
        expected_outcome = "failed" if failure_at == 0 else "partial_failure"
        if report["outcome"] != expected_outcome:
            fail(f"wrong rollout outcome when step {failure_at + 1} fails")
        if len(report["steps"]) != failure_at + 1:
            fail(f"rollout did not stop when step {failure_at + 1} failed")
        if client.calls != list(expected_calls[: failure_at + 1]):
            fail(f"wrong calls through failing rollout step {failure_at + 1}")
        if client.responses:
            fail(f"rollout stopped before consuming the failing step {failure_at + 1}")
        if report["steps"][-1]["status"] != "failed":
            fail(f"failing rollout step {failure_at + 1} was not reported")

    client = ScriptedClient(responses(None))
    report = rollout_forwarder_pair(client, primary, secondary)
    if report["outcome"] != "succeeded" or len(report["steps"]) != 4:
        fail("four successful steps must produce a succeeded rollout")
    if client.calls != list(expected_calls) or client.responses:
        fail("successful rollout did not perform exactly the four ordered changes")
    if any(step["status"] != "succeeded" for step in report["steps"]):
        fail("successful rollout contains a non-success step")


def assert_client_rejections_and_contract_surface() -> None:
    from mock.contract_server import ContractMock
    from vcf_log_forwarder import ForwarderSpec, VcfLogClient

    with ContractMock() as service, service.route_client_requests():
        client = VcfLogClient(service.base_url, "token", timeout=2)
        expect_raises(ValueError, client.patch_log_forwarder, "", {"enabled": True})
        for changes in (
            {"unknown": True},
            {"id": "replacement-id"},
            {"ssl_enabled": True},
        ):
            expect_raises(
                ValueError, client.patch_log_forwarder, "fw-001", changes
            )
        expect_raises(
            ValueError, client.patch_log_forwarder, "fw-001", {"enabled": None}
        )
        if service.requests:
            fail("locally rejected requests must not reach the mock")

        allowed = {
            ("POST", "/api/v2/logs/forwarders"),
            ("PATCH", "/api/v2/logs/forwarders/test"),
        }
        for method in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            for path in (
                "/api/v2/logs/forwarders",
                "/api/v2/logs/forwarders/test",
            ):
                if (method, path) in allowed:
                    continue
                request = Request(service.base_url + path, method=method)
                try:
                    service.open_url(request, timeout=2)
                except HTTPError as error:
                    if error.code != 404:
                        fail(
                            f"non-contract route returned {error.code}, expected 404"
                        )
                else:
                    fail(
                        f"mock unexpectedly served non-contract operation "
                        f"{method} {path}"
                    )


def main() -> None:
    assert_provenance()
    assert_stdlib_only()
    assert_model_and_validation()
    assert_rollout_and_wire()
    assert_rollout_outcomes_and_stopping()
    assert_client_rejections_and_contract_surface()
    print("PASS: VCF Operations log-forwarder contract integration verified")


if __name__ == "__main__":
    main()
