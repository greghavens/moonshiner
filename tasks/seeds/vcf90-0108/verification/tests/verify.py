#!/usr/bin/env python3
"""Protected deterministic verification for vcf90-0108."""

from __future__ import annotations

import ast
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

from test_support.mock_vcf_installer import ContractMockServer


EXPECTED_SOURCES = {
    "schemaVersion": 1,
    "repository": "vmware/vcf-api-specs",
    "repositoryUrl": "https://github.com/vmware/vcf-api-specs",
    "tag": "9.0.0.0",
    "repositoryCommitSha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
    "license": "Apache-2.0",
    "specPath": "specifications/vcf-installer/vcf-installer-openapi.json",
    "specUrl": "https://raw.githubusercontent.com/vmware/vcf-api-specs/85151f6b1bb58f13b6ac0304bfec53904bea085f/specifications/vcf-installer/vcf-installer-openapi.json",
    "operationIds": ["updateDepotSettings"],
    "operations": [
        {
            "operationId": "updateDepotSettings",
            "method": "PUT",
            "path": "/v1/system/settings/depot",
            "specJsonPointer": "/paths/~1v1~1system~1settings~1depot/put/operationId",
            "tag": "9.0.0.0",
            "repositoryCommitSha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
            "specPath": "specifications/vcf-installer/vcf-installer-openapi.json",
        }
    ],
    "derivation": {
        "kind": "focused projection of the pinned OpenAPI JSON",
        "documentationPageUsedAsContractSource": False,
    },
}

EXPECTED_CONTRACT = {
    "contractFormat": "focused-openapi-projection-v1",
    "source": {
        "repository": "vmware/vcf-api-specs",
        "tag": "9.0.0.0",
        "repositoryCommitSha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
        "specPath": "specifications/vcf-installer/vcf-installer-openapi.json",
        "license": "Apache-2.0",
        "openapi": "3.0.1",
        "apiVersion": "9.0.0.0",
    },
    "operations": [
        {
            "operationId": "updateDepotSettings",
            "method": "PUT",
            "path": "/v1/system/settings/depot",
            "summary": "Configure the depot credentials",
            "parameters": [],
            "requestBody": {
                "required": True,
                "contentType": "application/json",
                "schema": "DepotSettings",
            },
            "responses": {
                "202": {
                    "description": "Accepted",
                    "contentType": "application/json",
                    "schema": "DepotSettings",
                },
                "400": {
                    "description": "Bad Request",
                    "contentType": "application/json",
                    "schema": "Error",
                },
                "500": {
                    "description": "Internal Server Error",
                    "contentType": "application/json",
                    "schema": "Error",
                },
            },
        }
    ],
    "schemas": {
        "DepotAccount": {
            "type": "object",
            "required": [],
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
                "status": {"type": "string"},
                "message": {"type": "string"},
                "downloadToken": {"type": "string", "maxLength": 32},
            },
        },
        "DepotConfiguration": {
            "type": "object",
            "required": ["hostname", "isOfflineDepot", "port"],
            "properties": {
                "isOfflineDepot": {"type": "boolean"},
                "hostname": {"type": "string"},
                "port": {"type": "integer", "format": "int32"},
            },
        },
        "DepotSettings": {
            "type": "object",
            "required": [],
            "properties": {
                "vmwareAccount": {"$ref": "DepotAccount"},
                "dellEmcSupportAccount": {"$ref": "DepotAccount"},
                "offlineAccount": {"$ref": "DepotAccount"},
                "depotConfiguration": {"$ref": "DepotConfiguration"},
            },
            "specConstraint": "At least one of vmwareAccount, dellEmcSupportAccount or offlineAccount must be provided.",
        },
    },
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_contract() -> None:
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())
    contract = json.loads((ROOT / "docs" / "contract.json").read_text())
    check(sources == EXPECTED_SOURCES, "official source provenance is not exact")
    check(contract == EXPECTED_CONTRACT, "focused 9.0 contract is not exact")
    check("9.1" not in json.dumps((sources, contract)), "9.1 material is not allowed")


def verify_stdlib_only() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    check(project["project"].get("dependencies") == [], "runtime dependencies must be empty")
    allowed = set(sys.stdlib_module_names)
    for source in (ROOT / "src" / "vcf_installer").glob("*.py"):
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
            check(
                roots <= allowed | {"vcf_installer"},
                f"non-stdlib import in {source.name}: {roots}",
            )


def verify_wire_retry_and_effect() -> None:
    from vcf_installer import VCFInstallerClient

    token = "0123456789abcdef0123456789abcdef"
    expected_payload = {"vmwareAccount": {"downloadToken": token}}
    expected_body = json.dumps(
        expected_payload, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")

    with ContractMockServer(ROOT / "docs" / "contract.json") as server:
        client = VCFInstallerClient(server.base_url, timeout=2)
        result = client.set_depot_download_token(token)
        log = list(server.request_log)

        check(result == expected_payload, "decoded 202 response was not returned")
        check(server.settings == expected_payload, "depot state was not updated exactly")
        check(server.effect_count == 1, "the replay duplicated the settings effect")
        check(len(log) == 2, "the lost PUT response must be retried")
        check(
            [(item["method"], item["target"]) for item in log]
            == [
                ("PUT", "/v1/system/settings/depot"),
                ("PUT", "/v1/system/settings/depot"),
            ],
            "retry method or target is not exact",
        )
        check(
            all(item["body"] == expected_body for item in log),
            "the PUT and replay must use identical compact JSON bytes",
        )
        check(
            log[0]["headers"] == log[1]["headers"],
            "the PUT and replay must use identical headers",
        )
        for item in log:
            check(
                item["headers"].get("accept") == "application/json",
                "Accept must be application/json",
            )
            check(
                item["headers"].get("content-type") == "application/json",
                "Content-Type must be application/json",
            )

        sent = json.loads(log[0]["body"])
        check(set(sent) == {"vmwareAccount"}, "unset DepotSettings fields were sent")
        check(
            set(sent["vmwareAccount"]) == {"downloadToken"},
            "unset DepotAccount fields were sent",
        )

        unknown = Request(
            server.base_url + "/v1/system/settings/depot",
            method="GET",
        )
        try:
            urlopen(unknown, timeout=2)
        except HTTPError as error:
            check(error.code == 404, "unnamed mock operation must return 404")
        except RemoteDisconnected as error:
            raise AssertionError("unnamed operation closed the connection") from error
        else:
            raise AssertionError("mock served an operation outside the contract")


def verify_optional_fields() -> None:
    from vcf_installer import VCFInstallerClient

    cases = [
        ({"username": "depot-user"}, {"username": "depot-user"}),
        ({"password": ""}, {"password": ""}),
    ]
    for arguments, optional_fields in cases:
        token = "fedcba9876543210fedcba9876543210"
        account = {"downloadToken": token, **optional_fields}
        expected_payload = {"vmwareAccount": account}
        expected_body = json.dumps(
            expected_payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

        with ContractMockServer(
            ROOT / "docs" / "contract.json",
            drop_first_response=False,
        ) as server:
            client = VCFInstallerClient(server.base_url, timeout=2)
            result = client.set_depot_download_token(token, **arguments)
            log = list(server.request_log)

            check(result == expected_payload, "optional credentials changed the response")
            check(len(log) == 1, "a received response must not be replayed")
            check(log[0]["body"] == expected_body, "optional credentials were not exact")
            check(server.settings == expected_payload, "optional credentials were not applied")


def verify_response_handling() -> None:
    from vcf_installer import VCFInstallerClient

    token = "0123456789abcdef0123456789abcdef"

    for status in (200, 400):
        with ContractMockServer(
            ROOT / "docs" / "contract.json",
            drop_first_response=False,
            response_status=status,
        ) as server:
            client = VCFInstallerClient(server.base_url, timeout=2)
            try:
                client.set_depot_download_token(token)
            except Exception:
                pass
            else:
                raise AssertionError(f"HTTP {status} must not be accepted as success")
            check(
                len(server.request_log) == 1,
                f"HTTP {status} response must not be retried",
            )

    with ContractMockServer(
        ROOT / "docs" / "contract.json",
        drop_first_response=False,
        response_payload=[],
    ) as server:
        client = VCFInstallerClient(server.base_url, timeout=2)
        try:
            client.set_depot_download_token(token)
        except Exception:
            pass
        else:
            raise AssertionError("a non-object JSON response must not be returned")
        check(len(server.request_log) == 1, "a received JSON response must not be retried")


def main() -> None:
    verify_contract()
    verify_stdlib_only()
    verify_wire_retry_and_effect()
    verify_optional_fields()
    verify_response_handling()
    print("vcf90-0108 verification passed")


if __name__ == "__main__":
    main()
