"""Verifier for the VCF Operations report client.

Asserts the exact wire shape of every request the client sends, read back from
the contract-pinned mock's request log, plus the asynchronous polling behaviour
the contract requires. Only 127.0.0.1 is contacted.

Part of the verifier. Do not modify.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

from tests.harness import (
    AUTH_SOURCE,
    DEF_COMPLETES,
    DEF_FAILS,
    DEF_NEVER_FINISHES,
    FAST_POLLING,
    PASSWORD,
    RESOURCE_CLUSTER,
    RESOURCE_DATASTORE,
    ROOT,
    SRC,
    USERNAME,
    MockServer,
    content_type_of,
    load_contract,
    load_sources,
)

from vcfops_mock import TERMINAL_AFTER_POLLS, report_payload
from vcfops_report import VcfOperationsClient
from vcfops_report.errors import (
    ApiError,
    AuthenticationError,
    ReportGenerationFailed,
    ReportTimeout,
)

CONTRACT_DOC = load_contract()
AUTH_PREFIX = CONTRACT_DOC["wire"]["authorizationHeaderFormat"].split("{")[0]
CONTRACT_OPS = set(CONTRACT_DOC["operations"])


class MockBackedTest(unittest.TestCase):
    def setUp(self):
        self.mock = MockServer()
        self.addCleanup(self.mock.stop)

    def client(self, **overrides):
        kwargs = dict(FAST_POLLING)
        kwargs.update(overrides)
        client = VcfOperationsClient(
            self.mock.base_url, USERNAME, kwargs.pop("password", PASSWORD), **kwargs
        )
        self.addCleanup(self._quiet_close, client)
        return client

    @staticmethod
    def _quiet_close(client):
        try:
            client.close()
        except Exception:
            pass

    # -- shared assertions ------------------------------------------------

    def assertLoopbackOnly(self):
        for record in self.mock.records():
            host = record["headers"].get("host", "")
            self.assertTrue(
                host.startswith("127.0.0.1:"),
                "request went to a non-loopback host %r: %s" % (host, record["path"]),
            )

    def assertNoRejections(self):
        bad = [
            (r["seq"], r["operationId"], r["path"], r["status"], r["contractViolation"])
            for r in self.mock.records()
            if r["status"] >= 400
        ]
        self.assertEqual([], bad, "the mock rejected requests: %s" % bad)

    def assertAuthorized(self, record, token):
        self.assertEqual(
            AUTH_PREFIX + token,
            record["headers"].get("authorization"),
            "%s did not carry the acquired token in the contract's header format"
            % record["operationId"],
        )


class TestAcquireTokenWireShape(MockBackedTest):
    def test_unset_auth_source_is_omitted_not_sent_empty(self):
        client = self.client()
        token = client.acquire_token()

        record = self.mock.only("acquireToken")
        self.assertEqual("POST", record["method"])
        self.assertEqual("/suite-api/api/auth/token/acquire", record["path"])
        self.assertEqual("", record["query"])
        self.assertEqual("application/json", content_type_of(record))
        self.assertEqual("application/json", record["headers"].get("accept"))
        self.assertNotIn(
            "authorization",
            record["headers"],
            "acquireToken declares an empty security array; it must be sent unauthenticated",
        )

        self.assertEqual(["password", "username"], record["bodyKeys"])
        self.assertNotIn("authSource", record["bodyRaw"])
        self.assertNotIn("null", record["bodyRaw"])
        self.assertEqual(USERNAME, record["bodyJson"]["username"])
        self.assertEqual(PASSWORD, record["bodyJson"]["password"])
        self.assertEqual(200, record["status"])

        self.assertTrue(token)
        self.assertEqual(token, client.token)
        self.assertLoopbackOnly()

    def test_set_auth_source_is_sent(self):
        client = self.client(auth_source=AUTH_SOURCE)
        client.acquire_token()

        record = self.mock.only("acquireToken")
        self.assertEqual(["authSource", "password", "username"], record["bodyKeys"])
        self.assertEqual(AUTH_SOURCE, record["bodyJson"]["authSource"])
        self.assertEqual(200, record["status"])

    def test_rejected_credentials_raise_authentication_error(self):
        client = self.client(password="wrong-password")
        with self.assertRaises(AuthenticationError) as caught:
            client.acquire_token()
        self.assertEqual(401, caught.exception.status)
        self.assertEqual("acquireToken", caught.exception.operation_id)
        self.assertIsNone(client.token)


class TestCreateReportWireShape(MockBackedTest):
    def test_unset_optional_properties_are_omitted(self):
        client = self.client()
        token = client.acquire_token()
        report = client.create_report(DEF_COMPLETES, RESOURCE_CLUSTER)

        record = self.mock.only("createReport")
        self.assertEqual("POST", record["method"])
        self.assertEqual("/suite-api/api/reports", record["path"])
        self.assertEqual("", record["query"])
        self.assertEqual("application/json", content_type_of(record))
        self.assertEqual("application/json", record["headers"].get("accept"))
        self.assertAuthorized(record, token)

        self.assertEqual(
            ["reportDefinitionId", "resourceId"],
            record["bodyKeys"],
            "an optional report property the caller did not set was still sent",
        )
        self.assertNotIn("null", record["bodyRaw"])
        self.assertNotIn("[]", record["bodyRaw"])
        self.assertEqual(DEF_COMPLETES, record["bodyJson"]["reportDefinitionId"])
        self.assertEqual(RESOURCE_CLUSTER, record["bodyJson"]["resourceId"])
        self.assertEqual(200, record["status"])

        self.assertIn("id", report)
        self.assertEqual("QUEUED", report["status"])

    def test_set_optional_properties_are_sent_including_falsy_ones(self):
        client = self.client()
        client.acquire_token()
        client.create_report(
            DEF_COMPLETES,
            RESOURCE_DATASTORE,
            name="Q3 datastore inventory",
            subject=["datastore", "capacity"],
            publish=False,
        )

        record = self.mock.only("createReport")
        self.assertEqual(
            ["name", "publish", "reportDefinitionId", "resourceId", "subject"],
            record["bodyKeys"],
            "description was not set by the caller and must be absent, while "
            "publish=False was set and must be present",
        )
        self.assertIs(False, record["bodyJson"]["publish"])
        self.assertEqual(["datastore", "capacity"], record["bodyJson"]["subject"])
        self.assertEqual("Q3 datastore inventory", record["bodyJson"]["name"])
        self.assertEqual(200, record["status"])


class TestAsynchronousPolling(MockBackedTest):
    def test_polls_to_terminal_status_before_downloading(self):
        client = self.client()
        result = client.generate_report(DEF_COMPLETES, RESOURCE_CLUSTER, report_format="CSV")

        self.assertEqual("COMPLETED", result.status)
        self.assertGreaterEqual(result.polls, TERMINAL_AFTER_POLLS)

        polls = self.mock.records_for("getReport")
        self.assertGreaterEqual(
            len(polls),
            TERMINAL_AFTER_POLLS,
            "the report is not finished when createReport returns; getReport must be "
            "polled until it reports a terminal status",
        )
        token = self.mock.only("acquireToken")
        token_value = None
        for poll in polls:
            self.assertEqual("GET", poll["method"])
            self.assertEqual(
                "/suite-api/api/reports/%s" % result.report_id, poll["path"]
            )
            self.assertEqual("", poll["query"])
            self.assertEqual(200, poll["status"])
            self.assertTrue(
                poll["headers"].get("authorization", "").startswith(AUTH_PREFIX),
                "getReport must carry the Authorization header",
            )
            self.assertEqual("", poll["bodyRaw"], "GET requests must carry no body")
            token_value = poll["headers"]["authorization"]
        self.assertEqual(200, token["status"])
        self.assertEqual(AUTH_PREFIX + client.token, token_value)

        download = self.mock.only("downloadReport")
        self.assertGreater(
            download["seq"],
            polls[TERMINAL_AFTER_POLLS - 1]["seq"],
            "downloadReport was issued before polling observed a terminal status",
        )
        polls_before_download = [p for p in polls if p["seq"] < download["seq"]]
        self.assertGreaterEqual(len(polls_before_download), TERMINAL_AFTER_POLLS)

        self.assertEqual(report_payload(result.report_id, "CSV"), result.content)
        self.assertNoRejections()
        self.assertLoopbackOnly()

    def test_failed_generation_raises_and_never_downloads(self):
        client = self.client()
        with self.assertRaises(ReportGenerationFailed) as caught:
            client.generate_report(DEF_FAILS, RESOURCE_CLUSTER, report_format="CSV")

        self.assertEqual("FAILED", caught.exception.report["status"])
        polls = self.mock.records_for("getReport")
        self.assertEqual(
            "/suite-api/api/reports/%s" % caught.exception.report_id, polls[-1]["path"]
        )
        self.assertEqual(
            [],
            self.mock.records_for("downloadReport"),
            "a report that reached the terminal failure status must never be downloaded",
        )
        self.assertGreaterEqual(len(self.mock.records_for("getReport")), TERMINAL_AFTER_POLLS)
        self.assertNoRejections()

    def test_never_terminal_generation_times_out_and_stops(self):
        client = self.client(poll_interval=0.02, poll_timeout=0.4)
        with self.assertRaises(ReportTimeout) as caught:
            client.generate_report(DEF_NEVER_FINISHES, RESOURCE_CLUSTER, report_format="CSV")

        self.assertEqual("RUNNING", caught.exception.last_status)
        polls = self.mock.records_for("getReport")
        self.assertGreaterEqual(len(polls), 2, "the client must poll more than once")
        self.assertLessEqual(
            len(polls), 200, "the client must honour poll_interval between polls"
        )
        self.assertEqual(len(polls), caught.exception.polls)
        self.assertEqual(
            [],
            self.mock.records_for("downloadReport"),
            "a report that never reached a terminal status must never be downloaded",
        )
        self.assertNoRejections()

    def test_download_of_incomplete_report_is_refused_by_the_api(self):
        client = self.client()
        client.acquire_token()
        report = client.create_report(DEF_COMPLETES, RESOURCE_CLUSTER)
        with self.assertRaises(ApiError) as caught:
            client.download_report(report["id"], report_format="CSV")
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("downloadReport", caught.exception.operation_id)


class TestDownloadWireShape(MockBackedTest):
    def test_chosen_format_is_sent_with_the_matching_accept_header(self):
        client = self.client()
        result = client.generate_report(DEF_COMPLETES, RESOURCE_CLUSTER, report_format="CSV")

        record = self.mock.only("downloadReport")
        self.assertEqual("GET", record["method"])
        self.assertEqual(
            "/suite-api/api/reports/%s/download" % result.report_id, record["path"]
        )
        self.assertEqual("format=CSV", record["query"])
        self.assertEqual("text/csv", record["headers"].get("accept"))
        self.assertEqual("", record["bodyRaw"])
        self.assertEqual(200, record["status"])
        self.assertEqual(report_payload(result.report_id, "CSV"), result.content)

    def test_unset_format_is_omitted_from_the_query_string(self):
        client = self.client()
        result = client.generate_report(DEF_COMPLETES, RESOURCE_CLUSTER)

        record = self.mock.only("downloadReport")
        self.assertEqual(
            "",
            record["query"],
            "an unset optional query parameter must be absent from the request URI, "
            "not sent empty",
        )
        self.assertEqual({}, record["queryParams"])
        self.assertNotIn("format", record["rawPath"])
        self.assertNotIn("?", record["rawPath"])
        self.assertEqual(
            "application/pdf",
            record["headers"].get("accept"),
            "Accept must correspond to the effective format, which defaults to PDF",
        )
        self.assertEqual(report_payload(result.report_id, "PDF"), result.content)
        self.assertNoRejections()


class TestSessionLifecycle(MockBackedTest):
    def test_release_token_sends_no_body_and_runs_last(self):
        client = VcfOperationsClient(self.mock.base_url, USERNAME, PASSWORD, **FAST_POLLING)
        with client as session:
            result = session.generate_report(
                DEF_COMPLETES, RESOURCE_CLUSTER, report_format="CSV"
            )
        self.assertEqual("COMPLETED", result.status)
        self.assertIsNone(client.token)

        record = self.mock.only("releaseToken")
        self.assertEqual("POST", record["method"])
        self.assertEqual("/suite-api/api/auth/token/release", record["path"])
        self.assertEqual("", record["query"])
        self.assertEqual(
            "",
            record["bodyRaw"],
            "releaseToken defines no request body; it must be sent with no payload",
        )
        self.assertNotIn(
            "content-type",
            record["headers"],
            "releaseToken defines no request body; it must not declare a Content-Type",
        )
        self.assertTrue(record["headers"].get("authorization", "").startswith(AUTH_PREFIX))
        self.assertEqual(200, record["status"])

        records = self.mock.records()
        self.assertEqual(
            record["seq"], records[-1]["seq"], "releaseToken must be the final request"
        )
        self.assertNoRejections()

    def test_token_is_released_even_when_generation_fails(self):
        client = VcfOperationsClient(self.mock.base_url, USERNAME, PASSWORD, **FAST_POLLING)
        with self.assertRaises(ReportGenerationFailed):
            with client as session:
                session.generate_report(DEF_FAILS, RESOURCE_CLUSTER, report_format="CSV")
        self.assertEqual(1, len(self.mock.records_for("releaseToken")))
        self.assertIsNone(client.token)


class TestOnlyContractOperationsAreUsed(MockBackedTest):
    def test_client_touches_no_operation_outside_the_contract(self):
        client = VcfOperationsClient(self.mock.base_url, USERNAME, PASSWORD, **FAST_POLLING)
        with client as session:
            session.generate_report(DEF_COMPLETES, RESOURCE_CLUSTER, report_format="CSV")

        seen = set()
        for record in self.mock.records():
            self.assertIsNotNone(
                record["operationId"],
                "the client called %s %s, which docs/contract.json does not name"
                % (record["method"], record["rawPath"]),
            )
            self.assertIn(record["operationId"], CONTRACT_OPS)
            self.assertIsNone(record["contractViolation"], json.dumps(record, indent=2))
            seen.add(record["operationId"])
        self.assertEqual(
            {"acquireToken", "createReport", "getReport", "downloadReport", "releaseToken"},
            seen,
        )
        self.assertLoopbackOnly()

    def test_mock_serves_only_the_operations_the_contract_names(self):
        self.assertEqual(sorted(CONTRACT_OPS), sorted(self.mock.operations))

        def status_of(path, method="GET"):
            request = urllib.request.Request(self.mock.base_url + path, method=method)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                exc.read()
                return exc.code

        # getReports, getReportDefinitions and deleteReport all exist in the
        # upstream specification but are not named by this contract.
        self.assertEqual(405, status_of("/api/reports"))
        self.assertEqual(404, status_of("/api/reportdefinitions"))
        self.assertEqual(405, status_of("/api/reports/some-id", method="DELETE"))
        self.assertEqual(404, status_of("/api/reports/some-id/"))

        violations = [r["contractViolation"] for r in self.mock.records()]
        self.assertEqual(
            ["wrong_method", "unknown_operation", "wrong_method", "unknown_operation"],
            violations,
        )


class TestCommandLine(MockBackedTest):
    def temp_dir(self):
        path = tempfile.mkdtemp(prefix="vcfops-cli-")
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "vcfops_report", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_successful_run_writes_the_report_and_prints_a_summary(self):
        output = os.path.join(self.temp_dir(), "report.csv")
        proc = self.run_cli(
            "--base-url", self.mock.base_url,
            "--username", USERNAME,
            "--password", PASSWORD,
            "--report-definition-id", DEF_COMPLETES,
            "--resource-id", RESOURCE_CLUSTER,
            "--format", "CSV",
            "--output", output,
            "--poll-interval", "0.02",
            "--poll-timeout", "20",
        )
        self.assertEqual(0, proc.returncode, proc.stderr)

        summary = json.loads(proc.stdout.strip())
        self.assertEqual("COMPLETED", summary["status"])
        self.assertEqual(output, summary["outputPath"])
        self.assertGreaterEqual(summary["polls"], TERMINAL_AFTER_POLLS)

        with open(output, "rb") as handle:
            written = handle.read()
        self.assertEqual(report_payload(summary["reportId"], "CSV"), written)
        self.assertEqual(len(written), summary["bytes"])

        download = self.mock.only("downloadReport")
        self.assertEqual("format=CSV", download["query"])
        self.assertEqual("text/csv", download["headers"].get("accept"))
        create = self.mock.only("createReport")
        self.assertEqual(
            ["reportDefinitionId", "resourceId"],
            create["bodyKeys"],
            "CLI options the caller omitted must stay unset in createReport",
        )
        acquire = self.mock.only("acquireToken")
        self.assertEqual(
            ["password", "username"],
            acquire["bodyKeys"],
            "an omitted --auth-source must stay unset in acquireToken",
        )
        self.assertEqual(1, len(self.mock.records_for("releaseToken")))
        self.assertNoRejections()
        self.assertLoopbackOnly()

    def test_optional_arguments_are_forwarded_and_unset_format_stays_unset(self):
        output = os.path.join(self.temp_dir(), "report.pdf")
        proc = self.run_cli(
            "--base-url", self.mock.base_url,
            "--username", USERNAME,
            "--password", PASSWORD,
            "--auth-source", AUTH_SOURCE,
            "--report-definition-id", DEF_COMPLETES,
            "--resource-id", RESOURCE_DATASTORE,
            "--name", "Q3 datastore inventory",
            "--description", "Capacity and rightsizing details",
            "--subject", "datastore",
            "--subject", "capacity",
            "--no-publish",
            "--output", output,
            "--poll-interval", "0.02",
            "--poll-timeout", "20",
            "--request-timeout", "10",
        )
        self.assertEqual(0, proc.returncode, proc.stderr)

        summary = json.loads(proc.stdout.strip())
        with open(output, "rb") as handle:
            written = handle.read()
        self.assertEqual(report_payload(summary["reportId"], "PDF"), written)

        acquire = self.mock.only("acquireToken")
        self.assertEqual(AUTH_SOURCE, acquire["bodyJson"]["authSource"])

        create = self.mock.only("createReport")
        self.assertEqual(
            [
                "description",
                "name",
                "publish",
                "reportDefinitionId",
                "resourceId",
                "subject",
            ],
            create["bodyKeys"],
        )
        self.assertEqual("Q3 datastore inventory", create["bodyJson"]["name"])
        self.assertEqual(
            "Capacity and rightsizing details", create["bodyJson"]["description"]
        )
        self.assertEqual(["datastore", "capacity"], create["bodyJson"]["subject"])
        self.assertIs(False, create["bodyJson"]["publish"])

        download = self.mock.only("downloadReport")
        self.assertEqual("", download["query"])
        self.assertNotIn("?", download["rawPath"])
        self.assertEqual("application/pdf", download["headers"].get("accept"))
        self.assertEqual(1, len(self.mock.records_for("releaseToken")))
        self.assertNoRejections()
        self.assertLoopbackOnly()

    def test_failed_generation_exits_nonzero_and_reports_the_error(self):
        output = os.path.join(self.temp_dir(), "report.csv")
        proc = self.run_cli(
            "--base-url", self.mock.base_url,
            "--username", USERNAME,
            "--password", PASSWORD,
            "--report-definition-id", DEF_FAILS,
            "--resource-id", RESOURCE_CLUSTER,
            "--format", "CSV",
            "--output", output,
            "--poll-interval", "0.02",
            "--poll-timeout", "20",
        )
        self.assertEqual(1, proc.returncode)
        self.assertEqual("", proc.stdout.strip())
        failure = json.loads(proc.stderr.strip())
        self.assertEqual("ReportGenerationFailed", failure["error"])
        self.assertFalse(os.path.exists(output))
        self.assertEqual([], self.mock.records_for("downloadReport"))
        self.assertEqual(1, len(self.mock.records_for("releaseToken")))


class TestProvenance(unittest.TestCase):
    def test_contract_and_sources_agree_on_the_specification_revision(self):
        contract = load_contract()
        sources = load_sources()
        spec = next(s for s in sources["sources"] if s["kind"] == "openapi-specification")

        self.assertEqual(contract["source"]["commitSha"], spec["commitSha"])
        self.assertEqual(contract["source"]["specPath"], spec["specPath"])
        self.assertEqual(contract["source"]["specSha256"], spec["fileSha256"])
        self.assertEqual(40, len(spec["commitSha"]))
        self.assertEqual(
            "specifications/vcf-operations/vcf-operations-openapi.json", spec["specPath"]
        )
        self.assertEqual(
            sorted(CONTRACT_OPS),
            sorted(entry["operationId"] for entry in spec["operationIds"]),
        )
        for entry in spec["operationIds"]:
            operation = contract["operations"][entry["operationId"]]
            self.assertEqual(operation["method"], entry["method"])
            self.assertEqual(operation["path"], entry["specPath"])

    def test_the_implementation_uses_only_the_standard_library(self):
        package = os.path.join(SRC, "vcfops_report")
        third_party = ("requests", "httpx", "urllib3", "aiohttp", "pydantic", "yaml", "httplib2")
        pattern = re.compile(
            r"^\s*(?:import|from)\s+(%s)\b" % "|".join(third_party), re.MULTILINE
        )
        for name in sorted(os.listdir(package)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(package, name), "r", encoding="utf-8") as handle:
                source = handle.read()
            found = pattern.search(source)
            self.assertIsNone(
                found,
                "%s must depend on the standard library only, found %r"
                % (name, found.group(0).strip() if found else ""),
            )


if __name__ == "__main__":
    unittest.main()
