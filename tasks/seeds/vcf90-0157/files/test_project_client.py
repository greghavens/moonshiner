"""Protected acceptance verifier for the VCF Automation Project client."""

from __future__ import annotations

import http.client
import inspect
import json
from pathlib import Path

from mock_vcf import MockVCFAutomation
from vcf_automation import PrecheckFailed, ProjectClient, VCFAutomationError


ROOT = Path(__file__).parent
APPLICATION_HEADERS = {"authorization", "accept", "content-type", "content-length"}
TRANSPORT_HEADERS = {"host", "connection", "user-agent", "accept-encoding"}


def _application_headers(record: dict[str, object]) -> dict[str, str]:
    all_pairs = [
        (name.lower(), value) for name, value in record["headers"]  # type: ignore[union-attr]
    ]
    unexpected = [
        (name, value)
        for name, value in all_pairs
        if name not in APPLICATION_HEADERS | TRANSPORT_HEADERS
    ]
    assert not unexpected, f"unexpected request headers: {unexpected!r}"
    pairs = [(name, value) for name, value in all_pairs if name in APPLICATION_HEADERS]
    assert len(pairs) == len(dict(pairs)), f"duplicate application header: {pairs!r}"
    return dict(pairs)


def check_protected_reference_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
    )

    assert contract["product_version"] == "9.0"
    assert contract["source"]["kind"] == "reference_documentation"
    statement = contract["source"]["statement"].lower()
    assert "not a published api specification" in statement
    assert [operation["operation"] for operation in contract["operations"]] == [
        "Get Project",
        "Modify Project",
    ]
    assert [(operation["method"], operation["path_template"]) for operation in contract["operations"]] == [
        ("GET", "/project-service/api/projects/{id}"),
        ("PATCH", "/project-service/api/projects/{id}"),
    ]

    assert sources["source_type"] == "authoritative Broadcom xAPIs reference documentation"
    assert sources["sources"] == [
        {
            "url": "https://developer.broadcom.com/xapis/vm-apps-org-project/9.0/project-service/api/projects/id/get/",
            "operation": "Get Project",
            "date_fetched": "2026-08-13",
        },
        {
            "url": "https://developer.broadcom.com/xapis/vm-apps-org-project/9.0/project-service/api/projects/id/patch/",
            "operation": "Modify Project",
            "date_fetched": "2026-08-13",
        },
    ]


def check_public_api_shape() -> None:
    constructor = inspect.signature(ProjectClient)
    assert list(constructor.parameters) == ["base_url", "token", "timeout"]
    assert constructor.parameters["timeout"].default == 10.0

    modify = inspect.signature(ProjectClient.modify_project)
    assert list(modify.parameters) == [
        "self",
        "project_id",
        "expected_name",
        "name",
        "description",
        "constraints",
        "properties",
        "operation_timeout",
        "shared_resources",
    ]
    assert modify.parameters["project_id"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in list(modify.parameters)[2:]:
        assert modify.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert modify.parameters["expected_name"].default is inspect.Parameter.empty
    assert modify.parameters["name"].default is inspect.Parameter.empty
    for name in list(modify.parameters)[4:]:
        assert modify.parameters[name].default is None


def check_minimal_exact_wire_shape() -> None:
    project_id = "project /west"
    initial = {"id": project_id, "name": "Legacy", "orgId": "org-7"}
    with MockVCFAutomation(initial, token="token-abc") as mock:
        client = ProjectClient(mock.base_url, "token-abc")
        result = client.modify_project(
            project_id,
            expected_name="Legacy",
            name="Platform Core",
        )

        assert result == {"id": project_id, "name": "Platform Core", "orgId": "org-7"}
        assert mock.project == result
        assert len(mock.requests) == 2, mock.requests
        get_request, patch_request = mock.requests
        target = "/project-service/api/projects/project%20%2Fwest?apiVersion=2019-01-15"

        assert get_request["method"] == "GET"
        assert get_request["target"] == target
        assert get_request["query"] == [("apiVersion", "2019-01-15")]
        assert get_request["body"] == b""
        assert _application_headers(get_request) == {
            "authorization": "Bearer token-abc",
            "accept": "application/json",
        }
        assert ("Host", mock.authority) in get_request["headers"]

        expected_body = b'{"name":"Platform Core"}'
        assert patch_request["method"] == "PATCH"
        assert patch_request["target"] == target
        assert patch_request["query"] == [("apiVersion", "2019-01-15")]
        assert patch_request["body"] == expected_body
        assert _application_headers(patch_request) == {
            "authorization": "Bearer token-abc",
            "accept": "application/json",
            "content-type": "application/json",
            "content-length": str(len(expected_body)),
        }
        assert ("Host", mock.authority) in patch_request["headers"]


def check_optional_presence_is_not_truthiness() -> None:
    initial = {"id": "p-2", "name": "Before"}
    with MockVCFAutomation(initial) as mock:
        result = ProjectClient(mock.base_url, "test-token").modify_project(
            "p-2",
            expected_name="Before",
            name="After",
            description="",
            constraints={},
            properties={},
            operation_timeout=0,
            shared_resources=False,
        )

        expected = (
            b'{"name":"After","description":"","constraints":{},"properties":{},'
            b'"operationTimeout":0,"sharedResources":false}'
        )
        assert mock.requests[1]["body"] == expected
        body = json.loads(expected)
        assert body["constraints"] == {}
        assert result["description"] == ""
        assert result["operationTimeout"] == 0
        assert result["sharedResources"] is False


def check_compact_utf8_wire_encoding() -> None:
    project_id = "équipe/北"
    initial = {"id": project_id, "name": "Avant"}
    with MockVCFAutomation(initial) as mock:
        result = ProjectClient(mock.base_url, "test-token").modify_project(
            project_id,
            expected_name="Avant",
            name="Équipe 北",
            description="déjà",
        )

        assert mock.requests[0]["target"] == (
            "/project-service/api/projects/%C3%A9quipe%2F%E5%8C%97"
            "?apiVersion=2019-01-15"
        )
        assert mock.requests[1]["body"] == (
            '{"name":"Équipe 北","description":"déjà"}'.encode("utf-8")
        )
        assert result["name"] == "Équipe 北"


def check_name_precheck_gates_mutation() -> None:
    initial = {"id": "p-3", "name": "Somebody Else Updated It", "description": "stable"}
    with MockVCFAutomation(initial) as mock:
        client = ProjectClient(mock.base_url, "test-token")
        try:
            client.modify_project(
                "p-3",
                expected_name="Old Name",
                name="Must Not Appear",
                description="must not change",
            )
        except PrecheckFailed:
            pass
        else:
            raise AssertionError("name mismatch did not fail the precheck")

        assert [request["method"] for request in mock.requests] == ["GET"]
        assert mock.project == initial


def check_http_precheck_failure_gates_mutation() -> None:
    with MockVCFAutomation(None) as mock:
        client = ProjectClient(mock.base_url, "test-token")
        try:
            client.modify_project("missing", expected_name="Old", name="New")
        except VCFAutomationError:
            pass
        else:
            raise AssertionError("404 precheck was accepted")
        assert [request["method"] for request in mock.requests] == ["GET"]
        assert mock.project is None


def check_invalid_json_precheck_gates_mutation() -> None:
    initial = {"id": "p-bad-json", "name": "Before"}
    responses = [b"{not-json", b"[]"]
    for response_body in responses:
        with MockVCFAutomation(initial, get_body=response_body) as mock:
            client = ProjectClient(mock.base_url, "test-token")
            try:
                client.modify_project(
                    "p-bad-json",
                    expected_name="Before",
                    name="Must Not Appear",
                )
            except VCFAutomationError:
                pass
            else:
                raise AssertionError(f"precheck accepted response {response_body!r}")
            assert [request["method"] for request in mock.requests] == ["GET"]
            assert mock.project == initial


def check_patch_response_failures_are_client_errors() -> None:
    initial = {"id": "p-patch-error", "name": "Before"}
    patch_responses = [
        (503, b'{"message":"unavailable"}'),
        (200, b"{not-json"),
        (200, b"[]"),
    ]
    for patch_response in patch_responses:
        with MockVCFAutomation(initial, patch_response=patch_response) as mock:
            client = ProjectClient(mock.base_url, "test-token")
            try:
                client.modify_project(
                    "p-patch-error",
                    expected_name="Before",
                    name="After",
                )
            except VCFAutomationError:
                pass
            else:
                raise AssertionError(f"PATCH accepted response {patch_response!r}")
            assert [request["method"] for request in mock.requests] == ["GET", "PATCH"]
            assert mock.project == initial


def check_mock_serves_only_contract_operations() -> None:
    with MockVCFAutomation({"id": "p-4", "name": "Current"}) as mock:
        connection = http.client.HTTPConnection("127.0.0.1", int(mock.base_url.rsplit(":", 1)[1]))
        connection.request("GET", "/unlisted?apiVersion=2019-01-15")
        response = connection.getresponse()
        response.read()
        assert response.status == 404
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", int(mock.base_url.rsplit(":", 1)[1]))
        connection.request(
            "POST",
            "/project-service/api/projects/p-4?apiVersion=2019-01-15",
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 405
        connection.close()


def main() -> None:
    checks = [
        check_protected_reference_contract,
        check_public_api_shape,
        check_minimal_exact_wire_shape,
        check_optional_presence_is_not_truthiness,
        check_compact_utf8_wire_encoding,
        check_name_precheck_gates_mutation,
        check_http_precheck_failure_gates_mutation,
        check_invalid_json_precheck_gates_mutation,
        check_patch_response_failures_are_client_errors,
        check_mock_serves_only_contract_operations,
    ]
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"{len(checks)} checks passed")


if __name__ == "__main__":
    main()
