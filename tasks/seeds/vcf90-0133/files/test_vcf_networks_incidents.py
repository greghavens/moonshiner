#!/usr/bin/env python3
"""Protected acceptance verifier for the VCF Operations for Networks client."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qsl
from urllib.request import urlopen

from mock_vcf_networks import ContractMismatch, MockVcfServer, running_mock


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
COMMIT_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
OPERATION_ID = "listTroubleshootingIncidents"

EXPECTED_INCIDENTS = [
    {
        "entity_id": "incident-alpha",
        "name": "Alpha path",
        "start_entity_id": "vm-10",
        "status": "RUNNING",
    },
    {
        "entity_id": "incident-beta",
        "name": "Beta path",
        "start_entity_id": "vm-20",
        "status": "FAILED",
    },
    {
        "entity_id": "incident-delta",
        "name": "Delta path",
        "start_entity_id": "vm-40",
        "status": "COMPLETED",
    },
    {
        "entity_id": "incident-gamma",
        "name": "Gamma path",
        "start_entity_id": "vm-30",
        "status": "COMPLETED",
    },
    {
        "entity_id": "incident-zeta",
        "name": "Zeta path",
        "start_entity_id": "vm-50",
        "status": "COMPLETED",
    },
]


class ContractFixtureTests(unittest.TestCase):
    def test_contract_and_provenance_are_pinned_to_the_openapi_spec(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["spec_version"], "9.0.0.0")
        self.assertEqual(contract["server_base_path"], "/api/ni")
        self.assertEqual(list(contract["operations"]), [OPERATION_ID])
        operation = contract["operations"][OPERATION_ID]
        self.assertEqual(operation["operationId"], OPERATION_ID)
        self.assertEqual(operation["method"], "GET")
        self.assertEqual(operation["path"], "/gnt/troubleshoot/incidents")
        self.assertEqual(
            list(operation["query_parameters"]),
            ["size", "cursor", "start_entity_id"],
        )
        self.assertEqual(
            operation["success_response"]["schema"]["properties"]["cursor"]["type"],
            "string",
        )

        self.assertEqual(sources["tag"], "9.0.0.0")
        self.assertEqual(sources["commit_sha"], COMMIT_SHA)
        self.assertEqual(sources["spec_path"], SPEC_PATH)
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(sources["operation_ids"], [OPERATION_ID])
        self.assertIn(COMMIT_SHA, sources["raw_spec_url"])
        self.assertTrue(sources["raw_spec_url"].endswith(SPEC_PATH))

    def test_mock_rejects_a_contract_with_an_extra_operation(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        contract["operations"]["notInPinnedSpecSlice"] = {
            "method": "POST",
            "path": "/extra",
        }
        temporary = ROOT / ".contract-mismatch.json"
        temporary.write_text(json.dumps(contract), encoding="utf-8")
        try:
            with self.assertRaises(ContractMismatch):
                MockVcfServer(temporary)
        finally:
            temporary.unlink(missing_ok=True)


class ClientTests(unittest.TestCase):
    def test_package_uses_only_python_standard_library_dependencies(self) -> None:
        package = ROOT / "vcf_operations_networks"
        self.assertTrue(package.is_dir())
        source_paths = sorted(package.rglob("*.py"))
        self.assertTrue(source_paths)

        allowed_roots = set(sys.stdlib_module_names) | {"vcf_operations_networks"}
        for source_path in source_paths:
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"),
                filename=str(source_path),
            )
            for node in ast.walk(tree):
                imported_roots: list[str] = []
                if isinstance(node, ast.Import):
                    imported_roots = [alias.name.partition(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots = [node.module.partition(".")[0]]
                for imported_root in imported_roots:
                    self.assertIn(
                        imported_root,
                        allowed_roots,
                        f"{source_path.name} imports non-stdlib module {imported_root}",
                    )

    def test_complete_pagination_stable_order_and_exact_wire_shape(self) -> None:
        from vcf_operations_networks import OperationsForNetworksClient

        with running_mock() as server:
            client = OperationsForNetworksClient(server.appliance_url, "test-token")
            incidents = client.list_all_troubleshooting_incidents()

        self.assertEqual(incidents, EXPECTED_INCIDENTS)
        self.assertEqual(
            [
                (
                    entry["method"],
                    entry["path"],
                    dict(parse_qsl(entry["raw_query"], keep_blank_values=True)),
                )
                for entry in server.request_log
            ],
            [
                (
                    "GET",
                    "/api/ni/gnt/troubleshoot/incidents",
                    {"size": "2"},
                ),
                (
                    "GET",
                    "/api/ni/gnt/troubleshoot/incidents",
                    {"size": "2", "cursor": "MTA="},
                ),
                (
                    "GET",
                    "/api/ni/gnt/troubleshoot/incidents",
                    {"size": "2", "cursor": "MjA="},
                ),
            ],
        )
        for entry in server.request_log:
            headers = entry["headers"]
            self.assertEqual(headers.get("authorization"), "NetworkInsight test-token")
            self.assertEqual(headers.get("accept"), "application/json")
            self.assertEqual(entry["body"], "")
            self.assertNotIn("start_entity_id", entry["raw_query"])
            self.assertNotIn("start_entity_id=", entry["raw_query"])
        self.assertNotIn("cursor", server.request_log[0]["raw_query"])

    def test_supplied_optional_filter_is_encoded_and_kept_on_every_page(self) -> None:
        from vcf_operations_networks import OperationsForNetworksClient

        with running_mock() as server:
            client = OperationsForNetworksClient(server.appliance_url + "/", "filter-token")
            incidents = client.list_all_troubleshooting_incidents(
                size=3,
                start_entity_id="vm/group + 1",
            )

        self.assertEqual(incidents, EXPECTED_INCIDENTS)
        self.assertEqual(
            [
                dict(parse_qsl(entry["raw_query"], keep_blank_values=True))
                for entry in server.request_log
            ],
            [
                {"size": "3", "start_entity_id": "vm/group + 1"},
                {
                    "size": "3",
                    "start_entity_id": "vm/group + 1",
                    "cursor": "MTA=",
                },
            ],
        )
        for entry in server.request_log:
            raw_filter = next(
                component.partition("=")[2]
                for component in entry["raw_query"].split("&")
                if component.partition("=")[0] == "start_entity_id"
            )
            self.assertIn("%2b", raw_filter.lower())
            self.assertEqual(
                entry["headers"].get("authorization"),
                "NetworkInsight filter-token",
            )

    def test_cli_emits_one_byte_stable_complete_json_line(self) -> None:
        with running_mock() as server:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "vcf_operations_networks",
                    "--appliance-url",
                    server.appliance_url,
                    "--token",
                    "cli-token",
                    "--size",
                    "3",
                    "--start-entity-id",
                    "cli-start",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

        expected = json.dumps(
            EXPECTED_INCIDENTS,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, expected)
        self.assertEqual(
            [
                dict(parse_qsl(entry["raw_query"], keep_blank_values=True))
                for entry in server.request_log
            ],
            [
                {"size": "3", "start_entity_id": "cli-start"},
                {
                    "size": "3",
                    "start_entity_id": "cli-start",
                    "cursor": "MTA=",
                },
            ],
        )
        for entry in server.request_log:
            self.assertEqual(
                entry["headers"].get("authorization"),
                "NetworkInsight cli-token",
            )

    def test_mock_serves_no_uncontracted_api_operation(self) -> None:
        with running_mock() as server:
            with self.assertRaises(HTTPError) as raised:
                urlopen(server.appliance_url + "/api/ni/infra/nodes", timeout=2)
        self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
