#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0175."""

from __future__ import annotations

import ast
import json
import math
import sys
import tomllib
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_vcf import CONTRACT_PATH, VCFLogManagementMock  # noqa: E402
from vcf_ops_logs import (  # noqa: E402
    AgentGroup,
    ApiError,
    LogManagementClient,
    ResponseContractError,
)


SOURCES_PATH = ROOT / "docs" / "official_sources.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = ["getAllAgentGroupConfig"]
ROUTE = "/api/v2/agent/groups"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_contract_is_a_pinned_projection_of_the_official_spec(self) -> None:
        contract = load_json(CONTRACT_PATH)
        sources = load_json(SOURCES_PATH)
        source = contract["source"]

        self.assertEqual(source["kind"], "pinned-openapi-specification")
        self.assertEqual(source["repository"], "vmware/vcf-api-specs")
        self.assertEqual(source["repositoryCommitSha"], COMMIT)
        self.assertEqual(source["specPath"], SPEC_PATH)
        self.assertEqual(source["specBlobSha"], SPEC_BLOB)
        self.assertEqual(source["license"], "Apache-2.0")
        self.assertEqual(source["openapi"], "3.0.1")
        self.assertEqual(source["apiVersion"], "9.1.0.0")
        self.assertEqual(source["title"], "Log Management API")
        self.assertEqual(
            source["serverUrlInSpecification"], "http://localhost:8787"
        )
        self.assertEqual(
            contract["securitySchemes"]["OPSTokenAuthorization"],
            {
                "type": "apiKey",
                "in": "header",
                "name": "X-JWT-Token",
            },
        )

        operations = contract["operations"]
        self.assertEqual(
            [item["operationId"] for item in operations], OPERATION_IDS
        )
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(
            (operation["method"], operation["path"]), ("GET", ROUTE)
        )
        self.assertIs(operation["requestBody"], False)
        self.assertEqual(operation["security"], ["OPSTokenAuthorization"])
        self.assertEqual(len(operation["parameters"]), 1)
        pageable = operation["parameters"][0]
        self.assertEqual(
            {
                "name": pageable["name"],
                "in": pageable["in"],
                "required": pageable["required"],
                "schema": pageable["schema"],
                "style": pageable["style"],
                "explode": pageable["explode"],
                "focusedMembers": pageable["focusedMembers"],
                "focusedWireOrder": pageable["focusedWireOrder"],
                "unsetMembers": pageable["unsetMembers"],
                "unsetBehavior": pageable["unsetBehavior"],
            },
            {
                "name": "pageable",
                "in": "query",
                "required": True,
                "schema": "Pageable",
                "style": "form",
                "explode": True,
                "focusedMembers": ["page", "size"],
                "focusedWireOrder": ["page", "size"],
                "unsetMembers": ["sort"],
                "unsetBehavior": "omit",
            },
        )
        self.assertEqual(
            operation["responses"]["200"]["schema"],
            {"type": "array", "items": "Page"},
        )

        schemas = contract["schemas"]
        self.assertEqual(
            list(schemas["Pageable"]["properties"]),
            ["page", "size", "sort"],
        )
        self.assertEqual(
            schemas["Pageable"]["properties"]["page"]["minimum"], 0
        )
        self.assertEqual(
            schemas["Pageable"]["properties"]["size"]["minimum"], 1
        )
        self.assertEqual(
            schemas["Pageable"]["properties"]["sort"]["unsetBehavior"],
            "omit",
        )
        self.assertEqual(
            list(schemas["Page"]["properties"]),
            [
                "content",
                "empty",
                "first",
                "last",
                "number",
                "numberOfElements",
                "pageable",
                "size",
                "sort",
                "totalElements",
                "totalPages",
            ],
        )
        self.assertEqual(
            schemas["Page"]["properties"]["content"],
            {"type": "array", "items": {"type": "object"}},
        )
        self.assertEqual(
            list(schemas["AgentGroupResponse"]["properties"]),
            [
                "agentConfig",
                "autoUpdate",
                "constraints",
                "id",
                "info",
                "mpId",
                "name",
            ],
        )

        profile = contract["focusedCollectionProfile"]
        self.assertEqual(profile["operation"], "agentGroups.list")
        self.assertEqual(profile["responseArrayCardinalityPerRequest"], 1)
        self.assertEqual(profile["contentItemProfile"], "AgentGroupResponse")
        self.assertIn(
            "without an AgentGroupResponse reference",
            profile["contentProfileBoundary"],
        )
        self.assertEqual(profile["firstPage"], 0)
        self.assertEqual(
            profile["requiredContentProperties"],
            ["id", "name", "autoUpdate", "info"],
        )
        self.assertEqual(
            profile["ordering"],
            {
                "comparison": "Python case-sensitive Unicode code-point order",
                "keys": ["name", "id"],
            },
        )
        self.assertEqual(
            profile["projection"],
            {
                "type": "AgentGroup",
                "fieldOrder": ["id", "name", "auto_update", "info"],
                "sourceProperties": {
                    "id": "id",
                    "name": "name",
                    "auto_update": "autoUpdate",
                    "info": "info",
                },
            },
        )

        self.assertEqual(sources["repository"], "vmware/vcf-api-specs")
        self.assertEqual(
            sources["repositoryUrl"],
            "https://github.com/vmware/vcf-api-specs",
        )
        self.assertEqual(sources["repositoryCommitSha"], COMMIT)
        self.assertEqual(sources["specPath"], SPEC_PATH)
        self.assertEqual(sources["specBlobSha"], SPEC_BLOB)
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(sources["operationIds"], OPERATION_IDS)
        self.assertIn(COMMIT, sources["specUrl"])
        self.assertTrue(sources["specUrl"].endswith(SPEC_PATH))
        self.assertEqual(
            [
                {
                    "operationId": item["operationId"],
                    "method": item["method"],
                    "path": item["path"],
                    "openapiPointer": item["openapiPointer"],
                    "specLine": item["specLine"],
                    "repositoryCommitSha": item["repositoryCommitSha"],
                    "specPath": item["specPath"],
                }
                for item in sources["operations"]
            ],
            [
                {
                    "operationId": "getAllAgentGroupConfig",
                    "method": "GET",
                    "path": ROUTE,
                    "openapiPointer": "#/paths/~1api~1v2~1agent~1groups/get",
                    "specLine": 38,
                    "repositoryCommitSha": COMMIT,
                    "specPath": SPEC_PATH,
                }
            ],
        )
        self.assertIs(
            sources["derivation"]["documentationPageUsedAsContractSource"],
            False,
        )

    def test_production_package_is_standard_library_only(self) -> None:
        metadata = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["dependencies"], [])
        self.assertEqual(metadata["project"]["requires-python"], ">=3.11")

        stdlib = set(sys.stdlib_module_names)
        for source_path in sorted((ROOT / "vcf_ops_logs").glob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(
                            alias.name.split(".", 1)[0],
                            stdlib,
                            str(source_path),
                        )
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(
                        (node.module or "").split(".", 1)[0],
                        stdlib,
                        str(source_path),
                    )


class ClientTests(unittest.TestCase):
    @staticmethod
    def _header_values(
        request: dict[str, Any], name: str
    ) -> list[str]:
        return [
            value
            for key, value in request["headers"]
            if key == name.lower()
        ]

    @staticmethod
    def _expected(mock: VCFLogManagementMock) -> list[AgentGroup]:
        return [
            AgentGroup(
                id=item["id"],
                name=item["name"],
                auto_update=item["autoUpdate"],
                info=item["info"],
            )
            for item in sorted(
                mock.groups,
                key=lambda value: (value["name"], value["id"]),
            )
        ]

    def test_complete_inventory_is_stable_and_wire_exact(self) -> None:
        with VCFLogManagementMock() as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            first = client.list_all_agent_groups(page_size=2)
            second = client.list_all_agent_groups(page_size=2)

            expected = self._expected(mock)
            self.assertEqual(first, expected)
            self.assertEqual(second, expected)
            self.assertEqual(first, second)
            self.assertTrue(all(isinstance(item, AgentGroup) for item in first))

            expected_targets = [
                f"{ROUTE}?page=0&size=2",
                f"{ROUTE}?page=1&size=2",
                f"{ROUTE}?page=2&size=2",
            ] * 2
            self.assertEqual(
                [request["target"] for request in mock.requests],
                expected_targets,
            )
            self.assertEqual(
                [request["method"] for request in mock.requests],
                ["GET"] * 6,
            )
            self.assertEqual(
                [request["operationId"] for request in mock.requests],
                OPERATION_IDS * 6,
            )

            for index, request in enumerate(mock.requests):
                page = index % 3
                self.assertEqual(request["path"], ROUTE)
                self.assertEqual(
                    request["query"],
                    [("page", str(page)), ("size", "2")],
                )
                self.assertNotIn("sort", dict(request["query"]))
                self.assertTrue(
                    all(value != "" for _, value in request["query"])
                )
                self.assertNotEqual(request["target"][-1], "?")
                self.assertEqual(request["body"], b"")
                self.assertEqual(
                    self._header_values(request, "x-jwt-token"),
                    [mock.token],
                )
                self.assertEqual(
                    self._header_values(request, "accept"),
                    ["application/json"],
                )
                self.assertEqual(
                    self._header_values(request, "authorization"), []
                )
                self.assertEqual(
                    self._header_values(request, "content-type"), []
                )
                self.assertEqual(
                    self._header_values(request, "content-length"), []
                )
                self.assertEqual(
                    self._header_values(request, "transfer-encoding"), []
                )

    def test_page_size_validation_happens_before_traffic(self) -> None:
        with VCFLogManagementMock() as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            for invalid in (True, False, 0, -1, 1001, 1.5, "2", None):
                with self.subTest(invalid=invalid):
                    with self.assertRaises((TypeError, ValueError)):
                        client.list_all_agent_groups(page_size=invalid)
            self.assertEqual(mock.requests, [])

    def test_constructor_rejects_invalid_inputs(self) -> None:
        valid_origin = "http://127.0.0.1:8181"
        invalid_origins: tuple[Any, ...] = (
            7,
            "",
            "relative/path",
            "ftp://example.com",
            "https://user@example.com",
            "https://example.com/not-root",
            "https://example.com?x=1",
            "https://example.com#fragment",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                with self.assertRaises((TypeError, ValueError)):
                    LogManagementClient(origin, "token")
        for token in ("", " ", "bad\rvalue", "bad\nvalue", None, 9):
            with self.subTest(token=token):
                with self.assertRaises((TypeError, ValueError)):
                    LogManagementClient(valid_origin, token)
        for timeout in (True, False, 0, -1, math.inf, -math.inf, math.nan):
            with self.subTest(timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    LogManagementClient(
                        valid_origin,
                        "token",
                        timeout=timeout,
                    )

    def test_duplicate_across_pages_is_rejected(self) -> None:
        with VCFLogManagementMock(fault="duplicate") as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            with self.assertRaises(ResponseContractError):
                client.list_all_agent_groups(page_size=2)
            self.assertEqual(
                [request["target"] for request in mock.requests],
                [
                    f"{ROUTE}?page=0&size=2",
                    f"{ROUTE}?page=1&size=2",
                ],
            )

    def test_later_http_failure_raises_api_error_without_token(self) -> None:
        with VCFLogManagementMock(fault="late_http") as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            with self.assertRaises(ApiError) as raised:
                client.list_all_agent_groups(page_size=2)
            self.assertNotIn(mock.token, str(raised.exception))
            self.assertEqual(
                [request["target"] for request in mock.requests],
                [
                    f"{ROUTE}?page=0&size=2",
                    f"{ROUTE}?page=1&size=2",
                ],
            )

    def test_wrong_token_is_an_api_error_without_credential_leak(self) -> None:
        wrong_token = "wrong-runtime-secret"
        with VCFLogManagementMock() as mock:
            client = LogManagementClient(
                mock.base_url, wrong_token, timeout=2
            )
            with self.assertRaises(ApiError) as raised:
                client.list_all_agent_groups(page_size=2)
            self.assertNotIn(wrong_token, str(raised.exception))
            self.assertEqual(len(mock.requests), 1)

    def test_mock_rejects_routes_not_named_by_the_contract(self) -> None:
        with VCFLogManagementMock() as mock:
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"{mock.base_url}/api/v2/logs/search", timeout=2)
            self.assertEqual(raised.exception.code, 404)
            self.assertEqual(len(mock.requests), 1)
            self.assertIsNone(mock.requests[0]["operationId"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
