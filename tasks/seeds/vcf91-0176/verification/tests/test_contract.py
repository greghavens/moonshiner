import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
EXPECTED_SPEC = "specifications/vcf-operations/log-management-openapi.json"


class ContractProvenanceTests(unittest.TestCase):
    def test_official_source_is_pinned_to_specification(self):
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sources["repository"], "https://github.com/vmware/vcf-api-specs")
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(sources["commit_sha"], EXPECTED_COMMIT)
        self.assertEqual(sources["spec_path"], EXPECTED_SPEC)
        self.assertEqual(sources["operationIds"], ["updateLogForwarder"])
        self.assertIn(EXPECTED_COMMIT, sources["source_url"])
        self.assertIn(EXPECTED_SPEC, sources["source_url"])

    def test_focused_contract_has_exact_operation_and_security(self):
        contract = json.loads(
            (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["openapi"], "3.0.1")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(
            contract["x-official-source"],
            {
                "repository": "https://github.com/vmware/vcf-api-specs",
                "license": "Apache-2.0",
                "commit_sha": EXPECTED_COMMIT,
                "spec_path": EXPECTED_SPEC,
            },
        )
        self.assertEqual(list(contract["paths"]), ["/api/v2/logs/forwarders/{id}"])
        path_item = contract["paths"]["/api/v2/logs/forwarders/{id}"]
        self.assertEqual(list(path_item), ["put"])
        operation = path_item["put"]
        self.assertEqual(operation["operationId"], "updateLogForwarder")
        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/LogForwarder",
        )
        security = contract["components"]["securitySchemes"]["OPSTokenAuthorization"]
        self.assertEqual(
            (security["type"], security["in"], security["name"]),
            ("apiKey", "header", "X-JWT-Token"),
        )
        properties = contract["components"]["schemas"]["LogForwarder"]["properties"]
        self.assertTrue(properties["id"]["readOnly"])
        self.assertEqual(properties["protocol"]["enum"], ["SYSLOG", "RAW", "RAWPLUS"])
        self.assertEqual(properties["transportProtocol"]["enum"], ["TCP", "UDP"])


if __name__ == "__main__":
    unittest.main()
