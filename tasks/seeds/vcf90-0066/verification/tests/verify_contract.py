"""Acceptance test for the vcfops_export client.

Drives ``python -m vcfops_export`` against the loopback mock in tools/, then reads
the mock's request log and asserts the exact shape of what went over the wire:
the operation sequence, the ordering of the download relative to the poll that
observed a terminal state, the headers, and — the part that is easy to get wrong —
that optional fields the caller did not supply are *absent* from the request
rather than present and empty.

No VMware endpoint is contacted. The mock binds 127.0.0.1 on an ephemeral port and
advances its operation state by poll count, so the test is deterministic.
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

POLL = "getLastExportOperation"
USERNAME = mock_ops_server.VALID_USERNAME
PASSWORD = mock_ops_server.VALID_PASSWORD
AUTH_SOURCE = mock_ops_server.VALID_AUTH_SOURCE
PAYLOAD = mock_ops_server.EXPORT_PAYLOAD
EXPECTED_AUTH = "OpsToken " + mock_ops_server.MOCK_TOKEN

CONTENT_TYPES = ["POLICIES", "DASHBOARDS", "ALERT_DEFINITIONS"]

# Added by the 9.1.0.0 revision of the same specification file; a contract derived
# from 9.0.0.0 must not accept them.
NINE_ONE_ONLY = ["LI_EXTRACTED_FIELDS", "LI_AGENT_GROUPS", "LI_TEMPLATES", "CONFIG_TEMPLATES"]


def run_cli(base_url, output, *extra, timeout=60):
    env = dict(os.environ)
    src = os.path.join(REPO_ROOT, "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    argv = [
        sys.executable,
        "-m",
        "vcfops_export",
        "--base-url",
        base_url,
        "--username",
        USERNAME,
        "--password",
        PASSWORD,
        "--scope",
        "CUSTOM",
        "--content-types",
        ",".join(CONTENT_TYPES),
        "--output",
        output,
        "--poll-interval",
        "0.01",
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


class ExportContractTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = os.path.join(self.tmp.name, "export.zip")

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

    def only(self, mock, operation_id):
        matches = mock.requests(operation_id)
        self.assertEqual(
            len(matches), 1, "expected exactly one %s request, got %d" % (operation_id, len(matches))
        )
        return matches[0]

    def assert_no_unknown_routes(self, mock):
        stray = [e for e in mock.requests() if e["operationId"] is None]
        self.assertEqual(
            stray, [], "requests were made to paths the contract does not name: %s" % stray
        )

    # -- 1. happy path, no optional inputs --------------------------------

    def test_success_omits_every_unsupplied_optional_field(self):
        with mock_ops_server.RunningMock(scenario="success") as mock:
            proc = run_cli(mock.base_url, self.output)
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "expected exit 0" + info)
            self.assertTrue(os.path.exists(self.output), "no archive written" + info)
            with open(self.output, "rb") as handle:
                self.assertEqual(handle.read(), PAYLOAD, "archive contents differ" + info)

            self.assert_no_unknown_routes(mock)
            self.assertEqual(
                self.sequence(mock),
                ["acquireToken", "exportContent"] + [POLL] * 4 + ["download", "releaseToken"],
                "unexpected operation sequence" + info,
            )

            statuses = [(e["operationId"], e["status"]) for e in mock.requests()]
            self.assertEqual(
                [s for _, s in statuses],
                [200, 202, 200, 200, 200, 200, 200, 200],
                "unexpected status sequence %s%s" % (statuses, info),
            )

            # acquireToken: exactly the two required fields, and no Authorization
            # header, because the specification sets "security": [] on it.
            acquire = self.only(mock, "acquireToken")
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

            # exportContent: exactly the two required fields, no EncryptionPassword.
            export = self.only(mock, "exportContent")
            self.assertEqual(
                export["body_keys"],
                ["contentTypes", "scope"],
                "content-export body must carry only the required fields here" + info,
            )
            self.assertEqual(export["body"]["scope"], "CUSTOM", info)
            self.assertEqual(export["body"]["contentTypes"], CONTENT_TYPES, info)
            self.assertNotIn(
                "encryptionpassword",
                export["headers"],
                "EncryptionPassword is an optional header parameter: omit it when "
                "no password was supplied rather than sending it empty" + info,
            )
            self.assertEqual(export["headers"].get("authorization"), EXPECTED_AUTH, info)
            self.assertEqual(
                export["headers"].get("content-type", "").split(";")[0],
                "application/json",
                info,
            )
            self.assertEqual(export["query"], {}, info)

            # The download must follow the poll that reported FINISHED, and the
            # client must not have shortcut straight from the 202.
            polls = mock.requests(POLL)
            download = self.only(mock, "download")
            self.assertGreaterEqual(len(polls), 2, "the 202 must be polled, not assumed" + info)
            self.assertGreater(
                download["seq"], polls[-1]["seq"], "download preceded the final poll" + info
            )
            for poll in polls:
                self.assertEqual(poll["body_raw"], None, "poll must not carry a body" + info)
                self.assertEqual(poll["headers"].get("authorization"), EXPECTED_AUTH, info)

    # -- 2. happy path, both optional inputs supplied ----------------------

    def test_success_sends_optional_fields_when_supplied(self):
        with mock_ops_server.RunningMock(scenario="success") as mock:
            proc = run_cli(
                mock.base_url,
                self.output,
                "--auth-source",
                AUTH_SOURCE,
                "--encryption-password",
                "s3cret-key",
            )
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "expected exit 0" + info)
            self.assert_no_unknown_routes(mock)
            self.assertEqual(
                self.sequence(mock),
                ["acquireToken", "exportContent"] + [POLL] * 4 + ["download", "releaseToken"],
                info,
            )

            acquire = self.only(mock, "acquireToken")
            self.assertEqual(
                acquire["body_keys"], ["authSource", "password", "username"], info
            )
            self.assertEqual(acquire["body"]["authSource"], AUTH_SOURCE, info)

            export = self.only(mock, "exportContent")
            self.assertEqual(
                export["headers"].get("encryptionpassword"),
                "s3cret-key",
                "EncryptionPassword must be sent as a header, not in the body" + info,
            )
            self.assertEqual(
                export["body_keys"],
                ["contentTypes", "scope"],
                "the encryption password does not belong in the content-export body" + info,
            )

    def test_every_pinned_enum_value_is_accepted(self):
        with open(os.path.join(REPO_ROOT, "docs", "contract.json"), encoding="utf-8") as fh:
            contract = json.load(fh)
        all_content_types = contract["enums"]["content-export.contentTypes[]"]

        with mock_ops_server.RunningMock(scenario="immediate") as mock:
            proc = run_cli(
                mock.base_url,
                self.output,
                "--scope",
                "ALL",
                "--content-types",
                ",".join(all_content_types),
            )
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 0, "every pinned enum value must be accepted" + info)
            export = self.only(mock, "exportContent")
            self.assertEqual(export["body"]["scope"], "ALL", info)
            self.assertEqual(export["body"]["contentTypes"], all_content_types, info)
            self.assertEqual(
                self.sequence(mock),
                ["acquireToken", "exportContent", POLL, "download", "releaseToken"],
                "even an immediately finished export must be polled before download" + info,
            )

    # -- 3. terminal failure state ----------------------------------------

    def test_failed_export_is_not_downloaded(self):
        with mock_ops_server.RunningMock(scenario="failed") as mock:
            proc = run_cli(mock.base_url, self.output)
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 3, "expected exit 3 for a FAILED export" + info)
            self.assertEqual(
                mock.requests("download"),
                [],
                "download must not be attempted after a FAILED state" + info,
            )
            self.assertFalse(os.path.exists(self.output), "no archive should be written" + info)
            self.assertEqual(
                self.sequence(mock),
                ["acquireToken", "exportContent"] + [POLL] * 3 + ["releaseToken"],
                "the client must poll to FAILED and then release its token" + info,
            )
            self.assertIn(
                "Content export failed on node vcfops-01",
                proc.stderr.decode("utf-8", "replace"),
                "report the operation's errorMessages on stderr" + info,
            )

    # -- 4. never reaches a terminal state --------------------------------

    def test_poll_budget_is_respected(self):
        with mock_ops_server.RunningMock(scenario="stuck") as mock:
            proc = run_cli(mock.base_url, self.output, "--max-polls", "5")
            info = self.diagnose(proc, mock)

            self.assertEqual(proc.returncode, 4, "expected exit 4 when polls run out" + info)
            self.assertEqual(
                len(mock.requests(POLL)), 5, "--max-polls must bound the poll loop" + info
            )
            self.assertEqual(
                mock.requests("download"),
                [],
                "download must not be attempted without a terminal state" + info,
            )
            self.assertFalse(os.path.exists(self.output), info)
            self.assertEqual(
                self.sequence(mock)[-1],
                "releaseToken",
                "the token must be released after poll exhaustion" + info,
            )
            self.assertIn(
                "RUNNING",
                proc.stderr.decode("utf-8", "replace"),
                "poll-exhaustion diagnostics must include the last operation state" + info,
            )

    # -- 5. the contract is pinned to 9.0.0.0 -----------------------------

    def test_nine_one_content_types_are_rejected_before_any_request(self):
        for value in NINE_ONE_ONLY:
            with self.subTest(contentType=value):
                with mock_ops_server.RunningMock(scenario="success") as mock:
                    proc = subprocess_with_content_types(mock.base_url, self.output, value)
                    info = self.diagnose(proc, mock)
                    self.assertEqual(
                        proc.returncode,
                        2,
                        "%s exists only in the 9.1 revision of this specification and "
                        "must be rejected against the pinned 9.0.0.0 contract%s"
                        % (value, info),
                    )
                    self.assertEqual(
                        mock.requests(),
                        [],
                        "validate against the contract before opening a connection" + info,
                    )

    def test_unknown_or_empty_content_types_are_rejected_before_any_request(self):
        for value in ("NOT_A_CONTENT_TYPE", "POLICIES,,DASHBOARDS", ""):
            with self.subTest(contentTypes=value):
                with mock_ops_server.RunningMock(scenario="success") as mock:
                    proc = subprocess_with_content_types(mock.base_url, self.output, value)
                    info = self.diagnose(proc, mock)
                    self.assertEqual(proc.returncode, 2, info)
                    self.assertEqual(
                        mock.requests(),
                        [],
                        "all comma-separated values must be validated before connecting" + info,
                    )

    def test_unknown_scope_is_rejected_before_any_request(self):
        with mock_ops_server.RunningMock(scenario="success") as mock:
            proc = run_cli(mock.base_url, self.output, "--scope", "EVERYTHING")
            info = self.diagnose(proc, mock)
            self.assertEqual(proc.returncode, 2, info)
            self.assertEqual(mock.requests(), [], info)

    # -- 6. authentication ------------------------------------------------

    def test_bad_credentials_stop_before_export(self):
        with mock_ops_server.RunningMock(scenario="success") as mock:
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.join(REPO_ROOT, "src") + os.pathsep + env.get(
                "PYTHONPATH", ""
            )
            proc = subprocess.run(
                [
                    sys.executable, "-m", "vcfops_export",
                    "--base-url", mock.base_url,
                    "--username", USERNAME,
                    "--password", "wrong-password",
                    "--scope", "CUSTOM",
                    "--content-types", "POLICIES",
                    "--output", self.output,
                    "--poll-interval", "0.01",
                ],
                cwd=REPO_ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
            )
            info = self.diagnose(proc, mock)
            self.assertEqual(proc.returncode, 5, "expected exit 5 on a 401" + info)
            self.assertEqual(self.sequence(mock), ["acquireToken"], info)


class ContractProvenanceTest(unittest.TestCase):
    """The pinned contract must stay pinned."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "docs", "contract.json"), encoding="utf-8") as fh:
            self.contract = json.load(fh)
        with open(
            os.path.join(REPO_ROOT, "docs", "official_sources.json"), encoding="utf-8"
        ) as fh:
            self.sources = json.load(fh)

    def test_sources_agree_with_contract(self):
        spec = self.sources["specification"]
        self.assertEqual(spec["tag"], "9.0.0.0")
        self.assertEqual(spec["path"], "specifications/vcf-operations/vcf-operations-openapi.json")
        self.assertEqual(self.contract["source"]["tag"], spec["tag"])
        self.assertEqual(self.contract["source"]["commit_sha"], spec["commit_sha"])
        self.assertRegex(spec["commit_sha"], r"^[0-9a-f]{40}$")

    def test_every_contract_operation_is_attributed(self):
        attributed = {entry["operationId"] for entry in self.sources["operationIds"]}
        self.assertEqual(set(self.contract["operations"]), attributed)
        self.assertEqual(
            attributed,
            {"acquireToken", "releaseToken", "exportContent", "getLastExportOperation", "download"},
        )
        for entry in self.sources["operationIds"]:
            operation = self.contract["operations"][entry["operationId"]]
            self.assertEqual(operation["method"], entry["method"])
            self.assertEqual(operation["path"], entry["requestPath"])

    def test_content_type_enum_is_the_9_0_one(self):
        enum = self.contract["enums"]["content-export.contentTypes[]"]
        self.assertEqual(len(enum), 30, "the 9.0.0.0 enum has 30 members")
        for value in NINE_ONE_ONLY:
            self.assertNotIn(value, enum, "%s belongs to the 9.1 revision" % value)

    def test_polling_states_come_from_the_operation_state_enum(self):
        states = set(self.contract["enums"]["operation-details.state"])
        self.assertEqual(
            states,
            {"NOT_INITIALIZED", "INITIALIZED", "RUNNING", "FAILED", "FINISHED", "UNKNOWN"},
        )
        polling = self.contract["polling"]
        self.assertEqual(polling["terminal"], ["FINISHED", "FAILED"])
        self.assertTrue(set(polling["terminal"]).issubset(states))
        self.assertTrue(set(polling["nonTerminal"]).issubset(states))

    def test_implementation_uses_only_the_standard_library(self):
        package_root = os.path.join(REPO_ROOT, "src", "vcfops_export")
        third_party = []
        for filename in sorted(os.listdir(package_root)):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(package_root, filename)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    root = name.split(".", 1)[0]
                    if root not in sys.stdlib_module_names and root != "vcfops_export":
                        third_party.append((filename, name))
        self.assertEqual(third_party, [], "third-party imports are not allowed")


def subprocess_with_content_types(base_url, output, content_types):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(REPO_ROOT, "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable, "-m", "vcfops_export",
            "--base-url", base_url,
            "--username", USERNAME,
            "--password", PASSWORD,
            "--scope", "CUSTOM",
            "--content-types", content_types,
            "--output", output,
            "--poll-interval", "0.01",
        ],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
