"""PROTECTED FILE -- do not modify.

Asserts the exact wire shape VcfEvcGuard produces against the loopback fixture,
and that the precheck really gates the mutating call.
"""

import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock.vcenter_mock import (  # noqa: E402
    CHECK_TASK_ID,
    CLUSTER_ID,
    INITIAL_EVC_MODE,
    SESSION_TOKEN,
    SET_TASK_ID,
    MockVcenter,
)
from tests import support  # noqa: E402

EVC_PATH = "/api/vcenter/cluster/%s/evc-mode" % CLUSTER_ID
CHECK_TASK_PATH = "/api/cis/tasks/%s" % CHECK_TASK_ID
SET_TASK_PATH = "/api/cis/tasks/%s" % SET_TASK_ID


def has_null(node):
    """True when a null appears anywhere in the decoded JSON body."""
    if node is None:
        return True
    if isinstance(node, dict):
        return any(has_null(value) for value in node.values())
    if isinstance(node, list):
        return any(has_null(value) for value in node)
    return False


class WireProtocolTestCase(unittest.TestCase):
    scenario = "precheck_clean"
    evc_mode = support.TARGET_EVC_MODE

    @classmethod
    def setUpClass(cls):
        if cls is WireProtocolTestCase:
            raise unittest.SkipTest("base class")
        support.require_pwsh()
        support.ensure_module_written()
        cls.mock = MockVcenter(scenario=cls.scenario).start()
        try:
            cls.report = support.run_driver(
                "run_scenario.ps1",
                {
                    "ModulePath": support.MANIFEST,
                    "Port": cls.mock.port,
                    "Cluster": CLUSTER_ID,
                    "EvcModeJson": (
                        "none"
                        if cls.evc_mode is None
                        else json.dumps(cls.evc_mode, separators=(",", ":"))
                    ),
                },
            )
        except BaseException:
            cls.mock.stop()
            raise
        cls.requests = list(cls.mock.requests)
        cls.mock.stop()

    # -- shared helpers ---------------------------------------------------
    def assertDriverSucceeded(self):
        if not self.report.get("ok"):
            self.fail(
                "the driver could not complete the scenario.\n%s"
                % (self.report.get("error") or self.report.get("_stdout"))
            )

    def sequence(self):
        return [(r["method"], r["path"]) for r in self.requests if not r["off_contract"]]

    def only(self, method, path):
        found = [
            r
            for r in self.requests
            if r["method"] == method and r["path"] == path and not r["off_contract"]
        ]
        self.assertEqual(
            1, len(found), "expected exactly one %s %s, saw %d" % (method, path, len(found))
        )
        return found[0]

    def all_of(self, method, path):
        return [
            r
            for r in self.requests
            if r["method"] == method and r["path"] == path and not r["off_contract"]
        ]

    # -- assertions shared by every scenario ------------------------------
    def test_no_endpoint_outside_the_contract_is_touched(self):
        self.assertDriverSucceeded()
        stray = [
            "%s %s" % (r["method"], r["raw_path"]) for r in self.requests if r["off_contract"]
        ]
        self.assertEqual([], stray, "the client called endpoints the contract does not name")

    def test_session_is_created_with_basic_auth_and_no_body(self):
        self.assertDriverSucceeded()
        entry = self.only("POST", "/api/session")
        authorization = entry["headers"].get("authorization", "")
        self.assertTrue(
            authorization.lower().startswith("basic "),
            "Cis.Session_create must authenticate with the basic_auth scheme, got %r"
            % authorization,
        )
        decoded = base64.b64decode(authorization.split(None, 1)[1]).decode("utf-8")
        self.assertEqual("administrator@vsphere.local:VMw@re1!VMw@re1!", decoded)
        self.assertNotIn(
            "vmware-api-session-id",
            entry["headers"],
            "the login call must not carry a session token",
        )
        self.assertEqual("", entry["body"], "Cis.Session_create takes no request body")

    def test_every_other_call_uses_the_session_token_and_not_the_password(self):
        self.assertDriverSucceeded()
        for entry in self.requests:
            if entry["off_contract"] or entry["path"] == "/api/session":
                continue
            label = "%s %s" % (entry["method"], entry["path"])
            self.assertEqual(
                SESSION_TOKEN,
                entry["headers"].get("vmware-api-session-id"),
                "%s must carry the api_key_auth header vmware-api-session-id" % label,
            )
            self.assertNotIn(
                "authorization",
                entry["headers"],
                "%s must not replay the credential once a session exists" % label,
            )

    def test_check_set_is_a_post_with_the_contracted_query(self):
        self.assertDriverSucceeded()
        entry = self.only("POST", EVC_PATH)
        self.assertEqual(
            {"action": ["check-set"], "vmw-task": ["true"]},
            entry["query_params"],
            "Vcenter.Cluster.EvcMode_checkSet$Task is POST %s?action=check-set&vmw-task=true"
            % EVC_PATH,
        )
        self.assertIn("application/json", entry["headers"].get("content-type", ""))

    def test_check_task_is_polled_without_a_get_spec(self):
        self.assertDriverSucceeded()
        polls = self.all_of("GET", CHECK_TASK_PATH)
        self.assertGreaterEqual(
            len(polls), 2, "the check-set task must be polled until it reaches a terminal state"
        )
        for entry in polls:
            self.assertEqual(
                {},
                entry["query_params"],
                "the check-set task carries the result the gate needs, so Cis.Tasks_get must "
                "be called with no GetSpec query parameters at all -- saw %r" % entry["query"],
            )

    def test_request_bodies_omit_unset_optional_properties(self):
        self.assertDriverSucceeded()
        for entry in self.all_of("POST", EVC_PATH) + self.all_of("PUT", EVC_PATH):
            label = "%s %s" % (entry["method"], entry["path"])
            body = json.loads(entry["body"])
            self.assertIsInstance(body, dict, "%s body must be a SetSpec object" % label)
            self.assertFalse(
                has_null(body),
                "%s sent a null: Vcenter.Cluster.EvcMode.SetSpec properties that are not set "
                "must be omitted, not sent as null. Body was %s" % (label, entry["body"]),
            )
            if self.evc_mode is None:
                self.assertEqual(
                    {},
                    body,
                    "clearing EVC means a SetSpec with evc_mode omitted, so the body is {} -- "
                    "saw %s" % entry["body"],
                )
            else:
                self.assertEqual(
                    {"evc_mode": self.evc_mode},
                    body,
                    "%s must send exactly the requested SetSpec -- saw %s"
                    % (label, entry["body"]),
                )

    def test_result_has_exactly_the_documented_properties(self):
        self.assertDriverSucceeded()
        self.assertEqual(
            [
                "Applied",
                "BlockedReason",
                "CheckResults",
                "CheckStatus",
                "CheckTask",
                "Cluster",
                "EvcModeAfter",
                "EvcModeBefore",
                "SetStatus",
                "SetTask",
            ],
            sorted((self.report.get("result") or {}).keys()),
        )


class PrecheckCleanTests(WireProtocolTestCase):
    scenario = "precheck_clean"
    evc_mode = support.TARGET_EVC_MODE

    def test_full_call_sequence(self):
        self.assertDriverSucceeded()
        self.assertEqual(
            [
                ("POST", "/api/session"),
                ("GET", EVC_PATH),
                ("POST", EVC_PATH),
                ("GET", CHECK_TASK_PATH),
                ("GET", CHECK_TASK_PATH),
                ("PUT", EVC_PATH),
                ("GET", SET_TASK_PATH),
                ("GET", SET_TASK_PATH),
                ("GET", EVC_PATH),
            ],
            self.sequence(),
        )

    def test_set_is_a_put_with_only_the_task_query(self):
        self.assertDriverSucceeded()
        entry = self.only("PUT", EVC_PATH)
        self.assertEqual(
            {"vmw-task": ["true"]},
            entry["query_params"],
            "Vcenter.Cluster.EvcMode_set$Task is PUT %s?vmw-task=true -- it carries no action"
            % EVC_PATH,
        )
        self.assertIn("application/json", entry["headers"].get("content-type", ""))

    def test_check_and_set_send_the_same_spec(self):
        self.assertDriverSucceeded()
        check = json.loads(self.only("POST", EVC_PATH)["body"])
        applied = json.loads(self.only("PUT", EVC_PATH)["body"])
        self.assertEqual(
            check, applied, "the spec that was prechecked must be the spec that is applied"
        )

    def test_set_task_poll_excludes_the_result_and_omits_return_all(self):
        self.assertDriverSucceeded()
        polls = self.all_of("GET", SET_TASK_PATH)
        self.assertGreaterEqual(len(polls), 2)
        for entry in polls:
            self.assertEqual(
                {"exclude_result": ["true"]},
                entry["query_params"],
                "polling the set task needs no result, so the GetSpec is exactly "
                "exclude_result=true; return_all is not set and must therefore be absent "
                "from the query string rather than sent empty -- saw %r" % entry["query"],
            )

    def test_change_was_applied_and_reported(self):
        self.assertDriverSucceeded()
        self.assertIs(True, support.result_field(self.report, "Applied"))
        self.assertEqual([], list(support.result_field(self.report, "CheckResults") or []))
        self.assertEqual(CHECK_TASK_ID, support.result_field(self.report, "CheckTask"))
        self.assertEqual(SET_TASK_ID, support.result_field(self.report, "SetTask"))
        self.assertEqual("SUCCEEDED", support.result_field(self.report, "CheckStatus"))
        self.assertEqual("SUCCEEDED", support.result_field(self.report, "SetStatus"))
        self.assertIsNone(support.result_field(self.report, "BlockedReason"))

    def test_before_and_after_state_are_reported(self):
        self.assertDriverSucceeded()
        before = support.result_field(self.report, "EvcModeBefore")
        after = support.result_field(self.report, "EvcModeAfter")
        self.assertEqual(INITIAL_EVC_MODE["key"], (before or {}).get("key"))
        self.assertEqual(support.TARGET_EVC_MODE["key"], (after or {}).get("key"))


class PrecheckClearsEvcTests(WireProtocolTestCase):
    scenario = "precheck_clean"
    evc_mode = None

    def test_cluster_evc_mode_was_cleared(self):
        self.assertDriverSucceeded()
        self.assertIs(True, support.result_field(self.report, "Applied"))
        self.assertIsNone(support.result_field(self.report, "EvcModeAfter"))

    def test_reset_body_is_an_empty_spec(self):
        self.assertDriverSucceeded()
        for entry in self.all_of("POST", EVC_PATH) + self.all_of("PUT", EVC_PATH):
            self.assertNotIn(
                "evc_mode",
                entry["body"],
                "a SetSpec that clears EVC omits evc_mode entirely -- saw %s" % entry["body"],
            )


class PrecheckBlockedTests(WireProtocolTestCase):
    scenario = "precheck_blocked"
    evc_mode = support.TARGET_EVC_MODE

    def test_nothing_was_mutated(self):
        self.assertDriverSucceeded()
        self.assertEqual(
            [],
            [r["body"] for r in self.all_of("PUT", EVC_PATH)],
            "the precheck reported blocking errors, so Vcenter.Cluster.EvcMode_set$Task "
            "must never be called",
        )
        self.assertEqual(
            [],
            self.all_of("GET", SET_TASK_PATH),
            "no set task exists when the precheck blocks the change",
        )

    def test_call_sequence_stops_after_the_precheck(self):
        self.assertDriverSucceeded()
        self.assertEqual(
            [
                ("POST", "/api/session"),
                ("GET", EVC_PATH),
                ("POST", EVC_PATH),
                ("GET", CHECK_TASK_PATH),
                ("GET", CHECK_TASK_PATH),
            ],
            self.sequence(),
        )

    def test_blocking_findings_are_returned_to_the_caller(self):
        self.assertDriverSucceeded()
        self.assertIs(False, support.result_field(self.report, "Applied"))
        self.assertEqual("PRECHECK_ERRORS", support.result_field(self.report, "BlockedReason"))
        self.assertIsNone(support.result_field(self.report, "SetTask"))
        self.assertIsNone(support.result_field(self.report, "SetStatus"))
        self.assertIsNone(support.result_field(self.report, "EvcModeAfter"))
        self.assertEqual("SUCCEEDED", support.result_field(self.report, "CheckStatus"))
        findings = support.result_field(self.report, "CheckResults")
        self.assertEqual(
            2, len(findings), "every CheckResult the precheck reported must be surfaced"
        )
        rendered = json.dumps(findings)
        self.assertIn("cpuid.AVX512F", rendered)
        self.assertIn("host-1042", rendered)


class PrecheckTaskFailedTests(WireProtocolTestCase):
    scenario = "precheck_task_failed"
    evc_mode = support.TARGET_EVC_MODE

    def test_a_failed_precheck_task_also_blocks_the_change(self):
        self.assertDriverSucceeded()
        self.assertEqual(
            [],
            self.all_of("PUT", EVC_PATH),
            "a precheck task that ends FAILED is not a licence to mutate",
        )
        self.assertIs(False, support.result_field(self.report, "Applied"))
        self.assertEqual(
            "PRECHECK_TASK_FAILED", support.result_field(self.report, "BlockedReason")
        )
        self.assertEqual("FAILED", support.result_field(self.report, "CheckStatus"))
        self.assertEqual([], list(support.result_field(self.report, "CheckResults") or []))
        self.assertIsNone(support.result_field(self.report, "SetTask"))
        self.assertIsNone(support.result_field(self.report, "SetStatus"))
        self.assertIsNone(support.result_field(self.report, "EvcModeAfter"))


class SetTaskFailedTests(WireProtocolTestCase):
    scenario = "set_task_failed"
    evc_mode = support.TARGET_EVC_MODE

    def test_failed_set_task_is_reported_and_not_marked_applied(self):
        self.assertDriverSucceeded()
        self.assertIs(False, support.result_field(self.report, "Applied"))
        self.assertEqual(SET_TASK_ID, support.result_field(self.report, "SetTask"))
        self.assertEqual("FAILED", support.result_field(self.report, "SetStatus"))
        self.assertIsNone(support.result_field(self.report, "BlockedReason"))

    def test_failed_set_task_does_not_change_the_reported_mode(self):
        self.assertDriverSucceeded()
        before = support.result_field(self.report, "EvcModeBefore")
        after = support.result_field(self.report, "EvcModeAfter")
        self.assertEqual(INITIAL_EVC_MODE, before)
        self.assertEqual(INITIAL_EVC_MODE, after)


class TaskTimeoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.require_pwsh()
        support.ensure_module_written()
        cls.mock = MockVcenter(polls_before_terminal=1_000_000).start()
        try:
            cls.report = support.run_driver(
                "run_scenario.ps1",
                {
                    "ModulePath": support.MANIFEST,
                    "Port": cls.mock.port,
                    "Cluster": CLUSTER_ID,
                    "EvcModeJson": json.dumps(
                        support.TARGET_EVC_MODE, separators=(",", ":")
                    ),
                    "PollIntervalSeconds": 0.02,
                    "TimeoutSeconds": 1,
                },
                timeout=15,
            )
        except BaseException:
            cls.mock.stop()
            raise
        cls.requests = list(cls.mock.requests)
        cls.mock.stop()

    def test_nonterminal_check_task_exceeds_its_timeout_as_an_error(self):
        self.assertIs(False, self.report.get("ok"))
        self.assertIn("after 1 seconds", self.report.get("error") or "")

    def test_timeout_never_allows_the_set_call(self):
        writes = [
            request
            for request in self.requests
            if request["method"] == "PUT" and request["path"] == EVC_PATH
        ]
        self.assertEqual([], writes)


if __name__ == "__main__":
    unittest.main()
