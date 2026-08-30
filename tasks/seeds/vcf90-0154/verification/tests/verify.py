#!/usr/bin/env python3
"""Protected deterministic verification for vcf90-0154."""

from __future__ import annotations

import ast
import hashlib
from http.client import RemoteDisconnected
import json
from pathlib import Path
import sys
import tomllib
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from test_support.mock_vcf_automation import ContractMockServer


CONTRACT_SHA256 = "42e5254c3cb6ba7ad1a6cdb6d59d484db80f73d4d2f1a08719d7283a807b14f7"
SOURCES_SHA256 = "17f153e1ade4295cfcf3b9741e6c119c7bfd14bc5b8949453b3d650a318c2ea6"
SOURCE_URL = (
    "https://developer.broadcom.com/xapis/vm-apps-org-deployment/9.0/"
    "deployment/api/deployments/get/"
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_failure(callback: object, message: str) -> None:
    try:
        callback()  # type: ignore[operator]
    except Exception:
        return
    raise AssertionError(message)


def verify_contract() -> None:
    contract_bytes = (ROOT / "docs" / "contract.json").read_bytes()
    sources_bytes = (ROOT / "docs" / "official_sources.json").read_bytes()
    check(
        hashlib.sha256(contract_bytes).hexdigest() == CONTRACT_SHA256,
        "focused reference-derived contract is not exact",
    )
    check(
        hashlib.sha256(sources_bytes).hexdigest() == SOURCES_SHA256,
        "official source provenance is not exact",
    )

    contract = json.loads(contract_bytes)
    sources = json.loads(sources_bytes)
    check(
        contract["source"]["publishedSpecification"] is False,
        "contract must not claim to be a published specification",
    )
    check(
        "rather than a published specification" in contract["source"]["statement"],
        "contract must plainly identify its reference-documentation basis",
    )
    check(
        contract["source"]["pageUrls"] == [SOURCE_URL],
        "contract source URL set is not exact",
    )
    check(
        [(item["name"], item["method"], item["path"]) for item in contract["operations"]]
        == [("Get Deployments 1", "GET", "/deployment/api/deployments")],
        "focused contract operation set changed",
    )
    check(
        sources["pages"]
        == [
            {
                "url": SOURCE_URL,
                "operation": "Get Deployments 1",
                "method": "GET",
                "path": "/deployment/api/deployments",
                "fetchedOn": "2026-08-13",
            }
        ],
        "every official page must record its operation and fetch date",
    )


def verify_stdlib_only() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    check(project["project"].get("dependencies") == [], "runtime dependencies must be empty")
    allowed = set(sys.stdlib_module_names)
    for source in (ROOT / "src" / "vcf_automation").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level:
                roots = {"vcf_automation"}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            check(
                roots <= allowed | {"vcf_automation"},
                f"non-stdlib import in {source.name}: {roots}",
            )


def expected_order(items: list[dict[str, object]]) -> list[tuple[str, str]]:
    return [(str(item["name"]), str(item["id"])) for item in items]


def verify_complete_stable_collection_and_wire() -> None:
    from vcf_automation import VCFAutomationClient

    with ContractMockServer(ROOT / "docs" / "contract.json") as server:
        check(server.base_url.startswith("http://127.0.0.1:"), "mock is not loopback-only")
        client = VCFAutomationClient(
            server.base_url,
            "wire-token",
            page_size=2,
            timeout=2,
        )
        deployments = client.list_deployments()
        log = list(server.request_log)

    check(
        expected_order(deployments)
        == [
            ("alpha", "a-1"),
            ("alpha", "a-2"),
            ("bravo", "b-2"),
            ("charlie", "c-3"),
            ("delta", "d-4"),
        ],
        "all deployments were not emitted in stable name-and-id order",
    )
    expected_targets = [
        "/deployment/api/deployments?page=0&size=2&sort=name%2CASC",
        "/deployment/api/deployments?page=1&size=2&sort=name%2CASC",
        "/deployment/api/deployments?page=2&size=2&sort=name%2CASC",
    ]
    check(len(log) == 3, "the complete three-page collection was not requested exactly")
    check(
        [(entry["method"], entry["target"]) for entry in log]
        == [("GET", target) for target in expected_targets],
        "pagination request targets are not exact",
    )
    for entry in log:
        headers = entry["headers"]
        check(entry["body"] == b"", "Get Deployments must not send a request body")
        check(headers.get("accept") == "application/json", "Accept header is not exact")
        check(
            headers.get("authorization") == "Bearer wire-token",
            "Authorization header is not exact",
        )
        check("content-type" not in headers, "GET must not send Content-Type")
        check("content-length" not in headers, "GET must not send Content-Length")


def verify_optional_filters_and_omission() -> None:
    from vcf_automation import VCFAutomationClient

    cases = [
        (
            {"project_id": "proj A/B", "status": "CREATE_SUCCESSFUL"},
            [("alpha", "a-1"), ("alpha", "a-2"), ("bravo", "b-2")],
            [
                "/deployment/api/deployments?page=0&size=2&sort=name%2CASC&projects=proj+A%2FB&status=CREATE_SUCCESSFUL",
                "/deployment/api/deployments?page=1&size=2&sort=name%2CASC&projects=proj+A%2FB&status=CREATE_SUCCESSFUL",
            ],
        ),
        (
            {"project_id": "proj A/B"},
            [("alpha", "a-1"), ("alpha", "a-2"), ("bravo", "b-2")],
            [
                "/deployment/api/deployments?page=0&size=2&sort=name%2CASC&projects=proj+A%2FB",
                "/deployment/api/deployments?page=1&size=2&sort=name%2CASC&projects=proj+A%2FB",
            ],
        ),
        (
            {"status": "CREATE_SUCCESSFUL"},
            [
                ("alpha", "a-1"),
                ("alpha", "a-2"),
                ("bravo", "b-2"),
                ("delta", "d-4"),
            ],
            [
                "/deployment/api/deployments?page=0&size=2&sort=name%2CASC&status=CREATE_SUCCESSFUL",
                "/deployment/api/deployments?page=1&size=2&sort=name%2CASC&status=CREATE_SUCCESSFUL",
            ],
        ),
    ]
    for filters, expected, expected_targets in cases:
        with ContractMockServer(ROOT / "docs" / "contract.json") as server:
            client = VCFAutomationClient(
                server.base_url,
                "filter-token",
                page_size=2,
                timeout=2,
            )
            deployments = client.list_deployments(**filters)
            log = list(server.request_log)

        check(
            expected_order(deployments) == expected,
            "filtered pages were not collected and ordered",
        )
        check(
            [entry["target"] for entry in log] == expected_targets,
            "optional filters or their wire encoding are not exact",
        )
        for entry in log:
            check(entry["body"] == b"", "filtered GET must not send a request body")


def verify_mock_operation_boundary() -> None:
    with ContractMockServer(ROOT / "docs" / "contract.json") as server:
        requests = [
            Request(server.base_url + "/deployment/api/resources", method="GET"),
            Request(server.base_url + "/deployment/api/deployments", data=b"", method="POST"),
        ]
        for request in requests:
            try:
                urlopen(request, timeout=2)
            except HTTPError as error:
                check(error.code == 404, "unnamed mock operation must return 404")
            except RemoteDisconnected as error:
                raise AssertionError("unnamed mock operation closed the connection") from error
            else:
                raise AssertionError("mock served an operation outside the contract")


def verify_response_handling() -> None:
    from vcf_automation import VCFAutomationClient

    with ContractMockServer(
        ROOT / "docs" / "contract.json",
        response_status=201,
    ) as server:
        client = VCFAutomationClient(server.base_url, "token", page_size=2, timeout=2)
        expect_failure(
            client.list_deployments,
            "HTTP 201 must not be accepted as Get Deployments success",
        )
        check(len(server.request_log) == 1, "non-200 response must stop pagination")

    malformed_pages: list[object] = [
        [],
        {"number": 0, "last": True},
        {"content": [], "last": True},
        {"content": [], "number": 0},
        {"content": [], "number": 1, "last": True},
        {"content": {}, "number": 0, "last": True},
        {"content": [], "number": 0, "last": "true"},
        {"content": [None], "number": 0, "last": True},
        {"content": [{"name": 1, "id": "a-1"}], "number": 0, "last": True},
        {"content": [{"name": "alpha", "id": 1}], "number": 0, "last": True},
    ]
    for payload in malformed_pages:
        with ContractMockServer(
            ROOT / "docs" / "contract.json",
            response_payload=payload,
        ) as server:
            client = VCFAutomationClient(server.base_url, "token", page_size=2, timeout=2)
            expect_failure(
                client.list_deployments,
                "malformed PageDeployment response must fail",
            )
            check(len(server.request_log) == 1, "malformed response must stop pagination")


def main() -> None:
    verify_contract()
    verify_stdlib_only()
    verify_complete_stable_collection_and_wire()
    verify_optional_filters_and_omission()
    verify_mock_operation_boundary()
    verify_response_handling()
    print("vcf90-0154 verification passed")


if __name__ == "__main__":
    main()
