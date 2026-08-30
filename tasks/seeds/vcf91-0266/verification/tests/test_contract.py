"""The contract must stay pinned to the published OpenAPI specification."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
OPERATION_IDS = [
    "acquireToken",
    "getLdapDirectories",
    "getLdapSyncLogs",
    "getLdapSyncLogById",
    "queryAlert",
    "getAlertContributingSymptoms",
    "getSymptoms",
    "releaseToken",
]


def _load(name):
    return json.loads((ROOT / "docs" / name).read_text(encoding="utf-8"))


class OfficialSourcesTests(unittest.TestCase):
    def setUp(self):
        self.sources = _load("official_sources.json")

    def test_pinned_to_the_specification_repository_and_commit(self):
        self.assertEqual(
            self.sources["repository"], "https://github.com/vmware/vcf-api-specs"
        )
        self.assertEqual(self.sources["license"], "Apache-2.0")
        self.assertEqual(self.sources["commit_sha"], COMMIT)
        self.assertEqual(self.sources["spec_path"], SPEC_PATH)
        self.assertIn(COMMIT, self.sources["source_url"])
        self.assertIn(SPEC_PATH, self.sources["source_url"])

    def test_is_the_operations_api_not_log_management(self):
        self.assertNotIn("log-management", self.sources["spec_path"])
        self.assertEqual(
            self.sources["spec_info_title"], "VMware Cloud Foundation Operations API"
        )
        self.assertEqual(self.sources["spec_info_version"], "9.1.0.0")

    def test_records_every_operation_id_with_its_pointer(self):
        self.assertEqual(self.sources["operationIds"], OPERATION_IDS)
        recorded = [op["operationId"] for op in self.sources["operations"]]
        self.assertEqual(recorded, OPERATION_IDS)
        for operation in self.sources["operations"]:
            pointer = operation["openapi_pointer"]
            self.assertTrue(pointer.startswith("#/paths/~1api~1"), pointer)
            self.assertTrue(pointer.endswith("/" + operation["method"].lower()), pointer)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = _load("contract.json")

    def test_provenance_matches_official_sources(self):
        sources = _load("official_sources.json")
        source = self.contract["x-official-source"]
        self.assertEqual(source["commit_sha"], sources["commit_sha"])
        self.assertEqual(source["spec_path"], sources["spec_path"])
        self.assertEqual(source["spec_blob_sha"], sources["spec_blob_sha"])
        self.assertEqual(source["license"], "Apache-2.0")

    def test_serves_exactly_the_named_operations(self):
        self.assertEqual(self.contract["x-operation-ids"], OPERATION_IDS)
        found = [
            operation["operationId"]
            for path_item in self.contract["paths"].values()
            for operation in path_item.values()
        ]
        self.assertEqual(found, OPERATION_IDS)

    def test_base_path_and_security_scheme_come_from_the_specification(self):
        self.assertEqual(self.contract["openapi"], "3.0.1")
        self.assertEqual(self.contract["info"]["version"], "9.1.0.0")
        self.assertEqual(self.contract["servers"][0]["url"], "/suite-api")
        self.assertEqual(self.contract["x-wire-rules"]["server_base_path"], "/suite-api")
        scheme = self.contract["components"]["securitySchemes"]["Token-based-authorization"]
        self.assertEqual(
            (scheme["type"], scheme["in"], scheme["name"]),
            ("apiKey", "header", "Authorization"),
        )

    def test_acquire_token_is_the_only_unsecured_operation(self):
        unsecured = [
            operation["operationId"]
            for path_item in self.contract["paths"].values()
            for operation in path_item.values()
            if operation.get("security") == []
        ]
        self.assertEqual(unsecured, ["acquireToken"])
        self.assertEqual(
            self.contract["x-wire-rules"]["authorization"]["omitted_on"], ["acquireToken"]
        )

    def test_optional_fields_the_task_must_omit_are_declared_optional(self):
        credentials = self.contract["components"]["schemas"]["username-password"]
        self.assertEqual(list(credentials["properties"]), ["authSource", "password", "username"])
        self.assertEqual(sorted(credentials["required"]), ["password", "username"])

        query = self.contract["components"]["schemas"]["alert-query"]
        self.assertEqual(
            list(query["properties"]),
            ["activeOnly", "alertCriticality", "alertName", "alertStatus"],
        )
        self.assertNotIn("required", query)

        sync_logs = self.contract["paths"][
            "/api/fleet-management/iam/identity-providers/{idpConfigId}"
            "/ldap-directories/{ldapDirectoryId}/sync-logs"
        ]["get"]
        optional = {
            parameter["name"]
            for parameter in sync_logs["parameters"]
            if parameter["in"] == "query" and not parameter.get("required")
        }
        self.assertEqual(optional, {"last", "page", "pageSize"})

    def test_query_parameter_order_is_the_declared_order(self):
        symptoms = self.contract["paths"]["/api/symptoms"]["get"]
        names = [p["name"] for p in symptoms["parameters"] if p["in"] == "query"]
        self.assertEqual(
            names, ["resourceId", "activeOnly", "includeAlarmInfo", "page", "pageSize"]
        )

    def test_contributing_symptoms_takes_an_array_of_identifiers(self):
        operation = self.contract["paths"]["/api/alerts/contributingsymptoms"]["get"]
        parameter = operation["parameters"][0]
        self.assertEqual(parameter["name"], "id")
        self.assertTrue(parameter["required"])
        self.assertEqual(parameter["schema"]["type"], "array")


if __name__ == "__main__":
    unittest.main()
