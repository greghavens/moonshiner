#!/usr/bin/env python3
"""Offline acceptance verifier for the VCF 9.0 vSAN DP integration."""

from __future__ import annotations

import ast
import inspect
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mock_vsan_dp import VsanDataProtectionMock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CONTRACT_PATH = ROOT / "docs" / "contract.json"
OFFICIAL_SOURCES_PATH = ROOT / "docs" / "official_sources.json"
CREATE_OPERATION = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
TASK_OPERATION = "Snapservice.Tasks_get"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
COMMIT_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"


EXPECTED_SOURCES = {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "license": "Apache-2.0",
    "spec_path": SPEC_PATH,
    "tag": "9.0.0.0",
    "commit_sha": COMMIT_SHA,
    "source_url": (
        "https://github.com/vmware/vcf-api-specs/blob/"
        + COMMIT_SHA
        + "/"
        + SPEC_PATH
    ),
    "operation_ids": [CREATE_OPERATION, TASK_OPERATION],
}

EXPECTED_CONTRACT = {
    "source": {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "tag": "9.0.0.0",
        "commit_sha": COMMIT_SHA,
        "spec_path": SPEC_PATH,
    },
    "openapi": "3.0.3",
    "api_title": "Snapshot Appliance API",
    "api_version": "9.0.0.0",
    "api_base_path": "/api",
    "authentication": {
        "scheme": "api_key_auth",
        "type": "apiKey",
        "in": "header",
        "name": "vmware-api-session-id",
    },
    "operations": [
        {
            "operation_id": CREATE_OPERATION,
            "method": "POST",
            "path": "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots",
            "query": {"vmw-task": "true"},
            "path_parameters": [
                {"name": "cluster", "required": True, "type": "string"},
                {"name": "pg", "required": True, "type": "string"},
            ],
            "request_body": {
                "required": True,
                "content_type": "application/json",
                "schema": "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec",
                "required_fields": ["name"],
                "properties": {
                    "name": {"type": "string", "required": True},
                    "retention": {
                        "schema": "Snapservice.RetentionPeriod",
                        "required": False,
                    },
                },
            },
            "responses": {
                "202": {"content_type": "application/json", "type": "string"}
            },
        },
        {
            "operation_id": TASK_OPERATION,
            "method": "GET",
            "path": "/snapservice/tasks/{task}",
            "query": {},
            "path_parameters": [
                {"name": "task", "required": True, "type": "string"}
            ],
            "responses": {
                "200": {
                    "content_type": "application/json",
                    "schema": "Snapservice.Tasks.Info",
                }
            },
        },
    ],
    "schemas": {
        "Snapservice.RetentionPeriod": {
            "type": "object",
            "required": ["duration", "unit"],
            "properties": {
                "unit": {
                    "type": "string",
                    "enum": ["MINUTE", "HOUR", "DAY", "WEEK", "MONTH"],
                },
                "duration": {"type": "integer", "format": "int64"},
            },
        },
        "Snapservice.Tasks.Info": {
            "type": "object",
            "required": [
                "cancelable",
                "description",
                "operation",
                "service",
                "status",
            ],
            "status_enum": ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED"],
            "terminal_statuses": ["SUCCEEDED", "FAILED"],
        },
    },
}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def load_json(path):
    check(path.is_file(), "%s is missing" % path.relative_to(ROOT))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AssertionError("%s is invalid JSON: %s" % (path.relative_to(ROOT), error))


def normalize_contract_value(value, field=None):
    """Normalize arrays whose ordering has no contract meaning."""
    if isinstance(value, dict):
        return {
            key: normalize_contract_value(item, key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [normalize_contract_value(item) for item in value]
        if field == "operations":
            return sorted(items, key=lambda item: item["operation_id"])
        if field == "path_parameters":
            return sorted(items, key=lambda item: item["name"])
        if field in {
            "operation_ids",
            "required",
            "required_fields",
            "status_enum",
            "terminal_statuses",
        }:
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        return items
    return value


def check_json_subset(actual, expected, context):
    """Require the selected contract facts while permitting added documentation."""
    if isinstance(expected, dict):
        check(isinstance(actual, dict), "%s must be a JSON object" % context)
        missing = set(expected) - set(actual)
        check(not missing, "%s is missing fields: %s" % (context, sorted(missing)))
        for key, expected_value in expected.items():
            check_json_subset(actual[key], expected_value, "%s.%s" % (context, key))
        return
    if isinstance(expected, list):
        check(isinstance(actual, list), "%s must be a JSON array" % context)
        check(
            len(actual) == len(expected),
            "%s has the wrong number of entries" % context,
        )
        for index, expected_value in enumerate(expected):
            check_json_subset(actual[index], expected_value, "%s[%d]" % (context, index))
        return
    check(actual == expected, "%s does not match the selected 9.0 specification" % context)


def check_contract_documents():
    sources = normalize_contract_value(load_json(OFFICIAL_SOURCES_PATH))
    contract = normalize_contract_value(load_json(CONTRACT_PATH))
    expected_sources = normalize_contract_value(EXPECTED_SOURCES)
    expected_contract = normalize_contract_value(EXPECTED_CONTRACT)
    check_json_subset(sources, expected_sources, "official_sources.json")
    check_json_subset(contract, expected_contract, "contract.json")


def check_stdlib_only():
    package = ROOT / "vsan_dp"
    check((package / "__init__.py").is_file(), "vsan_dp package is missing")
    allowed = set(sys.stdlib_module_names) | {"vsan_dp"}
    for source_path in sorted(package.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                roots = [(node.module or "").split(".", 1)[0]]
            else:
                continue
            external = [root for root in roots if root and root not in allowed]
            check(not external, "%s imports non-stdlib module(s): %s" % (source_path, external))


def new_client(base_url):
    from vsan_dp import VsanDataProtectionClient

    return VsanDataProtectionClient(
        base_url=base_url,
        session_id="session-test-123",
        poll_interval=0.0,
    )


def assert_session_header(entry):
    headers = entry["headers"]
    check(headers.get("vmware-api-session-id") == "session-test-123", "session header is wrong")


def check_constructor_contract():
    from vsan_dp import VsanDataProtectionClient

    parameters = inspect.signature(VsanDataProtectionClient).parameters
    check("base_url" in parameters, "constructor is missing base_url")
    check("session_id" in parameters, "constructor is missing session_id")
    check("poll_interval" in parameters, "constructor is missing poll_interval")
    check(
        parameters["poll_interval"].default == 0.0,
        "poll_interval does not default to 0.0",
    )


def check_success_wire_shape():
    with VsanDataProtectionMock(CONTRACT_PATH) as server:
        unknown = server.base_url + "/snapservice/sites"
        try:
            urllib.request.urlopen(unknown)
        except urllib.error.HTTPError as error:
            check(error.code == 404, "mock did not reject an unnamed operation")
            error.close()
        else:
            raise AssertionError("mock served an operation absent from contract.json")
        server.requests.clear()

        final_task = new_client(server.base_url).create_protection_group_snapshot(
            "domain-c8", "pg-42", "nightly-manual"
        )
        check(final_task["status"] == "SUCCEEDED", "client did not return successful terminal task")
        check(final_task["result"] == {"snapshot": "snapshot-007"}, "task result changed")

        requests = list(server.requests)
        check(len(requests) == 4, "expected one create request and three task polls")
        create = requests[0]
        check(create["operation_id"] == CREATE_OPERATION, "wrong create operation")
        check(create["method"] == "POST", "snapshot create must use POST")
        check(
            create["target"]
            == "/api/snapservice/clusters/domain-c8/protection-groups/pg-42/snapshots?vmw-task=true",
            "snapshot create target is not exact",
        )
        check(create["query"] == "vmw-task=true", "async query is not exact")
        check(
            json.loads(create["body"]) == {"name": "nightly-manual"},
            "unset retention was not omitted",
        )
        content_type = create["headers"].get("content-type", "")
        check(
            content_type.split(";", 1)[0].strip().lower() == "application/json",
            "snapshot request body is not application/json",
        )
        assert_session_header(create)

        polls = requests[1:]
        check(
            [entry["operation_id"] for entry in polls] == [TASK_OPERATION] * 3,
            "polling used an operation outside the contract",
        )
        check(
            [entry["target"] for entry in polls]
            == ["/api/snapservice/tasks/task-42"] * 3,
            "task poll target is not exact",
        )
        for entry in polls:
            check(entry["method"] == "GET", "task polling must use GET")
            check(entry["query"] == "", "task polling sent an unexpected query")
            check(entry["body"] == "", "task polling sent a request body")
            assert_session_header(entry)


def check_optional_retention_and_failure():
    with VsanDataProtectionMock(CONTRACT_PATH, task_states=("SUCCEEDED",)) as server:
        task = new_client(server.base_url).create_protection_group_snapshot(
            "domain-c9",
            "pg-9",
            "weekly",
            retention={"duration": 7, "unit": "DAY"},
        )
        check(task["status"] == "SUCCEEDED", "provided retention call did not finish")
        body = json.loads(server.requests[0]["body"])
        check(
            body == {"name": "weekly", "retention": {"duration": 7, "unit": "DAY"}},
            "provided retention was not sent unchanged",
        )
        for entry in server.requests:
            assert_session_header(entry)

    from vsan_dp import TaskFailedError

    with VsanDataProtectionMock(CONTRACT_PATH, task_states=("RUNNING", "FAILED")) as server:
        try:
            new_client(server.base_url).create_protection_group_snapshot(
                "domain-c8", "pg-fail", "will-fail"
            )
        except TaskFailedError as error:
            check(
                error.task == server.task_responses[-1],
                "TaskFailedError did not carry the complete final task",
            )
        else:
            raise AssertionError("FAILED terminal task did not raise TaskFailedError")
        check(
            [entry["operation_id"] for entry in server.requests]
            == [CREATE_OPERATION, TASK_OPERATION, TASK_OPERATION],
            "failure path did not poll through the terminal state",
        )
        for entry in server.requests:
            assert_session_header(entry)


def main():
    check_contract_documents()
    check_stdlib_only()
    check_constructor_contract()
    check_success_wire_shape()
    check_optional_retention_and_failure()
    print("all vSAN Data Protection contract and wire checks passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FAIL: %s" % error, file=sys.stderr)
        raise
