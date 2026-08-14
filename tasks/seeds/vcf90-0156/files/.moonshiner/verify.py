"""Protected deterministic verifier for the VCF Automation seed."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_vcfa import ContractMock, RequestRecord  # noqa: E402
from vcf_automation import VcfAutomationClient, apply_deployment_change  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    assert isinstance(value, dict)
    return value


def _verify_fixture() -> dict[str, Any]:
    contract = _load_json(ROOT / "docs" / "contract.json")
    sources = _load_json(ROOT / "docs" / "official_sources.json")

    statement = contract["source"]["statement"]
    assert contract["source"]["kind"] == "reference-documentation"
    assert "not a published specification" in statement
    assert "vmware/vcf-api-specs" in statement

    operations = contract["operations"]
    actual = {
        (item["operationId"], item["method"], item["path"])
        for item in operations
    }
    expected = {
        (
            "patchDeployment",
            "PATCH",
            "/deployment/api/deployments/{deploymentId}",
        ),
        (
            "submitDeploymentActionRequest",
            "POST",
            "/deployment/api/deployments/{deploymentId}/requests",
        ),
        ("getRequest", "GET", "/deployment/api/requests/{requestId}"),
    }
    assert actual == expected

    pages = sources["pages"]
    assert len(pages) == len(operations)
    assert {page["operationId"] for page in pages} == {
        operation["operationId"] for operation in operations
    }
    for page in pages:
        assert page["fetchedOn"] == "2026-08-13"
        assert page["url"].startswith(
            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/9.0/"
        )
        assert page["operation"].split()[0] in {"GET", "POST", "PATCH"}
    return contract


def _verify_stdlib_only() -> None:
    local_modules = {path.stem for path in (ROOT / "src" / "vcf_automation").glob("*.py")}
    allowed = set(sys.stdlib_module_names) | local_modules | {"vcf_automation"}
    for source_path in (ROOT / "src" / "vcf_automation").glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                continue
            assert all(root in allowed for root in roots), (
                f"non-stdlib import in {source_path}: {roots}"
            )


def _verify_public_signatures() -> None:
    positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
    keyword_only = inspect.Parameter.KEYWORD_ONLY
    unset = inspect.Parameter.empty

    expected = {
        VcfAutomationClient.__init__: [
            ("self", positional, unset),
            ("base_url", positional, unset),
            ("token", positional, unset),
            ("timeout", positional, 10.0),
        ],
        VcfAutomationClient.patch_deployment: [
            ("self", positional, unset),
            ("deployment_id", positional, unset),
            ("name", keyword_only, None),
            ("description", keyword_only, None),
            ("icon_id", keyword_only, None),
        ],
        VcfAutomationClient.submit_deployment_action: [
            ("self", positional, unset),
            ("deployment_id", positional, unset),
            ("action_id", positional, unset),
            ("inputs", keyword_only, None),
            ("reason", keyword_only, None),
        ],
        VcfAutomationClient.get_request: [
            ("self", positional, unset),
            ("request_id", positional, unset),
        ],
        apply_deployment_change: [
            ("client", positional, unset),
            ("deployment_id", positional, unset),
            ("action_id", positional, unset),
            ("name", keyword_only, None),
            ("description", keyword_only, None),
            ("icon_id", keyword_only, None),
            ("inputs", keyword_only, None),
            ("reason", keyword_only, None),
            ("poll_interval", keyword_only, 1.0),
        ],
    }
    for callable_object, expected_parameters in expected.items():
        actual_parameters = list(inspect.signature(callable_object).parameters.values())
        actual = [
            (parameter.name, parameter.kind, parameter.default)
            for parameter in actual_parameters
        ]
        assert actual == expected_parameters, f"public signature changed: {callable_object}"


def _assert_json_request(record: RequestRecord, expected: dict[str, Any]) -> None:
    assert record.query == ""
    assert record.headers.get("accept") == "application/json"
    assert record.headers.get("content-type") == "application/json"
    assert record.headers.get("content-length") == str(len(record.body))
    assert json.loads(record.body) == expected
    assert b"null" not in record.body


def _exercise_individual_operations(contract: dict[str, Any]) -> None:
    """Check each public client operation, including path and empty-value edges."""
    seed = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    deployment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ":direct-deployment"))
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ":direct-request"))
    token = "token-" + seed[24:44]
    icon_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ":icon"))
    action_id = "action." + seed[48:60]

    responses = {
        "patchDeployment": {"operation": "patch", "marker": seed[44:48]},
        "submitDeploymentActionRequest": {
            "operation": "submit",
            "id": request_id,
        },
        "getRequest": {"operation": "get", "status": "SUCCESSFUL"},
    }

    def responder(operation_id: str, record: RequestRecord) -> tuple[int, Any]:
        return 200, responses[operation_id]

    with ContractMock(ROOT / "docs" / "contract.json", responder) as mock:
        client = VcfAutomationClient(mock.base_url, token)
        patched = client.patch_deployment(
            deployment_id,
            name=None,
            description="",
            icon_id=icon_id,
        )
        submitted = client.submit_deployment_action(
            deployment_id,
            action_id,
            inputs={},
            reason="",
        )
        fetched = client.get_request(request_id)
        records = mock.request_log

    assert patched == responses["patchDeployment"]
    assert submitted == responses["submitDeploymentActionRequest"]
    assert fetched == responses["getRequest"]
    assert [record.operation_id for record in records] == [
        "patchDeployment",
        "submitDeploymentActionRequest",
        "getRequest",
    ]
    assert [record.path for record in records] == [
        f"/deployment/api/deployments/{deployment_id}",
        f"/deployment/api/deployments/{deployment_id}/requests",
        f"/deployment/api/requests/{request_id}",
    ]
    assert records[0].path_parameters == {"deploymentId": deployment_id}
    assert records[1].path_parameters == {"deploymentId": deployment_id}
    assert records[2].path_parameters == {"requestId": request_id}
    assert all(record.headers.get("authorization") == f"Bearer {token}" for record in records)

    _assert_json_request(records[0], {"description": "", "iconId": icon_id})
    _assert_json_request(
        records[1], {"actionId": action_id, "inputs": {}, "reason": ""}
    )
    assert records[2].query == ""
    assert records[2].body == b""
    assert records[2].headers.get("accept") == "application/json"
    assert "content-type" not in records[2].headers


def _exercise_other_terminal_statuses() -> None:
    """All terminal contract statuses must complete without another poll."""

    class TerminalClient:
        def __init__(self, terminal_status: str) -> None:
            self.terminal_status = terminal_status
            self.polls = 0

        def patch_deployment(self, deployment_id: str, **fields: Any) -> dict[str, Any]:
            return {"id": deployment_id, "fields": fields}

        def submit_deployment_action(
            self, deployment_id: str, action_id: str, **fields: Any
        ) -> dict[str, Any]:
            return {
                "id": "request-id",
                "deploymentId": deployment_id,
                "actionId": action_id,
                "fields": fields,
            }

        def get_request(self, request_id: str) -> dict[str, Any]:
            self.polls += 1
            assert self.polls == 1, (
                f"workflow did not stop at terminal status {self.terminal_status}"
            )
            return {
                "id": request_id,
                "status": self.terminal_status,
                "details": "terminal details",
            }

    for status in ("APPROVAL_REJECTED", "ABORTED", "SUCCESSFUL"):
        client = TerminalClient(status)
        result = apply_deployment_change(
            client,  # type: ignore[arg-type]
            "deployment-id",
            "action-id",
            poll_interval=0,
        )
        assert client.polls == 1
        assert result.patch_succeeded is True
        assert result.action_submitted is True
        assert result.request_id == "request-id"
        assert result.status == status
        assert result.succeeded is (status == "SUCCESSFUL")
        expected_details = None if status == "SUCCESSFUL" else "terminal details"
        assert result.failure_details == expected_details


def _exercise_change(contract: dict[str, Any]) -> None:
    seed = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    deployment_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ":deployment"))
    request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, seed + ":request"))
    token = "token-" + seed[:20]
    new_name = "deployment-" + seed[20:32]
    action_id = "action." + seed[32:44]
    action_inputs = {"days": int(seed[44:46], 16) + 1}
    failure_details = "action failed " + seed[46:62]
    polls = 0

    def responder(operation_id: str, record: RequestRecord) -> tuple[int, Any]:
        nonlocal polls
        if operation_id == "patchDeployment":
            return 200, {
                "id": deployment_id,
                "name": new_name,
                "status": "UPDATE_SUCCESSFUL",
            }
        if operation_id == "submitDeploymentActionRequest":
            return 200, {
                "id": request_id,
                "deploymentId": deployment_id,
                "status": "CREATED",
            }
        if operation_id == "getRequest":
            polls += 1
            if polls == 1:
                return 200, {"id": request_id, "status": "INPROGRESS"}
            if polls == 2:
                return 200, {"id": request_id, "status": "COMPLETION"}
            return 200, {
                "id": request_id,
                "status": "FAILED",
                "details": failure_details,
            }
        raise AssertionError(f"mock received unknown operation {operation_id}")

    with ContractMock(ROOT / "docs" / "contract.json", responder) as mock:
        assert mock.base_url.startswith("http://127.0.0.1:")
        client = VcfAutomationClient(mock.base_url, token)
        result = apply_deployment_change(
            client,
            deployment_id,
            action_id,
            name=new_name,
            description=None,
            icon_id=None,
            inputs=action_inputs,
            reason=None,
            poll_interval=0,
        )
        records = mock.request_log

    assert result.deployment_id == deployment_id
    assert result.patch_succeeded is True
    assert result.patched_deployment == {
        "id": deployment_id,
        "name": new_name,
        "status": "UPDATE_SUCCESSFUL",
    }
    assert result.action_submitted is True
    assert result.request_id == request_id
    assert result.status == "FAILED"
    assert result.succeeded is False
    assert result.failure_details == failure_details
    assert result.final_request == {
        "id": request_id,
        "status": "FAILED",
        "details": failure_details,
    }

    assert len(records) == 5
    assert [record.operation_id for record in records] == [
        "patchDeployment",
        "submitDeploymentActionRequest",
        "getRequest",
        "getRequest",
        "getRequest",
    ]
    assert [record.method for record in records] == [
        "PATCH",
        "POST",
        "GET",
        "GET",
        "GET",
    ]
    expected_paths = [
        f"/deployment/api/deployments/{deployment_id}",
        f"/deployment/api/deployments/{deployment_id}/requests",
        f"/deployment/api/requests/{request_id}",
        f"/deployment/api/requests/{request_id}",
        f"/deployment/api/requests/{request_id}",
    ]
    assert [record.path for record in records] == expected_paths
    assert all(record.headers.get("authorization") == f"Bearer {token}" for record in records)

    _assert_json_request(records[0], {"name": new_name})
    _assert_json_request(
        records[1], {"actionId": action_id, "inputs": action_inputs}
    )
    for record in records[2:]:
        assert record.query == ""
        assert record.body == b""
        assert record.headers.get("accept") == "application/json"
        assert "content-type" not in record.headers


def main() -> None:
    contract = _verify_fixture()
    _verify_stdlib_only()
    _verify_public_signatures()
    _exercise_individual_operations(contract)
    _exercise_other_terminal_statuses()
    _exercise_change(contract)
    print("VCF Automation contract and wire behavior verified")


if __name__ == "__main__":
    main()
