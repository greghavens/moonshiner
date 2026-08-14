#!/usr/bin/env python3
"""Deterministic acceptance verifier; it never contacts a VMware endpoint."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from mock_server import create_server


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "src" / "VcfOperationsNetworks" / "VcfOperationsNetworks.psd1"
EXERCISE_PATH = ROOT / "tests" / "exercise.ps1"

EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
EXPECTED_OPERATIONS = [
    ("listApplications", "GET", "/groups/applications"),
    ("addApplication", "POST", "/groups/applications"),
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_contract() -> tuple[dict, str, str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    require(contract["openapi"] == "3.0.1", "contract must retain the source OpenAPI version")
    require(contract["info"]["version"] == "9.0.0.0", "contract must target VCF 9.0.0.0")
    require(sources["tag"] == "9.0.0.0", "official source tag changed")
    require(sources["commit"] == EXPECTED_COMMIT, "official source commit changed")
    require(sources["specPath"] == EXPECTED_SPEC_PATH, "official specification path changed")
    require(sources["license"] == "Apache-2.0", "official source license changed")

    source_operations = [
        (item["operationId"], item["method"], item["path"])
        for item in sources["operations"]
    ]
    require(source_operations == EXPECTED_OPERATIONS, "official operation list changed")

    contract_operations = []
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                contract_operations.append((operation["operationId"], method.upper(), path))
    require(contract_operations == EXPECTED_OPERATIONS, "mock contract exposes unexpected operations")

    get_operation = contract["paths"]["/groups/applications"]["get"]
    optional_names = [parameter["name"] for parameter in get_operation["parameters"]]
    require(optional_names == ["size", "cursor", "modifiedAfter"], "optional query contract changed")
    request_schema = contract["components"]["schemas"]["ApplicationRequest"]
    require(request_schema["required"] == ["name"], "application required fields changed")
    require(list(request_schema["properties"]) == ["name"], "application request fields changed")
    security = contract["components"]["securitySchemes"]["ApiKeyAuth"]
    require(
        security == {
            "type": "apiKey",
            "description": "API Key - NetworkInsight {token}",
            "name": "Authorization",
            "in": "header",
        },
        "authorization contract changed",
    )
    cursor = contract["components"]["schemas"]["PagedListResponse"]["properties"]["cursor"]["example"]
    return contract, contract["servers"][0]["url"] + "/groups/applications", cursor


def run_exercise(
    server,
    result_path: Path,
    name: str,
    token: str,
) -> tuple[subprocess.CompletedProcess[str], dict | None, list[dict]]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(EXERCISE_PATH),
                "-ModulePath",
                str(MANIFEST_PATH),
                "-Server",
                f"http://127.0.0.1:{server.server_port}",
                "-ResultPath",
                str(result_path),
                "-Name",
                name,
                "-Token",
                token,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
    records = (
        [json.loads(line) for line in server.log_path.read_text(encoding="utf-8").splitlines()]
        if server.log_path.exists()
        else []
    )
    return completed, result, records


def validate_wire(
    records: list[dict],
    collection_target: str,
    cursor: str,
    name: str,
    token: str,
) -> None:
    encoded_cursor = urlencode({"cursor": cursor})
    expected_next_target = f"{collection_target}?{encoded_cursor}"
    expected_body = json.dumps({"name": name}, separators=(",", ":"))

    for item in records:
        parsed = urlsplit(item["target"])
        require(parsed.path == collection_target, f"wrong request path: {item['target']}")
        require(
            item["headers"].get("authorization") == f"NetworkInsight {token}",
            "Authorization header has the wrong wire value",
        )
        if item["method"] == "GET":
            require(
                item["target"] in {collection_target, expected_next_target},
                f"list query is not contract-exact: {item['target']}",
            )
            if item["target"] == collection_target:
                require("?" not in item["target"], "unset cursor must omit the query marker")
            else:
                require(parsed.query == encoded_cursor, "cursor query wire encoding is not exact")
                require(
                    parse_qs(parsed.query, keep_blank_values=True) == {"cursor": [cursor]},
                    "cursor value changed",
                )
            require("size" not in parsed.query, "unset size query field must be omitted")
            require("modifiedAfter" not in parsed.query, "unset modifiedAfter query field must be omitted")
        elif item["method"] == "POST":
            require(item["target"] == collection_target, "create target is not exact")
            require(item["body"] == expected_body, "create JSON wire body is not exact")
            require(
                item["headers"].get("content-type") == "application/json",
                "create Content-Type must be exactly application/json",
            )
            require(set(json.loads(item["body"])) == {"name"}, "unset request fields must be omitted")


def validate_success_result(
    result: dict | None,
    entity_id: str,
    name: str,
    first_created: bool,
) -> None:
    require(result is not None, "successful exercise did not write a result")
    require(result["DependencyLoaded"] is True, "VMware.Sdk.Vcf.Ops was not loaded")
    require(result["First"]["EntityId"] == entity_id, "first call returned wrong entity")
    require(result["First"]["Name"] == name, "first call returned wrong name")
    require(result["First"]["Created"] is first_created, "first call returned wrong Created state")
    require(result["Second"]["EntityId"] == entity_id, "second call returned wrong entity")
    require(result["Second"]["Name"] == name, "second call returned wrong name")
    require(result["Second"]["Created"] is False, "second call must report Created=false")


def run_acceptance() -> None:
    _contract, collection_target, cursor = validate_contract()
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    require("VMware.Sdk.Vcf.Ops" in manifest, "module must retain the VMware.Sdk.Vcf.Ops prerequisite")

    with tempfile.TemporaryDirectory(prefix="vcf90-0129-") as temp_dir:
        temp = Path(temp_dir)

        dropped_name = 'Payments "Edge" \\ Gateway'
        dropped_token = "dropped-response-token"
        dropped = create_server(CONTRACT_PATH, temp / "dropped.jsonl")
        completed, result, records = run_exercise(
            dropped,
            temp / "dropped-result.json",
            dropped_name,
            dropped_token,
        )
        require(completed.returncode == 0, f"lost-response exercise failed:\n{completed.stdout}")
        validate_success_result(result, "application-0001", dropped_name, True)
        validate_wire(records, collection_target, cursor, dropped_name, dropped_token)
        require(
            [(item["operationId"], item["method"]) for item in records]
            == [
                ("listApplications", "GET"),
                ("listApplications", "GET"),
                ("addApplication", "POST"),
                ("listApplications", "GET"),
                ("listApplications", "GET"),
                ("listApplications", "GET"),
                ("listApplications", "GET"),
            ],
            "lost-response reconciliation sequence is incorrect",
        )
        require(len(dropped.applications) == 1, "lost-response retry duplicated the application")

        direct_name = "Inventory Application"
        direct_token = "direct-create-token"
        direct = create_server(
            CONTRACT_PATH,
            temp / "direct.jsonl",
            drop_first_create_response=False,
        )
        completed, result, records = run_exercise(
            direct,
            temp / "direct-result.json",
            direct_name,
            direct_token,
        )
        require(completed.returncode == 0, f"direct-create exercise failed:\n{completed.stdout}")
        validate_success_result(result, "application-0001", direct_name, True)
        validate_wire(records, collection_target, cursor, direct_name, direct_token)
        require(
            [(item["operationId"], item["method"]) for item in records]
            == [
                ("listApplications", "GET"),
                ("listApplications", "GET"),
                ("addApplication", "POST"),
                ("listApplications", "GET"),
                ("listApplications", "GET"),
            ],
            "direct-create or idempotent retry sequence is incorrect",
        )
        require(len(direct.applications) == 1, "direct-create retry duplicated the application")

        existing_name = "Existing Application"
        existing_token = "preflight-token"
        existing = create_server(
            CONTRACT_PATH,
            temp / "existing.jsonl",
            drop_first_create_response=False,
            first_page_contains_applications=True,
        )
        existing.applications.append(
            {
                "entity_id": "application-existing",
                "entity_type": "Application",
                "entity_name": existing_name,
            }
        )
        completed, result, records = run_exercise(
            existing,
            temp / "existing-result.json",
            existing_name,
            existing_token,
        )
        require(completed.returncode == 0, f"pre-existing exercise failed:\n{completed.stdout}")
        validate_success_result(result, "application-existing", existing_name, False)
        validate_wire(records, collection_target, cursor, existing_name, existing_token)
        require(
            [(item["operationId"], item["method"]) for item in records]
            == [("listApplications", "GET"), ("listApplications", "GET")],
            "first-page preflight must stop without creating",
        )
        require(len(existing.applications) == 1, "pre-existing application was duplicated")

        rejected_name = "Rejected Application"
        rejected_token = "rejected-create-token"
        rejected = create_server(
            CONTRACT_PATH,
            temp / "rejected.jsonl",
            drop_first_create_response=False,
            reject_create=True,
        )
        completed, result, records = run_exercise(
            rejected,
            temp / "rejected-result.json",
            rejected_name,
            rejected_token,
        )
        require(completed.returncode != 0, "unreconciled create failure must be rethrown")
        require(result is None, "failed create unexpectedly returned a result")
        require(
            "fixture rejected create" in completed.stdout,
            "the original create failure was not preserved",
        )
        validate_wire(records, collection_target, cursor, rejected_name, rejected_token)
        require(
            [(item["operationId"], item["method"]) for item in records]
            == [
                ("listApplications", "GET"),
                ("listApplications", "GET"),
                ("addApplication", "POST"),
                ("listApplications", "GET"),
                ("listApplications", "GET"),
            ],
            "failed create was not reconciled exactly once",
        )
        require(not rejected.applications, "rejected create mutated server state")


if __name__ == "__main__":
    try:
        run_acceptance()
    except Exception as error:  # verifier reports one stable failure line
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: contract, exact wire shape, create paths, reconciliation, and idempotency verified")
