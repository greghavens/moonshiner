"""Protected verification for the SDDC LCM support-bundle client.

Two things are checked:

1. ``docs/contract.json`` and ``docs/official_sources.json`` match the published
   specification ``specifications/sddc-lcm/sddc-lcm-openapi.yaml`` in
   ``vmware/vcf-api-specs`` at the pinned commit.
2. The client, driven against a loopback mock pinned to that contract, puts the
   exact expected bytes on the wire -- including omitting optional fields that
   the caller left unset, rather than sending them empty.

No VMware endpoint is contacted. Every request goes to 127.0.0.1.

PROTECTED: do not modify.
"""

import ast
import copy
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
CONTRACT_PATH = os.path.join(DOCS_DIR, "contract.json")
SOURCES_PATH = os.path.join(DOCS_DIR, "official_sources.json")
MOCK_PATH = os.path.join(HERE, "lcm_mock_server.py")

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# --- values pinned to the published specification ---------------------------

REPO_URL = "https://github.com/vmware/vcf-api-specs"
SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"

# The solver-facing task pins this repository revision. The content digests
# below independently pin the bytes retrieved from it.
SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_BLOB_SHA = "fa97e0975ac108c81173b5bdd4fde57f20b2e190"
SPEC_SHA256 = "158cab89bc56e1bb80b662a859499efc6ee57c1d35503d4d1f855809c213436c"

OPENAPI_VERSION = "3.0.4"
INFO_VERSION = "9.1.0.0"
SERVER_URL = "https://vcf.broadcom.com/sddc-lcm"

OP_GENERATE = "generateComponentSupportBundle"
OP_GET_TASK = "getTask"
OP_LIST_BUNDLES = "getComponentSupportBundles"
OPERATION_IDS = (OP_GENERATE, OP_GET_TASK, OP_LIST_BUNDLES)

PATH_BUNDLES = "/v1/components/{componentId}/support-bundles"
PATH_TASK = "/v1/tasks/{taskId}"

TASK_STATUS_ENUM = [
    "PENDING",
    "SCHEDULED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELED",
]
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED"}

# --- fixture identifiers served by the loopback mock ------------------------

COMPONENT_OK = "6f9a2c14-3b7d-4e58-9a10-2d5e8c7b4f31"
COMPONENT_FAIL = "0c3e7d91-8a4b-42f6-b5c8-1e9d6a0f2b73"
COMPONENT_STALLED = "4a8b1e60-9d27-4c3f-a6b5-7e2f0c9d8a14"

TASK_OK = "b1d4f8a2-5c6e-4712-8f3a-9e0d1c2b3a45"
TASK_FAIL = "7e5c3a90-1f8d-4b26-9c47-0a3b5d8e6f12"
TASK_STALLED = "c2f7b481-6a30-4d95-8e1b-5f4c9a2d7e03"

BUNDLE_OK = "sb-2f4c9e17"
UNKNOWN_TASK = "00000000-0000-4000-8000-000000000000"


def load_json(path, label):
    if not os.path.isfile(path):
        raise AssertionError("%s is missing (expected at %s)" % (label, path))
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise AssertionError("%s is not valid JSON: %s" % (label, exc))


class ContractDerivationTest(unittest.TestCase):
    """docs/contract.json must be derived from the published specification."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_json(CONTRACT_PATH, "docs/contract.json")

    def operation(self, operation_id):
        operations = self.contract.get("operations")
        self.assertIsInstance(operations, dict, "contract.operations must be an object")
        self.assertIn(operation_id, operations, "contract omits %s" % operation_id)
        entry = operations[operation_id]
        self.assertIsInstance(entry, dict, "%s must be an object" % operation_id)
        return entry

    def test_source_block_identifies_the_specification(self):
        source = self.contract.get("source")
        self.assertIsInstance(source, dict, "contract.source must be an object")
        self.assertEqual(source.get("spec_path"), SPEC_PATH)
        self.assertEqual(source.get("commit"), SPEC_COMMIT)
        self.assertEqual(source.get("openapi"), OPENAPI_VERSION)
        self.assertEqual(source.get("info_version"), INFO_VERSION)
        self.assertEqual(source.get("server_url"), SERVER_URL)
        repository = (source.get("repository") or "").rstrip("/")
        self.assertTrue(repository.startswith("https://"))
        self.assertEqual(repository.removesuffix(".git"), REPO_URL)

    def test_contract_uses_exactly_the_documented_shape(self):
        self.assertEqual(
            set(self.contract), {"source", "security", "operations", "task_status"}
        )
        self.assertEqual(
            set(self.contract["source"]),
            {"repository", "commit", "spec_path", "openapi", "info_version", "server_url"},
        )
        self.assertEqual(
            set(self.contract["security"]),
            {"scheme_name", "type", "scheme", "bearer_format"},
        )
        operation_keys = {
            "method",
            "path",
            "success_status",
            "response_schema",
            "response_is_array",
            "path_parameters",
            "header_parameters",
            "request_body",
        }
        for operation_id, entry in self.contract["operations"].items():
            self.assertEqual(set(entry), operation_keys, operation_id)
            for parameter in entry["path_parameters"]:
                self.assertEqual(
                    set(parameter), {"name", "required", "type", "format"}, operation_id
                )
            for parameter in entry["header_parameters"]:
                self.assertEqual(
                    set(parameter), {"name", "required", "type"}, operation_id
                )
            body = entry["request_body"]
            if body is not None:
                self.assertEqual(
                    set(body),
                    {"schema", "content_type", "required_properties", "optional_properties"},
                    operation_id,
                )
                for prop in body["optional_properties"]:
                    self.assertEqual(set(prop), {"name", "type"}, operation_id)
        self.assertEqual(
            set(self.contract["task_status"]), {"all", "terminal", "successful"}
        )

    def test_security_scheme_matches_the_specification(self):
        security = self.contract.get("security")
        self.assertIsInstance(security, dict, "contract.security must be an object")
        self.assertEqual(security.get("scheme_name"), "bearerToken")
        self.assertEqual(security.get("type"), "http")
        self.assertEqual(security.get("scheme"), "Bearer")
        self.assertEqual(security.get("bearer_format"), "JWT")

    def test_contract_names_exactly_the_three_operations(self):
        operations = self.contract.get("operations")
        self.assertIsInstance(operations, dict)
        self.assertEqual(
            sorted(operations),
            sorted(OPERATION_IDS),
            "contract must name exactly the three operationIds in scope, spelled "
            "as in the specification",
        )

    def test_generate_operation(self):
        entry = self.operation(OP_GENERATE)
        self.assertEqual(entry.get("method"), "POST")
        self.assertEqual(entry.get("path"), PATH_BUNDLES)
        self.assertEqual(entry.get("success_status"), 202)
        self.assertEqual(entry.get("response_schema"), "Task")
        self.assertFalse(entry.get("response_is_array"))

        path_params = entry.get("path_parameters")
        self.assertIsInstance(path_params, list)
        self.assertEqual(len(path_params), 1)
        self.assertEqual(path_params[0].get("name"), "componentId")
        self.assertTrue(path_params[0].get("required"))
        self.assertEqual(path_params[0].get("type"), "string")
        self.assertEqual(path_params[0].get("format"), "uuid")

        header_params = entry.get("header_parameters")
        self.assertIsInstance(header_params, list)
        self.assertEqual(
            [item.get("name") for item in header_params],
            ["X-Correlation-Id"],
            "the only header parameter declared on %s is X-Correlation-Id" % OP_GENERATE,
        )
        self.assertIs(
            header_params[0].get("required"),
            False,
            "X-Correlation-Id is an optional header parameter",
        )
        self.assertEqual(header_params[0].get("type"), "string")

    def test_generate_request_body(self):
        body = self.operation(OP_GENERATE).get("request_body")
        self.assertIsInstance(body, dict, "%s declares a request body" % OP_GENERATE)
        self.assertEqual(body.get("schema"), "ComponentSupportBundleSpec")
        self.assertEqual(body.get("content_type"), "application/json")
        self.assertEqual(
            body.get("required_properties"),
            [],
            "ComponentSupportBundleSpec declares no required properties",
        )
        optional = body.get("optional_properties")
        self.assertIsInstance(optional, list)
        self.assertEqual([item.get("name") for item in optional], ["lookBackWindow"])
        self.assertEqual(optional[0].get("type"), "integer")

    def test_get_task_operation(self):
        entry = self.operation(OP_GET_TASK)
        self.assertEqual(entry.get("method"), "GET")
        self.assertEqual(entry.get("path"), PATH_TASK)
        self.assertEqual(entry.get("success_status"), 200)
        self.assertEqual(entry.get("response_schema"), "Task")
        self.assertFalse(entry.get("response_is_array"))
        self.assertIsNone(entry.get("request_body"))
        self.assertEqual(entry.get("header_parameters"), [])

        path_params = entry.get("path_parameters")
        self.assertIsInstance(path_params, list)
        self.assertEqual(len(path_params), 1)
        self.assertEqual(
            path_params[0],
            {"name": "taskId", "required": True, "type": "string", "format": "uuid"},
        )

    def test_list_bundles_operation(self):
        entry = self.operation(OP_LIST_BUNDLES)
        self.assertEqual(entry.get("method"), "GET")
        self.assertEqual(entry.get("path"), PATH_BUNDLES)
        self.assertEqual(entry.get("success_status"), 200)
        self.assertEqual(entry.get("response_schema"), "SupportBundle")
        self.assertIs(
            entry.get("response_is_array"),
            True,
            "%s returns an array of SupportBundle" % OP_LIST_BUNDLES,
        )
        self.assertIsNone(entry.get("request_body"))
        self.assertEqual(entry.get("header_parameters"), [])
        self.assertEqual(
            entry.get("path_parameters"),
            [
                {
                    "name": "componentId",
                    "required": True,
                    "type": "string",
                    "format": "uuid",
                }
            ],
        )

    def test_task_status_enum(self):
        statuses = self.contract.get("task_status")
        self.assertIsInstance(statuses, dict, "contract.task_status must be an object")
        self.assertEqual(
            statuses.get("all"),
            TASK_STATUS_ENUM,
            "task_status.all must list the TaskStatus enum in specification order",
        )
        self.assertEqual(statuses.get("terminal"), ["SUCCEEDED", "FAILED", "CANCELED"])
        self.assertEqual(statuses.get("successful"), ["SUCCEEDED"])


class OfficialSourcesTest(unittest.TestCase):
    """docs/official_sources.json must record real, retrievable provenance."""

    @classmethod
    def setUpClass(cls):
        cls.doc = load_json(SOURCES_PATH, "docs/official_sources.json")

    def entry(self):
        sources = self.doc.get("sources")
        self.assertIsInstance(sources, list, "official_sources.sources must be a list")
        self.assertEqual(len(sources), 1, "exactly one specification source is in scope")
        self.assertIsInstance(sources[0], dict)
        return sources[0]

    def test_records_spec_path_commit_and_operation_ids(self):
        entry = self.entry()
        self.assertEqual(entry.get("spec_path"), SPEC_PATH)
        self.assertEqual(entry.get("commit"), SPEC_COMMIT)
        self.assertEqual(entry.get("license"), "Apache-2.0")
        repository = (entry.get("repository") or "").rstrip("/")
        self.assertTrue(repository.startswith("https://"))
        self.assertEqual(repository.removesuffix(".git"), REPO_URL)
        self.assertEqual(
            sorted(entry.get("operation_ids") or []),
            sorted(OPERATION_IDS),
            "every operationId used must be recorded",
        )

    def test_uses_exactly_the_documented_shape(self):
        self.assertEqual(set(self.doc), {"sources"})
        self.assertEqual(
            set(self.entry()),
            {
                "repository",
                "license",
                "spec_path",
                "commit",
                "spec_blob_sha",
                "spec_sha256",
                "operation_ids",
            },
        )

    def test_records_content_digests_of_the_retrieved_spec(self):
        entry = self.entry()
        self.assertEqual(
            entry.get("spec_blob_sha"),
            SPEC_BLOB_SHA,
            "spec_blob_sha must be the git blob sha of the specification file",
        )
        self.assertEqual(
            entry.get("spec_sha256"),
            SPEC_SHA256,
            "spec_sha256 must be the SHA-256 of the specification file's bytes",
        )

    def test_commit_agrees_with_the_contract(self):
        contract = load_json(CONTRACT_PATH, "docs/contract.json")
        self.assertEqual(
            (contract.get("source") or {}).get("commit"),
            self.entry().get("commit"),
            "contract.json and official_sources.json must cite the same commit",
        )

    def test_no_placeholder_hosts(self):
        for path, label in (
            (CONTRACT_PATH, "contract.json"),
            (SOURCES_PATH, "official_sources.json"),
        ):
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn(
                ".invalid", text, "%s must not cite placeholder hosts" % label
            )
            self.assertNotIn("example.com", text)


class ContractHelpersTest(unittest.TestCase):
    def test_load_and_lookup_helpers(self):
        import vcf_lcm.contract as contract_module

        contract = contract_module.load_contract(CONTRACT_PATH)
        self.assertEqual(
            contract_module.operation(contract, OP_GET_TASK),
            contract["operations"][OP_GET_TASK],
        )
        with self.assertRaises(KeyError):
            contract_module.operation(contract, "notAnOperation")
        terminal = contract_module.terminal_statuses(contract)
        successful = contract_module.successful_statuses(contract)
        self.assertIsInstance(terminal, frozenset)
        self.assertIsInstance(successful, frozenset)
        self.assertEqual(terminal, TERMINAL_STATUSES)
        self.assertEqual(successful, {"SUCCEEDED"})

    def test_load_rejects_a_contract_missing_a_required_operation(self):
        import vcf_lcm.contract as contract_module

        contract = copy.deepcopy(load_json(CONTRACT_PATH, "docs/contract.json"))
        del contract["operations"][OP_GET_TASK]
        handle = tempfile.NamedTemporaryFile(
            mode="w", prefix="incomplete-lcm-contract-", suffix=".json", delete=False
        )
        try:
            json.dump(contract, handle)
            handle.close()
            with self.assertRaises(ValueError):
                contract_module.load_contract(handle.name)
        finally:
            if not handle.closed:
                handle.close()
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    def test_default_path_honors_the_documented_environment_override(self):
        import vcf_lcm.contract as contract_module

        previous = os.environ.get(contract_module.CONTRACT_ENV_VAR)
        os.environ[contract_module.CONTRACT_ENV_VAR] = CONTRACT_PATH
        try:
            self.assertEqual(contract_module.default_contract_path(), CONTRACT_PATH)
        finally:
            if previous is None:
                os.environ.pop(contract_module.CONTRACT_ENV_VAR, None)
            else:
                os.environ[contract_module.CONTRACT_ENV_VAR] = previous


class PublicSignatureTest(unittest.TestCase):
    def assert_parameter_names(self, callable_object, expected):
        parameters = list(inspect.signature(callable_object).parameters.values())
        self.assertEqual([parameter.name for parameter in parameters], expected)
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                for parameter in parameters
            )
        )

    def test_client_public_signatures_match_the_stubs(self):
        import vcf_lcm

        client = vcf_lcm.SddcLcmClient
        self.assert_parameter_names(
            client.__init__,
            [
                "self",
                "base_url",
                "token",
                "contract",
                "contract_path",
                "poll_interval",
                "timeout",
            ],
        )
        self.assert_parameter_names(
            client.generate_support_bundle,
            ["self", "component_id", "look_back_window", "correlation_id"],
        )
        self.assert_parameter_names(client.get_task, ["self", "task_id"])
        self.assert_parameter_names(
            client.list_support_bundles, ["self", "component_id"]
        )
        self.assert_parameter_names(
            client.await_task, ["self", "task_id", "poll_interval", "timeout"]
        )
        self.assert_parameter_names(
            client.generate_support_bundle_and_wait,
            [
                "self",
                "component_id",
                "look_back_window",
                "correlation_id",
                "poll_interval",
                "timeout",
            ],
        )


class MockServer:
    """Runs tools/lcm_mock_server.py on loopback and exposes its request log."""

    def __init__(self, contract_path=CONTRACT_PATH):
        self.log_path = tempfile.NamedTemporaryFile(
            prefix="lcm-requests-", suffix=".jsonl", delete=False
        ).name
        self.proc = subprocess.Popen(
            [
                sys.executable,
                MOCK_PATH,
                "--contract",
                contract_path,
                "--log",
                self.log_path,
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = self.proc.stdout.readline().strip()
        if not line.startswith("READY "):
            self.proc.kill()
            stderr = self.proc.stderr.read()
            raise AssertionError(
                "mock server did not start (it is pinned to docs/contract.json "
                "and refuses a contract it cannot serve).\nstdout: %r\nstderr: %s"
                % (line, stderr)
            )
        _, self.base_url, self.token = line.split(" ", 2)

    def requests(self):
        with open(self.log_path, encoding="utf-8") as handle:
            return [json.loads(row) for row in handle if row.strip()]

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()
        try:
            os.unlink(self.log_path)
        except OSError:
            pass


class WireShapeTestCase(unittest.TestCase):
    """Base case: a fresh mock and a fresh client per test."""

    def setUp(self):
        import vcf_lcm

        self.vcf_lcm = vcf_lcm
        self.server = MockServer()
        self.addCleanup(self.server.stop)
        self.client = vcf_lcm.SddcLcmClient(
            self.server.base_url,
            self.server.token,
            contract_path=CONTRACT_PATH,
            poll_interval=0.01,
            timeout=10.0,
        )

    # -- helpers ------------------------------------------------------------
    def requests(self):
        return self.server.requests()

    def assert_only_contracted_paths(self, entries):
        allowed = (
            "/v1/components/%s/support-bundles",
            "/v1/tasks/%s",
        )
        for entry in entries:
            path = entry["path"]
            ok = (
                path.startswith("/v1/components/")
                and path.endswith("/support-bundles")
                and path.count("/") == 4
            ) or (path.startswith("/v1/tasks/") and path.count("/") == 3)
            self.assertTrue(
                ok,
                "request %d went to %r, which no contracted operation serves "
                "(contracted templates: %s)"
                % (entry["seq"], path, ", ".join(allowed)),
            )
            self.assertEqual(
                entry["query"], "", "no contracted operation takes a query string"
            )

    def assert_authenticated(self, entry):
        headers = entry["headers_lower"]
        self.assertIn("authorization", headers, "request %d has no Authorization header" % entry["seq"])
        self.assertEqual(
            headers["authorization"],
            "Bearer " + self.server.token,
            "the contract's security scheme is a JWT bearer token",
        )

    def header_names(self, entry):
        return {name.lower() for name, _ in entry["headers"]}


class SuccessfulWorkflowTest(WireShapeTestCase):
    def test_full_workflow_wire_shape(self):
        bundle = self.client.generate_support_bundle_and_wait(
            COMPONENT_OK, look_back_window=24, correlation_id="corr-7f21a9"
        )

        self.assertIsInstance(bundle, dict, "the workflow returns the SupportBundle")
        self.assertEqual(bundle.get("id"), BUNDLE_OK)
        self.assertEqual(bundle.get("name"), "support-bundle-vcf-ops-2f4c9e17.tgz")

        entries = self.requests()
        self.assert_only_contracted_paths(entries)
        for entry in entries:
            self.assert_authenticated(entry)
            self.assertLess(
                entry["status"],
                300,
                "request %d to %s was rejected with %d"
                % (entry["seq"], entry["path"], entry["status"]),
            )

        self.assertEqual(
            [(e["method"], e["path"]) for e in entries],
            [
                ("POST", "/v1/components/%s/support-bundles" % COMPONENT_OK),
                ("GET", "/v1/tasks/%s" % TASK_OK),
                ("GET", "/v1/tasks/%s" % TASK_OK),
                ("GET", "/v1/tasks/%s" % TASK_OK),
                ("GET", "/v1/components/%s/support-bundles" % COMPONENT_OK),
            ],
            "expected: start the task, poll it to a terminal status, then list "
            "the component's bundles",
        )

    def test_post_body_and_headers(self):
        self.client.generate_support_bundle(
            COMPONENT_OK, look_back_window=24, correlation_id="corr-7f21a9"
        )
        entry = self.requests()[0]
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], "/v1/components/%s/support-bundles" % COMPONENT_OK)
        self.assertEqual(
            entry["body_json"],
            {"lookBackWindow": 24},
            "the request body is the ComponentSupportBundleSpec the caller asked for",
        )
        headers = entry["headers_lower"]
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(headers.get("x-correlation-id"), "corr-7f21a9")
        self.assertEqual(
            headers.get("content-length"),
            str(len(entry["body_raw"].encode("utf-8"))),
            "Content-Length must match the body actually sent",
        )

    def test_get_requests_carry_no_body_or_content_type(self):
        self.client.generate_support_bundle_and_wait(COMPONENT_OK)
        for entry in self.requests():
            if entry["method"] != "GET":
                continue
            self.assertEqual(
                entry["body_raw"], "", "GET %s carried a body" % entry["path"]
            )
            self.assertNotIn(
                "content-type",
                self.header_names(entry),
                "GET %s sent a Content-Type header" % entry["path"],
            )


class OptionalFieldOmissionTest(WireShapeTestCase):
    def test_unset_body_property_is_omitted_not_sent_empty(self):
        self.client.generate_support_bundle(COMPONENT_OK)
        entry = self.requests()[0]
        self.assertEqual(
            entry["body_json"],
            {},
            "with no look-back window supplied the body must carry no properties",
        )
        self.assertEqual(
            entry["body_raw"],
            "{}",
            "the serialised body must be exactly {} -- not a null-valued "
            "property, not a zero, and not an empty payload",
        )
        self.assertNotIn("lookBackWindow", entry["body_raw"])

    def test_unset_header_is_absent_not_empty(self):
        self.client.generate_support_bundle(COMPONENT_OK, look_back_window=12)
        entry = self.requests()[0]
        self.assertEqual(entry["body_json"], {"lookBackWindow": 12})
        self.assertNotIn(
            "x-correlation-id",
            self.header_names(entry),
            "with no correlation id supplied the X-Correlation-Id header must be "
            "absent from the request, not present with an empty value",
        )

    def test_zero_is_a_value_and_is_sent(self):
        self.client.generate_support_bundle(COMPONENT_OK, look_back_window=0)
        entry = self.requests()[0]
        self.assertEqual(
            entry["body_json"],
            {"lookBackWindow": 0},
            "0 is a supplied value; only None means the property is unset",
        )


class PollingTest(WireShapeTestCase):
    def test_completion_is_polled_not_assumed(self):
        task = self.client.generate_support_bundle(COMPONENT_OK)
        self.assertEqual(task.get("id"), TASK_OK)
        self.assertNotIn(
            task.get("status"),
            TERMINAL_STATUSES,
            "the accepted task is not yet complete",
        )

        terminal = self.client.await_task(TASK_OK, poll_interval=0.01)
        self.assertEqual(terminal.get("status"), "SUCCEEDED")

        polls = [e for e in self.requests() if e["path"].startswith("/v1/tasks/")]
        self.assertEqual(
            len(polls),
            3,
            "the mock reports SUCCEEDED on the third read; polling must stop "
            "there -- neither earlier nor later",
        )

    def test_failed_task_raises_with_the_terminal_task_attached(self):
        with self.assertRaises(self.vcf_lcm.TaskFailedError) as caught:
            self.client.generate_support_bundle_and_wait(COMPONENT_FAIL)
        task = getattr(caught.exception, "task", None)
        self.assertIsInstance(task, dict, "TaskFailedError must carry the task")
        self.assertEqual(task.get("id"), TASK_FAIL)
        self.assertEqual(task.get("status"), "FAILED")

        entries = self.requests()
        polls = [e for e in entries if e["path"].startswith("/v1/tasks/")]
        self.assertEqual(len(polls), 3)
        self.assertEqual(
            [e for e in entries if e["method"] == "GET" and "support-bundles" in e["path"]],
            [],
            "a failed task must not be followed by a bundle listing",
        )

    def test_stalled_task_times_out(self):
        with self.assertRaises(self.vcf_lcm.TaskTimeoutError) as caught:
            self.client.generate_support_bundle_and_wait(
                COMPONENT_STALLED, poll_interval=0, timeout=0
            )
        entries = self.requests()
        self.assert_only_contracted_paths(entries)
        polls = [e for e in entries if e["path"] == "/v1/tasks/%s" % TASK_STALLED]
        self.assertEqual(
            len(polls),
            1,
            "await_task performs one immediate read before evaluating timeout",
        )
        self.assertEqual(getattr(caught.exception, "task", {}).get("id"), TASK_STALLED)
        self.assertEqual(
            [e for e in entries if e["method"] == "GET" and "support-bundles" in e["path"]],
            [],
            "a task that never became terminal must not be treated as complete",
        )


class ContractTaskStateTest(unittest.TestCase):
    def setUp(self):
        import vcf_lcm

        self.vcf_lcm = vcf_lcm
        self.client = vcf_lcm.SddcLcmClient(
            "http://127.0.0.1:1",
            "unused-token",
            contract_path=CONTRACT_PATH,
        )

    def test_every_declared_in_flight_status_is_polled(self):
        observed = []
        scripted = iter(["PENDING", "SCHEDULED", "RUNNING", "SUCCEEDED"])

        def get_task(task_id):
            status = next(scripted)
            observed.append(status)
            return {"id": task_id, "status": status}

        self.client.get_task = get_task
        terminal = self.client.await_task(TASK_OK, poll_interval=0, timeout=10)
        self.assertEqual(terminal.get("status"), "SUCCEEDED")
        self.assertEqual(observed, ["PENDING", "SCHEDULED", "RUNNING", "SUCCEEDED"])

    def test_canceled_task_is_a_failed_terminal_outcome(self):
        terminal = {"id": TASK_FAIL, "status": "CANCELED"}
        self.client.get_task = lambda task_id: terminal
        with self.assertRaises(self.vcf_lcm.TaskFailedError) as caught:
            self.client.await_task(TASK_FAIL, poll_interval=0, timeout=10)
        self.assertIs(caught.exception.task, terminal)


class ErrorHandlingTest(WireShapeTestCase):
    def test_not_found_surfaces_as_api_error(self):
        with self.assertRaises(self.vcf_lcm.LcmApiError) as caught:
            self.client.get_task(UNKNOWN_TASK)
        error = caught.exception
        self.assertEqual(getattr(error, "status_code", None), 404)
        payload = getattr(error, "payload", None)
        self.assertIsInstance(payload, dict, "the ErrorResponse body must be decoded")
        self.assertEqual(payload.get("code"), "NOT_FOUND")

    def test_bad_token_surfaces_as_api_error(self):
        client = self.vcf_lcm.SddcLcmClient(
            self.server.base_url,
            "not-the-right-token",
            contract_path=CONTRACT_PATH,
            poll_interval=0.01,
        )
        with self.assertRaises(self.vcf_lcm.LcmApiError) as caught:
            client.list_support_bundles(COMPONENT_OK)
        self.assertEqual(getattr(caught.exception, "status_code", None), 401)


class WorkflowResultSelectionTest(unittest.TestCase):
    def test_missing_resource_id_match_raises_api_error(self):
        import vcf_lcm

        client = vcf_lcm.SddcLcmClient(
            "http://127.0.0.1:1",
            "unused-token",
            contract_path=CONTRACT_PATH,
        )
        client.generate_support_bundle = lambda *args, **kwargs: {"id": TASK_OK}
        client.await_task = lambda *args, **kwargs: {
            "id": TASK_OK,
            "status": "SUCCEEDED",
            "resourceId": "missing-bundle",
        }
        client.list_support_bundles = lambda *args, **kwargs: [
            {"id": "some-other-bundle"}
        ]
        with self.assertRaises(vcf_lcm.LcmApiError):
            client.generate_support_bundle_and_wait(COMPONENT_OK)


class ContractDrivesTheClientTest(unittest.TestCase):
    def test_client_uses_mutated_contract_methods_and_paths_for_all_operations(self):
        import vcf_lcm

        contract = copy.deepcopy(load_json(CONTRACT_PATH, "docs/contract.json"))
        mutations = {
            OP_GENERATE: ("PUT", "/contract-driven/components/{componentId}/start"),
            OP_GET_TASK: ("PATCH", "/contract-driven/tasks/{taskId}"),
            OP_LIST_BUNDLES: (
                "DELETE",
                "/contract-driven/components/{componentId}/results",
            ),
        }
        for operation_id, (method, path) in mutations.items():
            contract["operations"][operation_id]["method"] = method
            contract["operations"][operation_id]["path"] = path

        handle = tempfile.NamedTemporaryFile(
            mode="w", prefix="lcm-contract-", suffix=".json", delete=False
        )
        try:
            json.dump(contract, handle)
            handle.close()
            self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))

            server = MockServer(handle.name)
            self.addCleanup(server.stop)
            client = vcf_lcm.SddcLcmClient(
                server.base_url,
                server.token,
                contract_path=handle.name,
                poll_interval=0,
                timeout=10,
            )
            client.generate_support_bundle(COMPONENT_OK)
            client.get_task(TASK_OK)
            client.list_support_bundles(COMPONENT_OK)

            self.assertEqual(
                [(entry["method"], entry["path"]) for entry in server.requests()],
                [
                    ("PUT", "/contract-driven/components/%s/start" % COMPONENT_OK),
                    ("PATCH", "/contract-driven/tasks/%s" % TASK_OK),
                    ("DELETE", "/contract-driven/components/%s/results" % COMPONENT_OK),
                ],
            )
        finally:
            if not handle.closed:
                handle.close()


class StandardLibraryOnlyTest(unittest.TestCase):
    def test_package_imports_only_the_python_standard_library(self):
        stdlib_names = getattr(sys, "stdlib_module_names", None)
        stdlib_root = os.path.realpath(sysconfig.get_path("stdlib"))
        site_roots = {
            os.path.realpath(path)
            for path in (sysconfig.get_path("purelib"), sysconfig.get_path("platlib"))
            if path
        }

        def is_stdlib(module_name):
            if module_name == "vcf_lcm":
                return True
            if stdlib_names is not None:
                return module_name in stdlib_names
            if module_name in sys.builtin_module_names:
                return True
            spec = importlib.util.find_spec(module_name)
            if spec is None:
                return False
            origin = spec.origin
            if origin in (None, "built-in", "frozen"):
                return True
            origin = os.path.realpath(origin)
            if any(origin == root or origin.startswith(root + os.sep) for root in site_roots):
                return False
            return origin == stdlib_root or origin.startswith(stdlib_root + os.sep)

        for filename in ("__init__.py", "client.py", "contract.py"):
            path = os.path.join(SRC_DIR, "vcf_lcm", filename)
            with open(path, encoding="utf-8") as source:
                tree = ast.parse(source.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [(node.module or "").split(".", 1)[0]]
                else:
                    continue
                for name in names:
                    self.assertTrue(
                        is_stdlib(name),
                        "%s imports non-standard-library module %s" % (filename, name),
                    )


if __name__ == "__main__":
    unittest.main()
