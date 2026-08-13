"""Checks on the derived contract and on the sources it was derived from.

PROTECTED FILE -- do not modify.

Nothing here reaches the network. The assertions are about what the recorded
contract says and whether the recorded provenance is well formed.
"""

import datetime
import json
import os
import re
import unittest
from urllib.parse import urlparse

from mock.server import MACHINE_SPECIFICATION_REQUIRED

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(ROOT, "docs", "official_sources.json")

OPERATION_IDS = ("createMachine", "getRequestTracker", "getMachine")
EXPECTED_OPERATIONS = {
    "createMachine": ("POST", "/iaas/api/machines"),
    "getRequestTracker": ("GET", "/iaas/api/request-tracker/{id}"),
    "getMachine": ("GET", "/iaas/api/machines/{id}"),
}
SOURCE_HOST = "developer.broadcom.com"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EXPECTED_SOURCE_URLS = {
    "createMachine": (
        "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/"
        "latest/iaas/api/machines/post/"
    ),
    "getRequestTracker": (
        "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/"
        "latest/iaas/api/request-tracker/id/get/"
    ),
    "getMachine": (
        "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/"
        "latest/iaas/api/machines/id/get/"
    ),
    "security": (
        "https://developer.broadcom.com/xapis/vm-apps-org-provisioning-service/"
        "latest/api-security-schema/"
    ),
}
EXPECTED_SOURCE_TITLES = {
    "createMachine": "Create Machine",
    "getRequestTracker": "Get Request Tracker",
    "getMachine": "Get Machine",
    "security": "Authentication",
}


def load(path):
    if not os.path.exists(path):
        raise AssertionError("{0} is missing".format(os.path.relpath(path, ROOT)))
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class ContractProvenanceTests(unittest.TestCase):
    """The contract has to say what it was built from."""

    def setUp(self):
        self.contract = load(CONTRACT_PATH)

    def test_identifies_the_product_and_version(self):
        self.assertEqual(self.contract.get("contract_version"), "1")
        product = self.contract.get("product", "")
        self.assertIsInstance(product, str)
        self.assertIn("automation", product.lower())
        self.assertEqual(self.contract.get("product_version"), "9.1")

    def test_states_that_the_source_is_reference_documentation(self):
        source = self.contract.get("source")
        self.assertIsInstance(source, dict, "contract.source is missing")
        self.assertEqual(source.get("type"), "reference-documentation")
        self.assertIs(source.get("published_specification"), False)
        self.assertEqual(source.get("sources_file"), "docs/official_sources.json")

        statement = source.get("statement", "")
        self.assertIsInstance(statement, str)
        self.assertGreaterEqual(len(statement), 40,
                                "the source statement must be a plain sentence, not a token")
        lowered = statement.lower()
        self.assertIn("reference documentation", lowered)
        self.assertTrue(
            any(phrase in lowered for phrase in ("not ", "rather than", "no published")),
            "the statement must say plainly that this is not a published specification",
        )

    def test_records_the_documented_security_scheme(self):
        security = self.contract.get("security")
        self.assertIsInstance(security, dict, "contract.security is missing")
        self.assertEqual(security.get("scheme"), "apiKey")
        self.assertEqual(security.get("in"), "header")
        self.assertEqual(security.get("name"), "Authorization")


class ContractOperationsTests(unittest.TestCase):
    def setUp(self):
        self.contract = load(CONTRACT_PATH)
        operations = self.contract.get("operations")
        self.assertIsInstance(operations, list, "contract.operations is missing")
        self.assertEqual(len(operations), len(OPERATION_IDS),
                         "contract must contain exactly three operations")
        self.ops = {}
        for operation in operations:
            self.assertIsInstance(operation, dict)
            self.assertNotIn(operation.get("id"), self.ops,
                             "operation ids must be unique")
            self.ops[operation.get("id")] = operation

    def test_names_exactly_the_three_contracted_operations(self):
        self.assertEqual(sorted(self.ops), sorted(OPERATION_IDS))

    def test_methods_and_paths_match_the_reference(self):
        for op_id, (method, path) in EXPECTED_OPERATIONS.items():
            operation = self.ops[op_id]
            self.assertEqual(operation.get("method"), method, op_id)
            self.assertEqual(operation.get("path"), path, op_id)

    def test_path_parameters_match_the_reference(self):
        self.assertNotIn("path_parameters", self.ops["createMachine"])
        for op_id in ("getRequestTracker", "getMachine"):
            params = self.ops[op_id].get("path_parameters")
            self.assertIsInstance(params, list)
            self.assertEqual(len(params), 1)
            self.assertEqual(params[0].get("name"), "id")
            self.assertEqual(params[0].get("type"), "string")
            self.assertIs(params[0].get("required"), True)

    def test_every_operation_documents_the_api_version_query_parameter(self):
        for op_id in OPERATION_IDS:
            params = self.ops[op_id].get("query_parameters")
            self.assertIsInstance(params, list, op_id)
            by_name = {param.get("name"): param for param in params}
            self.assertIn("apiVersion", by_name, op_id)
            self.assertIs(by_name["apiVersion"].get("required"), False, op_id)
            self.assertEqual(by_name["apiVersion"].get("type"), "string", op_id)

        self.assertEqual(
            {param.get("name") for param in self.ops["createMachine"]["query_parameters"]},
            {"apiVersion"},
        )
        self.assertEqual(
            {param.get("name") for param in self.ops["getRequestTracker"]["query_parameters"]},
            {"apiVersion"},
        )
        self.assertEqual(
            {param.get("name") for param in self.ops["getMachine"]["query_parameters"]},
            {"apiVersion", "$select"},
        )
        select = {param["name"]: param for param in
                  self.ops["getMachine"]["query_parameters"]}["$select"]
        self.assertEqual(select.get("type"), "string")
        self.assertIs(select.get("required"), False)

    def test_create_machine_body_splits_required_from_optional(self):
        body = self.ops["createMachine"].get("request_body")
        self.assertIsInstance(body, dict, "createMachine.request_body is missing")
        self.assertEqual(body.get("schema"), "MachineSpecification")
        self.assertIs(body.get("omit_unset_optional_fields"), True)

        required = set(body.get("required") or ())
        optional = set(body.get("optional") or ())
        self.assertTrue(required.isdisjoint(optional),
                        "a property cannot be both required and optional")

        self.assertEqual(required, {
            "name", "projectId", "flavor", "flavorRef", "image", "imageRef",
        })
        self.assertEqual(optional, {
            "deploymentId", "customProperties", "description", "nics", "disks",
            "bootConfig", "bootConfigSettings", "machineCount", "constraints",
            "imageDiskConstraints", "tags", "remoteAccess", "saltConfiguration",
        })

    def test_create_machine_is_marked_asynchronous(self):
        operation = self.ops["createMachine"]
        responses = operation.get("responses")
        self.assertIsInstance(responses, dict)
        self.assertEqual(responses.get("202"), "RequestTracker",
                         "create-machine is accepted, not completed")
        self.assertNotIn("200", responses)

        async_block = operation.get("async")
        self.assertIsInstance(async_block, dict, "createMachine.async is missing")
        self.assertEqual(async_block.get("returns"), "RequestTracker")
        self.assertEqual(async_block.get("tracker_id_field"), "id")
        self.assertEqual(async_block.get("poll_operation"), "getRequestTracker")

    def test_read_operations_are_synchronous_and_bodyless(self):
        for op_id in ("getRequestTracker", "getMachine"):
            operation = self.ops[op_id]
            self.assertNotIn("request_body", operation,
                             "{0} takes no request body".format(op_id))
            self.assertNotIn("async", operation)
        self.assertEqual(self.ops["getRequestTracker"]["responses"].get("200"),
                         "RequestTracker")
        self.assertEqual(self.ops["getMachine"]["responses"].get("200"), "Machine")

    def test_documented_error_responses_are_recorded(self):
        self.assertEqual(self.ops["createMachine"]["responses"], {
            "202": "RequestTracker",
            "400": "ServiceErrorResponse",
            "403": "ServiceErrorResponse",
        })
        expected_read_responses = {
            "200": "RequestTracker",
            "403": "ServiceErrorResponse",
            "404": "ServiceErrorResponse",
        }
        self.assertEqual(self.ops["getRequestTracker"]["responses"],
                         expected_read_responses)
        self.assertEqual(self.ops["getMachine"]["responses"], {
            "200": "Machine",
            "403": "ServiceErrorResponse",
            "404": "ServiceErrorResponse",
        })


class ContractSchemaTests(unittest.TestCase):
    def setUp(self):
        self.contract = load(CONTRACT_PATH)
        self.schemas = self.contract.get("schemas")
        self.assertIsInstance(self.schemas, dict, "contract.schemas is missing")

    def test_request_tracker_schema(self):
        tracker = self.schemas.get("RequestTracker")
        self.assertIsInstance(tracker, dict, "RequestTracker schema is missing")
        self.assertEqual(set(tracker.get("required") or ()),
                         {"id", "progress", "status", "selfLink"})
        self.assertEqual(set(tracker.get("optional") or ()),
                         {"name", "message", "resources", "deploymentId"})
        enums = tracker.get("enums") or {}
        self.assertEqual(set(enums.get("status") or ()),
                         {"FINISHED", "INPROGRESS", "FAILED"})
        self.assertEqual(set(tracker.get("terminal_states") or ()),
                         {"FINISHED", "FAILED"})
        self.assertEqual(tracker.get("resource_collection_field"), "resources")

    def test_machine_schema(self):
        machine = self.schemas.get("Machine")
        self.assertIsInstance(machine, dict, "Machine schema is missing")
        self.assertEqual(set(machine.get("required") or ()),
                         {"id", "externalRegionId", "powerState", "_links"})
        enums = machine.get("enums") or {}
        self.assertEqual(set(enums.get("powerState") or ()),
                         {"ON", "OFF", "GUEST_OFF", "UNKNOWN", "SUSPEND"})
        self.assertEqual(set(enums.get("provisioningStatus") or ()),
                         {"PROVISIONING", "READY", "SUSPEND"})


class ContractMatchesMockTests(unittest.TestCase):
    """The contract and the mock have to describe the same service."""

    def setUp(self):
        self.contract = load(CONTRACT_PATH)
        self.ops = {operation.get("id"): operation
                    for operation in self.contract.get("operations", [])}

    def test_required_properties_agree_with_the_served_mock(self):
        required = set(self.ops["createMachine"]["request_body"]["required"])
        self.assertTrue(set(MACHINE_SPECIFICATION_REQUIRED).issubset(required))

    def test_status_enum_covers_every_state_the_mock_reports(self):
        statuses = set(self.contract["schemas"]["RequestTracker"]["enums"]["status"])
        self.assertTrue({"INPROGRESS", "FINISHED", "FAILED"}.issubset(statuses))


class OfficialSourcesTests(unittest.TestCase):
    """Provenance for every claim the contract makes."""

    def setUp(self):
        self.document = load(SOURCES_PATH)
        self.sources = self.document.get("sources")
        self.assertIsInstance(self.sources, list, "sources must be a list")
        self.assertGreaterEqual(len(self.sources), 3)

    def test_entries_are_well_formed(self):
        allowed = set(OPERATION_IDS) | {"security", "overview"}
        for index, entry in enumerate(self.sources):
            where = "sources[{0}]".format(index)
            self.assertIsInstance(entry, dict, where)

            url = entry.get("url")
            self.assertIsInstance(url, str, where)
            parsed = urlparse(url)
            self.assertEqual(parsed.scheme, "https", where)
            self.assertEqual(parsed.netloc, SOURCE_HOST,
                             "{0} must cite the vendor reference host".format(where))
            self.assertTrue(parsed.path.startswith("/xapis/"), where)
            self.assertNotIn(".invalid", url, where)

            self.assertIn(entry.get("operation"), allowed, where)

            title = entry.get("title")
            self.assertIsInstance(title, str, where)
            self.assertTrue(title.strip(), "{0} needs the page title".format(where))

            fetched_on = entry.get("fetched_on")
            self.assertIsInstance(fetched_on, str, where)
            self.assertRegex(fetched_on, DATE_PATTERN, where)
            year, month, day = (int(part) for part in fetched_on.split("-"))
            datetime.date(year, month, day)
            self.assertGreaterEqual(year, 2024, "{0} fetch date looks wrong".format(where))

    def test_every_operation_is_evidenced_by_its_own_page(self):
        by_operation = {}
        for entry in self.sources:
            by_operation.setdefault(entry["operation"], set()).add(entry["url"])

        for op_id in OPERATION_IDS:
            self.assertIn(op_id, by_operation,
                          "no reference page recorded for {0}".format(op_id))

        seen = {}
        for op_id in OPERATION_IDS:
            for url in by_operation[op_id]:
                if url in seen and seen[url] != op_id:
                    self.fail("{0} is cited for both {1} and {2}; each operation is "
                              "documented on its own page".format(url, seen[url], op_id))
                seen[url] = op_id

    def test_cites_the_canonical_reference_pages(self):
        recorded = {(entry["operation"], entry["url"]): entry
                    for entry in self.sources}
        for operation, url in EXPECTED_SOURCE_URLS.items():
            self.assertIn((operation, url), recorded)
            self.assertEqual(recorded[(operation, url)]["title"],
                             EXPECTED_SOURCE_TITLES[operation])


if __name__ == "__main__":
    unittest.main()
