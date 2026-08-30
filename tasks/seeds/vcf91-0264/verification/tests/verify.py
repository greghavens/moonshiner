#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0264.

Starts contract-pinned loopback VCF Operations mocks on ephemeral ports, drives
the solution through successful response variants, both precheck refusals,
declared input validation, and API failures, then grades the resulting reports
and exact request wire shapes.  No live VMware endpoint is contacted.
"""

from __future__ import annotations

import ast
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
PACKAGE_DIR = ROOT / "vcfops_onboarding"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
RUNNER_PATH = ROOT / ".protected" / "run_case.py"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
OPERATION_IDS = [
    "acquireToken",
    "enumerateAdapterInstances",
    "testConnection",
    "createAdapterInstance",
]
ROUTES = [
    ("POST", "/api/auth/token/acquire"),
    ("GET", "/api/adapters"),
    ("POST", "/api/adapters/testConnection"),
    ("POST", "/api/adapters"),
]
BASE_PATH = "/suite-api"
TOKEN_PREFIX = "vRealizeOpsToken"
EXPORTS = [
    "OnboardingResult",
    "OperationsApiError",
    "PrecheckFailedError",
    "onboard_adapter_instance",
]
RESULT_FIELDS = [
    "name",
    "adapter_kind_key",
    "adapter_instance_id",
    "resource_kind_key",
    "collector_id",
    "credential_instance_id",
    "description",
    "existing_instance_count",
    "precheck_message",
]
VALIDATION_CASES = [
    "blank-base-url",
    "blank-username",
    "blank-password",
    "blank-name",
    "blank-adapter-kind",
    "blank-credential-name",
    "blank-credential-kind",
    "blank-auth-source",
    "blank-description",
    "fields-not-sequence",
    "fields-empty",
    "fields-malformed-pair",
    "fields-nonstring",
    "identifiers-not-sequence",
    "identifiers-malformed-pair",
    "identifiers-nonstring",
    "timeout-zero",
    "timeout-negative",
    "timeout-boolean",
    "timeout-not-number",
    "timeout-nan",
]
CREATE_MEMBERS = [
    "adapterKindKey",
    "collectorGroupId",
    "collectorId",
    "credential",
    "description",
    "monitoringInterval",
    "monitoringIntervalSeconds",
    "name",
    "physicalDatacenterId",
    "resourceIdentifiers",
]
CREDENTIAL_MEMBERS = [
    "adapterKindKey",
    "credentialKindKey",
    "editable",
    "fields",
    "id",
    "name",
]
UNSET_CREATE_MEMBERS = [
    "collectorGroupId",
    "collectorId",
    "monitoringInterval",
    "monitoringIntervalSeconds",
    "physicalDatacenterId",
]
FORBIDDEN_IMPORTS = {
    "requests",
    "httpx",
    "urllib3",
    "aiohttp",
    "httplib2",
    "pycurl",
    "websockets",
    "subprocess",
    "ctypes",
    "pip",
    "setuptools",
    "pkg_resources",
    "yaml",
    "pydantic",
    "attr",
    "attrs",
}
FORBIDDEN_TEXT = [
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "vmware.com",
    "os.system",
    "popen",
    "curl ",
    "wget ",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


# --------------------------------------------------------------------------
# contract projection
# --------------------------------------------------------------------------


def verify_contract() -> None:
    require(CONTRACT_PATH.is_file(), "docs/contract.json is missing")
    require(SOURCES_PATH.is_file(), "docs/official_sources.json is missing")
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)

    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "VCF Operations API version changed")
    require(
        source.get("apiTitle") == "VMware Cloud Foundation Operations API",
        "the contract must project the VCF Operations API, not log management",
    )
    require(source.get("serverBasePath") == BASE_PATH, "server base path changed")

    security = contract.get("security", {})
    require(
        security.get("schemeName") == "Token-based-authorization"
        and security.get("type") == "apiKey"
        and security.get("in") == "header"
        and security.get("headerName") == "Authorization"
        and security.get("tokenPrefix") == TOKEN_PREFIX,
        "contract security projection changed",
    )
    require(
        security.get("unauthenticatedOperationIds") == ["acquireToken"],
        "token acquisition must remain the only unauthenticated operation",
    )

    gate = contract.get("precheckGate", {})
    require(
        gate.get("prechecks") == ["enumerateAdapterInstances", "testConnection"],
        "contract precheck projection changed",
    )
    require(
        gate.get("mutatingOperationId") == "createAdapterInstance",
        "contract mutating operation changed",
    )

    encoding = contract.get("wireEncoding", {})
    require(
        encoding.get("jsonSeparators") == [",", ":"]
        and encoding.get("charset") == "utf-8"
        and encoding.get("requestContentType") == "application/json"
        and encoding.get("acceptHeader") == "application/json",
        "contract wire encoding changed",
    )

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract operationIds changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations] == ROUTES,
        "contract routes changed",
    )
    require(
        [item.get("authenticated") for item in operations]
        == [False, True, True, True],
        "contract authentication projection changed",
    )
    acquire, enumerate_op, test_op, create_op = operations

    require(
        acquire.get("requestBody", {}).get("schema") == "username-password"
        and acquire["requestBody"].get("required") is True
        and acquire["requestBody"].get("contentType") == "application/json",
        "acquireToken request body projection changed",
    )
    require(
        acquire.get("responses", {}).get("200", {}).get("schema") == "auth-token",
        "acquireToken success projection changed",
    )
    require(
        acquire.get("focusedWireProfile", {}).get("boundBodyMembers")
        == ["password", "username"]
        and acquire["focusedWireProfile"].get("conditionalBodyMembers")
        == ["authSource"]
        and acquire["focusedWireProfile"].get("unsetBehavior") == "omit"
        and acquire["focusedWireProfile"].get("sendsAuthorizationHeader") is False,
        "acquireToken wire profile changed",
    )

    require(
        enumerate_op.get("parameters")
        == [
            {
                "name": "adapterKindKey",
                "in": "query",
                "description": (
                    "If this parameter is specified, the API returns only "
                    "instances of this Adapter Kind."
                ),
                "required": False,
                "schema": {"type": "string", "default": ""},
            }
        ],
        "enumerateAdapterInstances query projection changed",
    )
    require(
        enumerate_op.get("requestBody") is False,
        "enumerateAdapterInstances must stay bodyless",
    )
    require(
        enumerate_op.get("responses", {}).get("200", {}).get("schema")
        == "adapter-instances",
        "enumerateAdapterInstances success projection changed",
    )

    for operation in (test_op, create_op):
        label = operation["operationId"]
        require(
            operation.get("requestBody", {}).get("schema")
            == "create-adapter-instance"
            and operation["requestBody"].get("required") is True,
            f"{label} request body projection changed",
        )
        require(
            operation.get("responses", {}).get("201", {}).get("schema")
            == "adapter-instance",
            f"{label} success projection changed",
        )
        require("400" in operation.get("responses", {}), f"{label} lost its 400 case")
        profile = operation.get("focusedWireProfile", {})
        require(
            profile.get("boundBodyMembers")
            == ["adapterKindKey", "credential", "name", "resourceIdentifiers"],
            f"{label} bound body members changed",
        )
        require(
            profile.get("conditionalBodyMembers") == ["description"],
            f"{label} conditional body members changed",
        )
        require(
            profile.get("unsetBodyMembers") == UNSET_CREATE_MEMBERS
            and profile.get("unsetBehavior") == "omit",
            f"{label} unset body members must be omitted",
        )
        nested = profile.get("nestedProfiles", [])
        require(len(nested) == 1, f"{label} nested credential profile changed")
        require(
            nested[0].get("schema") == "credential"
            and nested[0].get("boundMembers")
            == ["adapterKindKey", "credentialKindKey", "fields", "name"]
            and nested[0].get("unsetMembers") == ["editable", "id"]
            and nested[0].get("unsetBehavior") == "omit",
            f"{label} credential wire profile changed",
        )

    require(
        [item.get("name") for item in create_op.get("parameters", [])]
        == ["extractIdentifierDefaults", "force"],
        "createAdapterInstance query projection changed",
    )
    require(
        all(item.get("required") is False for item in create_op["parameters"]),
        "createAdapterInstance query members must stay optional",
    )
    require(
        create_op["parameters"][1].get("schema") == {"type": "boolean", "default": True},
        "the force default must remain true in the projection",
    )
    create_profile = create_op["focusedWireProfile"]
    require(
        create_profile.get("boundQueryMembers") == ["force"]
        and create_profile.get("boundQueryValues") == {"force": "false"}
        and create_profile.get("unsetQueryMembers") == ["extractIdentifierDefaults"],
        "createAdapterInstance query wire profile changed",
    )

    schemas = contract.get("schemas", {})
    require(
        list(schemas.get("username-password", {}).get("properties", {}))
        == ["authSource", "password", "username"],
        "username-password projection changed",
    )
    require(
        schemas.get("username-password", {}).get("required") == ["password", "username"],
        "username-password required members changed",
    )
    require(
        schemas.get("auth-token", {}).get("required") == ["token", "validity"],
        "auth-token required members changed",
    )
    require(
        list(schemas.get("create-adapter-instance", {}).get("properties", {}))
        == CREATE_MEMBERS,
        "create-adapter-instance projection changed",
    )
    require(
        schemas.get("create-adapter-instance", {}).get("required")
        == ["adapterKindKey", "name"],
        "create-adapter-instance required members changed",
    )
    require(
        list(schemas.get("credential", {}).get("properties", {}))
        == CREDENTIAL_MEMBERS,
        "credential projection changed",
    )
    require(
        list(schemas.get("name-value", {}).get("properties", {})) == ["name", "value"],
        "name-value projection changed",
    )
    require(
        schemas.get("adapter-instance", {}).get("required") == ["resourceKey"],
        "adapter-instance required members changed",
    )
    require(
        "adapterInstancesInfoDto" in schemas.get("adapter-instances", {}).get(
            "properties", {}
        ),
        "adapter-instances projection changed",
    )
    require("error" in schemas, "the error envelope must be projected")

    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source spec path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
        "the official source URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
                item.get("jsonPointer"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            (
                "acquireToken",
                "POST",
                "/api/auth/token/acquire",
                COMMIT,
                SPEC_PATH,
                "/paths/~1api~1auth~1token~1acquire/post/operationId",
            ),
            (
                "enumerateAdapterInstances",
                "GET",
                "/api/adapters",
                COMMIT,
                SPEC_PATH,
                "/paths/~1api~1adapters/get/operationId",
            ),
            (
                "testConnection",
                "POST",
                "/api/adapters/testConnection",
                COMMIT,
                SPEC_PATH,
                "/paths/~1api~1adapters~1testConnection/post/operationId",
            ),
            (
                "createAdapterInstance",
                "POST",
                "/api/adapters",
                COMMIT,
                SPEC_PATH,
                "/paths/~1api~1adapters/post/operationId",
            ),
        ],
        "each operation must repeat its exact pinned source location",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "a rendered documentation page must not be the contract source",
    )
    require(
        sources.get("derivation", {}).get("projectionFile") == "docs/contract.json",
        "official sources must point at the contract projection",
    )


# --------------------------------------------------------------------------
# solution shape
# --------------------------------------------------------------------------


def package_sources() -> list[Path]:
    return sorted(path for path in PACKAGE_DIR.rglob("*.py") if path.is_file())


def verify_solution_shape() -> None:
    require(PACKAGE_DIR.is_dir(), "create the vcfops_onboarding package")
    require(
        (PACKAGE_DIR / "__init__.py").is_file(),
        "vcfops_onboarding/__init__.py is missing",
    )
    modules = package_sources()
    require(modules, "the vcfops_onboarding package has no Python modules")

    joined = ""
    imports_json = False
    imports_urllib_request = False
    for path in modules:
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as error:
            raise VerificationError(f"{path.name} has a syntax error: {error}") from None
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                imports_json = imports_json or any(name == "json" for name in names)
                imports_urllib_request = imports_urllib_request or any(
                    name == "urllib.request" for name in names
                )
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [node.module or ""]
                imports_json = imports_json or node.module == "json"
                imports_urllib_request = imports_urllib_request or (
                    node.module == "urllib"
                    and any(alias.name == "request" for alias in node.names)
                )
            for name in names:
                root = name.split(".", 1)[0]
                if root == "vcfops_onboarding" or not root:
                    continue
                require(
                    root not in FORBIDDEN_IMPORTS,
                    f"{path.name} imports the forbidden module {root}",
                )
                require(
                    root in sys.stdlib_module_names,
                    f"{path.name} imports the non-stdlib module {root}",
                )
        joined += text + "\n"

    folded = joined.casefold()
    for forbidden in FORBIDDEN_TEXT:
        require(
            forbidden not in folded,
            f"the implementation must not hardcode or shell out via {forbidden!r}",
        )
    for operation_id in OPERATION_IDS:
        require(
            operation_id in joined,
            f"the implementation must name the contract operationId {operation_id}",
        )
    require(imports_json, "json must be the implementation's JSON transport")
    require(
        imports_urllib_request,
        "urllib.request must be the implementation's HTTP transport",
    )
    require(
        TOKEN_PREFIX in joined,
        "the implementation must send the contract authorization token prefix",
    )

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold()
        in {".so", ".dll", ".pyd", ".whl", ".tar", ".zip", ".nupkg"}
    ]
    require(not vendored, "the seed must not vendor binary or packaged dependencies")


# --------------------------------------------------------------------------
# runtime scenario
# --------------------------------------------------------------------------


def adapter_instance(
    name: str, adapter_kind: str, instance_id: str | None = None
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "resourceKey": {
            "name": name,
            "adapterKindKey": adapter_kind,
            "resourceKindKey": "VMwareAdapter Instance",
            "resourceIdentifiers": [],
        }
    }
    if instance_id is not None:
        entry["id"] = instance_id
    return entry


def make_case(
    key: str,
    outcome: str,
    marker: str,
    auth_source: str | None,
    description: str | None,
    *,
    omit_success_optionals: bool = False,
) -> dict[str, Any]:
    adapter_kind = "VMWARE"
    instance_name = f"VC Adapter {marker}"
    case: dict[str, Any] = {
        "key": key,
        "outcome": outcome,
        "username": f"svc-{marker}",
        "password": "pw-" + secrets.token_urlsafe(18),
        "authSource": auth_source,
        "token": secrets.token_urlsafe(24),
        "tokenValidity": 1786000000000 + len(marker),
        "tokenExpiresAt": "2026-08-04T18:00:00.000Z",
        "tokenRoles": ["ContentAdmin"],
        "adapterKindKey": adapter_kind,
        "instanceName": instance_name,
        "description": description,
        "credentialName": f"cred-{marker}",
        "credentialKindKey": "PrincipalCredential",
        "credentialFields": [
            ["USER", f"onboard-{marker}@example.test"],
            ["PASSWORD", "cred-" + secrets.token_urlsafe(14)],
        ],
        "resourceIdentifiers": [
            ["VCURL", f"vcenter-{marker}.example.test"],
            ["AUTODISCOVERY", "true"],
            ["PROCESSCHANGEEVENTS", "false"],
        ],
    }

    decoys = [
        adapter_instance(instance_name.upper(), adapter_kind, str(uuid.uuid4())),
        adapter_instance(instance_name + " ", adapter_kind, str(uuid.uuid4())),
    ]
    if outcome == "duplicate-name":
        first_conflicting_id = str(uuid.uuid4())
        second_conflicting_id = str(uuid.uuid4())
        inventory = decoys + [
            adapter_instance(instance_name, adapter_kind, first_conflicting_id),
            adapter_instance(instance_name, adapter_kind),
            adapter_instance(instance_name, adapter_kind, ""),
            adapter_instance(" " + instance_name, adapter_kind, str(uuid.uuid4())),
            adapter_instance(instance_name, adapter_kind, second_conflicting_id),
        ]
        case["conflictingInstanceIds"] = [
            first_conflicting_id,
            second_conflicting_id,
        ]
    else:
        inventory = decoys + [
            adapter_instance(
                instance_name.casefold(), adapter_kind, str(uuid.uuid4())
            )
        ]
        case["conflictingInstanceIds"] = []
    case["inventory"] = {"adapterInstancesInfoDto": inventory}
    case["existingInstanceCount"] = len(inventory)

    if outcome == "test-connection":
        case["testConnectionMessage"] = ""
        case["testedInstance"] = None
        case["createdInstance"] = None
        return case

    tested = adapter_instance(instance_name, adapter_kind)
    if not omit_success_optionals:
        tested["messageFromAdapterInstance"] = f"Connection verified for {marker}"
    case["testedInstance"] = tested

    created = adapter_instance(
        f"Stored {instance_name}", f"{adapter_kind}_SERVICE", str(uuid.uuid4())
    )
    if not omit_success_optionals:
        created["collectorId"] = 1 + len(marker) % 5
        created["credentialInstanceId"] = str(uuid.uuid4())
        created["description"] = f"stored description {marker}"
        created["messageFromAdapterInstance"] = "adapter instance created"
    case["createdInstance"] = created
    case["testConnectionMessage"] = ""
    return case


def make_scenario() -> dict[str, Any]:
    marker = secrets.token_hex(4)
    created = make_case(
        "created",
        "created",
        f"{marker}a",
        auth_source=None,
        description=f"vCenter onboarding {marker}a",
    )
    gated_test = make_case(
        "gate-test-connection",
        "test-connection",
        f"{marker}b",
        auth_source="Imported LDAP Server",
        description=None,
    )
    gated_test["resourceIdentifiers"] = []
    gated_test["useDefaultResourceIdentifiers"] = True
    gated_duplicate = make_case(
        "gate-duplicate-name",
        "duplicate-name",
        f"{marker}c",
        auth_source=f"AD-{marker}",
        description=f"vCenter onboarding {marker}c",
    )
    return {
        "mode": "gate-flow",
        "runValidation": True,
        "cases": [created, gated_test, gated_duplicate],
    }


def make_optional_success_scenario() -> dict[str, Any]:
    marker = secrets.token_hex(4)
    case = make_case(
        "created-with-omissions",
        "created",
        marker,
        auth_source=None,
        description=f"request description {marker}",
        omit_success_optionals=True,
    )
    case["credentialFields"][0] = ["", ""]
    case["inventory"]["adapterInstancesInfoDto"].extend(
        [
            adapter_instance(f"neighbour-{marker}-one", "VMWARE", str(uuid.uuid4())),
            adapter_instance(f"neighbour-{marker}-two", "VMWARE", str(uuid.uuid4())),
        ]
    )
    case["existingInstanceCount"] = len(
        case["inventory"]["adapterInstancesInfoDto"]
    )
    return {
        "mode": "optional-success",
        "cases": [case],
    }


def make_api_error_scenario() -> dict[str, Any]:
    marker = secrets.token_hex(4)
    statuses = [401, 404, 503, 400]
    cases = []
    for index, (operation_id, status) in enumerate(zip(OPERATION_IDS, statuses)):
        case = make_case(
            f"error-{operation_id}",
            "created",
            f"{marker}{index}",
            auth_source=None,
            description=None,
        )
        case["outcome"] = "api-error"
        case["failOperationId"] = operation_id
        case["failStatus"] = status
        case["failMessage"] = f"service failure {marker} for {operation_id}"
        cases.append(case)
    return {"mode": "api-errors", "cases": cases}


def expected_auth_body(case: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    if case["authSource"]:
        payload["authSource"] = case["authSource"]
    payload["password"] = case["password"]
    payload["username"] = case["username"]
    return compact(payload)


def expected_create_body(case: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "adapterKindKey": case["adapterKindKey"],
        "credential": {
            "adapterKindKey": case["adapterKindKey"],
            "credentialKindKey": case["credentialKindKey"],
            "fields": [
                {"name": name, "value": value}
                for name, value in case["credentialFields"]
            ],
            "name": case["credentialName"],
        },
    }
    if case["description"]:
        payload["description"] = case["description"]
    payload["name"] = case["instanceName"]
    payload["resourceIdentifiers"] = [
        {"name": name, "value": value} for name, value in case["resourceIdentifiers"]
    ]
    return compact(payload)


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise VerificationError(
                "loopback mock exited during startup: " + (stderr or stdout).strip()
            )
        if port_file.is_file():
            value = port_file.read_text(encoding="utf-8").strip()
            if value:
                return int(value)
        time.sleep(0.04)
    raise VerificationError("loopback mock did not publish its port")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_header(request: dict[str, Any], name: str) -> str:
    values = request.get("headerValues", {}).get(name.casefold(), [])
    require(len(values) == 1, f"header {name} must occur exactly once")
    return values[0]


def has_header(request: dict[str, Any], name: str) -> bool:
    return bool(request.get("headerValues", {}).get(name.casefold()))


def run_scenario(scenario: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0264-") as temporary:
        temp = Path(temporary)
        port_file = temp / "port.txt"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        result_file = temp / "result.json"
        scenario_file.write_text(compact(scenario), encoding="utf-8")
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(request_log),
                str(CONTRACT_PATH),
                str(scenario_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(port_file, server)
            invocation = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(RUNNER_PATH),
                    str(scenario_file),
                    f"http://127.0.0.1:{port}{BASE_PATH}/",
                    str(result_file),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=90,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            require(
                invocation.returncode == 0,
                "the solution package could not be driven: "
                + (invocation.stderr.strip() or invocation.stdout.strip()),
            )
            require(result_file.is_file(), "the case runner produced no result")
            report = load_json(result_file)
            diagnostics = invocation.stdout + invocation.stderr + compact(report)
            for case in scenario["cases"]:
                require(
                    case["password"] not in diagnostics,
                    "diagnostic output leaks the account password",
                )
                require(
                    case["token"] not in diagnostics,
                    "diagnostic output leaks the acquired token",
                )
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        return report, read_log(request_log)


def verify_report(scenario: dict[str, Any], report: dict[str, Any]) -> None:
    require(report.get("exports") == EXPORTS, "the package public surface changed")
    require(
        isinstance(report.get("packageFile"), str)
        and str(PACKAGE_DIR) in report["packageFile"],
        "vcfops_onboarding must be imported from the workspace package",
    )
    require(report.get("resultIsDataclass") is True, "OnboardingResult must be a dataclass")
    require(report.get("resultIsFrozen") is True, "OnboardingResult must be frozen")
    require(
        report.get("resultFieldOrder") == RESULT_FIELDS,
        "OnboardingResult field order changed",
    )
    require(
        report.get("precheckIsRuntimeError") is True
        and report.get("apiIsRuntimeError") is True,
        "both error types must derive from RuntimeError",
    )
    require(
        report.get("precheckIsApiError") is False,
        "PrecheckFailedError must be distinguishable from OperationsApiError",
    )

    validation = report.get("validation", [])
    require(
        [item.get("key") for item in validation] == VALIDATION_CASES,
        "the case runner did not exercise every declared input validation",
    )
    require(
        all(item.get("isValueError") is True for item in validation),
        "invalid input must raise ValueError before any request is sent",
    )

    cases = report.get("cases", [])
    require(
        [item.get("key") for item in cases]
        == [case["key"] for case in scenario["cases"]],
        "the case runner did not report every case",
    )
    created, gated_test, gated_duplicate = cases
    created_case, test_case, duplicate_case = scenario["cases"]

    require(
        created.get("kind") == "result",
        "the healthy onboarding failed: " + compact(created.get("error")),
    )
    require(
        created.get("resultIsOnboardingResult") is True,
        "the healthy onboarding returned the wrong type",
    )
    instance = created_case["createdInstance"]
    require(
        created.get("result")
        == {
            "name": instance["resourceKey"]["name"],
            "adapter_kind_key": instance["resourceKey"]["adapterKindKey"],
            "adapter_instance_id": instance["id"],
            "resource_kind_key": instance["resourceKey"]["resourceKindKey"],
            "collector_id": instance["collectorId"],
            "credential_instance_id": instance["credentialInstanceId"],
            "description": instance["description"],
            "existing_instance_count": created_case["existingInstanceCount"],
            "precheck_message": created_case["testedInstance"][
                "messageFromAdapterInstance"
            ],
        },
        "the onboarding result was guessed or did not read the service responses",
    )

    require(
        gated_test.get("kind") == "precheck",
        "a failing connection precheck must raise PrecheckFailedError, got "
        + compact(gated_test.get("error")),
    )
    error = gated_test["error"]
    require(error.get("stage") == "test-connection", "connection gate stage changed")
    require(
        error.get("reason") == test_case["testConnectionMessage"],
        "the refusal must carry the service error message verbatim",
    )
    require(error.get("statusCode") == 400, "connection gate status code changed")
    require(
        error.get("conflictingInstanceIds") == []
        and error.get("conflictingIsTuple") is True,
        "connection gate must report an empty conflicting-instance tuple",
    )

    require(
        gated_duplicate.get("kind") == "precheck",
        "a duplicate adapter name must raise PrecheckFailedError, got "
        + compact(gated_duplicate.get("error")),
    )
    error = gated_duplicate["error"]
    require(error.get("stage") == "duplicate-name", "duplicate gate stage changed")
    require(error.get("statusCode") is None, "the duplicate gate makes no failed call")
    require(
        error.get("conflictingInstanceIds")
        == duplicate_case["conflictingInstanceIds"],
        "the duplicate gate must report the exactly matching instance only",
    )
    require(
        error.get("conflictingIsTuple") is True,
        "conflicting_instance_ids must be a tuple",
    )
    require(
        isinstance(error.get("reason"), str)
        and duplicate_case["instanceName"] in error["reason"],
        "the duplicate refusal must name the requested adapter instance",
    )

    serialized = compact(report)
    for case in scenario["cases"]:
        require(
            case["password"] not in serialized,
            "diagnostic output leaks the account password",
        )
        require(
            case["token"] not in serialized,
            "diagnostic output leaks the acquired token",
        )


def verify_optional_success(
    scenario: dict[str, Any], report: dict[str, Any], requests: list[dict[str, Any]]
) -> None:
    case = scenario["cases"][0]
    records = report.get("cases", [])
    require(len(records) == 1, "the optional-response case was not reported")
    record = records[0]
    require(
        record.get("kind") == "result"
        and record.get("resultIsOnboardingResult") is True,
        "a successful response with omitted optional members must still succeed",
    )
    instance = case["createdInstance"]
    require(
        record.get("result")
        == {
            "name": instance["resourceKey"]["name"],
            "adapter_kind_key": instance["resourceKey"]["adapterKindKey"],
            "adapter_instance_id": instance["id"],
            "resource_kind_key": instance["resourceKey"]["resourceKindKey"],
            "collector_id": None,
            "credential_instance_id": None,
            "description": None,
            "existing_instance_count": case["existingInstanceCount"],
            "precheck_message": "",
        },
        "omitted optional response members must map to None or an empty precheck message",
    )
    require(
        [item.get("operationId") for item in requests] == OPERATION_IDS,
        "the optional-response success did not execute the gated operation sequence",
    )
    require(
        [item.get("responseStatus") for item in requests] == [200, 200, 201, 201],
        "the optional-response success changed a service outcome",
    )
    require(
        requests[2]["body"] == requests[3]["body"],
        "the optional-response create body differs from the tested body",
    )
    require(
        sum(item.get("operationId") == "createAdapterInstance" for item in requests)
        == 1,
        "the optional-response gate must mutate exactly once",
    )


def verify_api_errors(
    scenario: dict[str, Any], report: dict[str, Any], requests: list[dict[str, Any]]
) -> None:
    cases = scenario["cases"]
    records = report.get("cases", [])
    require(
        [item.get("key") for item in records] == [case["key"] for case in cases],
        "the API-error cases were not all reported",
    )
    for case, record in zip(cases, records):
        require(
            record.get("kind") == "api-error",
            f"{case['failOperationId']} did not raise OperationsApiError",
        )
        error = record.get("error", {})
        require(
            error.get("operationId") == case["failOperationId"]
            and error.get("statusCode") == case["failStatus"]
            and error.get("message") == case["failMessage"],
            f"{case['failOperationId']} did not preserve its API error fields",
        )

    expected_operations = [
        "acquireToken",
        "acquireToken",
        "enumerateAdapterInstances",
        "acquireToken",
        "enumerateAdapterInstances",
        "testConnection",
        "acquireToken",
        "enumerateAdapterInstances",
        "testConnection",
        "createAdapterInstance",
    ]
    require(
        [item.get("operationId") for item in requests] == expected_operations,
        "an API failure did not stop its onboarding immediately",
    )
    require(
        [item.get("responseStatus") for item in requests]
        == [401, 200, 404, 200, 200, 503, 200, 200, 201, 400],
        "the API-error service outcomes changed",
    )
    require(
        [item.get("sequence") for item in requests] == list(range(1, 11)),
        "the API-error request log is not a contiguous ordered record",
    )


def verify_wire(scenario: dict[str, Any], requests: list[dict[str, Any]]) -> None:
    created_case, test_case, duplicate_case = scenario["cases"]
    require(len(requests) == 9, f"expected exactly nine requests, saw {len(requests)}")
    require(
        [item.get("operationId") for item in requests]
        == [
            "acquireToken",
            "enumerateAdapterInstances",
            "testConnection",
            "createAdapterInstance",
            "acquireToken",
            "enumerateAdapterInstances",
            "testConnection",
            "acquireToken",
            "enumerateAdapterInstances",
        ],
        "operation sequence changed, an extra route was contacted, or the gate leaked",
    )
    require(
        [item.get("responseStatus") for item in requests]
        == [200, 200, 201, 201, 200, 200, 400, 200, 200],
        "the mock rejected a request or the gate outcome changed",
    )
    require(
        [item.get("sequence") for item in requests] == list(range(1, 10)),
        "the request log is not a contiguous ordered record",
    )

    create_targets = [
        item["rawTarget"]
        for item in requests
        if item["operationId"] == "createAdapterInstance"
    ]
    require(
        len(create_targets) == 1,
        "the mutating call must run exactly once, only for the healthy case",
    )

    expected_targets = [
        f"{BASE_PATH}/api/auth/token/acquire",
        f"{BASE_PATH}/api/adapters?adapterKindKey={created_case['adapterKindKey']}",
        f"{BASE_PATH}/api/adapters/testConnection",
        f"{BASE_PATH}/api/adapters?force=false",
        f"{BASE_PATH}/api/auth/token/acquire",
        f"{BASE_PATH}/api/adapters?adapterKindKey={test_case['adapterKindKey']}",
        f"{BASE_PATH}/api/adapters/testConnection",
        f"{BASE_PATH}/api/auth/token/acquire",
        f"{BASE_PATH}/api/adapters?adapterKindKey={duplicate_case['adapterKindKey']}",
    ]
    require(
        [item.get("rawTarget") for item in requests] == expected_targets,
        "exact raw request targets changed",
    )

    for index, request in enumerate(requests):
        require(
            one_header(request, "accept") == "application/json",
            f"request {index} must accept exactly application/json",
        )
        require(
            not has_header(request, "transfer-encoding"),
            f"request {index} must send a measured body, not a chunked one",
        )

    groups = [
        (created_case, requests[0:4]),
        (test_case, requests[4:7]),
        (duplicate_case, requests[7:9]),
    ]
    for case, group in groups:
        acquire = group[0]
        require(acquire["method"] == "POST", "acquireToken method changed")
        require(acquire["rawQuery"] == "", "acquireToken must send no query members")
        require(
            not has_header(acquire, "authorization"),
            "token acquisition is unauthenticated and must omit Authorization",
        )
        require(
            one_header(acquire, "content-type") == "application/json",
            "acquireToken Content-Type must be application/json",
        )
        expected_body = expected_auth_body(case)
        require(
            acquire["body"] == expected_body,
            f"acquireToken body bytes changed for case {case['key']}",
        )
        require(
            acquire["bodyLength"] == len(expected_body.encode("utf-8")),
            "acquireToken body length disagrees with its bytes",
        )
        require(
            int(one_header(acquire, "content-length")) == acquire["bodyLength"],
            "acquireToken Content-Length disagrees with its body",
        )
        parsed = json.loads(acquire["body"])
        if case["authSource"]:
            require(
                list(parsed) == ["authSource", "password", "username"],
                "bound authSource must keep contract member order",
            )
        else:
            require(
                list(parsed) == ["password", "username"],
                "an unset authSource must be omitted, not sent empty or null",
            )
            require(
                "authSource" not in acquire["body"],
                "an unset authSource must not appear on the wire at all",
            )

        listing = group[1]
        require(listing["method"] == "GET", "enumerateAdapterInstances method changed")
        require(listing["bodyLength"] == 0 and listing["body"] == "",
                "enumerateAdapterInstances must be bodyless")
        require(
            not has_header(listing, "content-type"),
            "a bodyless request must omit Content-Type",
        )
        require(
            listing["query"] == {"adapterKindKey": [case["adapterKindKey"]]},
            "the adapter listing must filter on the adapter kind only",
        )
        require(
            one_header(listing, "authorization")
            == f"{TOKEN_PREFIX} {case['token']}",
            "the acquired token must be replayed with the contract prefix",
        )

        for request in group[1:]:
            require(
                one_header(request, "authorization")
                == f"{TOKEN_PREFIX} {case['token']}",
                "every authenticated request must carry the acquired token",
            )

        if len(group) < 3:
            continue

        test = group[2]
        require(test["method"] == "POST", "testConnection method changed")
        require(test["rawQuery"] == "", "testConnection takes no query members")
        require(
            one_header(test, "content-type") == "application/json",
            "testConnection Content-Type must be application/json",
        )
        expected_body = expected_create_body(case)
        require(
            test["body"] == expected_body,
            f"testConnection body bytes changed for case {case['key']}",
        )
        require(
            int(one_header(test, "content-length")) == test["bodyLength"],
            "testConnection Content-Length disagrees with its body",
        )
        payload = json.loads(test["body"])
        expected_members = ["adapterKindKey", "credential"]
        if case["description"]:
            expected_members.append("description")
        expected_members += ["name", "resourceIdentifiers"]
        require(
            list(payload) == expected_members,
            "create-adapter-instance members changed or lost contract order",
        )
        for member in UNSET_CREATE_MEMBERS:
            require(
                member not in payload,
                f"unset optional member {member} was serialized",
            )
        if not case["description"]:
            require(
                "description" not in payload and "description" not in test["body"],
                "an unset description must be omitted, not sent empty or null",
            )
        credential = payload["credential"]
        require(
            list(credential)
            == ["adapterKindKey", "credentialKindKey", "fields", "name"],
            "credential members changed or lost contract order",
        )
        require(
            "id" not in credential and "editable" not in credential,
            "unset credential members must be omitted on a creation request",
        )
        require(
            [(item["name"], item["value"]) for item in credential["fields"]]
            == [tuple(pair) for pair in case["credentialFields"]],
            "credential field order must follow the caller, not be sorted",
        )
        require(
            [(item["name"], item["value"]) for item in payload["resourceIdentifiers"]]
            == [tuple(pair) for pair in case["resourceIdentifiers"]],
            "resource identifier order must follow the caller, not be sorted",
        )
        require(
            all(list(item) == ["name", "value"] for item in credential["fields"]),
            "name-value members changed or lost contract order",
        )

        if len(group) < 4:
            continue

        create = group[3]
        require(create["method"] == "POST", "createAdapterInstance method changed")
        require(
            create["rawQuery"] == "force=false",
            "createAdapterInstance must bind force=false and omit "
            "extractIdentifierDefaults",
        )
        require(
            "extractIdentifierDefaults" not in create["rawTarget"],
            "an unset optional query member must not appear on the wire",
        )
        require(
            create["body"] == test["body"],
            "the created body must be byte-identical to the tested body",
        )
        require(
            int(one_header(create, "content-length")) == create["bodyLength"],
            "createAdapterInstance Content-Length disagrees with its body",
        )


def verify_runtime() -> None:
    scenario = make_scenario()
    report, requests = run_scenario(scenario)
    verify_report(scenario, report)
    verify_wire(scenario, requests)

    optional_scenario = make_optional_success_scenario()
    optional_report, optional_requests = run_scenario(optional_scenario)
    verify_optional_success(optional_scenario, optional_report, optional_requests)

    error_scenario = make_api_error_scenario()
    error_report, error_requests = run_scenario(error_scenario)
    verify_api_errors(error_scenario, error_report, error_requests)


def main() -> int:
    try:
        verify_contract()
        verify_solution_shape()
        verify_runtime()
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
