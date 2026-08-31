from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import unittest

from tests.mock_vcf_installer import ContractMock
from vcf_installer import (
    ChangeReport,
    InstallerClient,
    ProxyConfiguration,
    ServiceConfiguration,
    StepResult,
    VCFInstallerAPIError,
    configure_depot_access,
)


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "fixture-access-token-never-real"


def service_configuration() -> ServiceConfiguration:
    return ServiceConfiguration(
        name="VCF Depot",
        service_type="VCF_DEPOT",
        key="depot-service-key",
        node_name="VCF Depot",
        address_type="Fqdn",
        address_value="vcf-flt01.vcf.lab",
    )


class ContractFixtureTests(unittest.TestCase):
    def test_contract_records_both_official_specs(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text())
        sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())
        expected = {
            "updateProxyConfiguration",
            "updateServicesConfig",
            "syncDepotMetadata",
        }
        self.assertEqual(set(contract["operations"]), expected)
        self.assertEqual(
            {item["operationId"] for source in sources["sources"] for item in source["operations"]},
            expected,
        )
        self.assertEqual(
            {source["specPath"] for source in sources["sources"]},
            {
                "specifications/vcf-installer/vcf-installer-openapi.json",
                "specifications/sddc-manager/sddc-manager-openapi.json",
            },
        )

    def test_package_is_standard_library_only(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"__future__", "vcf_installer"}
        for source_path in (ROOT / "vcf_installer").rglob("*.py"):
            tree = ast.parse(source_path.read_text(), source_path.name)
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name.partition(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module.partition(".")[0]]
                for name in names:
                    self.assertIn(name, allowed)


class BootstrapTests(unittest.TestCase):
    def test_success_matches_live_statuses_and_wire(self) -> None:
        with ContractMock(fail_operation=None) as mock:
            report = configure_depot_access(
                InstallerClient(mock.base_url, TOKEN, timeout=2.0),
                ProxyConfiguration(is_enabled=False),
                service_configuration(),
            )
            requests = list(mock.request_log)

        self.assertIsInstance(report, ChangeReport)
        self.assertEqual(
            [(step.operation_id, step.http_status) for step in report.steps],
            [
                ("updateProxyConfiguration", 202),
                ("updateServicesConfig", 200),
                ("syncDepotMetadata", 202),
            ],
        )
        self.assertEqual(
            [step.response for step in report.steps],
            [
                mock.success_responses["updateProxyConfiguration"],
                mock.success_responses["updateServicesConfig"],
                mock.success_responses["syncDepotMetadata"],
            ],
        )
        self.assertEqual(
            [(request.method, request.path, request.query) for request in requests],
            [
                ("PATCH", "/v1/system/proxy-configuration", ""),
                ("PUT", "/v1/services-config", ""),
                ("PATCH", "/v1/system/settings/depot/depot-sync-info", ""),
            ],
        )
        self.assertEqual(requests[0].body, b'{"isEnabled":false}')
        expected_services = (
            b'{"services":[{"name":"VCF Depot","type":"VCF_DEPOT",'
            b'"key":"depot-service-key","nodes":[{"name":"VCF Depot",'
            b'"addresses":[{"type":"Fqdn","value":"vcf-flt01.vcf.lab"}]}]}]}'
        )
        self.assertEqual(requests[1].body, expected_services)
        self.assertEqual(requests[2].body, b"")
        for request in requests:
            self.assertEqual(request.headers.get("accept"), "application/json")
            self.assertEqual(request.headers.get("authorization"), f"Bearer {TOKEN}")
            self.assertEqual(request.query, "")
        self.assertEqual(requests[0].headers.get("content-type"), "application/json")
        self.assertEqual(requests[1].headers.get("content-type"), "application/json")
        self.assertNotIn("content-type", requests[2].headers)

    def test_sync_failure_preserves_two_accepted_mutations(self) -> None:
        with ContractMock() as mock:
            with self.assertRaises(VCFInstallerAPIError) as caught:
                configure_depot_access(
                    InstallerClient(mock.base_url, TOKEN, timeout=2.0),
                    ProxyConfiguration(is_enabled=True, host="proxy.example.com", port=3128),
                    service_configuration(),
                )
            requests = list(mock.request_log)

        error = caught.exception
        self.assertEqual(error.operation_id, "syncDepotMetadata")
        self.assertEqual(error.status, 500)
        self.assertEqual(
            [(step.operation_id, step.http_status) for step in error.completed],
            [("updateProxyConfiguration", 202), ("updateServicesConfig", 200)],
        )
        self.assertEqual(len(requests), 3)
        self.assertIsInstance(error.completed, tuple)
        self.assertTrue(all(isinstance(step, StepResult) for step in error.completed))

    def test_middle_failure_stops_before_sync(self) -> None:
        with ContractMock(fail_operation="updateServicesConfig") as mock:
            with self.assertRaises(VCFInstallerAPIError) as caught:
                configure_depot_access(
                    InstallerClient(mock.base_url, TOKEN, timeout=2.0),
                    ProxyConfiguration(is_enabled=True),
                    service_configuration(),
                )
            requests = list(mock.request_log)
        self.assertEqual(caught.exception.operation_id, "updateServicesConfig")
        self.assertEqual(
            [(step.operation_id, step.http_status) for step in caught.exception.completed],
            [("updateProxyConfiguration", 202)],
        )
        self.assertEqual(len(requests), 2)

    def test_each_operation_requires_its_exact_success_status(self) -> None:
        cases = [
            ("updateProxyConfiguration", 200, 0),
            ("updateServicesConfig", 202, 1),
            ("syncDepotMetadata", 200, 2),
        ]
        for operation_id, wrong_status, completed_count in cases:
            with self.subTest(operation_id=operation_id):
                with ContractMock(
                    fail_operation=None,
                    status_overrides={operation_id: wrong_status},
                ) as mock:
                    with self.assertRaises(VCFInstallerAPIError) as caught:
                        configure_depot_access(
                            InstallerClient(mock.base_url, TOKEN, timeout=2.0),
                            ProxyConfiguration(is_enabled=False),
                            service_configuration(),
                        )
                self.assertEqual(caught.exception.operation_id, operation_id)
                self.assertEqual(caught.exception.status, wrong_status)
                self.assertEqual(len(caught.exception.completed), completed_count)

    def test_proxy_explicit_false_and_omission(self) -> None:
        with ContractMock(fail_operation=None) as mock:
            status, response = InstallerClient(mock.base_url, TOKEN).update_proxy_configuration(
                ProxyConfiguration(is_enabled=False, is_authenticated=False)
            )
            request = mock.request_log[0]
        self.assertEqual(status, 202)
        self.assertEqual(response["status"], "COMPLETED_WITH_SUCCESS")
        self.assertEqual(
            request.body,
            b'{"isEnabled":false,"isAuthenticated":false}',
        )
        self.assertNotIn(b"isConfigured", request.body)

    def test_public_report_is_immutable(self) -> None:
        step = StepResult("syncDepotMetadata", 202, {"syncStatus": "SYNC_IN_PROGRESS"})
        report = ChangeReport((step,))
        with self.assertRaises((AttributeError, TypeError)):
            report.steps += (step,)  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main(verbosity=2)
