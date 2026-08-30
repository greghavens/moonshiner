from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from mock_vcf import CONTRACT_PATH, VCFLogManagementMock
from vcf_logs import (
    AgentSecret,
    ApiError,
    LogManagementClient,
    ProvisioningFailed,
    ProvisioningTimeout,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"


class ContractTests(unittest.TestCase):
    def test_contract_and_provenance_are_pinned_to_the_official_spec(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["openapi"], "3.0.1")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(sources["repository"], "https://github.com/vmware/vcf-api-specs")
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(sources["commit_sha"], PINNED_COMMIT)
        self.assertEqual(sources["spec_path"], SPEC_PATH)
        self.assertEqual(
            sources["spec_blob_sha"],
            "4ada16fa39ec345674de4126174de94ea70d23a0",
        )
        self.assertEqual(
            sources["operationIds"],
            ["createAgentSecret", "listAgentSecrets"],
        )
        self.assertFalse(
            sources["derivation"]["documentationPageUsedAsContractSource"]
        )

        expected = {
            ("POST", "/api/v2/agent/secrets", "createAgentSecret"),
            ("GET", "/api/v2/agent/secrets", "listAgentSecrets"),
        }
        recorded = {
            (item["method"], item["path"], item["operationId"])
            for item in sources["operations"]
        }
        projected = {
            (method.upper(), path, operation["operationId"])
            for path, path_item in contract["paths"].items()
            for method, operation in path_item.items()
        }
        self.assertEqual(recorded, expected)
        self.assertEqual(projected, expected)
        for operation in sources["operations"]:
            self.assertEqual(operation["repositoryCommitSha"], PINNED_COMMIT)
            self.assertEqual(operation["specPath"], SPEC_PATH)

        request_schema = contract["components"]["schemas"]["AgentSecretCreateRequest"]
        self.assertNotIn("required", request_schema)
        self.assertEqual(set(request_schema["properties"]), {"name"})
        pageable = contract["components"]["schemas"]["Pageable"]
        self.assertEqual(set(pageable["properties"]), {"page", "size", "sort"})
        security = contract["components"]["securitySchemes"][
            "OPSTokenAuthorization"
        ]
        self.assertEqual(
            (security["type"], security["in"], security["name"]),
            ("apiKey", "header", "X-JWT-Token"),
        )

    def test_production_package_imports_only_the_standard_library(self) -> None:
        stdlib = set(sys.stdlib_module_names)
        for source_path in (ROOT / "vcf_logs").glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_level = alias.name.split(".", 1)[0]
                        self.assertIn(top_level, stdlib, source_path)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    module = (node.module or "").split(".", 1)[0]
                if module:
                    self.assertIn(module, stdlib, source_path)


class WireContractTests(unittest.TestCase):
    @staticmethod
    def _header_values(request: dict[str, object], name: str) -> list[str]:
        headers = request["headers"]
        assert isinstance(headers, list)
        return [value for key, value in headers if key == name.lower()]

    def test_anonymous_provisioning_omits_optionals_and_polls_to_active(self) -> None:
        with VCFLogManagementMock() as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            result = client.provision_agent_secret(poll_interval=0, timeout=2)

            self.assertIsInstance(result, AgentSecret)
            self.assertEqual(result.id, mock.secret_id)
            self.assertEqual(result.name, mock.generated_name)
            self.assertEqual(result.status, "ACTIVE")
            self.assertEqual(result.secret, mock.one_time_secret)

            requests = mock.requests
            self.assertEqual(
                [request["method"] for request in requests],
                ["POST", "GET", "GET"],
                "create must be followed by polls until a terminal status",
            )

            create = requests[0]
            self.assertEqual(create["target"], "/api/v2/agent/secrets")
            self.assertEqual(create["query"], [])
            self.assertEqual(create["body"], b"{}")
            self.assertEqual(create["json"], {})
            self.assertNotIn("name", create["json"])
            self.assertEqual(
                self._header_values(create, "x-jwt-token"), [mock.token]
            )
            self.assertEqual(
                self._header_values(create, "accept"), ["application/json"]
            )
            self.assertEqual(
                self._header_values(create, "content-type"), ["application/json"]
            )
            self.assertEqual(self._header_values(create, "content-length"), ["2"])
            self.assertEqual(self._header_values(create, "authorization"), [])

            for poll in requests[1:]:
                self.assertEqual(
                    poll["target"], "/api/v2/agent/secrets?page=0&size=100"
                )
                self.assertEqual(poll["query"], [("page", "0"), ("size", "100")])
                self.assertEqual(poll["body"], b"")
                self.assertEqual(
                    self._header_values(poll, "x-jwt-token"), [mock.token]
                )
                self.assertEqual(
                    self._header_values(poll, "accept"), ["application/json"]
                )
                self.assertEqual(self._header_values(poll, "content-type"), [])
                self.assertNotIn("sort", dict(poll["query"]))
                self.assertTrue(all(value != "" for _, value in poll["query"]))

    def test_named_create_uses_the_only_set_optional_field(self) -> None:
        with VCFLogManagementMock() as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            result = client.provision_agent_secret(
                "collector-01", poll_interval=0, timeout=2
            )

            self.assertEqual(result.name, "collector-01")
            self.assertEqual(
                mock.requests[0]["body"], b'{"name":"collector-01"}'
            )
            self.assertEqual(mock.requests[0]["json"], {"name": "collector-01"})
            self.assertEqual(len(mock.requests), 3)

    def test_terminal_failure_is_not_reported_as_success(self) -> None:
        with VCFLogManagementMock(("PENDING", "FAILED")) as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            with self.assertRaises(ProvisioningFailed) as raised:
                client.provision_agent_secret(poll_interval=0, timeout=2)
            self.assertEqual(raised.exception.secret_id, mock.secret_id)
            self.assertEqual(raised.exception.status, "FAILED")
            self.assertEqual(
                [request["method"] for request in mock.requests],
                ["POST", "GET", "GET"],
            )

    def test_poll_timeout_happens_after_a_real_status_request(self) -> None:
        with VCFLogManagementMock(("PENDING",)) as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            with self.assertRaises(ProvisioningTimeout):
                client.provision_agent_secret(poll_interval=0, timeout=0)
            self.assertEqual(
                [request["method"] for request in mock.requests], ["POST", "GET"]
            )

    def test_pageable_sort_values_repeat_only_when_supplied(self) -> None:
        with VCFLogManagementMock(("ACTIVE",)) as mock:
            client = LogManagementClient(mock.base_url, mock.token, timeout=2)
            client.create_agent_secret("sorted-agent")
            client.list_agent_secrets(
                page=2, size=7, sort=("name,asc", "id,asc")
            )
            poll = mock.requests[-1]
            self.assertEqual(
                poll["target"],
                "/api/v2/agent/secrets?"
                "page=2&size=7&sort=name%2Casc&sort=id%2Casc",
            )
            self.assertEqual(
                poll["query"],
                [
                    ("page", "2"),
                    ("size", "7"),
                    ("sort", "name,asc"),
                    ("sort", "id,asc"),
                ],
            )

    def test_non_success_response_raises_api_error(self) -> None:
        with VCFLogManagementMock() as mock:
            client = LogManagementClient(mock.base_url, "wrong-token", timeout=2)
            with self.assertRaises(ApiError) as raised:
                client.create_agent_secret()
            self.assertEqual(raised.exception.status, 403)
            self.assertEqual(len(mock.requests), 1)

    def test_mock_rejects_routes_not_named_by_the_contract(self) -> None:
        with VCFLogManagementMock() as mock:
            with self.assertRaises(HTTPError) as raised:
                urlopen(f"{mock.base_url}/api/v2/logs/search", timeout=2)
            self.assertEqual(raised.exception.code, 404)
            self.assertEqual(mock.requests[0]["path"], "/api/v2/logs/search")


if __name__ == "__main__":
    unittest.main()
