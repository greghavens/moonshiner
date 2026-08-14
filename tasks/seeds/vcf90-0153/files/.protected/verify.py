#!/usr/bin/env python3
"""Protected verification for the focused VCF Automation client."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".protected"))

from mock_vcf import RecordedRequest, ResponseStep, run_mock  # noqa: E402


EXPECTED_KEYS = (
    "retrieveAuthToken",
    "requestCatalogItemInstances_1",
    "getDeploymentById_1",
)
EXPECTED_METHODS = ("POST", "POST", "GET")
EXPECTED_PATHS = (
    "/iaas/api/login",
    "/catalog/api/items/{id}/request",
    "/deployment/api/deployments/{deploymentId}",
)
EXPECTED_SOURCE_OPERATIONS = (
    "POST Retrieve Auth Token",
    "POST Request Catalog Item Instances 1",
    "GET Get Deployment By Id 1",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text())
    provenance = contract.get("provenance", {})
    require(provenance.get("kind") == "reference-documentation", "wrong source kind")
    statement = str(provenance.get("statement", "")).lower()
    require("rather than a published specification" in statement, "source statement missing")
    require("vcf-api-specs" in statement, "repository absence not stated")
    operations = contract.get("operations", [])
    require(tuple(item.get("key") for item in operations) == EXPECTED_KEYS, "wrong operations")
    require(tuple(item.get("method") for item in operations) == EXPECTED_METHODS, "wrong methods")
    require(tuple(item.get("path") for item in operations) == EXPECTED_PATHS, "wrong paths")

    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())
    require(sources.get("source_kind") == "reference-documentation", "wrong source manifest kind")
    require(sources.get("fetched_at") == "2026-08-13", "source fetch date missing")
    pages = sources.get("sources", [])
    require(len(pages) == 3, "every operation must have one source page")
    require(tuple(item.get("operation") for item in pages) == EXPECTED_SOURCE_OPERATIONS, "source operations differ")
    for item in pages:
        url = item.get("url", "")
        require(url.startswith("https://developer.broadcom.com/xapis/"), "source is not Broadcom xAPIs")
        require("/9.0/" in url, "source URL is not pinned to 9.0")
        require(item.get("fetched_at") == "2026-08-13", "source page fetch date missing")


def validate_stdlib_only() -> None:
    source_path = ROOT / "vcf_automation" / "client.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    require(imported <= allowed, f"non-stdlib imports: {sorted(imported - allowed)}")


def header_values(request: RecordedRequest) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for name, value in request.headers:
        values[name.lower()].append(value)
    return values


def assert_single(headers: dict[str, list[str]], name: str, value: str) -> None:
    require(headers.get(name.lower()) == [value], f"expected one {name}: {value}")


def verify_wire(requests: tuple[RecordedRequest, ...]) -> None:
    require(len(requests) == 6, f"expected 6 requests, got {len(requests)}")
    require(
        [item.method for item in requests] == ["POST", "POST", "GET", "POST", "GET", "GET"],
        "request methods/order differ",
    )
    require(
        [item.target for item in requests]
        == [
            "/iaas/api/login",
            "/catalog/api/items/catalog%2Fitem%209/request",
            "/deployment/api/deployments/90010000-0000-4000-8000-000000000001",
            "/iaas/api/login",
            "/deployment/api/deployments/90010000-0000-4000-8000-000000000001",
            "/deployment/api/deployments/90010000-0000-4000-8000-000000000001",
        ],
        "raw request targets differ or optional queries were sent",
    )

    login_body = b'{"refreshToken":"fixture-refresh-token"}'
    catalog_body = (
        b'{"deploymentName":"edge-cache-01","inputs":{"cpu":2,"environment":"test"},'
        b'"projectId":"project-42"}'
    )
    require(requests[0].body == login_body and requests[3].body == login_body, "login JSON differs")
    require(requests[1].body == catalog_body, "catalog JSON differs")
    require(all(item.body == b"" for item in (requests[2], requests[4], requests[5])), "GET must be bodyless")

    optional_fields = (b"bulkRequestCount", b"reason", b"version", b"null")
    require(all(field not in requests[1].body for field in optional_fields), "unset catalog option was serialized")
    require(Counter(item.method for item in requests)["POST"] == 3, "catalog request was duplicated")
    require(sum("/catalog/api/items/" in item.target for item in requests) == 1, "catalog request was duplicated")

    for index, request in enumerate(requests):
        headers = header_values(request)
        assert_single(headers, "Accept", "application/json")
        if request.method == "POST":
            assert_single(headers, "Content-Type", "application/json")
            if "content-length" in headers:
                assert_single(headers, "Content-Length", str(len(request.body)))
        else:
            require("content-type" not in headers, "GET sent Content-Type")
            if "content-length" in headers:
                assert_single(headers, "Content-Length", "0")

    for index in (0, 3):
        require("authorization" not in header_values(requests[index]), "login sent bearer authorization")
    assert_single(header_values(requests[1]), "Authorization", "Bearer expiring-access")
    assert_single(header_values(requests[2]), "Authorization", "Bearer expiring-access")
    assert_single(header_values(requests[4]), "Authorization", "Bearer fresh-access")
    assert_single(header_values(requests[5]), "Authorization", "Bearer fresh-access")


def capture_error(action: object, expected_type: type[Exception]) -> Exception:
    require(callable(action), "test action is not callable")
    try:
        action()
    except Exception as error:
        require(
            type(error) is expected_type,
            f"expected {expected_type.__name__}, got {type(error).__name__}",
        )
        return error
    raise AssertionError(f"expected {expected_type.__name__}")


def login_step(token: str = "access-token") -> ResponseStep:
    return ResponseStep(
        "retrieveAuthToken",
        200,
        {"tokenType": "Bearer", "token": token},
    )


def catalog_step(
    deployment_id: str = "90010000-0000-4000-8000-000000000001",
    deployment_name: str = "edge-cache-01",
) -> ResponseStep:
    return ResponseStep(
        "requestCatalogItemInstances_1",
        200,
        [
            {
                "deploymentId": deployment_id,
                "deploymentName": deployment_name,
            }
        ],
    )


def default_request(CatalogRequest: type[object]) -> object:
    return CatalogRequest(
        deployment_name="edge-cache-01",
        inputs={"cpu": 2, "environment": "test"},
        project_id="project-42",
    )


def verify_primary_flow(
    CatalogRequest: type[object],
    DeploymentResult: type[object],
    VcfAutomationClient: type[object],
) -> None:
    request = default_request(CatalogRequest)
    with run_mock() as (origin, scenario):
        require(scenario.allowed_operation_keys == EXPECTED_KEYS, "mock allow-list is not contract-pinned")
        client = VcfAutomationClient(origin, "fixture-refresh-token", timeout=2.0)
        result = client.request_catalog_item("catalog/item 9", request)
        scenario.assert_consumed()
        requests = scenario.requests()

    require(
        result
        == DeploymentResult(
            deployment_id="90010000-0000-4000-8000-000000000001",
            deployment_name="edge-cache-01",
            project_id="project-42",
            status="CREATE_SUCCESSFUL",
        ),
        "terminal result differs",
    )
    verify_wire(requests)


def verify_explicit_options_and_encoding(
    CatalogRequest: type[object],
    DeploymentResult: type[object],
    VcfAutomationClient: type[object],
) -> None:
    script = (
        login_step("one-use-access"),
        catalog_step("deployment/with space?", "catalog-name"),
        ResponseStep(
            "getDeploymentById_1",
            200,
            {
                "name": "terminal-name",
                "status": "CREATE_SUCCESSFUL",
            },
        ),
    )
    request = CatalogRequest(
        deployment_name="café-東京",
        inputs={"enabled": False, "count": 0},
        project_id="project/blue",
        bulk_request_count=0,
        reason="",
        version="v 2",
    )
    with run_mock(script) as (origin, scenario):
        client = VcfAutomationClient(origin, "réfresh-token", timeout=2.0)
        result = client.request_catalog_item("item/東京 ?#", request)
        scenario.assert_consumed()
        requests = scenario.requests()

    require(
        result
        == DeploymentResult(
            deployment_id="deployment/with space?",
            deployment_name="terminal-name",
            project_id=None,
            status="CREATE_SUCCESSFUL",
        ),
        "explicit-option terminal result differs",
    )
    require(
        [item.target for item in requests]
        == [
            "/iaas/api/login",
            "/catalog/api/items/item%2F%E6%9D%B1%E4%BA%AC%20%3F%23/request",
            "/deployment/api/deployments/deployment%2Fwith%20space%3F",
        ],
        "identifier encoding or optional query omission differs",
    )
    require(
        requests[0].body == '{"refreshToken":"réfresh-token"}'.encode(),
        "UTF-8 login body differs",
    )
    require(
        requests[1].body
        == (
            '{"bulkRequestCount":0,"deploymentName":"café-東京",'
            '"inputs":{"enabled":false,"count":0},"projectId":"project/blue",'
            '"reason":"","version":"v 2"}'
        ).encode(),
        "explicit catalog values or property order differ",
    )
    require(requests[2].body == b"", "terminal GET must be bodyless")
    for request_item in requests:
        assert_single(header_values(request_item), "Accept", "application/json")
    require("authorization" not in header_values(requests[0]), "login sent authorization")
    assert_single(header_values(requests[1]), "Authorization", "Bearer one-use-access")
    assert_single(header_values(requests[2]), "Authorization", "Bearer one-use-access")
    require("content-type" not in header_values(requests[2]), "GET sent Content-Type")


def verify_api_errors(
    ApiError: type[Exception],
    CatalogRequest: type[object],
    VcfAutomationClient: type[object],
) -> None:
    cases = (
        (
            (ResponseStep("retrieveAuthToken", 403, {"message": "forbidden"}),),
            "retrieveAuthToken",
            403,
        ),
        (
            (
                login_step(),
                ResponseStep(
                    "requestCatalogItemInstances_1",
                    401,
                    {"message": "unauthorized"},
                ),
            ),
            "requestCatalogItemInstances_1",
            401,
        ),
        (
            (
                login_step(),
                catalog_step(),
                ResponseStep(
                    "getDeploymentById_1",
                    503,
                    {"message": "unavailable"},
                ),
            ),
            "getDeploymentById_1",
            503,
        ),
    )
    for script, operation, status in cases:
        with run_mock(script) as (origin, scenario):
            client = VcfAutomationClient(origin, "fixture-refresh-token", timeout=2.0)
            error = capture_error(
                lambda: client.request_catalog_item(
                    "catalog-item", default_request(CatalogRequest)
                ),
                ApiError,
            )
            scenario.assert_consumed()
        require(error.operation == operation, "ApiError operation differs")
        require(error.status == status, "ApiError status differs")


def verify_response_contract_errors(
    CatalogRequest: type[object],
    ResponseContractError: type[Exception],
    VcfAutomationClient: type[object],
) -> None:
    malformed_cases = (
        (
            ResponseStep("retrieveAuthToken", 200, b"not-json"),
        ),
        (
            ResponseStep(
                "retrieveAuthToken",
                200,
                {"tokenType": "Basic", "token": "access-token"},
            ),
        ),
        (
            ResponseStep(
                "retrieveAuthToken",
                200,
                {"tokenType": "Bearer", "token": " "},
            ),
        ),
        (
            login_step(),
            ResponseStep("requestCatalogItemInstances_1", 200, b"["),
        ),
        (
            login_step(),
            ResponseStep(
                "requestCatalogItemInstances_1",
                200,
                [
                    {"deploymentId": "one", "deploymentName": "one"},
                    {"deploymentId": "two", "deploymentName": "two"},
                ],
            ),
        ),
        (
            login_step(),
            ResponseStep(
                "requestCatalogItemInstances_1",
                200,
                [{"deploymentId": " ", "deploymentName": "name"}],
            ),
        ),
        (
            login_step(),
            ResponseStep(
                "requestCatalogItemInstances_1",
                200,
                [{"deploymentId": "deployment-id", "deploymentName": " "}],
            ),
        ),
        (
            login_step(),
            catalog_step(),
            ResponseStep(
                "getDeploymentById_1",
                200,
                {"status": "CREATE_SUCCESSFUL"},
            ),
        ),
        (
            login_step(),
            catalog_step(),
            ResponseStep(
                "getDeploymentById_1",
                200,
                {"name": "edge-cache-01", "status": "UPDATE_SUCCESSFUL"},
            ),
        ),
        (
            login_step(),
            catalog_step(),
            ResponseStep(
                "getDeploymentById_1",
                200,
                {
                    "id": "different-deployment",
                    "name": "edge-cache-01",
                    "status": "CREATE_SUCCESSFUL",
                },
            ),
        ),
        (
            login_step(),
            catalog_step(),
            ResponseStep(
                "getDeploymentById_1",
                200,
                {
                    "name": "edge-cache-01",
                    "projectId": 42,
                    "status": "CREATE_SUCCESSFUL",
                },
            ),
        ),
    )
    for script in malformed_cases:
        with run_mock(script) as (origin, scenario):
            client = VcfAutomationClient(origin, "fixture-refresh-token", timeout=2.0)
            capture_error(
                lambda: client.request_catalog_item(
                    "catalog-item", default_request(CatalogRequest)
                ),
                ResponseContractError,
            )
            scenario.assert_consumed()


def verify_deployment_failed(
    CatalogRequest: type[object],
    DeploymentFailed: type[Exception],
    VcfAutomationClient: type[object],
) -> None:
    deployment_id = "90010000-0000-4000-8000-000000000001"
    script = (
        login_step(),
        catalog_step(deployment_id),
        ResponseStep(
            "getDeploymentById_1",
            200,
            {
                "id": deployment_id,
                "name": "edge-cache-01",
                "status": "CREATE_FAILED",
            },
        ),
    )
    with run_mock(script) as (origin, scenario):
        client = VcfAutomationClient(origin, "fixture-refresh-token", timeout=2.0)
        error = capture_error(
            lambda: client.request_catalog_item(
                "catalog-item", default_request(CatalogRequest)
            ),
            DeploymentFailed,
        )
        scenario.assert_consumed()
    require(error.deployment_id == deployment_id, "DeploymentFailed id differs")


def main() -> int:
    validate_contract()
    validate_stdlib_only()

    from vcf_automation import (
        ApiError,
        CatalogRequest,
        DeploymentFailed,
        DeploymentResult,
        ResponseContractError,
        VcfAutomationClient,
    )

    verify_primary_flow(CatalogRequest, DeploymentResult, VcfAutomationClient)
    verify_explicit_options_and_encoding(
        CatalogRequest, DeploymentResult, VcfAutomationClient
    )
    verify_api_errors(ApiError, CatalogRequest, VcfAutomationClient)
    verify_response_contract_errors(
        CatalogRequest, ResponseContractError, VcfAutomationClient
    )
    verify_deployment_failed(CatalogRequest, DeploymentFailed, VcfAutomationClient)
    print("verified VCF Automation refresh, wire contract, and failure handling")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"verification failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
