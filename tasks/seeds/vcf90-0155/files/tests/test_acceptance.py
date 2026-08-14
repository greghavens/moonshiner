from __future__ import annotations

import http.client
import json
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from tests.mock_vcf import MockVCFAutomation
from vcf_automation import VCFAutomationClient


ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = (
    "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/"
    "deployment/api/deployments/deploymentId/patch/"
)


class DocumentationTests(unittest.TestCase):
    def test_contract_is_explicitly_reference_derived(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["source"]["kind"], "reference_documentation")
        statement = contract["source"]["statement"]
        self.assertIn("not a published API specification", statement)
        self.assertIn("vmware/vcf-api-specs", statement)
        self.assertEqual(
            [(item["method"], item["path"], item["operation"]) for item in contract["operations"]],
            [("PATCH", "/deployment/api/deployments/{deploymentId}", "Patch Deployment")],
        )

    def test_every_contract_operation_has_a_dated_official_page(self) -> None:
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            sources,
            [
                {
                    "url": SOURCE_URL,
                    "operation": (
                        "PATCH /deployment/api/deployments/{deploymentId} — Patch Deployment"
                    ),
                    "fetched_at": "2026-08-13",
                }
            ],
        )


class ClientWireTests(unittest.TestCase):
    maxDiff = None

    def assert_wire_request(
        self,
        entry: dict[str, object],
        *,
        base_url: str,
        deployment_id: str,
        body: bytes,
    ) -> None:
        authority = urlsplit(base_url).netloc
        path = f"/deployment/api/deployments/{deployment_id}"
        self.assertEqual(entry["request_line"], f"PATCH {path} HTTP/1.1")
        self.assertEqual(entry["method"], "PATCH")
        self.assertEqual(entry["path"], path)
        headers = [(name.lower(), value) for name, value in entry["headers"]]
        required = {
            "host": authority,
            "authorization": "Bearer fixture-token",
            "accept": "application/json",
            "content-type": "application/json",
            "content-length": str(len(body)),
        }
        for name, value in required.items():
            self.assertEqual(
                [header_value for header_name, header_value in headers if header_name == name],
                [value],
            )
        self.assertEqual(entry["body"], body)

    def test_lost_response_retries_same_patch_with_one_logical_effect(self) -> None:
        body = b'{"description":"Quarterly refresh"}'
        with MockVCFAutomation(drop_first_response=True) as service:
            client = VCFAutomationClient(service.base_url, "fixture-token")
            result = client.update_deployment(
                "deployment-42", description="Quarterly refresh"
            )

            self.assertEqual(result["id"], "deployment-42")
            self.assertEqual(result["description"], "Quarterly refresh")
            self.assertEqual(service.effect_count, 1)
            self.assertEqual(len(service.request_log), 2)
            for entry in service.request_log:
                self.assert_wire_request(
                    entry,
                    base_url=service.base_url,
                    deployment_id="deployment-42",
                    body=body,
                )

    def test_unset_optional_fields_are_absent_not_empty(self) -> None:
        updated_name = "Renamed deployment – 東京"
        body = '{"name":"Renamed deployment – 東京"}'.encode("utf-8")
        with MockVCFAutomation(drop_first_response=False) as service:
            client = VCFAutomationClient(service.base_url, "fixture-token")
            result = client.update_deployment("deployment-7", name=updated_name)

            self.assertEqual(result["name"], updated_name)
            self.assertEqual(len(service.request_log), 1)
            self.assert_wire_request(
                service.request_log[0],
                base_url=service.base_url,
                deployment_id="deployment-7",
                body=body,
            )
            decoded = json.loads(service.request_log[0]["body"])
            self.assertEqual(decoded, {"name": updated_name})
            self.assertNotIn("description", decoded)
            self.assertNotIn("iconId", decoded)

    def test_all_documented_fields_use_their_wire_names(self) -> None:
        icon_id = "11111111-2222-3333-4444-555555555555"
        body = (
            b'{"name":"Named","description":"Described","iconId":'
            b'"11111111-2222-3333-4444-555555555555"}'
        )
        with MockVCFAutomation(drop_first_response=False) as service:
            client = VCFAutomationClient(service.base_url, "fixture-token")
            result = client.update_deployment(
                "deployment-8",
                name="Named",
                description="Described",
                icon_id=icon_id,
            )

            self.assertEqual(result["iconId"], icon_id)
            self.assertEqual(len(service.request_log), 1)
            self.assert_wire_request(
                service.request_log[0],
                base_url=service.base_url,
                deployment_id="deployment-8",
                body=body,
            )

    def test_mock_rejects_operations_absent_from_contract(self) -> None:
        with MockVCFAutomation(drop_first_response=False) as service:
            parsed = urlsplit(service.base_url)
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
            connection.request("GET", "/deployment/api/deployments/deployment-9")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 405)
            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=2)
            connection.request("PATCH", "/not-in-the-contract", body=b"{}")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 404)
            connection.close()


if __name__ == "__main__":
    unittest.main()
