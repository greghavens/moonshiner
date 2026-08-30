"""PROTECTED FILE -- do not modify.

Checks that docs/contract.json and docs/official_sources.json record the wire
contract as the 9.0.0.0 revision of the vCenter automation specification
defines it -- and not the 9.1 revision of the same file.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import support  # noqa: E402

# The tag that must NOT be used: 9.1.0.0 revises the same vcenter.yaml.
WRONG_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
WRONG_TAG = "9.1.0.0"

EXPECTED_OPERATIONS = {
    "createSession": {
        "operationId": "Cis.Session_create",
        "method": "POST",
        "path": "/session",
        "path_parameters": [],
        "query": {},
        "optional_query_schema": None,
        "security": ["basic_auth"],
        "request_body": None,
        "request_body_required": False,
        "success_status": 201,
        "response_schema": "string",
        "error_statuses": [401, 503],
    },
    "getEvcMode": {
        "operationId": "Vcenter.Cluster.EvcMode_get",
        "method": "GET",
        "path": "/vcenter/cluster/{cluster}/evc-mode",
        "path_parameters": ["cluster"],
        "query": {},
        "optional_query_schema": None,
        "security": ["api_key_auth"],
        "request_body": None,
        "request_body_required": False,
        "success_status": 200,
        "response_schema": "Vcenter.Cluster.EvcMode.Info",
        "error_statuses": [401, 403, 404, 500],
    },
    "checkSetEvcMode": {
        "operationId": "Vcenter.Cluster.EvcMode_checkSet$Task",
        "method": "POST",
        "path": "/vcenter/cluster/{cluster}/evc-mode",
        "path_parameters": ["cluster"],
        "query": {"action": "check-set", "vmw-task": "true"},
        "optional_query_schema": None,
        "security": ["api_key_auth"],
        "request_body": "Vcenter.Cluster.EvcMode.SetSpec",
        "request_body_required": True,
        "success_status": 202,
        "response_schema": "string",
        "error_statuses": [400, 401, 403, 404, 500],
    },
    "setEvcMode": {
        "operationId": "Vcenter.Cluster.EvcMode_set$Task",
        "method": "PUT",
        "path": "/vcenter/cluster/{cluster}/evc-mode",
        "path_parameters": ["cluster"],
        "query": {"vmw-task": "true"},
        "optional_query_schema": None,
        "security": ["api_key_auth"],
        "request_body": "Vcenter.Cluster.EvcMode.SetSpec",
        "request_body_required": True,
        "success_status": 202,
        "response_schema": "string",
        "error_statuses": [400, 401, 403, 404, 500],
    },
    "getTask": {
        "operationId": "Cis.Tasks_get",
        "method": "GET",
        "path": "/cis/tasks/{task}",
        "path_parameters": ["task"],
        "query": {},
        "optional_query_schema": "Cis.Tasks.GetSpec",
        "security": ["api_key_auth"],
        "request_body": None,
        "request_body_required": False,
        "success_status": 200,
        "response_schema": "Cis.Task.Info",
        "error_statuses": [401, 403, 404, 500, 503],
    },
}

EXPECTED_SCHEMAS = {
    "Vcenter.Cluster.EvcMode.SetSpec": {
        "required": [],
        "optional": ["evc_mode"],
    },
    "Vcenter.EvcMode.EvcMode": {
        "required": ["key", "masks"],
        "optional": [],
    },
    "Vcenter.EvcMode.FeatureMask": {
        "required": ["key", "name", "value"],
        "optional": [],
    },
    "Vcenter.Cluster.EvcMode.CheckResult": {
        "required": ["error"],
        "optional": ["host_system"],
    },
    "Cis.Tasks.GetSpec": {
        "required": [],
        "optional": ["exclude_result", "return_all"],
    },
}

EXPECTED_SECURITY_SCHEMES = {
    "basic_auth": {"type": "http", "scheme": "basic"},
    "api_key_auth": {"type": "apiKey", "name": "vmware-api-session-id", "in": "header"},
}

TASK_STATUS_VALUES = ["BLOCKED", "FAILED", "PENDING", "RUNNING", "SUCCEEDED"]


class ContractSourceTests(unittest.TestCase):
    def setUp(self):
        self.contract = support.load_doc("contract.json")
        self.assertIn("source", self.contract, "contract.json needs a 'source' block")
        self.source = self.contract["source"]

    def test_source_names_the_specification_file(self):
        self.assertEqual(support.SPEC_REPO, self.source.get("repository"))
        self.assertEqual(support.SPEC_PATH, self.source.get("path"))

    def test_source_pins_the_nine_zero_tag_and_its_commit(self):
        self.assertEqual(
            support.SPEC_TAG,
            self.source.get("tag"),
            "the contract is derived from tag %s" % support.SPEC_TAG,
        )
        self.assertEqual(
            support.SPEC_COMMIT,
            str(self.source.get("commit", "")).lower(),
            "tag %s points at commit %s" % (support.SPEC_TAG, support.SPEC_COMMIT),
        )

    def test_source_records_the_specification_version_and_server(self):
        self.assertEqual("3.0.3", str(self.source.get("openapi")))
        self.assertEqual("9.0.0.0", str(self.source.get("info_version")))
        self.assertEqual("https://{host}/api", self.source.get("server_url"))

    def test_source_records_the_licence(self):
        self.assertIn("Apache-2.0", json.dumps(self.source))

    def test_the_nine_one_revision_is_not_used(self):
        self.assertNotEqual(
            WRONG_COMMIT,
            str(self.source.get("commit", "")).lower(),
            "commit %s is the 9.1.0.0 revision of vcenter.yaml, not 9.0.0.0" % WRONG_COMMIT,
        )
        for field in ("tag", "info_version"):
            self.assertNotEqual(
                WRONG_TAG,
                str(self.source.get(field, "")),
                "%s revises the same file; this contract is pinned to %s"
                % (WRONG_TAG, support.SPEC_TAG),
            )


class ContractOperationTests(unittest.TestCase):
    def setUp(self):
        self.contract = support.load_doc("contract.json")
        self.assertIn("operations", self.contract, "contract.json needs an 'operations' block")
        self.operations = self.contract["operations"]

    def test_exactly_the_five_contracted_operations_are_described(self):
        self.assertEqual(sorted(EXPECTED_OPERATIONS), sorted(self.operations))

    def test_operation_ids_are_the_ones_the_specification_uses(self):
        for key, expected in EXPECTED_OPERATIONS.items():
            with self.subTest(operation=key):
                self.assertEqual(
                    expected["operationId"], self.operations.get(key, {}).get("operationId")
                )

    def test_each_operation_matches_the_specification(self):
        for key, expected in EXPECTED_OPERATIONS.items():
            actual = self.operations.get(key, {})
            with self.subTest(operation=key, field="field set"):
                self.assertEqual(
                    sorted(expected),
                    sorted(actual),
                    "%s must contain exactly the documented operation fields" % key,
                )
            for field, value in expected.items():
                with self.subTest(operation=key, field=field):
                    self.assertIn(field, actual, "%s is missing '%s'" % (key, field))
                    got = actual[field]
                    if field == "error_statuses":
                        self.assertEqual(sorted(value), sorted(int(s) for s in got))
                    elif field in ("path_parameters", "security"):
                        self.assertEqual(sorted(value), sorted(got))
                    elif field in ("success_status",):
                        self.assertEqual(value, int(got))
                    elif field == "query":
                        self.assertEqual(
                            {k: str(v) for k, v in value.items()},
                            {k: str(v) for k, v in (got or {}).items()},
                        )
                    else:
                        self.assertEqual(value, got)

    def test_the_two_evc_write_operations_are_distinguished_by_method_and_action(self):
        check = self.operations["checkSetEvcMode"]
        apply_ = self.operations["setEvcMode"]
        self.assertEqual(check["path"], apply_["path"])
        self.assertNotEqual(check["method"], apply_["method"])
        self.assertNotIn(
            "action",
            apply_["query"],
            "Vcenter.Cluster.EvcMode_set$Task carries no action query parameter",
        )


class ContractSchemaTests(unittest.TestCase):
    def setUp(self):
        self.contract = support.load_doc("contract.json")
        self.assertIn("schemas", self.contract, "contract.json needs a 'schemas' block")
        self.schemas = self.contract["schemas"]

    def test_every_referenced_schema_is_described(self):
        for name in EXPECTED_SCHEMAS:
            self.assertIn(name, self.schemas, "schemas is missing %s" % name)

    def test_required_and_optional_properties_are_split_correctly(self):
        for name, expected in EXPECTED_SCHEMAS.items():
            actual = self.schemas.get(name, {})
            with self.subTest(schema=name, part="required"):
                self.assertEqual(sorted(expected["required"]), sorted(actual.get("required", [])))
            with self.subTest(schema=name, part="optional"):
                self.assertEqual(sorted(expected["optional"]), sorted(actual.get("optional", [])))

    def test_set_spec_treats_evc_mode_as_optional(self):
        set_spec = self.schemas["Vcenter.Cluster.EvcMode.SetSpec"]
        self.assertNotIn(
            "evc_mode",
            set_spec.get("required", []),
            "an absent evc_mode is what resets the cluster, so it cannot be required",
        )

    def test_security_schemes_are_recorded(self):
        schemes = self.contract.get("security_schemes")
        self.assertIsInstance(schemes, dict, "contract.json needs a 'security_schemes' block")
        for name, expected in EXPECTED_SECURITY_SCHEMES.items():
            with self.subTest(scheme=name):
                self.assertIn(name, schemes)
                actual = schemes[name]
                for field, value in expected.items():
                    self.assertEqual(
                        value, actual.get(field), "%s.%s" % (name, field)
                    )

    def test_task_status_enumeration_is_recorded(self):
        values = self.contract.get("task_status_values")
        self.assertIsInstance(
            values, list, "contract.json needs 'task_status_values' from Cis.Task.Status"
        )
        self.assertEqual(TASK_STATUS_VALUES, sorted(values))


class OfficialSourcesTests(unittest.TestCase):
    def setUp(self):
        self.doc = support.load_doc("official_sources.json")
        self.sources = self.doc.get("sources")
        self.assertIsInstance(self.sources, list, "official_sources.json needs a 'sources' list")
        self.assertTrue(self.sources, "official_sources.json records no sources")

    def test_the_specification_file_is_recorded_with_its_tag_and_commit(self):
        matches = [s for s in self.sources if s.get("path") == support.SPEC_PATH]
        self.assertTrue(
            matches, "no source records the path %s" % support.SPEC_PATH
        )
        entry = matches[0]
        self.assertEqual(support.SPEC_REPO, entry.get("repository"))
        self.assertEqual(support.SPEC_TAG, entry.get("tag"))
        self.assertEqual(support.SPEC_COMMIT, str(entry.get("commit", "")).lower())

    def test_every_operation_id_is_attributed_to_a_source(self):
        recorded = set()
        for entry in self.sources:
            recorded.update(entry.get("operation_ids", []))
        for operation_id in support.OPERATION_IDS.values():
            with self.subTest(operation_id=operation_id):
                self.assertIn(
                    operation_id,
                    recorded,
                    "official_sources.json does not attribute %s to a source" % operation_id,
                )

    def test_each_source_has_a_resolvable_url_and_a_read_date(self):
        for entry in self.sources:
            with self.subTest(url=entry.get("url")):
                url = entry.get("url", "")
                self.assertTrue(
                    url.startswith("https://"), "source url must be absolute: %r" % url
                )
                self.assertIn("vcf-api-specs", url)
                self.assertRegex(
                    str(entry.get("retrieved", "")),
                    r"^\d{4}-\d{2}-\d{2}$",
                    "each source needs a 'retrieved' date as YYYY-MM-DD",
                )

    def test_sources_point_at_the_pinned_commit_rather_than_a_moving_branch(self):
        for entry in self.sources:
            url = entry.get("url", "")
            with self.subTest(url=url):
                self.assertNotIn(
                    "/main/", url, "pin the URL to the tag or commit, not to a branch"
                )
                self.assertTrue(
                    support.SPEC_COMMIT in url or support.SPEC_TAG in url,
                    "url must reference %s or %s: %r"
                    % (support.SPEC_TAG, support.SPEC_COMMIT, url),
                )

    def test_the_nine_one_revision_is_not_cited(self):
        for entry in self.sources:
            with self.subTest(url=entry.get("url")):
                self.assertNotEqual(WRONG_COMMIT, str(entry.get("commit", "")).lower())
                self.assertNotEqual(WRONG_TAG, str(entry.get("tag", "")))
                self.assertNotIn(WRONG_COMMIT, entry.get("url", ""))


if __name__ == "__main__":
    unittest.main()
