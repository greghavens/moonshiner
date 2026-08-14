from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import tomllib
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from mock_vcf_networks import ContractMock
from vcf_networks import OperationsForNetworksClient, SearchBasedAlertUpdate


ROOT = Path(__file__).resolve().parent


class ContractProvenanceTests(unittest.TestCase):
    def test_package_is_stdlib_only(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(project["project"]["dependencies"], [])
        for source_path in sorted((ROOT / "vcf_networks").glob("*.py")):
            tree = ast.parse(source_path.read_text(), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.partition(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = [(node.module or "").partition(".")[0]]
                else:
                    continue
                for root in roots:
                    self.assertIn(
                        root,
                        sys.stdlib_module_names | {"vcf_networks"},
                        f"non-stdlib import {root!r} in {source_path.name}",
                    )

    def test_contract_and_official_source_are_pinned_to_the_openapi_spec(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text())
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text()
        )
        self.assertEqual(contract["openapi"], "3.0.1")
        self.assertEqual(contract["version"], "9.0.0.0")
        self.assertEqual(contract["server_base_path"], "/api/ni")
        self.assertEqual(
            [operation["operationId"] for operation in contract["operations"]],
            ["updateSearchBasedAlertConfig"],
        )
        operation = contract["operations"][0]
        self.assertEqual(operation["method"], "PUT")
        self.assertEqual(
            operation["path"], "/settings/alerts/search-based-alerts/{id}"
        )
        self.assertTrue(operation["request_body"]["required"])
        request_schema = contract["schemas"]["SearchBasedAlertConfigRequest"]
        self.assertEqual(request_schema["required"], [])
        self.assertEqual(
            list(request_schema["properties"]),
            [
                "alert_name",
                "search_criteria",
                "generate_alert_criteria",
                "alert_type",
                "severity",
                "notification_settings",
            ],
        )
        self.assertEqual(
            contract["security_schemes"]["ApiKeyAuth"],
            {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "value_prefix": "NetworkInsight ",
            },
        )
        self.assertEqual(
            sources,
            {
                "sources": [
                    {
                        "repository": "https://github.com/vmware/vcf-api-specs",
                        "tag": "9.0.0.0",
                        "commit_sha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
                        "spec_path": "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml",
                        "license": "Apache-2.0",
                        "operation_ids": ["updateSearchBasedAlertConfig"],
                    }
                ]
            },
        )

    def test_mock_rejects_every_route_not_named_by_the_contract(self) -> None:
        with ContractMock(failures_after_apply=0) as mock:
            with self.assertRaises(HTTPError) as caught:
                urlopen(mock.url + "/api/ni/version")
            self.assertEqual(caught.exception.code, 405)
            caught.exception.close()
            self.assertEqual(mock.requests, [])


class ClientWireTests(unittest.TestCase):
    def test_ambiguous_update_retries_identically_and_changes_state_once(self) -> None:
        update = SearchBasedAlertUpdate(
            alert_name="Edge packet loss",
            search_criteria="flows where loss_pct > 2",
            generate_alert_criteria="SEARCH_RESULT_CHANGE",
            severity="Warning",
        )
        expected_body = (
            b'{"alert_name":"Edge packet loss",'
            b'"search_criteria":"flows where loss_pct > 2",'
            b'"generate_alert_criteria":"SEARCH_RESULT_CHANGE",'
            b'"severity":"Warning"}'
        )

        with ContractMock(failures_after_apply=1) as mock:
            client = OperationsForNetworksClient(
                mock.url + "/", "fixture-token", timeout=2
            )
            response = client.update_search_based_alert("alert/id 7?#%\u00fc", update)

            self.assertEqual(len(mock.requests), 2)
            for request in mock.requests:
                self.assertEqual(request.method, "PUT")
                self.assertEqual(
                    request.target,
                    (
                        "/api/ni/settings/alerts/search-based-alerts/"
                        "alert%2Fid%207%3F%23%25%C3%BC"
                    ),
                )
                self.assertEqual(request.body, expected_body)
                self.assertEqual(
                    request.headers["authorization"],
                    "NetworkInsight fixture-token",
                )
                self.assertEqual(request.headers["accept"], "application/json")
                self.assertEqual(request.headers["content-type"], "application/json")
                self.assertEqual(
                    request.headers["content-length"], str(len(expected_body))
                )
                self.assertNotIn("idempotency-key", request.headers)
            self.assertEqual(mock.requests[0], mock.requests[1])
            self.assertEqual(mock.effect_count, 1)
            self.assertEqual(
                mock.state["alert/id 7?#%\u00fc"],
                {
                    "alert_name": "Edge packet loss",
                    "search_criteria": "flows where loss_pct > 2",
                    "generate_alert_criteria": "SEARCH_RESULT_CHANGE",
                    "severity": "Warning",
                },
            )
            self.assertEqual(
                response,
                {
                    "entity_id": "alert/id 7?#%\u00fc",
                    "enabled": True,
                    "alert_name": "Edge packet loss",
                    "search_criteria": "flows where loss_pct > 2",
                    "generate_alert_criteria": "SEARCH_RESULT_CHANGE",
                    "severity": "Warning",
                },
            )

    def test_explicit_false_and_null_are_present_while_unset_fields_are_omitted(
        self,
    ) -> None:
        update = SearchBasedAlertUpdate(
            alert_name="Push health",
            alert_type=None,
            notification_settings=[
                {"type": "PUSH_NOTIFICATION", "enabled": False}
            ],
        )
        expected = {
            "alert_name": "Push health",
            "alert_type": None,
            "notification_settings": [
                {"type": "PUSH_NOTIFICATION", "enabled": False}
            ],
        }

        with ContractMock(failures_after_apply=0) as mock:
            client = OperationsForNetworksClient(
                mock.url, "fixture-token", timeout=2
            )
            client.update_search_based_alert("alert-8", update)
            self.assertEqual(len(mock.requests), 1)
            self.assertEqual(json.loads(mock.requests[0].body), expected)
            self.assertEqual(list(json.loads(mock.requests[0].body)), list(expected))
            self.assertNotIn("search_criteria", json.loads(mock.requests[0].body))
            self.assertNotIn(
                "generate_alert_criteria", json.loads(mock.requests[0].body)
            )
            self.assertNotIn("severity", json.loads(mock.requests[0].body))

    def test_explicit_empty_values_are_not_mistaken_for_unset(self) -> None:
        update = SearchBasedAlertUpdate(
            alert_name="",
            search_criteria="",
            notification_settings=[],
        )
        expected = {
            "alert_name": "",
            "search_criteria": "",
            "notification_settings": [],
        }

        with ContractMock(failures_after_apply=0) as mock:
            client = OperationsForNetworksClient(
                mock.url, "fixture-token", timeout=2
            )
            client.update_search_based_alert("alert-empty", update)
            self.assertEqual(len(mock.requests), 1)
            self.assertEqual(json.loads(mock.requests[0].body), expected)


if __name__ == "__main__":
    unittest.main()
