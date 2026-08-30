"""Protected verifier for vcfa_rotate. Do not modify this file.

Everything here runs against the loopback mock in vcfa_mock.py. No live VMware endpoint
is contacted. The mock's request log is the source of truth for what actually went on
the wire.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import vcfa_mock  # noqa: E402
from vcfa_rotate import VcfaClient, rotate_named_credential  # noqa: E402

TOKEN = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.mock-provider-token"
EXPECTED_ACCEPT = "application/json;version=9.1.0"

with open(os.path.join(REPO_ROOT, "docs", "contract.json"), encoding="utf-8") as _handle:
    CONTRACT = json.load(_handle)

CONTRACT_ROUTES = [
    (op["method"], re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", op["path"]) + "$"))
    for op in CONTRACT["operations"]
]

PLACEHOLDER_DESCRIPTIONS = {"null": None, "empty string": "", "empty list": [], "empty object": {}}


def is_placeholder(value):
    if value is None:
        return "null"
    if isinstance(value, str) and value == "":
        return "empty string"
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return "empty list"
    if isinstance(value, dict) and len(value) == 0:
        return "empty object"
    return None


def body_keys(entry):
    body = entry.get("body")
    return set(body) if isinstance(body, dict) else set()


class MockBackedTestCase(unittest.TestCase):
    """Starts a fresh mock and client per test, so each log holds only that test's traffic."""

    def setUp(self):
        self.server = vcfa_mock.start()
        self.client = VcfaClient(self.server.base_url, TOKEN)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    # -- log helpers --------------------------------------------------------------

    def requests(self, method=None, path_contains=None):
        out = self.server.state.requests()
        if method is not None:
            out = [e for e in out if e["method"] == method]
        if path_contains is not None:
            out = [e for e in out if path_contains in e["path"]]
        return out

    def only_request(self, method, path_contains):
        matches = self.requests(method, path_contains)
        self.assertEqual(
            1, len(matches), "expected exactly one %s %s request, saw %d"
            % (method, path_contains, len(matches)),
        )
        return matches[0]


# ======================================================================================
# Wire shape: query parameters
# ======================================================================================


class QueryParameterWireShapeTest(MockBackedTestCase):
    def test_unset_optional_query_parameters_are_absent_entirely(self):
        self.client.query_named_credentials(filter_expr="name==vc-prod-01-svc")
        entry = self.only_request("GET", "/namedCredentials")

        self.assertEqual(200, entry["status"], entry)
        self.assertEqual(
            {"filter", "page", "pageSize"},
            set(entry["query"]),
            "sortAsc/sortDesc were not passed, so they must not appear in the query string",
        )
        self.assertNotIn("sortAsc", entry["query_raw"])
        self.assertNotIn("sortDesc", entry["query_raw"])
        self.assertEqual("name==vc-prod-01-svc", entry["query"]["filter"])

    def test_required_paging_parameters_are_sent_even_at_their_defaults(self):
        self.client.query_audit_trail()
        entry = self.only_request("GET", "/auditTrail")

        self.assertEqual(200, entry["status"], entry)
        self.assertEqual(
            {"page", "pageSize"},
            set(entry["query"]),
            "page and pageSize are documented as required and must be sent; nothing else was passed",
        )
        self.assertEqual("1", entry["query"]["page"])
        self.assertEqual("25", entry["query"]["pageSize"])

    def test_optional_query_parameters_survive_when_they_are_passed(self):
        self.client.query_named_credentials(sort_desc="", page=2, page_size=0)
        entry = self.requests("GET", "/namedCredentials")[-1]

        self.assertEqual({"sortDesc", "page", "pageSize"}, set(entry["query"]))
        self.assertEqual("", entry["query"]["sortDesc"])
        self.assertEqual("2", entry["query"]["page"])
        self.assertEqual("0", entry["query"]["pageSize"])
        self.assertNotIn("filter", entry["query_raw"])


# ======================================================================================
# Wire shape: request bodies
# ======================================================================================


class RequestBodyWireShapeTest(MockBackedTestCase):
    def test_get_named_credential_returns_the_decoded_model(self):
        credential = self.client.get_named_credential(vcfa_mock.SEED_CREDENTIAL_ID)
        entry = self.only_request("GET", "/namedCredentials/")

        self.assertEqual(200, entry["status"], entry)
        self.assertEqual(vcfa_mock.SEED_CREDENTIAL_ID, credential["id"])
        self.assertEqual("vc-prod-01-svc", credential["name"])

    def test_create_named_credential_omits_the_unset_optional_entity(self):
        self.client.create_named_credential(
            name="wire-shape-plain", username="a@vsphere.local", password="s3cret"
        )
        entry = self.only_request("POST", "/namedCredentials")

        self.assertEqual(201, entry["status"], entry)
        self.assertEqual({"name", "username", "password"}, body_keys(entry))
        self.assertNotIn("entity", entry["body_raw"])

    def test_create_named_credential_carries_entity_when_it_is_set(self):
        entity = {"name": "vc-prod-01", "id": vcfa_mock.VC_URN}
        self.client.create_named_credential(
            name="wire-shape-with-entity",
            username="b@vsphere.local",
            password="s3cret",
            entity=entity,
        )
        entry = self.requests("POST", "/namedCredentials")[-1]

        self.assertEqual(201, entry["status"], entry)
        self.assertEqual({"name", "username", "password", "entity"}, body_keys(entry))
        self.assertEqual(entity, entry["body"]["entity"])

    def test_test_connection_omits_every_unset_optional_field(self):
        self.client.test_connection(host="vc-prod-01.lab.example.com", port=443)
        entry = self.only_request("POST", "/testConnection")

        self.assertEqual(200, entry["status"], entry)
        self.assertEqual(
            {"host", "port"},
            body_keys(entry),
            "only host and port were passed; the six optional fields must be absent, not "
            "sent as null/\"\"/[]/{} -- the reference documents server-side defaults that "
            "only apply to an absent key",
        )
        for field in (
            "secure",
            "timeout",
            "hostnameVerificationAlgorithm",
            "additionalCAIssuers",
            "proxyConnection",
            "preConfiguredProxy",
        ):
            self.assertNotIn(field, entry["body_raw"])

    def test_test_connection_keeps_a_falsy_optional_that_was_explicitly_set(self):
        self.client.test_connection(
            host="vc-prod-01.lab.example.com",
            port=443,
            secure=False,
            timeout=5,
            hostname_verification_algorithm="",
            additional_ca_issuers=[],
            proxy_connection={},
            pre_configured_proxy="",
        )
        entry = self.requests("POST", "/testConnection")[-1]

        self.assertEqual(200, entry["status"], entry)
        self.assertEqual(
            {
                "host",
                "port",
                "secure",
                "timeout",
                "hostnameVerificationAlgorithm",
                "additionalCAIssuers",
                "proxyConnection",
                "preConfiguredProxy",
            },
            body_keys(entry),
        )
        self.assertIs(
            False,
            entry["body"]["secure"],
            "secure=False was set explicitly; a falsy value is not an unset value",
        )
        self.assertEqual(5, entry["body"]["timeout"])
        self.assertEqual("", entry["body"]["hostnameVerificationAlgorithm"])
        self.assertEqual([], entry["body"]["additionalCAIssuers"])
        self.assertEqual({}, entry["body"]["proxyConnection"])
        self.assertEqual("", entry["body"]["preConfiguredProxy"])

    def test_update_named_credential_keeps_every_explicit_falsy_optional(self):
        created = self.client.create_named_credential(
            name="wire-shape-update", username="update-user", password="old-secret"
        )
        updated = self.client.update_named_credential(
            created["id"], name="", username="", password="", entity={}
        )
        entry = self.only_request("PUT", "/namedCredentials/")

        self.assertEqual(200, entry["status"], entry)
        self.assertEqual({"name", "username", "password", "entity"}, body_keys(entry))
        self.assertEqual("", entry["body"]["name"])
        self.assertEqual("", entry["body"]["username"])
        self.assertEqual("", entry["body"]["password"])
        self.assertEqual({}, entry["body"]["entity"])
        self.assertEqual("", updated["name"])
        self.assertEqual({}, updated["entity"])

    def test_port_is_sent_as_a_json_number_not_a_string(self):
        self.client.test_connection(host="vc-prod-01.lab.example.com", port=443)
        entry = self.requests("POST", "/testConnection")[-1]
        self.assertIsInstance(entry["body"]["port"], int)
        self.assertNotIsInstance(entry["body"]["port"], bool)

    def test_delete_returns_the_tracking_task_uri_from_the_location_header(self):
        created = self.client.create_named_credential(
            name="wire-shape-disposable", username="c@vsphere.local", password="s3cret"
        )
        task_uri = self.client.delete_named_credential(created["id"])
        entry = self.requests("DELETE", "/namedCredentials")[-1]

        self.assertEqual(202, entry["status"], entry)
        self.assertIsInstance(task_uri, str)
        self.assertTrue(
            task_uri.startswith("http"),
            "a 202 carries no body; the tracking task URI must be read off the Location "
            "header, got %r" % (task_uri,),
        )


# ======================================================================================
# Wire shape: headers
# ======================================================================================


class HeaderWireShapeTest(MockBackedTestCase):
    def test_every_request_pins_the_api_version_and_carries_the_bearer_token(self):
        self.client.query_named_credentials()
        self.client.get_named_credential(vcfa_mock.SEED_CREDENTIAL_ID)
        self.client.get_virtual_center(vcfa_mock.VC_URN)
        self.client.test_connection(host="vc-prod-01.lab.example.com", port=443)

        entries = self.requests()
        self.assertEqual(4, len(entries))
        for entry in entries:
            self.assertEqual(
                EXPECTED_ACCEPT,
                entry["headers"]["accept"],
                "Accept must pin the contract's API version on %s %s"
                % (entry["method"], entry["path"]),
            )
            self.assertEqual("Bearer " + TOKEN, entry["headers"]["authorization"])
            self.assertLess(entry["status"], 400, entry)

    def test_json_content_type_accompanies_bodies_and_only_bodies(self):
        self.client.test_connection(host="vc-prod-01.lab.example.com", port=443)
        self.client.query_named_credentials()

        with_body = self.requests("POST", "/testConnection")[-1]
        without_body = self.requests("GET", "/namedCredentials")[-1]

        self.assertEqual("application/json", (with_body["headers"]["content-type"] or "").split(";")[0])
        self.assertEqual("", without_body["body_raw"])


# ======================================================================================
# Timeout safety
# ======================================================================================


class RotationTimeoutSafetyTest(MockBackedTestCase):
    def rotate(self, max_polls):
        return rotate_named_credential(
            self.client,
            credential_name="vc-prod-01-svc",
            vc_urn=vcfa_mock.VC_URN,
            new_username="timeout-test@vsphere.local",
            new_password="timeout-test-secret",
            probe_host="vc-prod-01.lab.example.com",
            probe_port=443,
            max_polls=max_polls,
        )

    def assert_old_credential_was_left_safe(self):
        self.assertIn(vcfa_mock.SEED_CREDENTIAL_ID, self.server.state.credentials)
        self.assertEqual([], self.requests("DELETE", "/namedCredentials/"))
        self.assertEqual([], self.server.state.events("stranded_in_flight_requests"))
        self.assertEqual([], self.server.state.events("terminated_vcenter_sessions"))

    def test_reconnect_timeout_leaves_the_old_credential_in_place(self):
        with self.assertRaises(TimeoutError):
            self.rotate(max_polls=1)
        self.assert_old_credential_was_left_safe()

    def test_drain_timeout_leaves_the_old_credential_in_place(self):
        from unittest import mock

        polls = vcfa_mock.RECONNECT_AFTER_VC_POLLS
        with mock.patch.object(vcfa_mock, "DRAIN_AFTER_AUDIT_POLLS", polls + 1):
            with self.assertRaises(TimeoutError):
                self.rotate(max_polls=polls)
        self.assertEqual(1, len(self.server.state.events("vcenter_reconnected_on_new_secret")))
        self.assert_old_credential_was_left_safe()


# ======================================================================================
# The rotation
# ======================================================================================


class RotationTest(unittest.TestCase):
    """One rotation, then everything is asserted against the log and the final state."""

    @classmethod
    def setUpClass(cls):
        cls.server = vcfa_mock.start()
        cls.state = cls.server.state
        cls.old = dict(cls.state.credentials[vcfa_mock.SEED_CREDENTIAL_ID])
        cls.new_username = "svc-vcfa-2026q3@vsphere.local"
        cls.new_password = "N3w-Secret-2026-Q3"
        client = VcfaClient(cls.server.base_url, TOKEN)
        cls.result = rotate_named_credential(
            client,
            credential_name=cls.old["name"],
            vc_urn=vcfa_mock.VC_URN,
            new_username=cls.new_username,
            new_password=cls.new_password,
            probe_host="vc-prod-01.lab.example.com",
            probe_port=443,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def reqs(self, method=None, path_contains=None):
        out = self.state.requests()
        if method is not None:
            out = [e for e in out if e["method"] == method]
        if path_contains is not None:
            out = [e for e in out if path_contains in e["path"]]
        return out

    def seq_of(self, method, path_contains):
        matches = self.reqs(method, path_contains)
        self.assertTrue(matches, "no %s request to a path containing %r was made"
                        % (method, path_contains))
        return matches[0]["seq"]

    # -- the safety property ------------------------------------------------------

    def test_no_in_flight_request_was_stranded(self):
        stranded = self.state.events("stranded_in_flight_requests")
        self.assertEqual(
            [],
            stranded,
            "the rotation aborted work that was already running: %s"
            % json.dumps(stranded, indent=2),
        )

    def test_the_vcenter_sessions_were_never_torn_down(self):
        self.assertEqual(
            [],
            self.state.events("terminated_vcenter_sessions"),
            "retiring the old credential killed the vCenter sessions, which means it was "
            "retired while the vCenter was still using it",
        )

    def test_the_secret_was_never_overwritten_in_place(self):
        in_place = self.reqs("PUT", "/namedCredentials/")
        self.assertEqual(
            [],
            in_place,
            "updateNamedCredential overwrites a live secret with no dual-secret window; "
            "rotation must create a replacement credential instead",
        )

    def test_every_audit_event_reached_a_successful_terminal_state(self):
        statuses = sorted({e["eventStatus"] for e in self.state.audit_events})
        self.assertEqual(["SUCCESS"], statuses,
                         "audit events did not all finish cleanly: %s" % statuses)

    # -- ordering -----------------------------------------------------------------

    def test_the_endpoint_was_probed_before_anything_was_changed(self):
        probe = self.seq_of("POST", "/testConnection")
        create = self.seq_of("POST", "/namedCredentials")
        self.assertLess(probe, create,
                        "probe the endpoint before creating the replacement credential")

    def test_the_replacement_credential_existed_before_the_vcenter_was_repointed(self):
        create = self.seq_of("POST", "/namedCredentials")
        repoint = self.seq_of("PUT", "/virtualCenters/")
        self.assertLess(create, repoint)

    def test_the_old_credential_was_retired_only_after_the_vcenter_reconnected(self):
        reconnected = self.state.events("vcenter_reconnected_on_new_secret")
        self.assertEqual(1, len(reconnected),
                         "the vCenter never came back up on the new secret")
        delete = self.reqs("DELETE", "/namedCredentials/")
        self.assertEqual(1, len(delete), "expected exactly one retire")
        self.assertLess(reconnected[0]["seq"], delete[0]["seq"])

    def test_the_audit_trail_was_polled_for_in_flight_work_before_retiring(self):
        reconnected_seq = self.state.events("vcenter_reconnected_on_new_secret")[0]["seq"]
        delete_seq = self.reqs("DELETE", "/namedCredentials/")[0]["seq"]
        drain_polls = [
            e for e in self.reqs("GET", "/auditTrail")
            if reconnected_seq < e["seq"] < delete_seq
        ]
        self.assertGreaterEqual(
            len(drain_polls),
            vcfa_mock.DRAIN_AFTER_AUDIT_POLLS,
            "the old credential was retired without waiting for in-flight requests to drain",
        )

    def test_the_vcenter_was_polled_until_it_reported_itself_connected(self):
        repoint_seq = self.seq_of("PUT", "/virtualCenters/")
        polls = [e for e in self.reqs("GET", "/virtualCenters/") if e["seq"] > repoint_seq]
        self.assertGreaterEqual(len(polls), vcfa_mock.RECONNECT_AFTER_VC_POLLS)

    # -- what was retired ---------------------------------------------------------

    def test_the_old_credential_was_the_one_retired(self):
        entry = self.reqs("DELETE", "/namedCredentials/")[0]
        self.assertTrue(
            entry["path"].endswith(self.old["id"]),
            "the retired credential was %s, expected the old one %s"
            % (entry["path"], self.old["id"]),
        )
        self.assertNotIn(self.old["id"], self.state.credentials)

    def test_exactly_one_credential_survives_and_it_holds_the_new_secret(self):
        survivors = list(self.state.credentials.values())
        self.assertEqual(1, len(survivors), survivors)
        self.assertEqual(self.new_username, survivors[0]["username"])
        self.assertEqual(self.new_password, survivors[0]["password"])
        self.assertEqual(survivors[0]["id"], self.result["new_credential_id"])

    def test_the_vcenter_ends_connected_on_the_new_secret(self):
        vc = self.state.vcenter
        self.assertTrue(vc["isConnected"], vc)
        self.assertEqual("CONNECTED", vc["listenerState"])
        self.assertEqual(self.new_username, vc["username"])
        self.assertEqual(self.new_password, vc["password"])

    # -- the repoint payload ------------------------------------------------------

    def test_the_repoint_payload_omits_response_only_fields(self):
        entry = self.reqs("PUT", "/virtualCenters/")[0]
        read_only = sorted(body_keys(entry) & vcfa_mock.VCENTER_READ_ONLY)
        self.assertEqual([], read_only,
                         "response-only fields must not be echoed back on update: %s" % read_only)
        self.assertLessEqual({"name", "url", "username"}, body_keys(entry))

    # -- global sweeps ------------------------------------------------------------

    def test_no_request_body_carried_a_placeholder_in_place_of_an_unset_field(self):
        offenders = []
        for entry in self.reqs():
            body = entry.get("body")
            if not isinstance(body, dict):
                continue
            for key, value in body.items():
                kind = is_placeholder(value)
                if kind is not None:
                    offenders.append("%s %s -> %s: %s" % (entry["method"], entry["path"], key, kind))
        self.assertEqual(
            [],
            offenders,
            "an unset optional field must be absent from the body, not sent as a "
            "placeholder:\n  " + "\n  ".join(offenders),
        )

    def test_no_query_string_carried_an_empty_parameter(self):
        offenders = [
            "%s %s?%s" % (e["method"], e["path"], e["query_raw"])
            for e in self.reqs()
            if any(v == "" for v in e["query"].values())
        ]
        self.assertEqual([], offenders, offenders)

    def test_only_operations_named_in_the_contract_were_called(self):
        offenders = []
        for entry in self.reqs():
            if not any(
                entry["method"] == method and pattern.match(entry["path"])
                for method, pattern in CONTRACT_ROUTES
            ):
                offenders.append("%s %s" % (entry["method"], entry["path"]))
        self.assertEqual([], offenders,
                         "called operations the contract does not name: %s" % offenders)

    def test_no_request_failed(self):
        failures = [
            "%s %s -> %s" % (e["method"], e["path"], e["status"])
            for e in self.reqs()
            if e["status"] >= 400
        ]
        self.assertEqual([], failures, failures)

    # -- the returned result ------------------------------------------------------

    def test_the_result_reports_what_happened(self):
        self.assertLessEqual(
            {
                "old_credential_id",
                "new_credential_id",
                "probe",
                "repoint_task_uri",
                "retire_task_uri",
                "drain_polls",
            },
            set(self.result),
        )
        self.assertEqual(self.old["id"], self.result["old_credential_id"])
        self.assertNotEqual(self.result["old_credential_id"], self.result["new_credential_id"])
        self.assertTrue(self.result["repoint_task_uri"].startswith("http"))
        self.assertTrue(self.result["retire_task_uri"].startswith("http"))
        self.assertGreaterEqual(self.result["drain_polls"], 1)
        self.assertTrue(self.result["probe"]["targetProbe"]["canConnect"])


# ======================================================================================
# Repository invariants
# ======================================================================================


class PackageInvariantTest(unittest.TestCase):
    def test_public_signatures_are_unchanged(self):
        import inspect

        expected = {
            VcfaClient.__init__: "(self, base_url, token, timeout=30.0)",
            VcfaClient.query_named_credentials: (
                "(self, filter_expr=None, sort_asc=None, sort_desc=None, page=1, page_size=25)"
            ),
            VcfaClient.get_named_credential: "(self, credential_id)",
            VcfaClient.create_named_credential: "(self, name, username, password, entity=None)",
            VcfaClient.update_named_credential: (
                "(self, credential_id, name=None, username=None, password=None, entity=None)"
            ),
            VcfaClient.delete_named_credential: "(self, credential_id)",
            VcfaClient.test_connection: (
                "(self, host, port, secure=None, timeout=None, "
                "hostname_verification_algorithm=None, additional_ca_issuers=None, "
                "proxy_connection=None, pre_configured_proxy=None)"
            ),
            VcfaClient.get_virtual_center: "(self, vc_urn)",
            VcfaClient.update_virtual_center: "(self, vc_urn, body)",
            VcfaClient.query_audit_trail: (
                "(self, filter_expr=None, sort_asc=None, sort_desc=None, page=1, page_size=25)"
            ),
            rotate_named_credential: (
                "(client, credential_name, vc_urn, new_username, new_password, probe_host, "
                "probe_port, max_polls=30)"
            ),
        }
        actual = {callable_: str(inspect.signature(callable_)) for callable_ in expected}
        self.assertEqual(expected, actual)

    def test_the_package_imports_nothing_outside_the_standard_library(self):
        import ast

        package_dir = os.path.join(REPO_ROOT, "vcfa_rotate")
        offenders = []
        for filename in sorted(os.listdir(package_dir)):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(package_dir, filename)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import inside the package
                        continue
                    roots = [(node.module or "").split(".")[0]]
                else:
                    continue
                for root in roots:
                    if root and root not in sys.stdlib_module_names and root != "vcfa_rotate":
                        offenders.append("%s imports %s" % (filename, root))
        self.assertEqual([], offenders, offenders)

    def test_the_contract_declares_that_it_is_derived_from_reference_docs(self):
        provenance = CONTRACT["provenance"]
        self.assertEqual("reference-documentation", provenance["kind"])
        self.assertFalse(provenance["isPublishedSpecification"])
        self.assertFalse(provenance["specificationFound"])
        self.assertIn("vcf-api-specs", provenance["specificationRepositoryChecked"])
        statement = " ".join(provenance["statement"])
        self.assertIn("NOT A PUBLISHED SPECIFICATION", statement)

    def test_every_contract_operation_is_traceable_to_a_dated_source_page(self):
        with open(os.path.join(REPO_ROOT, "docs", "official_sources.json"), encoding="utf-8") as h:
            sources = json.load(h)
        by_url = {}
        for page in sources["pages"]:
            self.assertTrue(page["url"].startswith("https://developer.broadcom.com/xapis/"), page)
            self.assertRegex(page["dateFetched"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(page["documents"].strip())
            by_url[page["url"]] = page
        for op in CONTRACT["operations"]:
            self.assertIn(op["source"], by_url,
                          "operation %s cites a page missing from official_sources.json"
                          % op["operationId"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
