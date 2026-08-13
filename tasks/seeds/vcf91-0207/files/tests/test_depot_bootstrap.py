from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import unittest

from tests.mock_vcf_installer import ContractMock
from vcf_installer import (
    ChangeReport,
    DepotSettings,
    InstallerClient,
    ProxyConfiguration,
    StepResult,
    VCFInstallerAPIError,
    configure_depot_access,
)


ROOT = Path(__file__).resolve().parents[1]


class ContractFixtureTests(unittest.TestCase):
    def test_contract_and_provenance_are_pinned_to_the_same_spec_operations(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        expected_sha = "c3f3b52c845dd967cabbc21680e893292077d5ba"
        expected_path = "specifications/vcf-installer/vcf-installer-openapi.json"
        expected_ids = [
            "updateProxyConfiguration",
            "updateDepotSettings",
            "syncDepotMetadata",
        ]

        self.assertEqual(contract["derivedFrom"]["commitSha"], expected_sha)
        self.assertEqual(contract["derivedFrom"]["specPath"], expected_path)
        self.assertEqual(sources["commitSha"], expected_sha)
        self.assertEqual(sources["specPath"], expected_path)
        self.assertEqual(list(contract["operations"]), expected_ids)
        self.assertEqual(
            [source["operationId"] for source in sources["operationIds"]], expected_ids
        )
        for source in sources["operationIds"]:
            operation = contract["operations"][source["operationId"]]
            self.assertEqual(operation["method"], source["method"])
            self.assertEqual(operation["path"], source["path"])

    def test_client_package_has_no_third_party_imports(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"__future__", "vcf_installer"}
        package_root = ROOT / "vcf_installer"
        for source_path in package_root.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path.name)
            imported_roots: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.extend(alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.append(node.module.partition(".")[0])
            for imported_root in imported_roots:
                local_module = ROOT / f"{imported_root}.py"
                local_package = ROOT / imported_root / "__init__.py"
                self.assertTrue(
                    imported_root in allowed
                    or local_module.is_file()
                    or local_package.is_file(),
                    f"{source_path.relative_to(ROOT)} imports third-party module "
                    f"{imported_root}, but this task is stdlib-only",
                )


class DepotBootstrapTests(unittest.TestCase):
    def test_success_returns_all_accepted_steps_without_empty_account_objects(self) -> None:
        with ContractMock(fail_operation=None) as mock:
            report = configure_depot_access(
                InstallerClient(mock.base_url, timeout=2.0),
                ProxyConfiguration(is_enabled=False),
                DepotSettings(),
            )
            requests = list(mock.request_log)

        self.assertIsInstance(report, ChangeReport)
        self.assertIsInstance(report.steps, tuple)
        self.assertEqual(
            [(step.operation_id, step.http_status) for step in report.steps],
            [
                ("updateProxyConfiguration", 202),
                ("updateDepotSettings", 202),
                ("syncDepotMetadata", 202),
            ],
        )
        self.assertEqual(
            [step.response for step in report.steps],
            [
                mock.success_responses["updateProxyConfiguration"],
                mock.success_responses["updateDepotSettings"],
                mock.success_responses["syncDepotMetadata"],
            ],
        )
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0].body, b'{"isEnabled":false}')
        self.assertEqual(
            requests[1].body,
            b'{"depotConfiguration":{"isOfflineDepot":false}}',
        )
        self.assertEqual(requests[2].body, b"")

    def test_late_sync_failure_preserves_accepted_steps_and_exact_wire_shape(self) -> None:
        activation_code = "ACTIVATION-CODE-VCF91"
        with ContractMock() as mock:
            client = InstallerClient(mock.base_url, timeout=2.0)
            proxy = ProxyConfiguration(
                is_enabled=True,
                host="proxy.example.com",
                port=3128,
            )
            depot = DepotSettings(
                download_activation_code=activation_code,
                is_offline_depot=False,
            )

            with self.assertRaises(VCFInstallerAPIError) as caught:
                configure_depot_access(client, proxy, depot)

            error = caught.exception
            self.assertEqual(error.operation_id, "syncDepotMetadata")
            self.assertEqual(error.status, 500)
            self.assertEqual(error.error, mock.sync_error)
            self.assertIn("VCF_DEPOT_SYNC_FAILED", str(error))
            self.assertIn("Depot metadata index could not be refreshed", str(error))
            self.assertNotIn(activation_code, str(error))
            self.assertIsInstance(error.completed, tuple)
            self.assertEqual(len(error.completed), 2)
            self.assertTrue(all(isinstance(item, StepResult) for item in error.completed))
            self.assertEqual(
                [(item.operation_id, item.http_status) for item in error.completed],
                [
                    ("updateProxyConfiguration", 202),
                    ("updateDepotSettings", 202),
                ],
            )
            self.assertEqual(
                error.completed[0].response,
                {
                    "id": "task-proxy-91",
                    "name": "Update Proxy Configuration",
                    "status": "IN_PROGRESS",
                    "creationTimestamp": "2026-05-13T12:00:00Z",
                },
            )
            self.assertEqual(
                error.completed[1].response,
                {
                    "vmwareAccount": {
                        "status": "DEPOT_CONNECTION_SUCCESSFUL",
                        "message": "Credentials accepted",
                    },
                    "depotConfiguration": {"isOfflineDepot": False},
                },
            )

            requests = list(mock.request_log)

        self.assertEqual(len(requests), 3, "stop after the first failing operation")
        self.assertEqual(
            [(request.method, request.path, request.query) for request in requests],
            [
                ("PATCH", "/v1/system/proxy-configuration", ""),
                ("PUT", "/v1/system/settings/depot", ""),
                ("PATCH", "/v1/system/settings/depot/depot-sync-info", ""),
            ],
        )

        expected_proxy = b'{"isEnabled":true,"host":"proxy.example.com","port":3128}'
        expected_depot = (
            b'{"vmwareAccount":{"downloadActivationCode":"ACTIVATION-CODE-VCF91"},'
            b'"depotConfiguration":{"isOfflineDepot":false}}'
        )
        self.assertEqual(requests[0].body, expected_proxy)
        self.assertEqual(requests[1].body, expected_depot)
        self.assertEqual(requests[2].body, b"")

        self.assertEqual(requests[0].headers.get("accept"), "application/json")
        self.assertEqual(requests[1].headers.get("accept"), "application/json")
        self.assertEqual(requests[2].headers.get("accept"), "application/json")
        self.assertEqual(requests[0].headers.get("content-type"), "application/json")
        self.assertEqual(requests[1].headers.get("content-type"), "application/json")
        self.assertNotIn("content-type", requests[2].headers)
        self.assertEqual(requests[0].headers.get("content-length"), str(len(expected_proxy)))
        self.assertEqual(requests[1].headers.get("content-length"), str(len(expected_depot)))
        self.assertEqual(requests[2].headers.get("content-length"), "0")

        # These assertions make omission failures easy to diagnose independently of bytes.
        proxy_body = json.loads(requests[0].body)
        depot_body = json.loads(requests[1].body)
        self.assertEqual(set(proxy_body), {"isEnabled", "host", "port"})
        self.assertNotIn("isConfigured", proxy_body)
        self.assertEqual(set(depot_body), {"vmwareAccount", "depotConfiguration"})
        self.assertEqual(set(depot_body["vmwareAccount"]), {"downloadActivationCode"})
        self.assertEqual(set(depot_body["depotConfiguration"]), {"isOfflineDepot"})
        self.assertFalse(depot_body["depotConfiguration"]["isOfflineDepot"])

    def test_explicit_false_is_sent_while_unset_proxy_fields_are_omitted(self) -> None:
        with ContractMock() as mock:
            client = InstallerClient(mock.base_url, timeout=2.0)
            status, response = client.update_proxy_configuration(
                ProxyConfiguration(is_enabled=False)
            )
            requests = list(mock.request_log)

        self.assertEqual(status, 202)
        self.assertEqual(response["id"], "task-proxy-91")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].body, b'{"isEnabled":false}')

    def test_middle_failure_stops_before_sync_and_keeps_only_the_proxy_step(self) -> None:
        with ContractMock(fail_operation="updateDepotSettings") as mock:
            client = InstallerClient(mock.base_url, timeout=2.0)
            with self.assertRaises(VCFInstallerAPIError) as caught:
                configure_depot_access(
                    client,
                    ProxyConfiguration(is_enabled=True),
                    DepotSettings(download_token="token-accepted-by-mock"),
                )
            requests = list(mock.request_log)

        error = caught.exception
        self.assertEqual(error.operation_id, "updateDepotSettings")
        self.assertEqual(error.status, 500)
        self.assertEqual(error.error, mock.error_responses["updateDepotSettings"])
        self.assertIsInstance(error.completed, tuple)
        self.assertEqual(len(error.completed), 1)
        self.assertEqual(error.completed[0].operation_id, "updateProxyConfiguration")
        self.assertEqual(error.completed[0].http_status, 202)
        self.assertEqual(
            error.completed[0].response,
            mock.success_responses["updateProxyConfiguration"],
        )
        self.assertEqual(
            [(request.method, request.path) for request in requests],
            [("PATCH", "/v1/system/proxy-configuration"), ("PUT", "/v1/system/settings/depot")],
        )

    def test_all_writable_fields_use_exact_schema_names_and_keep_false_and_zero(self) -> None:
        with ContractMock(fail_operation=None) as mock:
            client = InstallerClient(mock.base_url, timeout=2.0)
            client.update_proxy_configuration(
                ProxyConfiguration(
                    is_enabled=True,
                    host="proxy.example.com",
                    port=0,
                    transfer_protocol="HTTPS",
                    username="proxy-user",
                    password="proxy-password",
                    is_authenticated=False,
                )
            )
            client.update_depot_settings(
                DepotSettings(
                    download_activation_code="activation-code",
                    download_token="download-token",
                    username="online-user",
                    password="online-password",
                    offline_username="offline-user",
                    offline_password="offline-password",
                    is_offline_depot=False,
                    hostname="depot.example.com",
                    port=0,
                    url="https://depot.example.com/content",
                )
            )
            requests = list(mock.request_log)

        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[0].body,
            b'{"isEnabled":true,"host":"proxy.example.com","port":0,'
            b'"transferProtocol":"HTTPS","username":"proxy-user",'
            b'"password":"proxy-password","isAuthenticated":false}',
        )
        self.assertEqual(
            requests[1].body,
            b'{"vmwareAccount":{"downloadActivationCode":"activation-code",'
            b'"downloadToken":"download-token","username":"online-user",'
            b'"password":"online-password"},"offlineAccount":{'
            b'"username":"offline-user","password":"offline-password"},'
            b'"depotConfiguration":{"isOfflineDepot":false,'
            b'"hostname":"depot.example.com","port":0,'
            b'"url":"https://depot.example.com/content"}}',
        )

    def test_first_non_pinned_2xx_status_stops_the_workflow_as_an_api_error(self) -> None:
        with ContractMock(
            fail_operation=None,
            status_overrides={"updateProxyConfiguration": 200},
        ) as mock:
            client = InstallerClient(mock.base_url, timeout=2.0)
            with self.assertRaises(VCFInstallerAPIError) as caught:
                configure_depot_access(
                    client,
                    ProxyConfiguration(is_enabled=True),
                    DepotSettings(),
                )
            requests = list(mock.request_log)

        error = caught.exception
        self.assertEqual(error.operation_id, "updateProxyConfiguration")
        self.assertEqual(error.status, 200)
        self.assertEqual(error.error, mock.success_responses["updateProxyConfiguration"])
        self.assertEqual(error.completed, ())
        self.assertEqual(len(requests), 1)

    def test_public_report_value_is_immutable(self) -> None:
        step = StepResult("syncDepotMetadata", 202, {"syncStatus": "IN_PROGRESS"})
        report = ChangeReport((step,))
        self.assertEqual(report.steps, (step,))
        with self.assertRaises((AttributeError, TypeError)):
            report.steps += (step,)  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main(verbosity=2)
