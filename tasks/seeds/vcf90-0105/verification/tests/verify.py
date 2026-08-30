#!/usr/bin/env python3
"""Protected deterministic verification for vcf90-0105."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
import sys
import tomllib
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

TASK_ID = "123e4567-e89b-42d3-a456-556642440000"
MOCK_SHA256 = "17dc4c9ebd28484da3e5a55ccc902f8b21707125c431619207a9c45a1ab1c749"
EXPECTED_SOURCES = {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "license": "Apache-2.0",
    "tag": "9.0.0.0",
    "commit": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
    "specificationPath": "specifications/vcf-installer/vcf-installer-openapi.json",
    "specificationUrl": "https://raw.githubusercontent.com/vmware/vcf-api-specs/85151f6b1bb58f13b6ac0304bfec53904bea085f/specifications/vcf-installer/vcf-installer-openapi.json",
    "operationIds": ["deploySddc", "getSddcTaskByID"],
}

EXPECTED_CONTRACT = {
    "openapi": "3.0.1",
    "title": "VMware Cloud Foundation Installer API Reference Guide",
    "version": "9.0.0.0",
    "operations": {
        "deploySddc": {
            "method": "POST",
            "path": "/v1/sddcs",
            "optionalQueryParameters": {
                "skipValidations": {"type": "boolean", "default": False}
            },
            "requestBody": {
                "required": True,
                "contentType": "application/json",
                "schema": "SddcSpec",
            },
            "successResponse": {"status": 202, "schema": "SddcTask"},
        },
        "getSddcTaskByID": {
            "method": "GET",
            "path": "/v1/sddcs/{id}",
            "pathParameters": {
                "id": {
                    "required": True,
                    "type": "string",
                    "minimum": 36,
                    "maximum": 36,
                }
            },
            "successResponse": {"status": 200, "schema": "SddcTask"},
        },
    },
    "schemas": {
        "SddcSpec": {
            "type": "object",
            "required": ["dnsSpec", "networkSpecs", "sddcId", "vcenterSpec"],
            "properties": {
                "sddcId": "string",
                "workflowType": "string",
                "hostSpecs": "array",
                "version": "string",
                "vcenterSpec": "SddcVcenterSpec",
                "clusterSpec": "SddcClusterSpec",
                "dvsSpecs": "array",
                "nsxtSpec": "SddcNsxtSpec",
                "networkSpecs": "array",
                "dnsSpec": "DnsSpec",
                "ntpServers": "array",
                "sddcManagerSpec": "SddcManagerSpec",
                "managementPoolName": "string",
                "ceipEnabled": "boolean",
                "skipEsxThumbprintValidation": "boolean",
                "skipGatewayPingValidation": "boolean",
                "securitySpec": "SecuritySpec",
                "datastoreSpec": "SddcDatastoreSpec",
                "vcfOperationsFleetManagementSpec": "VcfOperationsFleetManagementSpec",
                "vcfOperationsSpec": "VcfOperationsSpec",
                "vcfOperationsCollectorSpec": "VcfOperationsCollectorSpec",
                "vcfAutomationSpec": "VcfAutomationSpec",
                "vcfInstanceName": "string",
            },
        },
        "SddcTask": {
            "type": "object",
            "required": ["creationTimestamp", "status"],
            "properties": {
                "id": "string",
                "name": "string",
                "status": "string",
                "localizableNamePack": "MessagePack",
                "creationTimestamp": "string",
                "sddcSubTasks": "array",
                "milestones": "array",
            },
            "statusValues": [
                "IN_PROGRESS",
                "COMPLETED_WITH_SUCCESS",
                "ROLLBACK_SUCCESS",
                "COMPLETED_WITH_FAILURE",
            ],
        },
    },
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def schema_name(value: object) -> object:
    """Return a compact schema name/type from compact or OpenAPI-style JSON."""

    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        return value["$ref"].rsplit("/", 1)[-1]
    if "schema" in value:
        return schema_name(value["schema"])
    return value.get("type")


def response_schema(operation: dict[str, object], status: int) -> object:
    compact = operation.get("successResponse")
    if isinstance(compact, dict) and compact.get("status") == status:
        return schema_name(compact.get("schema"))
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return None
    response = responses.get(str(status), responses.get(status))
    if not isinstance(response, dict):
        return None
    content = response.get("content")
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    return schema_name(media) if isinstance(media, dict) else None


def verify_and_load_support() -> None:
    """Load the loopback fixture only after its protected digest is verified."""

    fixture = ROOT / "test_support" / "mock_vcf_installer.py"
    check(
        hashlib.sha256(fixture.read_bytes()).hexdigest() == MOCK_SHA256,
        "the shipped contract mock was modified",
    )
    from test_support.mock_vcf_installer import ContractMockServer as server_class

    globals()["ContractMockServer"] = server_class


def verify_contract() -> None:
    sources_path = ROOT / "docs" / "official_sources.json"
    contract_path = ROOT / "docs" / "contract.json"
    check(sources_path.is_file(), "docs/official_sources.json is missing")
    check(contract_path.is_file(), "docs/contract.json is missing")
    sources = json.loads(sources_path.read_text())
    contract = json.loads(contract_path.read_text())

    check(isinstance(sources, dict), "official source provenance must be an object")
    for key, value in EXPECTED_SOURCES.items():
        if key == "operationIds":
            check(
                set(sources.get(key, ())) == set(value),
                "official source operationIds are not exact",
            )
        else:
            check(sources.get(key) == value, f"official source {key} is not exact")

    check(contract.get("openapi") == "3.0.1", "wrong OpenAPI version")
    check(contract.get("title") == EXPECTED_CONTRACT["title"], "wrong API title")
    check(contract.get("version") == "9.0.0.0", "wrong API version")
    operations = contract.get("operations")
    check(isinstance(operations, dict), "contract operations must be an object")
    check(
        set(operations) == set(EXPECTED_CONTRACT["operations"]),
        "contract must contain exactly the two requested operations",
    )
    for operation_id, expected in EXPECTED_CONTRACT["operations"].items():
        operation = operations[operation_id]
        check(isinstance(operation, dict), f"{operation_id} must be an object")
        check(operation.get("method") == expected["method"], f"wrong {operation_id} method")
        check(operation.get("path") == expected["path"], f"wrong {operation_id} path")

    deploy = operations["deploySddc"]
    query = deploy.get("optionalQueryParameters")
    query = query.get("skipValidations") if isinstance(query, dict) else None
    if query is None:
        parameters = deploy.get("parameters", ())
        query = next(
            (
                item.get("schema")
                for item in parameters
                if isinstance(item, dict)
                and item.get("name") == "skipValidations"
                and item.get("in") == "query"
                and not item.get("required", False)
            ),
            None,
        )
    check(
        isinstance(query, dict)
        and query.get("type") == "boolean"
        and query.get("default") is False,
        "deploySddc skipValidations is not faithful",
    )

    request_body = deploy.get("requestBody")
    check(isinstance(request_body, dict), "deploySddc request body is missing")
    if "contentType" in request_body:
        request_schema = schema_name(request_body.get("schema"))
        request_media_type = request_body.get("contentType")
    else:
        content = request_body.get("content")
        media = content.get("application/json") if isinstance(content, dict) else None
        request_schema = schema_name(media) if isinstance(media, dict) else None
        request_media_type = "application/json" if media is not None else None
    check(request_body.get("required") is True, "deploySddc request body must be required")
    check(request_media_type == "application/json", "deploySddc must consume JSON")
    check(request_schema == "SddcSpec", "deploySddc must consume SddcSpec")
    check(response_schema(deploy, 202) == "SddcTask", "deploySddc 202 response is wrong")

    lookup = operations["getSddcTaskByID"]
    path_parameters = lookup.get("pathParameters")
    path_id = path_parameters.get("id") if isinstance(path_parameters, dict) else None
    if path_id is None:
        parameters = lookup.get("parameters", ())
        path_id = next(
            (
                item.get("schema")
                for item in parameters
                if isinstance(item, dict)
                and item.get("name") == "id"
                and item.get("in") == "path"
                and item.get("required") is True
            ),
            None,
        )
    check(
        isinstance(path_id, dict)
        and path_id.get("type") == "string"
        and path_id.get("minimum") == 36
        and path_id.get("maximum") == 36,
        "getSddcTaskByID id parameter is not faithful",
    )
    check(
        path_id.get("required", True) is True,
        "getSddcTaskByID id parameter must be required",
    )
    check(
        response_schema(lookup, 200) == "SddcTask",
        "getSddcTaskByID 200 response is wrong",
    )

    schemas = contract.get("schemas")
    check(isinstance(schemas, dict), "contract schemas must be an object")
    check(set(schemas) == {"SddcSpec", "SddcTask"}, "contract schemas are not focused")
    for current_schema_name, expected in EXPECTED_CONTRACT["schemas"].items():
        schema = schemas[current_schema_name]
        check(
            schema.get("type") == "object",
            f"{current_schema_name} must be an object",
        )
        check(
            set(schema.get("required", ())) == set(expected["required"]),
            f"{current_schema_name} required fields are not faithful",
        )
        properties = schema.get("properties")
        check(
            isinstance(properties, dict),
            f"{current_schema_name} properties must be an object",
        )
        check(
            set(properties) == set(expected["properties"]),
            f"{current_schema_name} fields are not a focused extraction",
        )
        for field, expected_type in expected["properties"].items():
            actual_type = schema_name(properties[field])
            check(
                actual_type == expected_type,
                f"{current_schema_name}.{field} has the wrong type",
            )

    task_schema = schemas["SddcTask"]
    status_values = task_schema.get("statusValues")
    if status_values is None and isinstance(task_schema["properties"]["status"], dict):
        status_values = task_schema["properties"]["status"].get("enum")
    check(
        set(status_values or ())
        == set(EXPECTED_CONTRACT["schemas"]["SddcTask"]["statusValues"]),
        "SddcTask status values are not faithful to 9.0",
    )
    check("9.1" not in json.dumps((sources, contract)), "9.1 material is not allowed")


def verify_stdlib_only() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    check(
        project["project"].get("dependencies", []) == [],
        "runtime dependencies must be empty",
    )
    allowed = set(sys.stdlib_module_names) | {"__future__", "vcf_installer"}
    for source in (ROOT / "src" / "vcf_installer").rglob("*.py"):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level:
                roots = {"vcf_installer"}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            check(roots <= allowed, f"non-stdlib import in {source.name}: {roots}")


def verify_api_surface() -> None:
    from vcf_installer import VCFInstallerClient

    constructor = inspect.signature(VCFInstallerClient)
    check("base_url" in constructor.parameters, "constructor must accept base_url")
    timeout = constructor.parameters.get("timeout")
    check(
        timeout is not None and timeout.default is not inspect.Parameter.empty,
        "constructor timeout must be optional",
    )

    method = inspect.signature(VCFInstallerClient.deploy_sddc_and_wait)
    skip = method.parameters.get("skip_validations")
    interval = method.parameters.get("poll_interval")
    check(
        skip is not None
        and skip.kind is inspect.Parameter.KEYWORD_ONLY
        and skip.default is None,
        "skip_validations must be keyword-only and default to None",
    )
    check(
        interval is not None
        and interval.kind is inspect.Parameter.KEYWORD_ONLY
        and interval.default == 1.0,
        "poll_interval must be keyword-only and default to 1.0",
    )


def verify_wire_and_polling() -> None:
    from vcf_installer import VCFInstallerClient

    spec = json.loads((ROOT / "fixtures" / "sddc_spec.json").read_text())
    spec["dnsSpec"]["subdomain"] = "münchen.example.com"
    original = json.loads(json.dumps(spec))
    contract_path = ROOT / "docs" / "contract.json"
    with ContractMockServer(contract_path) as server:
        client = VCFInstallerClient(server.base_url, timeout=2)
        result = client.deploy_sddc_and_wait(spec, poll_interval=0)
        log = list(server.request_log)

        check(spec == original, "client mutated the supplied SddcSpec")
        check(result["status"] == "COMPLETED_WITH_SUCCESS", "terminal result not returned")
        check(
            [(item["method"], item["target"]) for item in log]
            == [
                ("POST", "/v1/sddcs"),
                ("GET", f"/v1/sddcs/{TASK_ID}"),
                ("GET", f"/v1/sddcs/{TASK_ID}"),
            ],
            "request sequence or targets are not exact; unset query must be absent",
        )
        expected_body = json.dumps(
            original, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        check(log[0]["body"] == expected_body, "POST body bytes are not compact exact JSON")
        check(log[0]["headers"].get("content-type") == "application/json", "wrong Content-Type")
        check(
            all(item["headers"].get("accept") == "application/json" for item in log),
            "wrong Accept header",
        )
        check(all(item["body"] == b"" for item in log[1:]), "GET requests must have empty bodies")
        optional = set(EXPECTED_CONTRACT["schemas"]["SddcSpec"]["properties"]) - set(
            EXPECTED_CONTRACT["schemas"]["SddcSpec"]["required"]
        )
        sent = json.loads(log[0]["body"])
        check(not (optional & sent.keys()), "unset optional body fields were synthesized")

        try:
            urlopen(server.base_url + "/v1/sddcs/latest", timeout=2)
        except HTTPError as error:
            check(error.code == 404, "unknown mock route must return 404")
        else:
            raise AssertionError("mock served an operation outside the contract")


def verify_explicit_booleans() -> None:
    from vcf_installer import VCFInstallerClient

    spec = json.loads((ROOT / "fixtures" / "sddc_spec.json").read_text())
    for value, encoded in ((False, "false"), (True, "true")):
        with ContractMockServer(ROOT / "docs" / "contract.json") as server:
            client = (
                VCFInstallerClient(server.base_url)
                if value is False
                else VCFInstallerClient(server.base_url, timeout=2)
            )
            client.deploy_sddc_and_wait(spec, skip_validations=value, poll_interval=0)
            check(
                server.request_log[0]["target"]
                == f"/v1/sddcs?skipValidations={encoded}",
                f"explicit {value} must be serialized as lowercase {encoded}",
            )


def verify_terminal_and_invalid_statuses() -> None:
    from vcf_installer import VCFInstallerClient

    spec = json.loads((ROOT / "fixtures" / "sddc_spec.json").read_text())
    terminal_statuses = (
        "COMPLETED_WITH_SUCCESS",
        "ROLLBACK_SUCCESS",
        "COMPLETED_WITH_FAILURE",
    )
    for terminal in terminal_statuses:
        with ContractMockServer(
            ROOT / "docs" / "contract.json", poll_statuses=(terminal,)
        ) as server:
            result = VCFInstallerClient(server.base_url, timeout=2).deploy_sddc_and_wait(
                spec, poll_interval=0
            )
            check(result["status"] == terminal, f"{terminal} was not returned")
            check(
                [entry["method"] for entry in server.request_log] == ["POST", "GET"],
                f"polling did not stop at {terminal}",
            )

    for accepted_terminal in terminal_statuses:
        with ContractMockServer(
            ROOT / "docs" / "contract.json",
            accepted_status=accepted_terminal,
            poll_statuses=("COMPLETED_WITH_SUCCESS",),
        ) as server:
            result = VCFInstallerClient(
                server.base_url, timeout=2
            ).deploy_sddc_and_wait(spec, poll_interval=0)
            check(
                result["status"] == "COMPLETED_WITH_SUCCESS",
                "the accepted response was incorrectly treated as completion",
            )
            check(
                [entry["method"] for entry in server.request_log] == ["POST", "GET"],
                "every accepted deployment must perform a task lookup",
            )

    invalid_cases = (
        {"accepted_status": "QUEUED"},
        {"poll_statuses": ("QUEUED",)},
    )
    for server_options in invalid_cases:
        with ContractMockServer(
            ROOT / "docs" / "contract.json", **server_options
        ) as server:
            try:
                VCFInstallerClient(server.base_url, timeout=2).deploy_sddc_and_wait(
                    spec, poll_interval=0
                )
            except Exception:
                pass
            else:
                raise AssertionError("a status outside the 9.0 contract was accepted")


def verify_polling_policy() -> None:
    import vcf_installer.client as client_module
    from vcf_installer import VCFInstallerClient

    spec = json.loads((ROOT / "fixtures" / "sddc_spec.json").read_text())
    delays: list[float] = []
    original_sleep = client_module.time.sleep
    client_module.time.sleep = delays.append
    try:
        with ContractMockServer(
            ROOT / "docs" / "contract.json",
            poll_statuses=(
                "IN_PROGRESS",
                "IN_PROGRESS",
                "COMPLETED_WITH_SUCCESS",
            ),
        ) as server:
            VCFInstallerClient(server.base_url, timeout=2).deploy_sddc_and_wait(
                spec, poll_interval=0.25
            )
    finally:
        client_module.time.sleep = original_sleep
    check(
        len(delays) == 3 and all(delay == 0.25 for delay in delays),
        "poll_interval was not honored between nonterminal polls",
    )

    long_sequence = ("IN_PROGRESS",) * 105 + ("COMPLETED_WITH_SUCCESS",)
    with ContractMockServer(
        ROOT / "docs" / "contract.json", poll_statuses=long_sequence
    ) as server:
        result = VCFInstallerClient(server.base_url, timeout=2).deploy_sddc_and_wait(
            spec, poll_interval=0
        )
        check(result["status"] == "COMPLETED_WITH_SUCCESS", "long poll did not finish")
        check(
            len(server.request_log) == 107,
            "polling stopped before the service returned a terminal status",
        )


def main() -> None:
    verify_and_load_support()
    verify_contract()
    verify_stdlib_only()
    verify_api_surface()
    verify_wire_and_polling()
    verify_explicit_booleans()
    verify_terminal_and_invalid_statuses()
    verify_polling_policy()
    print("vcf90-0105 verification passed")


if __name__ == "__main__":
    main()
