"""Protected end-to-end acceptance verifier for the VCF 9.1 integration."""

from __future__ import annotations

import ast
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mock_nsx import PASSWORD, USERNAME, running_mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
sys.path.insert(0, str(ROOT))
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
OPERATIONS = [
    (
        "OrgsOrgIdProjectsProjectIdInfraUpdateTraceflowConfig",
        "PUT",
        "/orgs/{org-id}/projects/{project-id}/infra/traceflows/{traceflow-id}",
    ),
    (
        "OrgsOrgIdProjectsProjectIdInfraReadTraceflowStatus",
        "GET",
        "/orgs/{org-id}/projects/{project-id}/infra/traceflows/{traceflow-id}/status",
    ),
    (
        "OrgsOrgIdProjectsProjectIdInfraListTraceflowObservations",
        "GET",
        "/orgs/{org-id}/projects/{project-id}/infra/traceflows/{traceflow-id}/observations",
    ),
]


def check(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_provenance_and_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    check(contract["basePath"] == "/policy/api/v1", "wrong NSX Policy basePath")
    check(contract["security"] == {"type": "basic", "name": "BasicAuth"}, "wrong auth")
    check(contract["derived_from"]["commit_sha"] == COMMIT, "contract commit drift")
    check(contract["derived_from"]["spec_path"] == SPEC_PATH, "contract source drift")
    check(sources["commit_sha"] == COMMIT, "official source commit drift")
    check(sources["spec_path"] == SPEC_PATH, "official source path drift")
    check(sources["license"] == "Apache-2.0", "official source license missing")

    contract_ops = [
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    ]
    source_ops = [
        (item["operationId"], item["method"], item["path"])
        for item in sources["operations"]
    ]
    check(contract_ops == OPERATIONS, "contract operation subset drift")
    check(source_ops == OPERATIONS, "official source operation list drift")
    schemas = contract["schemas"]
    check(
        schemas["Traceflow"]["properties"]["operation_state"]["enum"]
        == ["IN_PROGRESS", "FINISHED", "FAILED"],
        "terminal state contract drift",
    )
    check(
        schemas["TraceflowObservationListResult"]["properties"]["results"]["type"]
        == "array",
        "observation collection contract drift",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_traceflow"
    check((package / "__init__.py").is_file(), "vcf_traceflow package is missing")
    python_files = sorted(package.rglob("*.py"))
    check(python_files, "vcf_traceflow contains no Python source")
    local_modules = {path.stem for path in python_files}
    local_modules.add("vcf_traceflow")
    stdlib = set(sys.stdlib_module_names)
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.split(".", 1)[0]}
            else:
                continue
            forbidden = roots - stdlib - local_modules
            check(not forbidden, f"non-stdlib import in {path.name}: {sorted(forbidden)}")


def sample_config() -> dict[str, object]:
    return {
        "source_id": "/orgs/acme/projects/payments/infra/segments/app/ports/source",
        "is_transient": True,
        "timeout": 10,
        "packet": {
            "resource_type": "FieldsPacketData",
            "frame_size": 128,
            "routed": True,
            "transport_type": "UNICAST",
            "eth_header": {
                "src_mac": "00:50:56:8f:49:60",
                "dst_mac": "00:50:56:8f:2f:97",
                "eth_type": 2048,
            },
            "ip_header": {
                "src_ip": "172.16.14.11",
                "dst_ip": "172.16.16.13",
                "protocol": 1,
                "ttl": 64,
            },
            "transport_header": {
                "icmp_echo_request_header": {"id": 7, "sequence": 11}
            },
        },
    }


def assert_sorted(observations: list[dict[str, object]]) -> None:
    keys = [
        (
            item["sequence_no"],
            item["timestamp_micro"],
            item["transport_node_id"],
        )
        for item in observations
    ]
    check(keys == sorted(keys), f"observations are not deterministically sorted: {keys}")
    check([key[0] for key in keys] == [0, 1, 1, 2], "observation elements changed")
    check(keys[1][1] < keys[2][1], "timestamp tie-break was not applied")


def verify_success_and_wire_contract(client_type) -> None:
    enforcement_path = "/infra/sites/default/enforcement-points/default"
    with running_mock(CONTRACT_PATH) as server:
        client = client_type(
            server.base_url + "/",
            USERNAME,
            PASSWORD,
            poll_interval=0,
            request_timeout=2,
        )
        reports = []
        for _ in range(2):
            reports.append(
                client.run_project_traceflow(
                    org_id="acme west",
                    project_id="payments/edge",
                    traceflow_id="checkout probe",
                    config=sample_config(),
                    enforcement_point_path=enforcement_path,
                    max_polls=5,
                )
            )
        for report in reports:
            check(report["traceflow_id"] == "checkout probe", "traceflow id missing")
            check(report["operation_state"] == "FINISHED", "terminal state missing")
            check(report["request_status"] == "SUCCESS", "request status missing")
            check(isinstance(report["observations"], list), "observations must be a list")
            assert_sorted(report["observations"])
        check(
            reports[0]["observations"] == reports[1]["observations"],
            "wire-order flips changed the returned collection",
        )

        log = server.request_log
        check(len(log) == 10, f"unexpected request count/order: {len(log)}")
        expected_ids = [
            OPERATIONS[0][0],
            OPERATIONS[1][0],
            OPERATIONS[1][0],
            OPERATIONS[1][0],
            OPERATIONS[2][0],
        ] * 2
        check([entry["operationId"] for entry in log] == expected_ids, "wrong call order")
        check(
            [entry["method"] for entry in log]
            == ["PUT", "GET", "GET", "GET", "GET"] * 2,
            "wrong HTTP methods",
        )
        for entry in log:
            path = str(entry["path"])
            check("/orgs/acme%20west/" in path, "org-id was not segment encoded")
            check("/projects/payments%2Fedge/" in path, "project-id was not segment encoded")
            check(path.endswith("checkout%20probe") or "checkout%20probe/" in path, "traceflow-id encoding")
            check(
                entry["query"] == {"enforcement_point_path": [enforcement_path]},
                "enforcement_point_path was not preserved",
            )
            check(entry["accept"] == "application/json", "missing JSON Accept header")
            check(entry["response_status"] == 200, f"mock rejected request: {entry}")
        for entry in log[::5]:
            check(entry["body"] == sample_config(), "PUT body changed from TraceflowConfig")
            check(
                str(entry["content_type"]).startswith("application/json"),
                "PUT content type is not JSON",
            )


def verify_http_error(client_type, error_type) -> None:
    with running_mock(CONTRACT_PATH) as server:
        client = client_type(
            server.base_url, USERNAME, "wrong-password", poll_interval=0
        )
        try:
            client.run_project_traceflow(
                org_id="acme",
                project_id="payments",
                traceflow_id="auth-check",
                config=sample_config(),
                max_polls=3,
            )
        except error_type as error:
            check(getattr(error, "status_code", None) == 401, "HTTP status not exposed")
        else:
            raise AssertionError("401 did not raise NsxPolicyError")
        check(len(server.request_log) == 1, "client continued after HTTP failure")


def verify_failed_terminal(client_type, failed_type) -> None:
    with running_mock(CONTRACT_PATH) as server:
        client = client_type(server.base_url, USERNAME, PASSWORD, poll_interval=0)
        try:
            client.run_project_traceflow(
                org_id="acme",
                project_id="payments",
                traceflow_id="will-fail",
                config=sample_config(),
                max_polls=5,
            )
        except failed_type:
            pass
        else:
            raise AssertionError("FAILED terminal state did not raise TraceflowFailed")
        ids = [entry["operationId"] for entry in server.request_log]
        check(ids == [OPERATIONS[0][0], OPERATIONS[1][0], OPERATIONS[1][0]], "failure polling")
        check(OPERATIONS[2][0] not in ids, "observations fetched after failed traceflow")


def verify_poll_exhaustion(client_type, timeout_type) -> None:
    with running_mock(CONTRACT_PATH) as server:
        client = client_type(server.base_url, USERNAME, PASSWORD, poll_interval=0)
        try:
            client.run_project_traceflow(
                org_id="acme",
                project_id="payments",
                traceflow_id="never-finishes",
                config=sample_config(),
                max_polls=2,
            )
        except timeout_type:
            pass
        else:
            raise AssertionError("max_polls exhaustion did not raise TraceflowTimeout")
        ids = [entry["operationId"] for entry in server.request_log]
        check(ids == [OPERATIONS[0][0], OPERATIONS[1][0], OPERATIONS[1][0]], "poll bound")
        check(OPERATIONS[2][0] not in ids, "observations fetched before terminal state")


def verify_mock_rejects_unnamed_operation() -> None:
    with running_mock(CONTRACT_PATH) as server:
        request = urllib.request.Request(
            server.base_url + "/policy/api/v1/version",
            headers={"Accept": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=2)
        except urllib.error.HTTPError as error:
            check(error.code == 404, "unnamed operation did not return 404")
        else:
            raise AssertionError("mock served an operation absent from the contract")
        check(server.request_log[0]["operationId"] is None, "unnamed operation was mapped")


def main() -> None:
    verify_provenance_and_contract()
    verify_stdlib_only()
    verify_mock_rejects_unnamed_operation()

    from vcf_traceflow import (
        NsxPolicyError,
        NsxPolicyTraceflowClient,
        TraceflowFailed,
        TraceflowTimeout,
    )

    verify_success_and_wire_contract(NsxPolicyTraceflowClient)
    verify_http_error(NsxPolicyTraceflowClient, NsxPolicyError)
    verify_failed_terminal(NsxPolicyTraceflowClient, TraceflowFailed)
    verify_poll_exhaustion(NsxPolicyTraceflowClient, TraceflowTimeout)
    print("PASS: VCF 9.1 project Traceflow contract verified")


if __name__ == "__main__":
    main()
