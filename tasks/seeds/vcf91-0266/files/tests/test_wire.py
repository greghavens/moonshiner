"""Exact request wire shape, read back from the mock's request log."""

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qsl

from vcfops_triage import AlertQuery, Credentials, OperationsClient, OperationsError

from . import fixtures
from .mock_vcf_operations import contract_mock
from .support import EXPECTED_AUTHORIZATION, PASSWORD, USERNAME, headers_of, nominal_run, run_triage

_IDP = fixtures.IDP_CONFIG_ID
_DIRECTORY = fixtures.FAILED_DIRECTORY_ID
_DIRECTORIES = (
    "/suite-api/api/fleet-management/iam/identity-providers/" + _IDP + "/ldap-directories"
)
_SYNC_LOGS = _DIRECTORIES + "/" + _DIRECTORY + "/sync-logs"

# (operationId, method, path, query) in the order the procedure performs them.
EXPECTED_REQUESTS = [
    ("acquireToken", "POST", "/suite-api/api/auth/token/acquire", ""),
    ("getLdapDirectories", "GET", _DIRECTORIES, ""),
    ("getLdapSyncLogs", "GET", _SYNC_LOGS, "page=0&pageSize=5"),
    ("getLdapSyncLogs", "GET", _SYNC_LOGS, "page=1&pageSize=5"),
    ("getLdapSyncLogs", "GET", _SYNC_LOGS, "page=2&pageSize=5"),
    (
        "getLdapSyncLogById",
        "GET",
        _SYNC_LOGS + "/" + fixtures.FIRST_FAILURE_SYNC_LOG_ID,
        "",
    ),
    ("queryAlert", "POST", "/suite-api/api/alerts/query", ""),
    (
        "getAlertContributingSymptoms",
        "GET",
        "/suite-api/api/alerts/contributingsymptoms",
        "id={}&id={}&id={}".format(
            fixtures.ALERT_ID_DATASTORE, fixtures.ALERT_ID_IDENTITY, fixtures.ALERT_ID_CPU
        ),
    ),
    (
        "getSymptoms",
        "GET",
        "/suite-api/api/symptoms",
        "resourceId={}&activeOnly=true&includeAlarmInfo=true".format(
            fixtures.RESOURCE_ID_OPS_MANAGER
        ),
    ),
    (
        "getSymptoms",
        "GET",
        "/suite-api/api/symptoms",
        "resourceId={}&activeOnly=true&includeAlarmInfo=true".format(
            fixtures.RESOURCE_ID_CLUSTER
        ),
    ),
    ("releaseToken", "POST", "/suite-api/api/auth/token/release", ""),
]

ACQUIRE_BODY = '{"password":"' + PASSWORD + '","username":"' + USERNAME + '"}'
ALERT_QUERY_BODY = '{"activeOnly":true,"alertCriticality":["CRITICAL","IMMEDIATE"]}'


class RequestSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = nominal_run().log

    def test_exact_operation_sequence(self):
        actual = [
            (entry["operationId"], entry["method"], entry["path"], entry["query"])
            for entry in self.log
        ]
        self.assertEqual(actual, EXPECTED_REQUESTS)

    def test_every_request_was_accepted(self):
        self.assertEqual([entry["status"] for entry in self.log], [200] * len(self.log))

    def test_no_request_left_the_contract(self):
        self.assertEqual([e for e in self.log if e["operationId"] is None], [])

    def test_no_request_target_carries_a_bare_question_mark(self):
        for entry in self.log:
            if "?" in entry["target"]:
                self.assertNotEqual(entry["query"], "", entry["target"])
            self.assertEqual(
                entry["target"],
                entry["path"] + ("?" + entry["query"] if entry["query"] else ""),
            )


class HeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = nominal_run().log

    def test_accept_is_sent_exactly_once_everywhere(self):
        for entry in self.log:
            self.assertEqual(
                headers_of(entry).get("accept"),
                ["application/json"],
                entry["operationId"],
            )

    def test_authorization_is_the_issued_token_and_absent_on_acquire(self):
        for entry in self.log:
            authorization = headers_of(entry).get("authorization")
            if entry["operationId"] == "acquireToken":
                self.assertIsNone(authorization)
            else:
                self.assertEqual(
                    authorization, [EXPECTED_AUTHORIZATION], entry["operationId"]
                )

    def test_content_type_only_on_operations_that_carry_a_body(self):
        with_body = {"acquireToken", "queryAlert"}
        for entry in self.log:
            content_type = headers_of(entry).get("content-type")
            if entry["operationId"] in with_body:
                self.assertEqual(content_type, ["application/json"], entry["operationId"])
            else:
                self.assertIsNone(content_type, entry["operationId"])


class RequestBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log = nominal_run().log

    def test_only_the_two_body_operations_send_bytes(self):
        with_body = {"acquireToken", "queryAlert"}
        for entry in self.log:
            if entry["operationId"] in with_body:
                self.assertIsInstance(entry["body_text"], str)
            else:
                self.assertIsNone(entry["body_text"], entry["operationId"])

    def test_acquire_token_body_is_compact_and_in_schema_property_order(self):
        entry = self.log[0]
        self.assertEqual(entry["operationId"], "acquireToken")
        self.assertEqual(entry["body_text"], ACQUIRE_BODY)
        self.assertEqual(list(entry["body"]), ["password", "username"])

    def test_alert_query_body_is_compact_and_in_schema_property_order(self):
        entry = next(e for e in self.log if e["operationId"] == "queryAlert")
        self.assertEqual(entry["body_text"], ALERT_QUERY_BODY)
        self.assertEqual(list(entry["body"]), ["activeOnly", "alertCriticality"])


class UnsetOptionalFieldTests(unittest.TestCase):
    """Unset optional fields are omitted, never sent as an empty value."""

    @classmethod
    def setUpClass(cls):
        cls.log = nominal_run().log

    def test_unset_optional_body_properties_are_absent(self):
        acquire = self.log[0]["body"]
        self.assertNotIn("authSource", acquire)

        alert_query = next(e for e in self.log if e["operationId"] == "queryAlert")["body"]
        self.assertNotIn("alertName", alert_query)
        self.assertNotIn("alertStatus", alert_query)

    def test_no_body_property_is_sent_empty(self):
        for entry in self.log:
            if entry["body"] is None:
                continue
            for name, value in entry["body"].items():
                self.assertIsNotNone(value, (entry["operationId"], name))
                self.assertNotIn(
                    value, ("", [], {}), (entry["operationId"], name)
                )

    def test_unset_optional_query_parameters_are_absent(self):
        for entry in self.log:
            names = [name for name, _ in parse_qsl(entry["query"], keep_blank_values=True)]
            if entry["operationId"] == "getLdapSyncLogs":
                self.assertEqual(names, ["page", "pageSize"])
                self.assertNotIn("last", names)
            elif entry["operationId"] == "getSymptoms":
                self.assertEqual(names, ["resourceId", "activeOnly", "includeAlarmInfo"])
            elif entry["operationId"] == "queryAlert":
                self.assertEqual(names, [])

    def test_no_query_parameter_is_sent_blank(self):
        for entry in self.log:
            for name, value in parse_qsl(entry["query"], keep_blank_values=True):
                self.assertNotEqual(value, "", (entry["operationId"], name))

    def test_a_set_optional_property_is_sent_in_schema_order(self):
        run = run_triage(
            credentials=Credentials(
                username=USERNAME, password=PASSWORD, auth_source="CORP-AD"
            )
        )
        entry = run.log[0]
        self.assertEqual(entry["operationId"], "acquireToken")
        self.assertEqual(
            entry["body_text"],
            '{"authSource":"CORP-AD","password":"'
            + PASSWORD
            + '","username":"'
            + USERNAME
            + '"}',
        )


class ClientWireEdgeCaseTests(unittest.TestCase):
    """Exercise strict wire rules that the nominal triage only uses one way."""

    def _run_client(self, action):
        with tempfile.TemporaryDirectory() as workspace:
            log_path = Path(workspace) / "client.jsonl"
            with contract_mock(log_path) as mock:
                client = OperationsClient(mock.base_url)
                client.acquire_token(Credentials(username=USERNAME, password=PASSWORD))
                try:
                    action(client)
                finally:
                    client.release_token()
                return mock.read_log()

    def test_every_path_parameter_is_encoded_as_one_segment(self):
        def call(client):
            with self.assertRaises(OperationsError):
                client.get_ldap_sync_log(
                    "idp/config", "directory value", "sync?log#part"
                )

        log = self._run_client(call)
        entry = log[1]
        self.assertEqual(entry["operationId"], "getLdapSyncLogById")
        self.assertEqual(
            entry["path"],
            "/suite-api/api/fleet-management/iam/identity-providers/"
            "idp%2Fconfig/ldap-directories/directory%20value/sync-logs/"
            "sync%3Flog%23part",
        )

    def test_false_query_booleans_use_lowercase_literals(self):
        log = self._run_client(
            lambda client: client.get_symptoms(
                fixtures.RESOURCE_ID_OPS_MANAGER,
                active_only=False,
                include_alarm_info=False,
            )
        )
        entry = log[1]
        self.assertEqual(entry["operationId"], "getSymptoms")
        self.assertEqual(
            entry["query"],
            "resourceId={}&activeOnly=false&includeAlarmInfo=false".format(
                fixtures.RESOURCE_ID_OPS_MANAGER
            ),
        )

    def test_all_set_alert_properties_follow_schema_order(self):
        log = self._run_client(
            lambda client: client.query_alerts(
                AlertQuery(
                    active_only=False,
                    alert_criticality=("WARNING",),
                    alert_name="stale",
                    alert_status=("ACTIVE",),
                )
            )
        )
        entry = log[1]
        self.assertEqual(entry["operationId"], "queryAlert")
        self.assertEqual(
            entry["body_text"],
            '{"activeOnly":false,"alertCriticality":["WARNING"],'
            '"alertName":"stale","alertStatus":["ACTIVE"]}',
        )


class MockContractPinningTests(unittest.TestCase):
    """The mock itself only answers for operations the contract names."""

    def _probe(self, method, target, header_pairs, body=b""):
        with tempfile.TemporaryDirectory() as workspace:
            log_path = Path(workspace) / "probe.jsonl"
            with contract_mock(log_path) as mock:
                status, payload = mock.probe(method, target, header_pairs, body)
                return status, payload, mock.read_log()

    def test_an_operation_outside_the_contract_is_refused_and_logged(self):
        status, payload, log = self._probe(
            "GET",
            "/suite-api/api/logs/queryconfigs",
            [("accept", "application/json")],
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["errorCode"], "NOT_FOUND")
        self.assertEqual([entry["operationId"] for entry in log], [None])

    def test_a_contract_route_without_the_base_path_is_refused(self):
        status, _, _ = self._probe(
            "GET", "/api/symptoms", [("accept", "application/json")]
        )
        self.assertEqual(status, 404)

    def test_a_contract_route_with_the_wrong_method_is_refused(self):
        status, _, _ = self._probe(
            "GET", "/suite-api/api/alerts/query", [("accept", "application/json")]
        )
        self.assertEqual(status, 404)

    def test_an_undeclared_query_parameter_is_refused(self):
        status, payload, _ = self._probe(
            "POST",
            "/suite-api/api/auth/token/acquire?verbose=true",
            [("accept", "application/json"), ("content-type", "application/json")],
            b'{"password":"p","username":"u"}',
        )
        self.assertEqual(status, 400)
        self.assertIn("verbose", json.loads(payload)["message"])

    def test_an_authenticated_operation_refuses_an_unissued_token(self):
        status, _, _ = self._probe(
            "GET",
            "/suite-api/api/symptoms",
            [("accept", "application/json"), ("authorization", "vRealizeOpsToken forged")],
        )
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
