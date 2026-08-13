"""Acceptance test for the vcfops_triage client.

Drives ``python -m vcfops_triage`` against the loopback mock in tools/, then reads
the mock's request log and asserts the exact shape of what went over the wire: the
operation sequence, the status sequence, which token signed which request, that a
401 is answered by re-acquiring and replaying *that one request* rather than
restarting the batch, and — the part that is easy to get wrong — that optional
fields and query parameters the caller did not supply are *absent* from the
request rather than present and empty.

No VMware endpoint is contacted. The mock binds 127.0.0.1 on an ephemeral port and
retires tokens by request count rather than by clock, so the test is deterministic.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import mock_ops_server  # noqa: E402

ACQUIRE = "acquireToken"
QUERY = "queryAlert"
NOTE = "addAlertNote"
RELEASE = "releaseToken"

USERNAME = mock_ops_server.VALID_USERNAME
PASSWORD = mock_ops_server.VALID_PASSWORD
AUTH_SOURCE = mock_ops_server.VALID_AUTH_SOURCE
TOKEN_1, TOKEN_2, TOKEN_3 = mock_ops_server.MOCK_TOKENS[:3]

CRITICALITY = ["CRITICAL", "IMMEDIATE"]
NOTE_TEXT = "Triaged by run book RB-114"

# The five active CRITICAL/IMMEDIATE alerts in the mock inventory, in server order.
MATCHING = [
    alert["alertId"]
    for alert in mock_ops_server.ALERTS
    if alert["alertLevel"] in CRITICALITY and alert["status"] != "CANCELED"
]

# The pinned 9.0.0.0 alertCriticality vocabulary.
CRITICALITY_ENUM = [
    "UNKNOWN",
    "NONE",
    "INFORMATION",
    "WARNING",
    "IMMEDIATE",
    "CRITICAL",
    "AUTO",
]

SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_TAG = "9.0.0.0"
SPEC_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_SHA256 = "e5c7bac42fcde67011f8f880144b1a5c690e737380f5275e694be29bb545dd0e"
NINE_ONE_SHA256 = "8179d8c128d9b3a81ed89d12ab958dc3c43eb664d4ca999e3b19d3e70efc14af"
OPERATION_IDS = {ACQUIRE, QUERY, NOTE, RELEASE}


def auth_value(token):
    return "OpsToken " + token


def run_cli(base_url, output, *extra, timeout=60):
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        os.path.join(REPO_ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    env["PYTHONUNBUFFERED"] = "1"
    argv = [
        sys.executable,
        "-m",
        "vcfops_triage",
        "--base-url",
        base_url,
        "--username",
        USERNAME,
        "--password",
        PASSWORD,
        "--criticality",
        ",".join(CRITICALITY),
        "--note",
        NOTE_TEXT,
        "--output",
        output,
    ]
    argv.extend(extra)
    return subprocess.run(
        argv,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


class TriageContractTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = os.path.join(self.tmp.name, "triage-report.json")

    # -- helpers ----------------------------------------------------------

    def diagnose(self, proc, mock):
        return "\nexit=%s\nstdout:\n%s\nstderr:\n%s\nrequest log:\n%s" % (
            proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"),
            "\n".join(json.dumps(e, sort_keys=True) for e in mock.requests()),
        )

    def sequence(self, mock):
        return [entry["operationId"] for entry in mock.requests()]

    def statuses(self, mock):
        return [entry["status"] for entry in mock.requests()]

    def report(self, info):
        self.assertTrue(os.path.exists(self.output), "no report written" + info)
        with open(self.output, encoding="utf-8") as handle:
            return json.load(handle)

    def assert_no_unknown_routes(self, mock):
        stray = [e for e in mock.requests() if e["operationId"] is None]
        self.assertEqual(
            stray, [], "requests were made to paths the contract does not name: %s" % stray
        )

    def assert_notes_are_exactly_once_each(self, mock, alert_ids, info):
        accepted = [e for e in mock.requests(NOTE) if e["status"] == 201]
        noted = [e["pathParams"]["id"] for e in accepted]
        self.assertEqual(
            noted,
            list(alert_ids),
            "every matched alert must be annotated exactly once, in server order; "
            "an alert appearing twice means work was repeated after the refresh"
            + info,
        )
        for entry in accepted:
            self.assertEqual(
                entry["body_keys"],
                ["content"],
                "alert-note-content carries only content; the alert id is a path "
                "parameter, not a body field" + info,
            )
            self.assertEqual(entry["body"]["content"], NOTE_TEXT, info)
            self.assertEqual(entry["query"], {}, "addAlertNote takes no query" + info)
            self.assertEqual(
                entry["headers"].get("content-type", "").split(";")[0],
                "application/json",
                info,
            )

    def assert_query_body(self, entry, expected_keys, info):
        self.assertEqual(
            entry["body_keys"],
            expected_keys,
            "alert-query must carry exactly the criteria that were supplied; an "
            "omitted criterion is absent, never null, \"\" or []" + info,
        )
        self.assertIs(entry["body"]["activeOnly"], True, info)
        self.assertEqual(entry["body"]["alertCriticality"], CRITICALITY, info)

    # -- 1. no expiry: the baseline wire shape ----------------------------

    def test_stable_run_walks_pages_and_omits_unsupplied_optionals(self):
        with mock_ops_server.RunningMock(scenario="stable") as mock:
            proc = run_cli(mock.base_url, self.output, "--page-size", "2")
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "expected exit 0" + info)
            self.assert_no_unknown_routes(mock)
            self.assertEqual(
                self.sequence(mock),
                [ACQUIRE, QUERY, NOTE, NOTE, QUERY, NOTE, NOTE, QUERY, NOTE, RELEASE],
                "a page must be annotated before the next page is fetched, and the "
                "short final page must end the loop" + info,
            )
            self.assertEqual(
                self.statuses(mock),
                [200, 200, 201, 201, 200, 201, 201, 200, 201, 200],
                "unexpected status sequence" + info,
            )

            # acquireToken: exactly the two required fields, and no Authorization
            # header, because the specification sets "security": [] on it.
            acquires = mock.requests(ACQUIRE)
            self.assertEqual(len(acquires), 1, "one token should have sufficed" + info)
            acquire = acquires[0]
            self.assertEqual(
                acquire["body_keys"],
                ["password", "username"],
                "acquireToken body must omit authSource entirely when it was not "
                "supplied, not send it as null or an empty string" + info,
            )
            self.assertEqual(acquire["body"]["username"], USERNAME, info)
            self.assertEqual(acquire["body"]["password"], PASSWORD, info)
            self.assertNotIn("authorization", acquire["headers"], info)
            self.assertEqual(
                acquire["headers"].get("content-type", "").split(";")[0],
                "application/json",
                info,
            )

            queries = mock.requests(QUERY)
            self.assertEqual(
                [q["query"] for q in queries],
                [
                    {"page": ["0"], "pageSize": ["2"]},
                    {"page": ["1"], "pageSize": ["2"]},
                    {"page": ["2"], "pageSize": ["2"]},
                ],
                "pages must be walked in order from 0" + info,
            )
            for query in queries:
                self.assert_query_body(query, ["activeOnly", "alertCriticality"], info)

            self.assert_notes_are_exactly_once_each(mock, MATCHING, info)

            # Everything after the token was acquired is signed with it.
            for entry in mock.requests()[1:]:
                self.assertEqual(
                    entry["headers"].get("authorization"), auth_value(TOKEN_1), info
                )

            release = mock.requests(RELEASE)
            self.assertEqual(len(release), 1, info)
            self.assertEqual(
                release[0]["body_raw"], None, "releaseToken takes no body" + info
            )

            report = self.report(info)
            self.assertEqual(report["alertsMatched"], 5, info)
            self.assertEqual(report["pagesFetched"], 3, info)
            self.assertEqual(report["tokenRefreshes"], 0, info)
            self.assertEqual(
                [item["alertId"] for item in report["annotated"]], MATCHING, info
            )
            self.assertEqual(
                [item["noteId"] for item in report["annotated"]],
                [mock_ops_server.NOTE_IDS[a] for a in MATCHING],
                "the report must carry the note ids the server actually created" + info,
            )

    # -- 2. the token dies before a page fetch ----------------------------

    def test_expiry_before_a_page_replays_only_that_page(self):
        with mock_ops_server.RunningMock(scenario="expire_before_page") as mock:
            proc = run_cli(mock.base_url, self.output, "--page-size", "2")
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "the run must survive the 401" + info)
            self.assert_no_unknown_routes(mock)
            self.assertEqual(
                self.sequence(mock),
                [
                    ACQUIRE, QUERY, NOTE, NOTE,
                    QUERY,            # 401: the token is finished
                    ACQUIRE,          # refresh
                    QUERY,            # replay of the rejected page, nothing else
                    NOTE, NOTE, QUERY, NOTE, RELEASE,
                ],
                "on a 401 the client must re-acquire and replay just the rejected "
                "request, not restart the batch" + info,
            )
            self.assertEqual(
                self.statuses(mock),
                [200, 200, 201, 201, 401, 200, 200, 201, 201, 200, 201, 200],
                "unexpected status sequence" + info,
            )

            entries = mock.requests()
            rejected, refresh, replay = entries[4], entries[5], entries[6]

            self.assertEqual(
                (rejected["path"], rejected["query"], rejected["body"]),
                (replay["path"], replay["query"], replay["body"]),
                "the replay must be the same request, not a re-derived one" + info,
            )
            self.assertEqual(
                rejected["headers"].get("authorization"), auth_value(TOKEN_1), info
            )
            self.assertEqual(
                replay["headers"].get("authorization"),
                auth_value(TOKEN_2),
                "the replay must be signed with the new token" + info,
            )
            self.assertNotIn(
                "authorization",
                refresh["headers"],
                "the refresh is an acquireToken call and carries no Authorization"
                + info,
            )
            self.assertEqual(refresh["body_keys"], ["password", "username"], info)

            # Page 1 was requested twice and only because it was rejected the first
            # time; pages 0 and 2 were fetched once each.
            self.assertEqual(
                [q["query"]["page"][0] for q in mock.requests(QUERY)],
                ["0", "1", "1", "2"],
                "no page may be re-fetched except the one the 401 killed" + info,
            )
            self.assertEqual(
                [q["query"]["page"][0] for q in mock.requests(QUERY, status=200)],
                ["0", "1", "2"],
                info,
            )

            self.assert_notes_are_exactly_once_each(mock, MATCHING, info)
            self.assertEqual(
                mock.requests(NOTE, status=401), [], "no note was rejected here" + info
            )
            self.assertEqual(
                mock.requests(RELEASE)[0]["headers"].get("authorization"),
                auth_value(TOKEN_2),
                "release the token you are actually holding" + info,
            )

            report = self.report(info)
            self.assertEqual(report["tokenRefreshes"], 1, info)
            self.assertEqual(report["pagesFetched"], 3, info)
            self.assertEqual(
                [item["alertId"] for item in report["annotated"]], MATCHING, info
            )

    # -- 3. the token dies part way through a page's notes -----------------

    def test_expiry_between_notes_does_not_repeat_accepted_work(self):
        with mock_ops_server.RunningMock(scenario="expire_before_note") as mock:
            proc = run_cli(mock.base_url, self.output, "--page-size", "2")
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "the run must survive the 401" + info)
            self.assert_no_unknown_routes(mock)
            self.assertEqual(
                self.sequence(mock),
                [
                    ACQUIRE, QUERY, NOTE,
                    NOTE,             # 401 on the second alert of page 0
                    ACQUIRE,          # refresh
                    NOTE,             # replay of that one note
                    QUERY, NOTE, NOTE, QUERY, NOTE, RELEASE,
                ],
                "the refresh must resume at the note that failed, not at the top of "
                "the page" + info,
            )
            self.assertEqual(
                self.statuses(mock),
                [200, 200, 201, 401, 200, 201, 200, 201, 201, 200, 201, 200],
                "unexpected status sequence" + info,
            )

            entries = mock.requests()
            rejected, replay = entries[3], entries[5]
            self.assertEqual(
                rejected["pathParams"]["id"],
                MATCHING[1],
                "the 401 lands on the second matched alert" + info,
            )
            self.assertEqual(
                (rejected["path"], rejected["body"]),
                (replay["path"], replay["body"]),
                "the replay must target the same alert with the same note" + info,
            )
            self.assertEqual(
                replay["headers"].get("authorization"), auth_value(TOKEN_2), info
            )

            # The first alert's note was accepted before the token died and must not
            # be sent again.
            self.assertEqual(
                [e["pathParams"]["id"] for e in mock.requests(NOTE)],
                [MATCHING[0], MATCHING[1], MATCHING[1], MATCHING[2], MATCHING[3], MATCHING[4]],
                "only the rejected note may be re-sent" + info,
            )
            self.assert_notes_are_exactly_once_each(mock, MATCHING, info)

            # Page 0 was fetched once: the client kept the alerts it already had.
            self.assertEqual(
                [q["query"]["page"][0] for q in mock.requests(QUERY)],
                ["0", "1", "2"],
                "a page already in hand must not be re-fetched after a refresh" + info,
            )

            report = self.report(info)
            self.assertEqual(report["tokenRefreshes"], 1, info)
            self.assertEqual(
                [item["alertId"] for item in report["annotated"]], MATCHING, info
            )

    def test_expiry_on_release_refreshes_and_replays_only_release(self):
        with mock_ops_server.RunningMock(scenario="expire_before_release") as mock:
            proc = run_cli(mock.base_url, self.output, "--page-size", "2")
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "the run must survive the 401" + info)
            self.assert_no_unknown_routes(mock)
            self.assertEqual(
                self.sequence(mock),
                [
                    ACQUIRE, QUERY, NOTE, NOTE, QUERY, NOTE, NOTE, QUERY, NOTE,
                    RELEASE,          # 401: the token expired at cleanup
                    ACQUIRE,          # refresh
                    RELEASE,          # replay only the rejected release
                ],
                "the contract's 401 rule covers every authenticated operation, "
                "including releaseToken" + info,
            )
            self.assertEqual(
                self.statuses(mock),
                [200, 200, 201, 201, 200, 201, 201, 200, 201, 401, 200, 200],
                info,
            )
            releases = mock.requests(RELEASE)
            self.assertEqual(
                [entry["headers"].get("authorization") for entry in releases],
                [auth_value(TOKEN_1), auth_value(TOKEN_2)],
                "the replay must use the newly acquired token" + info,
            )
            self.assertEqual(
                [entry["body_raw"] for entry in releases], [None, None], info
            )
            self.assert_notes_are_exactly_once_each(mock, MATCHING, info)
            report = self.report(info)
            self.assertEqual(report["tokenRefreshes"], 1, info)
            self.assertEqual(
                [item["alertId"] for item in report["annotated"]], MATCHING, info
            )

    # -- 4. the refresh budget is finite ----------------------------------

    def test_refresh_budget_is_bounded_and_no_token_is_released(self):
        with mock_ops_server.RunningMock(scenario="always_expired") as mock:
            # A short timeout: an unbounded refresh loop must not be allowed to
            # sit here spinning against the mock for a minute.
            proc = run_cli(
                mock.base_url,
                self.output,
                "--page-size",
                "2",
                "--max-refreshes",
                "2",
                timeout=25,
            )
            info = self.diagnose(proc, mock)

            self.assertEqual(
                proc.returncode, 5, "expected exit 5 once refreshes run out" + info
            )
            self.assertEqual(
                self.sequence(mock),
                [ACQUIRE, QUERY, ACQUIRE, QUERY, ACQUIRE, QUERY],
                "--max-refreshes must bound the refresh loop" + info,
            )
            self.assertEqual(
                self.statuses(mock), [200, 401, 200, 401, 200, 401], info
            )
            self.assertEqual(
                [e["headers"].get("authorization") for e in mock.requests(QUERY)],
                [auth_value(TOKEN_1), auth_value(TOKEN_2), auth_value(TOKEN_3)],
                "each replay must use the token that was just acquired" + info,
            )
            self.assertEqual(
                mock.requests(RELEASE),
                [],
                "there is no live token to release on this path" + info,
            )
            self.assertEqual(mock.requests(NOTE), [], "nothing was annotated" + info)
            self.assertFalse(os.path.exists(self.output), "no report expected" + info)

    # -- 5. optional query parameter and optional criteria -----------------

    def test_pagesize_is_omitted_when_not_supplied(self):
        with mock_ops_server.RunningMock(scenario="stable") as mock:
            proc = run_cli(mock.base_url, self.output)
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, info)
            queries = mock.requests(QUERY)
            self.assertEqual(
                len(queries),
                1,
                "the server's default pageSize of 1000 holds all five alerts, so one "
                "short page ends the loop" + info,
            )
            self.assertEqual(
                queries[0]["query"],
                {"page": ["0"]},
                "pageSize is optional with a documented default: leave it off the URL "
                "rather than sending it empty" + info,
            )
            self.assert_notes_are_exactly_once_each(mock, MATCHING, info)
            self.assertEqual(self.report(info)["pagesFetched"], 1, info)

    def test_supplied_optionals_are_sent(self):
        with mock_ops_server.RunningMock(scenario="stable") as mock:
            proc = run_cli(
                mock.base_url,
                self.output,
                "--auth-source",
                AUTH_SOURCE,
                "--alert-name",
                "contention",
            )
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, info)
            acquire = mock.requests(ACQUIRE)[0]
            self.assertEqual(
                acquire["body_keys"], ["authSource", "password", "username"], info
            )
            self.assertEqual(acquire["body"]["authSource"], AUTH_SOURCE, info)

            query = mock.requests(QUERY)[0]
            self.assertEqual(
                query["body_keys"],
                ["activeOnly", "alertCriticality", "alertName"],
                info,
            )
            self.assertEqual(query["body"]["alertName"], "contention", info)

            expected = [
                alert["alertId"]
                for alert in mock_ops_server.ALERTS
                if alert["alertId"] in MATCHING
                and "contention" in alert["alertDefinitionName"].lower()
            ]
            self.assertEqual(len(expected), 3, "fixture sanity" + info)
            self.assert_notes_are_exactly_once_each(mock, expected, info)
            self.assertEqual(self.report(info)["alertsMatched"], 3, info)

    # -- 6. validation happens before the first connection -----------------

    def test_unknown_criticality_is_rejected_before_any_request(self):
        for value in ("SEVERE", "critical", "CRITICAL,MAJOR"):
            with self.subTest(criticality=value):
                with mock_ops_server.RunningMock(scenario="stable") as mock:
                    proc = run_cli_with(mock.base_url, self.output, criticality=value)
                    info = self.diagnose(proc, mock)
                    self.assertEqual(
                        proc.returncode,
                        2,
                        "%r is not in the pinned 9.0.0.0 alertCriticality enum%s"
                        % (value, info),
                    )
                    self.assertEqual(
                        mock.requests(),
                        [],
                        "validate against the contract before opening a connection"
                        + info,
                    )

    def test_page_size_below_the_contract_minimum_is_rejected(self):
        with mock_ops_server.RunningMock(scenario="stable") as mock:
            proc = run_cli(mock.base_url, self.output, "--page-size", "0")
            info = self.diagnose(proc, mock)
            self.assertEqual(proc.returncode, 2, info)
            self.assertEqual(mock.requests(), [], info)

    # -- 7. authentication -------------------------------------------------

    def test_bad_credentials_stop_at_the_first_call(self):
        with mock_ops_server.RunningMock(scenario="stable") as mock:
            proc = run_cli_with(mock.base_url, self.output, password="wrong-password")
            info = self.diagnose(proc, mock)
            self.assertEqual(proc.returncode, 5, "expected exit 5 on a 401" + info)
            self.assertEqual(
                self.sequence(mock),
                [ACQUIRE],
                "a rejected credential is not a stale token: do not retry it" + info,
            )


class ContractProvenanceTest(unittest.TestCase):
    """The pinned contract must stay pinned to the 9.0.0.0 specification."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "docs", "contract.json"), encoding="utf-8") as fh:
            self.contract = json.load(fh)
        with open(
            os.path.join(REPO_ROOT, "docs", "official_sources.json"), encoding="utf-8"
        ) as fh:
            self.sources = json.load(fh)

    def test_sources_agree_with_contract(self):
        spec = self.sources["specification"]
        self.assertEqual(spec["tag"], SPEC_TAG)
        self.assertEqual(spec["path"], SPEC_PATH)
        self.assertEqual(spec["commit_sha"], SPEC_SHA)
        self.assertEqual(spec["sha256"], SPEC_SHA256)
        self.assertRegex(spec["commit_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(spec["license"], "Apache-2.0")

        source = self.contract["source"]
        self.assertEqual(source["tag"], spec["tag"])
        self.assertEqual(source["path"], spec["path"])
        self.assertEqual(source["commit_sha"], spec["commit_sha"])
        self.assertEqual(source["spec_sha256"], spec["sha256"])

    def test_contract_is_not_the_9_1_revision_of_the_same_file(self):
        excluded = self.sources["not_derived_from"]
        self.assertEqual(excluded["tag"], "9.1.0.0")
        self.assertEqual(excluded["path"], SPEC_PATH)
        self.assertEqual(excluded["sha256"], NINE_ONE_SHA256)
        self.assertNotEqual(self.contract["source"]["spec_sha256"], NINE_ONE_SHA256)

    def test_every_contract_operation_is_attributed(self):
        attributed = {entry["operationId"] for entry in self.sources["operationIds"]}
        self.assertEqual(set(self.contract["operations"]), attributed)
        self.assertEqual(attributed, OPERATION_IDS)
        for entry in self.sources["operationIds"]:
            operation = self.contract["operations"][entry["operationId"]]
            self.assertEqual(operation["method"], entry["method"])
            self.assertEqual(operation["path"], entry["requestPath"])
            self.assertEqual(entry["path"], SPEC_PATH)
            self.assertEqual(entry["commit_sha"], SPEC_SHA)

    def test_criticality_enum_is_the_spec_one(self):
        self.assertEqual(
            self.contract["enums"]["alert-query.alertCriticality[]"], CRITICALITY_ENUM
        )
        query = self.contract["operations"][QUERY]["requestBody"]
        self.assertEqual(
            query["properties"]["alertCriticality"]["items"]["enum"], CRITICALITY_ENUM
        )
        self.assertEqual(
            query["required"], [], "alert-query declares no required properties"
        )

    def test_paging_parameters_carry_the_spec_defaults(self):
        params = {
            item["name"]: item
            for item in self.contract["operations"][QUERY]["queryParameters"]
        }
        self.assertEqual(sorted(params), ["page", "pageSize"])
        self.assertEqual(params["page"]["default"], 0)
        self.assertEqual(params["pageSize"]["default"], 1000)
        self.assertFalse(params["page"]["required"])
        self.assertFalse(params["pageSize"]["required"])
        self.assertEqual(self.contract["paging"]["minimumPageSize"], 1)

    def test_only_acquire_token_is_unauthenticated(self):
        operations = self.contract["operations"]
        self.assertFalse(operations[ACQUIRE]["authenticated"])
        for name in (QUERY, NOTE, RELEASE):
            self.assertTrue(operations[name]["authenticated"], name)
        self.assertEqual(self.contract["security"]["headerName"], "Authorization")
        self.assertEqual(
            self.contract["security"]["schemeName"], "Token-based-authorization"
        )


class ImplementationConstraintTest(unittest.TestCase):
    """The deliverable must use only Python's standard library."""

    def test_package_has_no_third_party_imports(self):
        package = os.path.join(REPO_ROOT, "src", "vcfops_triage")
        imported = set()
        for name in os.listdir(package):
            if not name.endswith(".py"):
                continue
            path = os.path.join(package, name)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])

        allowed = set(sys.stdlib_module_names) | {"vcfops_triage"}
        self.assertEqual(
            sorted(imported - allowed),
            [],
            "vcfops_triage must not import third-party packages",
        )


class MockPinningTest(unittest.TestCase):
    """The mock may serve only what the contract names."""

    def test_unlisted_paths_are_not_served(self):
        with mock_ops_server.RunningMock(scenario="stable") as mock:
            import urllib.error
            import urllib.request

            for path in (
                "/suite-api/api/alerts",
                "/suite-api/api/resources",
                "/suite-api/api/auth/currentuser",
            ):
                request = urllib.request.Request(mock.base_url + path, method="GET")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=10)
                self.assertEqual(caught.exception.code, 404, path)


def run_cli_with(base_url, output, criticality=None, password=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        os.path.join(REPO_ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    return subprocess.run(
        [
            sys.executable, "-m", "vcfops_triage",
            "--base-url", base_url,
            "--username", USERNAME,
            "--password", password or PASSWORD,
            "--criticality", criticality or ",".join(CRITICALITY),
            "--note", NOTE_TEXT,
            "--output", output,
            "--page-size", "2",
        ],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
