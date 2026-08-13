from __future__ import annotations

import ast
import json
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vcf_logs import Forwarder, VCFLogsClient  # noqa: E402

from tests.mock_vcf_logs import MockVCFLogs  # noqa: E402


class ContractProvenanceTests(unittest.TestCase):
    def test_contract_and_sources_are_the_pinned_9_0_spec_subset(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )

        self.assertEqual(contract["title"], "VCF Operations for Logs")
        self.assertEqual(contract["openapi"], "3.0.1")
        self.assertEqual(contract["server_base_path"], "/api/v2")
        self.assertEqual(contract["source_tag"], "9.0.0.0")
        self.assertEqual(
            [operation["operationId"] for operation in contract["operations"]],
            ["POST_sessions", "POST_log-forwarder"],
        )
        self.assertEqual(sources["tag"], "9.0.0.0")
        self.assertEqual(
            sources["commit_sha"], "85151f6b1bb58f13b6ac0304bfec53904bea085f"
        )
        self.assertEqual(
            sources["spec_path"],
            "specifications/vcf-operations/vcf-operations-for-logs-openapi.json",
        )
        self.assertEqual(
            sources["operation_ids"], ["POST_sessions", "POST_log-forwarder"]
        )
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(sources["repository"], "https://github.com/vmware/vcf-api-specs")
        self.assertEqual(
            sources["spec_url"],
            "https://raw.githubusercontent.com/vmware/vcf-api-specs/85151f6b1bb58f13b6ac0304bfec53904bea085f/specifications/vcf-operations/vcf-operations-for-logs-openapi.json",
        )

        forwarder = contract["operations"][1]
        self.assertEqual(forwarder["method"], "POST")
        self.assertEqual(forwarder["path"], "/log-forwarder")
        self.assertEqual(forwarder["security"], [{"Bearer": []}])
        schema = forwarder["requestBody"]["schema"]
        self.assertEqual(
            schema["required"], ["name", "host", "port", "protocol", "sslEnabled"]
        )
        self.assertEqual(
            set(schema["properties"]),
            {
                "acceptCert",
                "name",
                "host",
                "port",
                "protocol",
                "sslEnabled",
                "workerCount",
                "connectionRefreshInterval",
                "diskCacheSize",
                "tags",
                "filter",
                "transportProtocol",
                "forwardComplementaryFields",
                "testConnection",
            },
        )

    def test_package_imports_only_the_python_standard_library(self) -> None:
        imported_roots: set[str] = set()
        for path in (ROOT / "src" / "vcf_logs").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])
        self.assertEqual(imported_roots - sys.stdlib_module_names, set())


class ClientAcceptanceTests(unittest.TestCase):
    def test_forwarder_maps_every_field_and_preserves_explicit_empty_values(self) -> None:
        body = Forwarder(
            name="explicit-empty",
            host="logs-empty.example.test",
            port=514,
            protocol="SYSLOG",
            ssl_enabled=False,
            accept_cert=False,
            worker_count=0,
            connection_refresh_interval=1,
            disk_cache_size=0,
            tags={},
            filter="",
            transport_protocol="UDP",
            forward_complementary_fields=False,
            test_connection=False,
        ).as_request()

        self.assertEqual(
            body,
            {
                "name": "explicit-empty",
                "host": "logs-empty.example.test",
                "port": 514,
                "protocol": "SYSLOG",
                "sslEnabled": False,
                "acceptCert": False,
                "workerCount": 0,
                "connectionRefreshInterval": 1,
                "diskCacheSize": 0,
                "tags": {},
                "filter": "",
                "transportProtocol": "UDP",
                "forwardComplementaryFields": False,
                "testConnection": False,
            },
        )

    def test_fully_successful_run_is_marked_complete(self) -> None:
        with MockVCFLogs() as mock:
            report = VCFLogsClient(mock.base_url).configure_forwarders(
                "api-user",
                "s3cret",
                [
                    Forwarder(
                        name="only-forwarder",
                        host="logs-only.example.test",
                        port=9543,
                        protocol="CFAPI",
                        ssl_enabled=True,
                    )
                ],
                provider="vIDM",
            )

            self.assertEqual(
                mock.request_log[0]["raw_body"],
                '{"password":"s3cret","provider":"vIDM","username":"api-user"}',
            )

        self.assertEqual(
            report,
            {
                "completed": True,
                "results": [
                    {
                        "name": "only-forwarder",
                        "status": "created",
                        "id": "forwarder-001",
                    }
                ],
            },
        )

    def test_multistep_failure_keeps_prior_results_and_exact_wire_shape(self) -> None:
        forwarders = [
            Forwarder(
                name="edge-primary",
                host="logs-a.example.test",
                port=9543,
                protocol="CFAPI",
                ssl_enabled=True,
            ),
            Forwarder(
                name="edge-secondary",
                host="logs-b.example.test",
                port=514,
                protocol="SYSLOG",
                ssl_enabled=False,
                tags={"site": "chi"},
                transport_protocol="TCP_OCTET",
                forward_complementary_fields=False,
            ),
            Forwarder(
                name="edge-dr",
                host="logs-dr.example.test",
                port=1514,
                protocol="SYSLOG",
                ssl_enabled=True,
                accept_cert=False,
                worker_count=0,
            ),
            Forwarder(
                name="must-not-run",
                host="logs-unused.example.test",
                port=514,
                protocol="SYSLOG",
                ssl_enabled=False,
            ),
        ]

        with MockVCFLogs() as mock:
            client = VCFLogsClient(mock.base_url + "/")
            report = client.configure_forwarders("api-user", "s3cret", forwarders)

            self.assertEqual(
                report,
                {
                    "completed": False,
                    "results": [
                        {
                            "name": "edge-primary",
                            "status": "created",
                            "id": "forwarder-001",
                        },
                        {
                            "name": "edge-secondary",
                            "status": "created",
                            "id": "forwarder-002",
                        },
                        {
                            "name": "edge-dr",
                            "status": "failed",
                            "http_status": 409,
                            "error_code": "FIELD_ERROR",
                            "error_message": "Forwarder with specified name already exists.",
                        },
                    ],
                },
            )

            expected_bodies = [
                '{"password":"s3cret","provider":"Local","username":"api-user"}',
                '{"host":"logs-a.example.test","name":"edge-primary","port":9543,"protocol":"CFAPI","sslEnabled":true}',
                '{"forwardComplementaryFields":false,"host":"logs-b.example.test","name":"edge-secondary","port":514,"protocol":"SYSLOG","sslEnabled":false,"tags":{"site":"chi"},"transportProtocol":"TCP_OCTET"}',
                '{"acceptCert":false,"host":"logs-dr.example.test","name":"edge-dr","port":1514,"protocol":"SYSLOG","sslEnabled":true,"workerCount":0}',
            ]
            self.assertEqual(
                [(entry["method"], entry["path"], entry["query"]) for entry in mock.request_log],
                [
                    ("POST", "/api/v2/sessions", ""),
                    ("POST", "/api/v2/log-forwarder", ""),
                    ("POST", "/api/v2/log-forwarder", ""),
                    ("POST", "/api/v2/log-forwarder", ""),
                ],
            )
            self.assertEqual(
                [entry["raw_body"] for entry in mock.request_log], expected_bodies
            )
            for index, entry in enumerate(mock.request_log):
                self.assertEqual(entry["content_type"], "application/json")
                self.assertEqual(entry["accept"], "application/json")
                self.assertEqual(entry["content_length"], str(len(expected_bodies[index].encode())))
            self.assertIsNone(mock.request_log[0]["authorization"])
            self.assertEqual(
                [entry["authorization"] for entry in mock.request_log[1:]],
                ["Bearer vcf90-loopback-session"] * 3,
            )

            first_body = json.loads(mock.request_log[1]["raw_body"])
            self.assertEqual(
                set(first_body), {"name", "host", "port", "protocol", "sslEnabled"}
            )
            self.assertNotIn("tags", first_body)
            self.assertNotIn("filter", first_body)
            self.assertNotIn("transportProtocol", first_body)

    def test_mock_rejects_operations_outside_the_contract(self) -> None:
        with MockVCFLogs() as mock:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(mock.base_url + "/api/v2/version", timeout=2)
            self.assertEqual(caught.exception.code, 404)
            self.assertEqual(
                [(entry["method"], entry["path"]) for entry in mock.request_log],
                [("GET", "/api/v2/version")],
            )


if __name__ == "__main__":
    unittest.main()
