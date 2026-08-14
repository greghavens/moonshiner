#!/usr/bin/env python3
"""Deterministic verifier; all HTTP traffic stays on the loopback fixture."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.contract_mock import RunningContractMock  # noqa: E402
from vcf_operations_networks import VcfOperationsForNetworksClient  # noqa: E402


EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
CLIENT_SOURCE = ROOT / "src" / "vcf_operations_networks" / "client.py"


def assert_request(
    entry: dict[str, Any],
    *,
    method: str,
    operation_id: str,
    path: str,
    authorization: str | None,
    query: dict[str, str],
) -> None:
    split = urlsplit(entry["target"])
    pairs = parse_qsl(split.query, keep_blank_values=True)
    assert len(pairs) == len(dict(pairs)), entry
    assert entry["method"] == method, entry
    assert entry["operationId"] == operation_id, entry
    assert split.path == path, entry
    assert dict(pairs) == query, entry
    assert entry["authorization"] == authorization, entry
    assert entry["accept"] == "application/json", entry
    if method == "GET":
        assert entry["content_type"] is None, entry
        assert entry["body"] == b"", entry


def verify_provenance_and_contract() -> None:
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))

    assert sources["tag"] == "9.0.0.0"
    assert sources["commit_sha"] == EXPECTED_SHA
    assert sources["specification_path"] == EXPECTED_SPEC
    assert sources["license"] == "Apache-2.0"

    expected_operations = [
        {"operationId": "create", "method": "POST", "path": "/auth/token"},
        {"operationId": "listVms", "method": "GET", "path": "/entities/vms"},
    ]
    assert sources["operations"] == expected_operations
    assert [
        {key: operation[key] for key in ("operationId", "method", "path")}
        for operation in contract["operations"]
    ] == expected_operations
    assert contract["server_base_path"] == "/api/ni"
    list_contract = contract["operations"][1]
    assert [parameter["name"] for parameter in list_contract["query_parameters"]] == [
        "size",
        "cursor",
        "start_time",
        "end_time",
    ]
    assert contract["security_schemes"]["ApiKeyAuth"] == {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
        "value_format": "NetworkInsight {token}",
    }


def verify_public_signatures_and_standard_library_only() -> None:
    assert str(inspect.signature(VcfOperationsForNetworksClient.__init__)) == (
        "(self, base_url: 'str', username: 'str', password: 'str', *, "
        "domain: 'dict[str, Any] | None' = None, timeout: 'float' = 5.0) -> 'None'"
    )
    assert str(inspect.signature(VcfOperationsForNetworksClient.list_vms)) == (
        "(self, *, size: 'int | float | None' = None, "
        "start_time: 'int | float | None' = None, "
        "end_time: 'int | float | None' = None) -> 'list[dict[str, Any]]'"
    )

    tree = ast.parse(CLIENT_SOURCE.read_text(encoding="utf-8"), filename=str(CLIENT_SOURCE))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots <= sys.stdlib_module_names, imported_roots - sys.stdlib_module_names


def verify_expiry_resume_and_wire_shape() -> None:
    with RunningContractMock("expiry") as mock:
        assert mock.server_address[0] == "127.0.0.1"
        client = VcfOperationsForNetworksClient(
            mock.base_url,
            "admin@example.test",
            "correct horse battery staple",
        )
        results = client.list_vms(size=2)

        assert [result["entity_id"] for result in results] == [
            "18230:1:alpha",
            "18230:1:beta",
            "18230:1:gamma",
        ]
        assert mock.state.expired_once
        assert mock.state.issued_tokens == 2

        log = mock.state.log
        assert len(log) == 5, log
        for index in (0, 3):
            assert_request(
                log[index],
                method="POST",
                operation_id="create",
                path="/api/ni/auth/token",
                authorization=None,
                query={},
            )
            assert log[index]["content_type"] == "application/json"
            assert json.loads(log[index]["body"]) == {
                "username": "admin@example.test",
                "password": "correct horse battery staple",
            }

        assert_request(
            log[1],
            method="GET",
            operation_id="listVms",
            path="/api/ni/entities/vms",
            authorization="NetworkInsight token-1",
            query={"size": "2"},
        )
        interrupted_query = {"size": "2", "cursor": "cursor-page-2"}
        assert_request(
            log[2],
            method="GET",
            operation_id="listVms",
            path="/api/ni/entities/vms",
            authorization="NetworkInsight token-1",
            query=interrupted_query,
        )
        assert_request(
            log[4],
            method="GET",
            operation_id="listVms",
            path="/api/ni/entities/vms",
            authorization="NetworkInsight token-2",
            query=interrupted_query,
        )


def verify_optional_arguments_and_encoding() -> None:
    domain = {"domain_type": "LDAP", "value": "west/ops + platform"}
    with RunningContractMock("optionals") as mock:
        client = VcfOperationsForNetworksClient(
            mock.base_url,
            'ops "lead"',
            "snowman ☃ and slash \\",
            domain=domain,
        )
        results = client.list_vms(
            size=1.5,
            start_time=1700000000,
            end_time=1700000999.25,
        )

        assert [result["entity_id"] for result in results] == [
            "18230:1:delta",
            "18230:1:epsilon",
        ]
        log = mock.state.log
        assert len(log) == 3, log
        assert_request(
            log[0],
            method="POST",
            operation_id="create",
            path="/api/ni/auth/token",
            authorization=None,
            query={},
        )
        assert log[0]["content_type"] == "application/json"
        assert json.loads(log[0]["body"]) == {
            "username": 'ops "lead"',
            "password": "snowman ☃ and slash \\",
            "domain": domain,
        }
        common_query = {
            "size": "1.5",
            "start_time": "1700000000",
            "end_time": "1700000999.25",
        }
        assert_request(
            log[1],
            method="GET",
            operation_id="listVms",
            path="/api/ni/entities/vms",
            authorization="NetworkInsight token-1",
            query=common_query,
        )
        assert "cursor" not in dict(parse_qsl(urlsplit(log[1]["target"]).query))
        assert_request(
            log[2],
            method="GET",
            operation_id="listVms",
            path="/api/ni/entities/vms",
            authorization="NetworkInsight token-1",
            query={**common_query, "cursor": "next/+?=& segment"},
        )


def verify_all_optional_arguments_are_omitted() -> None:
    with RunningContractMock("omission") as mock:
        client = VcfOperationsForNetworksClient(mock.base_url, "user", "password")
        assert client.list_vms() == []

        log = mock.state.log
        assert len(log) == 2, log
        authentication = json.loads(log[0]["body"])
        assert authentication == {"username": "user", "password": "password"}
        assert "domain" not in authentication
        assert_request(
            log[1],
            method="GET",
            operation_id="listVms",
            path="/api/ni/entities/vms",
            authorization="NetworkInsight token-1",
            query={},
        )
        assert log[1]["target"] == "/api/ni/entities/vms"


def main() -> None:
    verify_provenance_and_contract()
    verify_public_signatures_and_standard_library_only()
    verify_expiry_resume_and_wire_shape()
    verify_optional_arguments_and_encoding()
    verify_all_optional_arguments_are_omitted()
    print("verification passed")


if __name__ == "__main__":
    main()
