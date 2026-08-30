"""Protected integration verifier for the VCF Installer seed."""

from __future__ import annotations

import json
import ast
from contextlib import ExitStack
import inspect
import sys
import time
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from testsupport import ContractPinnedInstallerMock  # noqa: E402
from vcf_installer import InstallerAPIError, VCFInstallerClient  # noqa: E402
import vcf_installer.client as client_module  # noqa: E402


class InstallerIntegrationTests(unittest.TestCase):
    def test_public_signatures_and_standard_library_only(self) -> None:
        self.assertEqual(
            "(self, base_url: 'str', *, timeout: 'float' = 10.0) -> 'None'",
            str(inspect.signature(VCFInstallerClient.__init__)),
        )
        self.assertEqual(
            "(self, *, username: 'str | None' = None, password: 'str | None' = None, api_key: 'str | None' = None, id_token: 'str | None' = None) -> 'dict[str, Any]'",
            str(inspect.signature(VCFInstallerClient.create_token)),
        )
        self.assertEqual(
            "(self) -> 'str'",
            str(inspect.signature(VCFInstallerClient.refresh_access_token)),
        )
        self.assertEqual(
            "(self, sddc_spec: 'Mapping[str, Any]', *, skip_validations: 'bool | None' = None) -> 'dict[str, Any]'",
            str(inspect.signature(VCFInstallerClient.deploy_sddc)),
        )
        self.assertEqual(
            "(self, task_id: 'str') -> 'dict[str, Any]'",
            str(inspect.signature(VCFInstallerClient.get_sddc_task)),
        )
        self.assertEqual(
            "(self, sddc_spec: 'Mapping[str, Any]', *, skip_validations: 'bool | None' = None, poll_interval: 'float' = 1.0) -> 'dict[str, Any]'",
            str(inspect.signature(VCFInstallerClient.deploy_and_wait)),
        )

        client_source = ROOT / "src" / "vcf_installer" / "client.py"
        tree = ast.parse(client_source.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.partition(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertLessEqual(imported_roots, sys.stdlib_module_names | {"__future__"})

    def test_credentials_refresh_and_skip_validation_values(self) -> None:
        sddc_spec = MappingProxyType(
            {
                "sddcId": "query-shape-sddc",
                "nested": {"enabled": False, "count": 0},
            }
        )

        with ContractPinnedInstallerMock() as mock:
            client = VCFInstallerClient(mock.base_url, timeout=2)
            pair = client.create_token(
                username="named-user",
                api_key="api-key-value",
                id_token="id-token-value",
            )
            false_result = client.deploy_sddc(
                sddc_spec, skip_validations=False
            )
            true_result = client.deploy_sddc(
                sddc_spec, skip_validations=True
            )
            refreshed = client.refresh_access_token()

        self.assertEqual("expired-access", pair["accessToken"])
        self.assertEqual("fresh-access", refreshed)
        self.assertEqual("fresh-access", client.access_token)
        self.assertEqual("refresh-token-1", client.refresh_token_id)
        self.assertEqual("IN_PROGRESS", false_result["status"])
        self.assertEqual("IN_PROGRESS", true_result["status"])

        token_request, false_request, true_request, refresh_request = mock.request_log
        self.assertEqual(
            {
                "username": "named-user",
                "apiKey": "api-key-value",
                "idToken": "id-token-value",
            },
            token_request["json_body"],
        )
        self.assertNotIn("password", token_request["json_body"])
        self.assertEqual("skipValidations=false", false_request["query"])
        self.assertEqual("/v1/sddcs?skipValidations=false", false_request["target"])
        self.assertEqual("skipValidations=true", true_request["query"])
        self.assertEqual("/v1/sddcs?skipValidations=true", true_request["target"])
        self.assertEqual(sddc_spec, false_request["json_body"])
        self.assertEqual(sddc_spec, true_request["json_body"])
        self.assertEqual("refresh-token-1", refresh_request["json_body"])
        self.assertNotIn("authorization", refresh_request["headers"])

    def test_deploy_and_wait_sleeps_between_nonterminal_results(self) -> None:
        client = VCFInstallerClient("https://installer.example.test")
        sddc_spec = {"sddcId": "sleep-check"}
        original_sleep = time.sleep
        sleep = Mock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(time, "sleep", sleep))
            for name, value in vars(client_module).items():
                if value is original_sleep:
                    stack.enter_context(patch.object(client_module, name, sleep))
            deploy = stack.enter_context(patch.object(
                client,
                "deploy_sddc",
                return_value={"id": "task-1", "status": "IN_PROGRESS"},
            ))
            get_task = stack.enter_context(patch.object(
                client,
                "get_sddc_task",
                side_effect=[
                    {"id": "task-1", "status": "IN_PROGRESS"},
                    {"id": "task-1", "status": "ROLLBACK_SUCCESS"},
                ],
            ))
            result = client.deploy_and_wait(
                sddc_spec,
                skip_validations=True,
                poll_interval=0.25,
            )

        self.assertEqual("ROLLBACK_SUCCESS", result["status"])
        deploy.assert_called_once_with(sddc_spec, skip_validations=True)
        self.assertEqual([call("task-1"), call("task-1")], get_task.call_args_list)
        self.assertEqual([call(0.25), call(0.25)], sleep.call_args_list)

    def test_expired_token_refreshes_without_resubmitting(self) -> None:
        sddc_spec = {
            "sddcId": "lab-sddc",
            "managementPoolName": "bringup-pool",
            "dnsSpec": {"subdomain": "vcf.example.test"},
        }

        with ContractPinnedInstallerMock() as mock:
            client = VCFInstallerClient(mock.base_url, timeout=2)
            pair = client.create_token(
                username="admin@local",
                password="Secret-For-Loopback",
            )
            result = client.deploy_and_wait(sddc_spec, poll_interval=0)

        self.assertEqual("expired-access", pair["accessToken"])
        self.assertEqual("fresh-access", client.access_token)
        self.assertEqual("COMPLETED_WITH_SUCCESS", result["status"])

        log = mock.request_log
        self.assertEqual(
            [
                ("POST", "/v1/tokens", "createToken", 201),
                ("POST", "/v1/sddcs", "deploySddc", 202),
                ("GET", "/v1/sddcs/123e4567-e89b-42d3-a456-556642440000", "getSddcTaskByID", 200),
                ("GET", "/v1/sddcs/123e4567-e89b-42d3-a456-556642440000", "getSddcTaskByID", 401),
                ("PATCH", "/v1/tokens/access-token/refresh", "refreshAccessToken", 200),
                ("GET", "/v1/sddcs/123e4567-e89b-42d3-a456-556642440000", "getSddcTaskByID", 200),
                ("GET", "/v1/sddcs/123e4567-e89b-42d3-a456-556642440000", "getSddcTaskByID", 200),
            ],
            [
                (
                    entry["method"],
                    entry["target"],
                    entry["operationId"],
                    entry["response_status"],
                )
                for entry in log
            ],
        )

        token_request, deploy_request, *poll_and_refresh = log
        self.assertEqual(
            {"username": "admin@local", "password": "Secret-For-Loopback"},
            token_request["json_body"],
        )
        self.assertNotIn("apiKey", token_request["json_body"])
        self.assertNotIn("idToken", token_request["json_body"])
        self.assertNotIn("authorization", token_request["headers"])

        self.assertEqual("", deploy_request["query"])
        self.assertNotIn("?", deploy_request["target"])
        self.assertEqual(sddc_spec, deploy_request["json_body"])
        self.assertEqual(
            "Bearer expired-access", deploy_request["headers"].get("authorization")
        )

        refresh_request = log[4]
        self.assertEqual("refresh-token-1", refresh_request["json_body"])
        self.assertEqual(b'"refresh-token-1"', refresh_request["raw_body"])
        self.assertNotIn("authorization", refresh_request["headers"])

        for entry in log:
            self.assertEqual("application/json", entry["headers"].get("accept"))
            if entry["raw_body"]:
                self.assertEqual(
                    "application/json", entry["headers"].get("content-type")
                )

        self.assertEqual(1, sum(entry["operationId"] == "deploySddc" for entry in log))
        failed_poll = log[3]
        retried_poll = log[5]
        self.assertEqual(failed_poll["method"], retried_poll["method"])
        self.assertEqual(failed_poll["target"], retried_poll["target"])

    def test_http_error_retains_status_path_and_json_body(self) -> None:
        with ContractPinnedInstallerMock() as mock:
            client = VCFInstallerClient(mock.base_url, timeout=2)
            client.create_token(username="admin@local", password="loopback-only")
            with self.assertRaises(InstallerAPIError) as raised:
                client.get_sddc_task("missing-task")

        error = raised.exception
        self.assertEqual(404, error.status)
        self.assertEqual("GET", error.method)
        self.assertEqual("/v1/sddcs/missing-task", error.path)
        self.assertEqual(
            {"message": "operation not in pinned contract"}, error.body
        )

    def test_contract_and_source_pin_are_exact(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text())
        sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())
        operation_ids = [item["operationId"] for item in contract["operations"]]
        self.assertEqual(
            [
                "createToken",
                "deploySddc",
                "refreshAccessToken",
                "getSddcTaskByID",
            ],
            operation_ids,
        )
        self.assertEqual(operation_ids, sources["operationIds"])
        self.assertEqual("Apache-2.0", sources["license"])
        self.assertEqual(
            "https://github.com/vmware/vcf-api-specs", sources["repository"]
        )
        self.assertEqual("9.0.0.0", sources["tag"])
        self.assertEqual(
            "85151f6b1bb58f13b6ac0304bfec53904bea085f",
            sources["commitSha"],
        )
        self.assertEqual(
            "specifications/vcf-installer/vcf-installer-openapi.json",
            sources["specPath"],
        )


if __name__ == "__main__":
    unittest.main()
