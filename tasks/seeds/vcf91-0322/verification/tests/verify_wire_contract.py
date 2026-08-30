"""Protected verifier for the VCF Automation deployment triage task.

It starts the contract-pinned loopback mock on 127.0.0.1, runs ``python -m vcfa.diagnose``
against it in a subprocess, then asserts two things:

  * the diagnosis report names the failure that is only visible in the request's events
    and event logs, and
  * every HTTP request the client produced has exactly the wire shape that
    ``docs/contract.json`` describes, including that optional parameters and optional
    body fields the caller did not set were omitted rather than sent empty, and
  * transient failures are remediated with the original inputs while permanent failures
    leave the appliance unchanged.

No VMware endpoint is contacted. Run it with::

    python3 -m unittest tests.verify_wire_contract -v
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from urllib.parse import parse_qs

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vcfa_mock.server import MockVcfAutomation  # noqa: E402

# --------------------------------------------------------------------------
# Integrity of the files the verifier trusts. These are task scaffolding and
# must reach the verifier unmodified.
# --------------------------------------------------------------------------

PROTECTED_DIGESTS = {
    "docs/contract.json": "735d1fb59f4b537b2f66a07289e23e9c910e39ed62d35902f0b6f8c97d41a9b3",
    "docs/official_sources.json": "f33044495a53ed7cb05f576b3f4cad9b2172508abaf71f22144f05d069b6341a",
    "vcfa_mock/server.py": "9f0f9095593396ee0d1a288f2e7cbaa74cfdcca20b3ae6ffc136ea35051e7344",
    "vcfa_mock/fixtures.json": "bea6ad4198811a5bec87dcf564ad3891d168229c5d489dcd93ee827c54ccfc5d",
}

# --------------------------------------------------------------------------
# What the deployment's events and event logs actually say.
# --------------------------------------------------------------------------

DEPLOYMENT_NAME = "payments-uat-07"
DEPLOYMENT_ID = "7c3f1a92-5d84-4b6e-9a02-1f5ac8d43b17"
DEPLOYMENT_STATUS = "ACTION_FAILED"
FAILED_REQUEST_ID = "9f4d2c61-8b07-4e3a-a5d9-6c2b1f80ae35"
FAILED_ACTION_ID = "Deployment.PowerOn"
FAILED_EVENT_ID = "f28d5c03-7a49-4b16-93ef-c04a1b8e6572"
HEALTHY_EVENT_ID = "d70b16a4-91e5-4f38-8c02-5a6db93f74e1"
FAILED_RESOURCE_NAME = "payments-uat-07-app-01"
FAILED_RESOURCE_ID = "e91d0b56-4c72-48af-b30d-27e6a5f194c2"
ROOT_CAUSE_CODE = "vim.fault.TaskInProgress"
ROOT_CAUSE_MESSAGE = (
    "ERROR vim.fault.TaskInProgress: The operation is not allowed in the current state. "
    "Another task is already in progress on entity 'vm-20418'."
)
ROOT_CAUSE_LOG_ROW = 4
CLASSIFICATION = "transient"
PERMANENT_ROOT_CAUSE_CODE = "vim.fault.NoPermission"
PERMANENT_ROOT_CAUSE_MESSAGE = (
    "ERROR vim.fault.NoPermission: Permission to perform this operation was denied."
)
PERMANENT_CLASSIFICATION = "permanent"

REPORT_KEYS = {
    "deployment_id",
    "deployment_name",
    "deployment_status",
    "failed_request_id",
    "failed_action_id",
    "failed_event_id",
    "failed_resource_name",
    "failed_resource_id",
    "root_cause_code",
    "root_cause_message",
    "root_cause_log_row",
    "classification",
    "dismissed_request_id",
    "resubmitted_request_id",
}

# Populated by setUpModule.
MOCK = None
TMPDIR = None
RUN = None
REPORT = None
RECORDS = []
ACCESS_TOKEN = None
RESUBMITTED_REQUEST_ID = None
ACTION_INPUTS = None
PERMANENT_MOCK = None
PERMANENT_TMPDIR = None
PERMANENT_RUN = None
PERMANENT_REPORT = None
PERMANENT_RECORDS = []


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def setUpModule():
    global MOCK, TMPDIR, RUN, REPORT, RECORDS, ACCESS_TOKEN, RESUBMITTED_REQUEST_ID
    global ACTION_INPUTS, PERMANENT_MOCK, PERMANENT_TMPDIR, PERMANENT_RUN
    global PERMANENT_REPORT, PERMANENT_RECORDS, ROOT_CAUSE_MESSAGE

    for relative, expected in PROTECTED_DIGESTS.items():
        actual = _sha256(os.path.join(ROOT, relative))
        if actual != expected:
            raise AssertionError(
                f"{relative} has been modified. It is task scaffolding and the verifier "
                f"trusts it as shipped (expected sha256 {expected}, found {actual})."
            )

    TMPDIR = tempfile.TemporaryDirectory(prefix="vcfa-verify-")
    base_fixtures = os.path.join(ROOT, "vcfa_mock", "fixtures.json")
    with open(base_fixtures, "r", encoding="utf-8") as handle:
        fixtures = json.load(handle)

    # Per-run values the client can only learn over the wire prevent a fixture-shaped,
    # hard-coded report from satisfying the verifier.
    ACCESS_TOKEN = "vcfa." + uuid.uuid4().hex + "." + uuid.uuid4().hex
    RESUBMITTED_REQUEST_ID = str(uuid.uuid4())
    ACTION_INPUTS = {"force": False, "requestNonce": uuid.uuid4().hex}
    fixtures["access_token"] = ACCESS_TOKEN
    fixtures["new_request_id"] = RESUBMITTED_REQUEST_ID
    fixtures["requests"][FAILED_REQUEST_ID]["inputs"] = ACTION_INPUTS
    fault_entity = "vm-" + uuid.uuid4().hex[:12]
    ROOT_CAUSE_MESSAGE = (
        "ERROR vim.fault.TaskInProgress: The operation is not allowed in the current "
        f"state. Another task is already in progress on entity '{fault_entity}'."
    )
    fixtures["logs_by_event"][FAILED_EVENT_ID][3]["message"] = ROOT_CAUSE_MESSAGE

    run_fixtures = os.path.join(TMPDIR.name, "fixtures.json")
    with open(run_fixtures, "w", encoding="utf-8") as handle:
        json.dump(fixtures, handle)

    log_path = os.path.join(TMPDIR.name, "requests.jsonl")
    report_path = os.path.join(TMPDIR.name, "diagnosis.json")

    MOCK = MockVcfAutomation(log_path=log_path, fixtures_path=run_fixtures)
    base_url = MOCK.start()

    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    RUN = subprocess.run(
        [
            sys.executable,
            "-m",
            "vcfa.diagnose",
            "--base-url",
            base_url,
            "--tenant",
            fixtures["tenant"],
            "--api-token",
            fixtures["api_token"],
            "--deployment",
            DEPLOYMENT_NAME,
            "--out",
            report_path,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    # Join the server before reading its JSONL log so the final response cannot race
    # with the final record append.
    MOCK.stop()
    RECORDS = MOCK.records()
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as handle:
            try:
                REPORT = json.load(handle)
            except ValueError as exc:
                REPORT = {"__parse_error__": str(exc)}

    # Exercise the other branch of the end-user requirement against an independent
    # appliance state. This log explicitly says a permission change is required and
    # explicitly distinguishes "retry after a change" from "retry as-is".
    with open(base_fixtures, "r", encoding="utf-8") as handle:
        permanent_fixtures = json.load(handle)
    permanent_fixtures["access_token"] = (
        "vcfa." + uuid.uuid4().hex + "." + uuid.uuid4().hex
    )
    permanent_fixtures["new_request_id"] = str(uuid.uuid4())
    failed_log = permanent_fixtures["logs_by_event"][FAILED_EVENT_ID]
    failed_log[3]["message"] = PERMANENT_ROOT_CAUSE_MESSAGE
    failed_log[4]["message"] = (
        "INFO  Retry only after granting VirtualMachine.Interact.PowerOn to the service "
        "account; a configuration change is required."
    )

    PERMANENT_TMPDIR = tempfile.TemporaryDirectory(prefix="vcfa-verify-permanent-")
    permanent_run_fixtures = os.path.join(PERMANENT_TMPDIR.name, "fixtures.json")
    with open(permanent_run_fixtures, "w", encoding="utf-8") as handle:
        json.dump(permanent_fixtures, handle)

    permanent_log_path = os.path.join(PERMANENT_TMPDIR.name, "requests.jsonl")
    permanent_report_path = os.path.join(PERMANENT_TMPDIR.name, "diagnosis.json")
    PERMANENT_MOCK = MockVcfAutomation(
        log_path=permanent_log_path, fixtures_path=permanent_run_fixtures
    )
    permanent_base_url = PERMANENT_MOCK.start()
    PERMANENT_RUN = subprocess.run(
        [
            sys.executable,
            "-m",
            "vcfa.diagnose",
            "--base-url",
            permanent_base_url,
            "--tenant",
            permanent_fixtures["tenant"],
            "--api-token",
            permanent_fixtures["api_token"],
            "--deployment",
            DEPLOYMENT_NAME,
            "--out",
            permanent_report_path,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    PERMANENT_MOCK.stop()
    PERMANENT_RECORDS = PERMANENT_MOCK.records()
    if os.path.exists(permanent_report_path):
        with open(permanent_report_path, "r", encoding="utf-8") as handle:
            try:
                PERMANENT_REPORT = json.load(handle)
            except ValueError as exc:
                PERMANENT_REPORT = {"__parse_error__": str(exc)}


def tearDownModule():
    if MOCK is not None:
        MOCK.stop()
    if PERMANENT_MOCK is not None:
        PERMANENT_MOCK.stop()
    if TMPDIR is not None:
        TMPDIR.cleanup()
    if PERMANENT_TMPDIR is not None:
        PERMANENT_TMPDIR.cleanup()


def _failure_context() -> str:
    lines = ["", "--- vcfa.diagnose stdout ---", (RUN.stdout or "").strip()]
    lines += ["--- vcfa.diagnose stderr ---", (RUN.stderr or "").strip()]
    lines += ["--- mock request log ---"]
    for record in RECORDS:
        lines.append(
            "{seq:>2}  {method:<4} {path}{qs}  -> {status}  [{op}]".format(
                seq=record["seq"],
                method=record["method"],
                path=record["path"],
                qs=("?" + record["raw_query"]) if record["raw_query"] else "",
                status=record["status"],
                op=record["operation_id"] or "no matching contract operation",
            )
        )
        if record["body"]:
            lines.append("      body: " + record["body"])
    return "\n".join(lines)


def _permanent_failure_context() -> str:
    lines = [
        "",
        "--- permanent vcfa.diagnose stdout ---",
        (PERMANENT_RUN.stdout or "").strip(),
        "--- permanent vcfa.diagnose stderr ---",
        (PERMANENT_RUN.stderr or "").strip(),
        "--- permanent mock request log ---",
    ]
    for record in PERMANENT_RECORDS:
        lines.append(
            "{seq:>2}  {method:<4} {path}{qs}  -> {status}  [{op}]".format(
                seq=record["seq"],
                method=record["method"],
                path=record["path"],
                qs=("?" + record["raw_query"]) if record["raw_query"] else "",
                status=record["status"],
                op=record["operation_id"] or "no matching contract operation",
            )
        )
    return "\n".join(lines)


class VcfaTriageTestCase(unittest.TestCase):
    longMessage = True

    def setUp(self):
        self.addTypeEqualityFunc(dict, "assertDictEqual")

    def fail_with(self, message):
        self.fail(message + "\n" + _failure_context())

    def records_for(self, operation_id):
        return [r for r in RECORDS if r["operation_id"] == operation_id]

    def only_record_for(self, operation_id):
        found = self.records_for(operation_id)
        if len(found) != 1:
            self.fail_with(
                f"expected exactly one {operation_id} request, found {len(found)}"
            )
        return found[0]

    # -- the run itself ---------------------------------------------------

    def test_cli_exits_zero(self):
        if RUN.returncode != 0:
            self.fail_with(f"python -m vcfa.diagnose exited {RUN.returncode}")

    def test_report_was_written(self):
        if REPORT is None:
            self.fail_with("vcfa.diagnose did not write the --out report")
        self.assertNotIn("__parse_error__", REPORT, "the report is not valid JSON")

    # -- the diagnosis ----------------------------------------------------

    def test_report_has_exactly_the_required_keys(self):
        self.assertIsNotNone(REPORT, "no report was written")
        self.assertEqual(
            REPORT_KEYS,
            set(REPORT),
            "the report must carry exactly the documented key set",
        )

    def test_report_identifies_the_deployment_and_failed_request(self):
        self.assertEqual(DEPLOYMENT_ID, REPORT.get("deployment_id"))
        self.assertEqual(DEPLOYMENT_NAME, REPORT.get("deployment_name"))
        self.assertEqual(DEPLOYMENT_STATUS, REPORT.get("deployment_status"))
        self.assertEqual(FAILED_REQUEST_ID, REPORT.get("failed_request_id"))
        self.assertEqual(FAILED_ACTION_ID, REPORT.get("failed_action_id"))

    def test_report_pins_the_failure_to_the_right_event_and_resource(self):
        self.assertEqual(
            FAILED_EVENT_ID,
            REPORT.get("failed_event_id"),
            "the failing event is the one whose log carries the fault, not the first "
            "event that has logs",
        )
        self.assertEqual(FAILED_RESOURCE_NAME, REPORT.get("failed_resource_name"))
        self.assertEqual(FAILED_RESOURCE_ID, REPORT.get("failed_resource_id"))

    def test_report_names_the_root_cause_from_the_event_log(self):
        self.assertEqual(ROOT_CAUSE_CODE, REPORT.get("root_cause_code"))
        self.assertEqual(ROOT_CAUSE_MESSAGE, REPORT.get("root_cause_message"))
        self.assertEqual(ROOT_CAUSE_LOG_ROW, REPORT.get("root_cause_log_row"))
        self.assertEqual(CLASSIFICATION, REPORT.get("classification"))

    def test_report_records_the_remediation(self):
        self.assertEqual(FAILED_REQUEST_ID, REPORT.get("dismissed_request_id"))
        self.assertEqual(
            RESUBMITTED_REQUEST_ID,
            REPORT.get("resubmitted_request_id"),
            "the resubmitted request id must come from the response to the day-2 "
            "action, which is only knowable over the wire",
        )

    # -- the traffic as a whole -------------------------------------------

    def test_every_request_matched_a_contract_operation(self):
        stray = [r for r in RECORDS if r["operation_id"] is None]
        if stray:
            self.fail_with(f"{len(stray)} request(s) hit no operation in the contract")

    def test_no_request_was_rejected(self):
        bad = [r for r in RECORDS if r["status"] >= 400]
        if bad:
            self.fail_with(f"{len(bad)} request(s) came back 4xx/5xx")

    def test_exact_request_inventory(self):
        expected = [
            "auth.token.exchange",
            "deployments.list",
            "deployments.requests.list",
            "requests.get",
            "requests.events.list",
            "requests.events.logs.get",
            "requests.events.logs.get",
            "deployments.resources.list",
            "requests.action",
            "deployments.requests.submitAction",
        ]
        actual = sorted(r["operation_id"] or "?" for r in RECORDS)
        if actual != sorted(expected):
            self.fail_with(
                "the client did not make exactly the expected set of calls\n"
                f"      expected: {sorted(expected)}\n"
                f"      actual:   {actual}"
            )

    def test_no_query_parameter_was_sent_empty(self):
        for record in RECORDS:
            for name, values in record["query"].items():
                for value in values:
                    if value == "":
                        self.fail_with(
                            f"request {record['seq']} ({record['operation_id']}) sent "
                            f"query parameter {name!r} with an empty value; an optional "
                            "parameter that is not set must be omitted"
                        )

    def test_no_json_body_field_was_sent_empty(self):
        for record in RECORDS:
            if not record["body"]:
                continue
            content_type = (record["headers"].get("content-type") or "").split(";")[0]
            if content_type.strip() != "application/json":
                continue
            payload = json.loads(record["body"])
            for key, value in payload.items():
                if value is None or value == "" or value == {} or value == []:
                    self.fail_with(
                        f"request {record['seq']} ({record['operation_id']}) sent body "
                        f"field {key!r} as {value!r}; an optional field that is not set "
                        "must be omitted"
                    )

    def test_token_is_exchanged_first_and_carried_on_every_other_call(self):
        self.assertTrue(RECORDS, "the client made no requests at all")
        if RECORDS[0]["operation_id"] != "auth.token.exchange":
            self.fail_with("the first request must be the token exchange")
        for record in RECORDS[1:]:
            self.assertEqual(
                "Bearer " + ACCESS_TOKEN,
                record["headers"].get("authorization"),
                f"request {record['seq']} ({record['operation_id']}) must carry the "
                "access token returned by the token exchange",
            )

    def test_every_request_accepts_json(self):
        for record in RECORDS:
            self.assertEqual(
                "application/json",
                record["headers"].get("accept"),
                f"request {record['seq']} ({record['operation_id']}) must send "
                "Accept: application/json",
            )

    def test_remediation_is_the_last_two_calls_in_order(self):
        self.assertGreaterEqual(len(RECORDS), 2)
        if RECORDS[-2]["operation_id"] != "requests.action":
            self.fail_with("the failed request must be dismissed before anything is resubmitted")
        if RECORDS[-1]["operation_id"] != "deployments.requests.submitAction":
            self.fail_with("the resubmitted day-2 action must be the final call")

    # -- per-operation wire shape -----------------------------------------

    def test_token_exchange_wire_shape(self):
        record = self.only_record_for("auth.token.exchange")
        self.assertEqual("POST", record["method"])
        self.assertEqual("/tm/oauth/tenant/payments/token", record["path"])
        self.assertEqual({}, record["query"], "the token exchange takes no query string")
        self.assertEqual(
            "application/x-www-form-urlencoded",
            (record["headers"].get("content-type") or "").split(";")[0].strip(),
        )
        self.assertNotIn(
            "authorization",
            record["headers"],
            "the token exchange must not carry an Authorization header",
        )
        form = parse_qs(record["body"], keep_blank_values=True)
        self.assertEqual({"grant_type", "refresh_token"}, set(form))
        self.assertEqual(["refresh_token"], form["grant_type"])

    def test_deployments_list_wire_shape(self):
        record = self.only_record_for("deployments.list")
        self.assertEqual("GET", record["method"])
        self.assertEqual("/deployment/api/deployments", record["path"])
        self.assertEqual(
            {"name": [DEPLOYMENT_NAME]},
            record["query"],
            "look the deployment up by its exact name and send nothing else; the "
            "contract's other filters are not in use on this call",
        )
        self.assertEqual("", record["body"])

    def test_deployment_requests_list_wire_shape(self):
        record = self.only_record_for("deployments.requests.list")
        self.assertEqual("GET", record["method"])
        self.assertEqual(
            f"/deployment/api/deployments/{DEPLOYMENT_ID}/requests", record["path"]
        )
        self.assertEqual(
            {}, record["query"], "no filter, page or sort override is in use on this call"
        )
        self.assertEqual("", record["body"])

    def test_request_get_wire_shape(self):
        record = self.only_record_for("requests.get")
        self.assertEqual("GET", record["method"])
        self.assertEqual(f"/deployment/api/requests/{FAILED_REQUEST_ID}", record["path"])
        self.assertEqual(
            {}, record["query"], "this operation declares no query parameters at all"
        )
        self.assertEqual("", record["body"])

    def test_request_events_list_wire_shape(self):
        record = self.only_record_for("requests.events.list")
        self.assertEqual("GET", record["method"])
        self.assertEqual(
            f"/deployment/api/requests/{FAILED_REQUEST_ID}/events", record["path"]
        )
        self.assertEqual({}, record["query"])
        self.assertEqual("", record["body"])

    def test_event_logs_are_read_for_exactly_the_events_that_have_them(self):
        records = self.records_for("requests.events.logs.get")
        paths = sorted(r["path"] for r in records)
        expected = sorted(
            f"/deployment/api/requests/{FAILED_REQUEST_ID}/events/{event_id}/logs"
            for event_id in (HEALTHY_EVENT_ID, FAILED_EVENT_ID)
        )
        if paths != expected:
            self.fail_with(
                "logs must be read for every event that reports hasLogs true, and for "
                "no other event\n"
                f"      expected: {expected}\n"
                f"      actual:   {paths}"
            )
        for record in records:
            self.assertEqual("GET", record["method"])
            self.assertEqual(
                {},
                record["query"],
                "sinceRow is optional and no caller here asked for a partial log, so it "
                "must not appear on the wire",
            )
            self.assertEqual("", record["body"])

    def test_deployment_resources_list_wire_shape(self):
        record = self.only_record_for("deployments.resources.list")
        self.assertEqual("GET", record["method"])
        self.assertEqual(
            f"/deployment/api/deployments/{DEPLOYMENT_ID}/resources", record["path"]
        )
        self.assertEqual({}, record["query"])
        self.assertEqual("", record["body"])

    def test_dismiss_wire_shape(self):
        record = self.only_record_for("requests.action")
        self.assertEqual("POST", record["method"])
        self.assertEqual(f"/deployment/api/requests/{FAILED_REQUEST_ID}", record["path"])
        self.assertEqual({"action": ["dismiss"]}, record["query"])
        self.assertEqual("", record["body"], "this operation takes no request body")
        self.assertNotIn(
            "content-type",
            record["headers"],
            "no body was sent, so the request must not describe one with a Content-Type "
            "header",
        )

    def test_submit_action_wire_shape(self):
        record = self.only_record_for("deployments.requests.submitAction")
        self.assertEqual("POST", record["method"])
        self.assertEqual(
            f"/deployment/api/deployments/{DEPLOYMENT_ID}/requests", record["path"]
        )
        self.assertEqual({}, record["query"], "this operation declares no query parameters")
        self.assertEqual(
            "application/json",
            (record["headers"].get("content-type") or "").split(";")[0].strip(),
        )
        payload = json.loads(record["body"])
        self.assertEqual(
            {"actionId": FAILED_ACTION_ID, "inputs": ACTION_INPUTS},
            payload,
            "the resubmission must carry the exact inputs read from the full failed "
            "request, while reason remains omitted because none was supplied",
        )


class PermanentFailureTestCase(unittest.TestCase):
    """The permanent branch must diagnose but never mutate appliance state."""

    def fail_with(self, message):
        self.fail(message + "\n" + _permanent_failure_context())

    def test_permanent_run_succeeds_and_writes_the_required_report(self):
        if PERMANENT_RUN.returncode != 0:
            self.fail_with(
                f"permanent scenario exited {PERMANENT_RUN.returncode}"
            )
        if PERMANENT_REPORT is None:
            self.fail_with("permanent scenario did not write the --out report")
        self.assertNotIn("__parse_error__", PERMANENT_REPORT)
        self.assertEqual(REPORT_KEYS, set(PERMANENT_REPORT))

    def test_permanent_report_identifies_and_classifies_the_fault(self):
        self.assertIsNotNone(PERMANENT_REPORT, _permanent_failure_context())
        self.assertEqual(DEPLOYMENT_ID, PERMANENT_REPORT.get("deployment_id"))
        self.assertEqual(FAILED_REQUEST_ID, PERMANENT_REPORT.get("failed_request_id"))
        self.assertEqual(FAILED_EVENT_ID, PERMANENT_REPORT.get("failed_event_id"))
        self.assertEqual(FAILED_RESOURCE_ID, PERMANENT_REPORT.get("failed_resource_id"))
        self.assertEqual(
            PERMANENT_ROOT_CAUSE_CODE,
            PERMANENT_REPORT.get("root_cause_code"),
        )
        self.assertEqual(
            PERMANENT_ROOT_CAUSE_MESSAGE,
            PERMANENT_REPORT.get("root_cause_message"),
        )
        self.assertEqual(ROOT_CAUSE_LOG_ROW, PERMANENT_REPORT.get("root_cause_log_row"))
        self.assertEqual(
            PERMANENT_CLASSIFICATION,
            PERMANENT_REPORT.get("classification"),
        )

    def test_permanent_failure_makes_no_mutating_request(self):
        self.assertIsNone(PERMANENT_REPORT.get("dismissed_request_id"))
        self.assertIsNone(PERMANENT_REPORT.get("resubmitted_request_id"))
        writes = [
            record
            for record in PERMANENT_RECORDS
            if record["operation_id"]
            in ("requests.action", "deployments.requests.submitAction")
        ]
        if writes:
            self.fail_with(
                "a permanent failure must not dismiss or resubmit the failed request"
            )

    def test_permanent_failure_still_reads_every_required_record_once(self):
        expected = sorted(
            [
                "auth.token.exchange",
                "deployments.list",
                "deployments.requests.list",
                "requests.get",
                "requests.events.list",
                "requests.events.logs.get",
                "requests.events.logs.get",
                "deployments.resources.list",
            ]
        )
        actual = sorted(record["operation_id"] or "?" for record in PERMANENT_RECORDS)
        if actual != expected:
            self.fail_with(
                "the permanent branch must make the required reads exactly once\n"
                f"      expected: {expected}\n"
                f"      actual:   {actual}"
            )
        rejected = [record for record in PERMANENT_RECORDS if record["status"] >= 400]
        if rejected:
            self.fail_with("the permanent branch sent a rejected request")


if __name__ == "__main__":
    unittest.main(verbosity=2)
