"""Checks for the VCF Operations change runbook.

These run entirely against the loopback appliance stand-in in
``tools/vcfops_mock.py``. No real VMware endpoint is contacted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "tools" / "vcfops_mock.py"
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
CHANGE_REQUEST = ROOT / "runbook" / "change_request.json"

SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_VERSION = "9.1.0.0"

OPERATION_IDS = [
    "acquireToken",
    "createCustomGroup",
    "createMaintenanceSchedules",
    "assignPolicy",
]

# Facts read off specifications/vcf-operations/vcf-operations-openapi.json at
# commit 3949fc3 (tag 9.1.0.0) of github.com/vmware/vcf-api-specs.
EXPECTED_OPERATIONS = {
    "acquireToken": {
        "method": "POST",
        "path": "/api/auth/token/acquire",
        "path_params": [],
        "request_schema": "username-password",
        "required": ["password", "username"],
        "optional": ["authSource"],
        "success_status": 200,
        "documented_error_statuses": [401],
    },
    "createCustomGroup": {
        "method": "POST",
        "path": "/api/resources/groups",
        "path_params": [],
        "request_schema": "custom-group",
        "required": ["membershipDefinition", "resourceKey"],
        "optional": ["autoResolveMembership", "id", "links", "policy"],
        "success_status": 201,
        "documented_error_statuses": [],
    },
    "createMaintenanceSchedules": {
        "method": "POST",
        "path": "/api/maintenanceschedules",
        "path_params": [],
        "request_schema": "maintenance-schedule",
        "required": ["key", "schedule"],
        "optional": ["id"],
        "success_status": 201,
        "documented_error_statuses": [400, 422],
    },
    "assignPolicy": {
        "method": "PUT",
        "path": "/api/policies/{id}/assign",
        "path_params": ["id"],
        "request_schema": "policy-assignment-param",
        "required": [],
        "optional": ["groupIds", "resourceAssignments"],
        "success_status": 200,
        "documented_error_statuses": [],
    },
}

USERNAME = "svc-runbook"
PASSWORD = "Runb00k!Ops"
TOKEN = "OpsTkn-" + hashlib.sha256(f"{USERNAME}:{PASSWORD}".encode()).hexdigest()[:32]

_UUID_NS = uuid.UUID("6f1a5c7e-3b42-4d18-9a06-0c2e7d5b8419")
GROUP_NAME = "VCF91-Patch-Wave-1"
GROUP_ID = str(uuid.uuid5(_UUID_NS, f"custom-group:{GROUP_NAME}"))
POLICY_ID = "b1c9f0e2-4a76-4d1e-9f3c-0d5a8e2b6741"
CONFLICT_KEY = "vcf91-patch-wave-1"
FRESH_KEY = "vcf91-patch-wave-2"
FRESH_SCHEDULE_ID = str(uuid.uuid5(_UUID_NS, f"maintenance-schedule:{FRESH_KEY}"))

CONFLICT_MESSAGE = (
    f"A maintenance schedule with key '{CONFLICT_KEY}' already exists on this "
    "cluster; schedule keys must be unique."
)

RESOURCES = [
    "3d0a9f21-6c74-4f0b-8e35-1a7c92db4e60",
    "8b41c5de-2f19-4a7d-9c02-64e8f3517ab9",
    "c72e6a08-51bd-4e93-a4f6-0db29c8347e1",
]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class Appliance:
    """Runs tools/vcfops_mock.py on an ephemeral loopback port."""

    def __init__(self, workdir: Path):
        self.log = workdir / "requests.ndjson"
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--log",
                str(self.log),
                "--port",
                "0",
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.proc.stdout.readline()
        if not line.strip():
            err = self.proc.stderr.read()
            raise AssertionError(
                "tools/vcfops_mock.py refused to start against docs/contract.json:\n" + err
            )
        self.port = json.loads(line)["port"]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.suite_api = self.base_url + "/suite-api"

    def requests(self):
        if not self.log.exists():
            return []
        entries = [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        entries.sort(key=lambda e: e["seq"])
        return entries

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=15)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


def run_runbook(
    appliance,
    change_request: Path,
    workdir: Path,
    auth_source=None,
    username=USERNAME,
    password=PASSWORD,
):
    report = workdir / "report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["VCFOPS_BASE_URL"] = appliance.base_url
    env["VCFOPS_USERNAME"] = username
    env["VCFOPS_PASSWORD"] = password
    env.pop("VCFOPS_AUTH_SOURCE", None)
    if auth_source is not None:
        env["VCFOPS_AUTH_SOURCE"] = auth_source

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "vcfops_runbook",
            "--change-request",
            str(change_request),
            "--report",
            str(report),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc, report


def load_report(proc, report_path):
    if not report_path.exists():
        raise AssertionError(
            "the runbook did not write a report.\n"
            f"exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return json.loads(report_path.read_text(encoding="utf-8"))


def find_empties(value, path="<body>"):
    """Locations holding null / "" / [] / {} — i.e. fields sent empty."""
    hits = []
    if value is None or value == "" or value == [] or value == {}:
        hits.append(path)
        return hits
    if isinstance(value, dict):
        for key, sub in value.items():
            hits.extend(find_empties(sub, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            hits.extend(find_empties(sub, f"{path}[{index}]"))
    return hits


class ApplianceCase(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="vcfops-check-"))
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.appliance = Appliance(self.workdir)
        self.addCleanup(self.appliance.close)

    def assertNoEmptyFields(self, body, label):
        hits = find_empties(body)
        self.assertEqual(
            [],
            hits,
            f"{label}: optional fields must be omitted, not sent empty. "
            f"Sent empty at: {hits}. Body: {json.dumps(body, sort_keys=True)}",
        )

    def assertAbsent(self, body, keys, label):
        for key in keys:
            self.assertNotIn(key, body, f"{label}: '{key}' was not set and must not be sent")


# ---------------------------------------------------------------------------
# contract derivation
# ---------------------------------------------------------------------------


class TestContract(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CONTRACT.exists(), "docs/contract.json is missing")
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_top_level_shape(self):
        self.assertEqual("/suite-api", self.contract.get("base_path"))
        self.assertEqual(
            {"header": "Authorization", "scheme": "OpsToken"}, self.contract.get("auth")
        )
        source = self.contract.get("source")
        self.assertIsInstance(source, dict, "contract needs a 'source' object")
        self.assertEqual(SPEC_PATH, source.get("spec_path"))
        self.assertEqual(SPEC_COMMIT, source.get("commit_sha"))
        self.assertEqual(SPEC_VERSION, source.get("spec_version"))

    def test_names_exactly_the_operations_used(self):
        got = [op["operationId"] for op in self.contract["operations"]]
        self.assertEqual(
            OPERATION_IDS,
            got,
            "operations must be the four operationIds the runbook calls, in call order",
        )

    def test_each_operation_matches_the_specification(self):
        for op in self.contract["operations"]:
            operation_id = op["operationId"]
            with self.subTest(operationId=operation_id):
                expected = EXPECTED_OPERATIONS[operation_id]
                actual = {key: op.get(key) for key in expected}
                self.assertEqual(expected, actual)

    def test_api_methods_and_paths_are_not_duplicated_in_package_source(self):
        package = ROOT / "vcfops_runbook"
        self.assertTrue(package.is_dir(), "vcfops_runbook package is missing")
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
        )
        for literal in [
            "/suite-api",
            "/api/auth/token/acquire",
            "/api/resources/groups",
            "/api/maintenanceschedules",
            "/api/policies/{id}/assign",
        ]:
            self.assertNotIn(
                literal,
                source,
                f"{literal!r} must come from docs/contract.json, not package source",
            )
        for method in {op["method"] for op in EXPECTED_OPERATIONS.values()}:
            self.assertNotRegex(
                source,
                rf"['\"]{method}['\"]",
                f"HTTP method {method} must come from docs/contract.json",
            )


class TestOfficialSources(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCES.exists(), "docs/official_sources.json is missing")
        self.doc = json.loads(SOURCES.read_text(encoding="utf-8"))

    def test_records_spec_path_commit_and_operations(self):
        self.assertEqual(SPEC_PATH, self.doc.get("spec_path"))
        self.assertEqual(SPEC_COMMIT, self.doc.get("commit_sha"))
        self.assertEqual(sorted(OPERATION_IDS), sorted(self.doc.get("operation_ids") or []))
        self.assertEqual(
            len(OPERATION_IDS),
            len(self.doc.get("operation_ids") or []),
            "operation_ids must not contain duplicates",
        )

    def test_records_repository_and_licence(self):
        self.assertEqual(
            "https://github.com/vmware/vcf-api-specs", self.doc.get("repository")
        )
        self.assertEqual("Apache-2.0", self.doc.get("license"))

    def test_points_at_the_operations_api_not_log_management(self):
        blob = json.dumps(self.doc)
        self.assertNotIn("log-management", blob)


class TestAppliancePin(ApplianceCase):
    def test_mock_starts_against_the_derived_contract(self):
        self.assertIsNotNone(self.appliance.port)

    def test_unnamed_operations_are_not_served(self):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self.appliance.suite_api + "/api/resources/groups/types",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=15)
        self.assertEqual(404, ctx.exception.code)


# ---------------------------------------------------------------------------
# the change that fails part-way through
# ---------------------------------------------------------------------------


class TestPartialFailureRun(ApplianceCase):
    def setUp(self):
        super().setUp()
        self.proc, report_path = run_runbook(self.appliance, CHANGE_REQUEST, self.workdir)
        self.report = load_report(self.proc, report_path)
        self.requests = self.appliance.requests()

    def test_stops_at_the_failed_step(self):
        sent = [entry["operationId"] for entry in self.requests]
        self.assertEqual(
            ["acquireToken", "createCustomGroup", "createMaintenanceSchedules"],
            sent,
            "the runbook must stop once a step fails and must not send the later step",
        )

    def test_exit_code_reports_failure(self):
        self.assertEqual(
            1,
            self.proc.returncode,
            f"a change that did not fully apply must exit 1.\nstdout:\n{self.proc.stdout}"
            f"\nstderr:\n{self.proc.stderr}",
        )

    def test_acquire_token_wire_shape(self):
        entry = self.requests[0]
        self.assertEqual("POST", entry["method"])
        self.assertEqual("/suite-api/api/auth/token/acquire", entry["path"])
        self.assertEqual({}, entry["query"])
        self.assertEqual(
            "application/json", entry["headers"].get("content-type", "").split(";")[0]
        )
        self.assertNotIn(
            "authorization",
            entry["headers"],
            "the token acquisition call is unauthenticated",
        )
        self.assertEqual({"username": USERNAME, "password": PASSWORD}, entry["body"])
        self.assertAbsent(entry["body"], ["authSource"], "acquireToken")
        self.assertNoEmptyFields(entry["body"], "acquireToken")
        self.assertEqual(200, entry["status"])

    def test_create_custom_group_wire_shape(self):
        entry = self.requests[1]
        self.assertEqual("POST", entry["method"])
        self.assertEqual("/suite-api/api/resources/groups", entry["path"])
        self.assertEqual({}, entry["query"])
        self.assertEqual(f"OpsToken {TOKEN}", entry["headers"].get("authorization"))
        self.assertEqual(
            {
                "resourceKey": {
                    "name": GROUP_NAME,
                    "adapterKindKey": "Container",
                    "resourceKindKey": "Environment",
                },
                "membershipDefinition": {"includedResources": RESOURCES},
                "autoResolveMembership": False,
            },
            entry["body"],
        )
        self.assertAbsent(entry["body"], ["id", "links", "policy"], "createCustomGroup")
        self.assertAbsent(
            entry["body"]["resourceKey"],
            ["links", "extension", "resourceIdentifiers"],
            "createCustomGroup.resourceKey",
        )
        self.assertAbsent(
            entry["body"]["membershipDefinition"],
            ["excludedResources", "rules", "custom-group-properties"],
            "createCustomGroup.membershipDefinition",
        )
        self.assertNoEmptyFields(entry["body"], "createCustomGroup")
        self.assertEqual(201, entry["status"])

    def test_create_maintenance_schedule_wire_shape(self):
        entry = self.requests[2]
        self.assertEqual("POST", entry["method"])
        self.assertEqual("/suite-api/api/maintenanceschedules", entry["path"])
        self.assertEqual({}, entry["query"])
        self.assertEqual(f"OpsToken {TOKEN}", entry["headers"].get("authorization"))
        self.assertEqual(
            {
                "key": CONFLICT_KEY,
                "schedule": {
                    "scheduleType": "ONCE",
                    "hour": 22,
                    "minuteOfTheHour": 30,
                    "duration": 120,
                    "startDate": "09/12/2026",
                    "timeZone": "America/Los_Angeles",
                },
            },
            entry["body"],
        )
        self.assertAbsent(entry["body"], ["id"], "createMaintenanceSchedules")
        self.assertAbsent(
            entry["body"]["schedule"],
            [
                "expirationDate",
                "expireRuns",
                "recurrence",
                "daysOfTheWeek",
                "dayOfTheMonth",
                "daysOfTheMonth",
                "month",
                "months",
                "weeksOfTheMonth",
            ],
            "createMaintenanceSchedules.schedule",
        )
        self.assertNoEmptyFields(entry["body"], "createMaintenanceSchedules")
        self.assertEqual(422, entry["status"])

    def test_report_outcome(self):
        self.assertEqual("partial_failure", self.report.get("outcome"))

    def test_report_step_results(self):
        self.assertEqual(
            [
                {"operationId": "acquireToken", "status": "succeeded", "httpStatus": 200},
                {
                    "operationId": "createCustomGroup",
                    "status": "succeeded",
                    "httpStatus": 201,
                },
                {
                    "operationId": "createMaintenanceSchedules",
                    "status": "failed",
                    "httpStatus": 422,
                },
                {"operationId": "assignPolicy", "status": "skipped", "httpStatus": None},
            ],
            self.report.get("steps"),
        )

    def test_report_lists_the_changes_that_were_applied(self):
        self.assertEqual(
            [{"operationId": "createCustomGroup", "resourceId": GROUP_ID}],
            self.report.get("appliedChanges"),
            "the custom group really was created and must be reported, with its id; "
            "acquiring a token changes nothing and is not an applied change",
        )

    def test_report_carries_the_server_failure_verbatim(self):
        self.assertEqual(
            {
                "operationId": "createMaintenanceSchedules",
                "httpStatus": 422,
                "message": CONFLICT_MESSAGE,
            },
            self.report.get("failure"),
        )


# ---------------------------------------------------------------------------
# the same change against an appliance where every step applies
# ---------------------------------------------------------------------------


class TestFullyAppliedRun(ApplianceCase):
    def setUp(self):
        super().setUp()
        request = json.loads(CHANGE_REQUEST.read_text(encoding="utf-8"))
        request["maintenanceSchedule"]["key"] = FRESH_KEY
        path = self.workdir / "change_request.json"
        path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.proc, report_path = run_runbook(self.appliance, path, self.workdir)
        self.report = load_report(self.proc, report_path)
        self.requests = self.appliance.requests()

    def test_all_four_steps_are_sent_in_order(self):
        self.assertEqual(OPERATION_IDS, [entry["operationId"] for entry in self.requests])

    def test_exit_code_reports_success(self):
        self.assertEqual(
            0,
            self.proc.returncode,
            f"stdout:\n{self.proc.stdout}\nstderr:\n{self.proc.stderr}",
        )

    def test_every_call_uses_json_content_type(self):
        for entry in self.requests:
            with self.subTest(operationId=entry["operationId"]):
                self.assertEqual(
                    "application/json",
                    entry["headers"].get("content-type", "").split(";")[0],
                )

    def test_assign_policy_wire_shape(self):
        entry = self.requests[3]
        self.assertEqual("PUT", entry["method"])
        self.assertEqual(
            f"/suite-api/api/policies/{POLICY_ID}/assign",
            entry["path"],
            "the policy identifier belongs in the path, not the body",
        )
        self.assertEqual({}, entry["query"])
        self.assertEqual(f"OpsToken {TOKEN}", entry["headers"].get("authorization"))
        self.assertEqual(
            {"groupIds": [GROUP_ID]},
            entry["body"],
            "the group created earlier in this run is the one assigned",
        )
        self.assertAbsent(entry["body"], ["resourceAssignments"], "assignPolicy")
        self.assertNoEmptyFields(entry["body"], "assignPolicy")
        self.assertEqual(200, entry["status"])

    def test_report_outcome_and_steps(self):
        self.assertEqual("success", self.report.get("outcome"))
        self.assertIsNone(self.report.get("failure"))
        self.assertEqual(
            [
                {"operationId": "acquireToken", "status": "succeeded", "httpStatus": 200},
                {
                    "operationId": "createCustomGroup",
                    "status": "succeeded",
                    "httpStatus": 201,
                },
                {
                    "operationId": "createMaintenanceSchedules",
                    "status": "succeeded",
                    "httpStatus": 201,
                },
                {"operationId": "assignPolicy", "status": "succeeded", "httpStatus": 200},
            ],
            self.report.get("steps"),
        )

    def test_report_lists_every_applied_change(self):
        self.assertEqual(
            [
                {"operationId": "createCustomGroup", "resourceId": GROUP_ID},
                {
                    "operationId": "createMaintenanceSchedules",
                    "resourceId": FRESH_SCHEDULE_ID,
                },
                {"operationId": "assignPolicy", "resourceId": POLICY_ID},
            ],
            self.report.get("appliedChanges"),
        )


class TestAuthSourceIsConditional(ApplianceCase):
    def test_auth_source_is_sent_only_when_configured(self):
        proc, _ = run_runbook(
            self.appliance, CHANGE_REQUEST, self.workdir, auth_source="vIDMAuthSource"
        )
        self.assertIn(proc.returncode, (0, 1), f"stderr:\n{proc.stderr}")
        entry = self.appliance.requests()[0]
        self.assertEqual(
            {
                "username": USERNAME,
                "password": PASSWORD,
                "authSource": "vIDMAuthSource",
            },
            entry["body"],
            "authSource is optional: send it when it is configured, omit it otherwise",
        )


# ---------------------------------------------------------------------------
# failures at every other step, and optional empty-value omission
# ---------------------------------------------------------------------------


class TestTokenFailure(ApplianceCase):
    def test_token_failure_is_reported_and_no_mutation_is_attempted(self):
        proc, report_path = run_runbook(
            self.appliance,
            CHANGE_REQUEST,
            self.workdir,
            password="not-the-local-password",
        )
        report = load_report(proc, report_path)

        self.assertEqual(1, proc.returncode)
        self.assertEqual(["acquireToken"], [r["operationId"] for r in self.appliance.requests()])
        self.assertEqual(
            [
                {"operationId": "acquireToken", "status": "failed", "httpStatus": 401},
                {"operationId": "createCustomGroup", "status": "skipped", "httpStatus": None},
                {
                    "operationId": "createMaintenanceSchedules",
                    "status": "skipped",
                    "httpStatus": None,
                },
                {"operationId": "assignPolicy", "status": "skipped", "httpStatus": None},
            ],
            report.get("steps"),
        )
        self.assertEqual("failure", report.get("outcome"))
        self.assertEqual([], report.get("appliedChanges"))
        self.assertEqual(
            {
                "operationId": "acquireToken",
                "httpStatus": 401,
                "message": "Invalid username or password.",
            },
            report.get("failure"),
        )
        self.assertIn("outcome: failure", proc.stdout)
        self.assertIn("acquireToken: failed (HTTP 401)", proc.stdout)


class TestCustomGroupFailure(ApplianceCase):
    def test_group_failure_is_failure_when_no_change_landed_in_this_run(self):
        request = json.loads(CHANGE_REQUEST.read_text(encoding="utf-8"))
        request["maintenanceSchedule"]["key"] = "vcf91-step-two-prime"
        path = self.workdir / "prime.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        prime_proc, _ = run_runbook(self.appliance, path, self.workdir)
        self.assertEqual(0, prime_proc.returncode, prime_proc.stderr)

        request["maintenanceSchedule"]["key"] = "vcf91-step-two-never-reached"
        path = self.workdir / "duplicate-group.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        before = len(self.appliance.requests())
        proc, report_path = run_runbook(self.appliance, path, self.workdir)
        report = load_report(proc, report_path)
        requests = self.appliance.requests()[before:]

        self.assertEqual(1, proc.returncode)
        self.assertEqual(
            ["acquireToken", "createCustomGroup"],
            [entry["operationId"] for entry in requests],
        )
        self.assertEqual("failure", report.get("outcome"))
        self.assertEqual([], report.get("appliedChanges"))
        self.assertEqual(
            [
                {"operationId": "acquireToken", "status": "succeeded", "httpStatus": 200},
                {"operationId": "createCustomGroup", "status": "failed", "httpStatus": 409},
                {
                    "operationId": "createMaintenanceSchedules",
                    "status": "skipped",
                    "httpStatus": None,
                },
                {"operationId": "assignPolicy", "status": "skipped", "httpStatus": None},
            ],
            report.get("steps"),
        )
        self.assertEqual(
            {
                "operationId": "createCustomGroup",
                "httpStatus": 409,
                "message": f"A custom group named '{GROUP_NAME}' already exists.",
            },
            report.get("failure"),
        )


class TestPolicyAssignmentFailure(ApplianceCase):
    def test_policy_failure_reports_both_changes_that_already_landed(self):
        group_name = "VCF91-Policy-Failure-Group"
        schedule_key = "vcf91-policy-failure-schedule"
        missing_policy = "00000000-0000-0000-0000-000000000000"
        request = json.loads(CHANGE_REQUEST.read_text(encoding="utf-8"))
        request["customGroup"]["name"] = group_name
        request["maintenanceSchedule"]["key"] = schedule_key
        request["policyAssignment"]["policyId"] = missing_policy
        path = self.workdir / "missing-policy.json"
        path.write_text(json.dumps(request), encoding="utf-8")

        proc, report_path = run_runbook(self.appliance, path, self.workdir)
        report = load_report(proc, report_path)

        self.assertEqual(1, proc.returncode)
        self.assertEqual(OPERATION_IDS, [r["operationId"] for r in self.appliance.requests()])
        self.assertEqual("partial_failure", report.get("outcome"))
        self.assertEqual(
            [
                {
                    "operationId": "createCustomGroup",
                    "resourceId": str(uuid.uuid5(_UUID_NS, f"custom-group:{group_name}")),
                },
                {
                    "operationId": "createMaintenanceSchedules",
                    "resourceId": str(
                        uuid.uuid5(_UUID_NS, f"maintenance-schedule:{schedule_key}")
                    ),
                },
            ],
            report.get("appliedChanges"),
        )
        self.assertEqual(
            {
                "operationId": "assignPolicy",
                "httpStatus": 404,
                "message": f"No policy found with identifier '{missing_policy}'.",
            },
            report.get("failure"),
        )


class TestOptionalEmptyValuesAreOmitted(ApplianceCase):
    def test_empty_optional_values_are_not_serialized(self):
        request = json.loads(CHANGE_REQUEST.read_text(encoding="utf-8"))
        request["customGroup"]["name"] = "VCF91-Optional-Omission-Group"
        request["customGroup"]["autoResolveMembership"] = None
        request["customGroup"]["includedResources"] = []
        request["maintenanceSchedule"]["key"] = "vcf91-optional-omission"
        request["maintenanceSchedule"]["hour"] = 0
        request["maintenanceSchedule"]["minuteOfTheHour"] = 0
        request["maintenanceSchedule"]["startDate"] = ""
        request["maintenanceSchedule"]["timeZone"] = None
        path = self.workdir / "empty-optionals.json"
        path.write_text(json.dumps(request), encoding="utf-8")

        proc, _ = run_runbook(self.appliance, path, self.workdir)
        self.assertEqual(0, proc.returncode, proc.stderr)
        requests = self.appliance.requests()

        group_body = requests[1]["body"]
        self.assertEqual({}, group_body["membershipDefinition"])
        self.assertAbsent(
            group_body,
            ["autoResolveMembership", "id", "links", "policy"],
            "createCustomGroup",
        )
        self.assertAbsent(
            group_body["membershipDefinition"],
            ["includedResources", "excludedResources", "rules", "custom-group-properties"],
            "createCustomGroup.membershipDefinition",
        )

        schedule_body = requests[2]["body"]["schedule"]
        self.assertEqual(
            {
                "scheduleType": "ONCE",
                "hour": 0,
                "minuteOfTheHour": 0,
                "duration": 120,
            },
            schedule_body,
        )
        self.assertAbsent(schedule_body, ["startDate", "timeZone"], "schedule")


if __name__ == "__main__":
    unittest.main()
