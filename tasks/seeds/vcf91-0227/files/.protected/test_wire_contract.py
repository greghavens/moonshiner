"""Protected verification for the SDDC LCM component-upgrade client.

Three things are checked:

1. ``docs/contract.json`` and ``docs/official_sources.json`` match the published
   specification ``specifications/sddc-lcm/sddc-lcm-openapi.yaml`` in
   ``vmware/vcf-api-specs`` at the pinned commit.
2. The client, driven against a loopback mock pinned to that contract, puts the
   exact expected bytes on the wire -- including omitting optional fields that
   the caller left unset, rather than sending them empty.
3. An access token that expires part way through a run is refreshed and the
   rejected request replayed, without re-issuing any work already accepted.

No VMware endpoint is contacted. Every request goes to 127.0.0.1.

PROTECTED: do not modify.
"""

import ast
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import sysconfig
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

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

SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
# The two content digests are the proof that the specification file was really
# retrieved, so they are not written out here for the taking: what is pinned is
# the SHA-256 *of each expected digest string*. Fetch the file and the digests
# fall out; they cannot be recalled from memory.
SPEC_BLOB_SHA_GUARD = (
    "253aff68edfa2ffc516325365f3a8db4ee73bb7c170e8493d5594d82ab7cb343"
)
SPEC_SHA256_GUARD = (
    "05bc1e13b84d7b42a7264e65398640fdf5c981902630657a0cde44a78d5725d8"
)

OPENAPI_VERSION = "3.0.4"
INFO_VERSION = "9.1.0.0"
SERVER_URL = "https://vcf.broadcom.com/sddc-lcm"

OP_HEALTH = "getHealth"
OP_RESOLVE = "resolveDepotComponents"
OP_ACTION = "performComponentAction"
OP_GET_TASK = "getTask"
OP_RETRY_TASK = "retryTask"
OPERATION_IDS = (OP_HEALTH, OP_RESOLVE, OP_ACTION, OP_GET_TASK, OP_RETRY_TASK)

PATH_HEALTH = "/v1/health"
PATH_DEPOT_COMPONENTS = "/v1/depot/components"
PATH_COMPONENT = "/v1/components/{componentId}"
PATH_TASK = "/v1/tasks/{taskId}"

ACTION_ENUM = ["shutdown", "restart", "start", "refresh", "precheck", "apply"]

TASK_STATUS_ENUM = [
    "PENDING",
    "SCHEDULED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELED",
]
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED"}
SUCCESSFUL_STATUSES = {"SUCCEEDED"}

#: The request-body schemas in scope plus everything reachable from them.
EXPECTED_SCHEMA_NAMES = {
    "DepotComponentsSpec",
    "FleetDepotSpec",
    "ComponentVersionSpec",
    "ComponentUpgradeSpec",
    "ComponentDesiredSpec",
    "SoftwareSpec",
    "DepotSpec",
    "LcmPlatformSpec",
}

#: name -> (required list in spec order, [(property, type, schema, items)] in spec order)
EXPECTED_SCHEMAS = {
    "DepotComponentsSpec": (
        ["componentVersions", "fleetDepotSpec"],
        [
            ("fleetDepotSpec", "object", "FleetDepotSpec", None),
            ("version", "string", None, None),
            ("componentVersions", "array", None, {"type": "object", "schema": "ComponentVersionSpec"}),
        ],
    ),
    "FleetDepotSpec": (
        ["certificate", "fqdn"],
        [("fqdn", "string", None, None), ("certificate", "string", None, None)],
    ),
    "ComponentVersionSpec": (
        ["component"],
        [("component", "string", None, None), ("version", "string", None, None)],
    ),
    "ComponentUpgradeSpec": (
        ["componentSpec"],
        [
            ("componentSpec", "object", "ComponentDesiredSpec", None),
            ("lcmPlatformSpec", "object", "LcmPlatformSpec", None),
            ("correlationId", "string", None, None),
        ],
    ),
    "ComponentDesiredSpec": (
        ["depot", "software"],
        [
            ("software", "object", "SoftwareSpec", None),
            ("policy", "object", None, None),
            ("depot", "object", "DepotSpec", None),
            ("userInput", "object", None, None),
            ("additionalInput", "object", None, None),
        ],
    ),
    "SoftwareSpec": (["version"], [("version", "string", None, None)]),
    "DepotSpec": (
        ["url"],
        [
            ("url", "string", None, None),
            ("certificate", "array", None, {"type": "string", "schema": None}),
        ],
    ),
    "LcmPlatformSpec": (
        ["performBackup"],
        [("performBackup", "boolean", None, None)],
    ),
}

# --- fixture identifiers served by the loopback mock ------------------------

COMPONENT_OK = "d3f1a6c2-7b48-4e91-a05d-6c2e8f7b1a34"
COMPONENT_RETRY = "5e2b9d84-1c6f-4a37-9e80-3b7d5a1c6e29"
COMPONENT_STALL = "7c0a4e59-2d81-4b63-8f47-9a1e5c3d0b72"
COMPONENT_CANCEL = "8d1b5f60-3e92-4c74-9a58-0b2f6d4e1c83"

TASK_OK = "9a4c7e21-5d38-4f60-b1a7-2e6c9d0b3f85"
TASK_RETRY = "1f8d5b30-4a92-4c76-8b53-7e0a2c4d9f61"
TASK_STALL = "4b6e2a17-8f50-4d29-a3c6-1d8b7e5f0a94"
TASK_CANCEL = "6c7f3b28-9a41-4e50-b2d6-8f1a5c3e7b90"

UNKNOWN_TASK = "00000000-0000-4000-8000-000000000000"

DEPOT_FQDN = "depot.vcf.lab.local"
DEPOT_CERT = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIDMjCCAhegAwIBAgIQI0tRU5EIENFUlRJRklDQVRF\n"
    "-----END CERTIFICATE-----\n"
)
TARGET_VERSION = "9.1.0.0000.24178562"
MANIFEST_URL = (
    "https://depot.broadcom.com/PROD/vcf/9.1.0/vcf-ops/"
    "9.1.0.0000.24178562/manifest.json"
)
CORRELATION_ID = "f0e9d8c7-b6a5-4321-fedc-ba9876543210"


def load_json(path, label):
    if not os.path.isfile(path):
        raise AssertionError("%s is missing (expected at %s)" % (label, path))
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise AssertionError("%s is not valid JSON: %s" % (label, exc))


# ===========================================================================
# 1. the contract must be derived from the specification
# ===========================================================================


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
        self.assertEqual(
            source.get("commit"),
            SPEC_COMMIT,
            "contract.source.commit must be the sha of the repository revision "
            "the specification file was retrieved from",
        )
        self.assertEqual(source.get("openapi"), OPENAPI_VERSION)
        self.assertEqual(source.get("info_version"), INFO_VERSION)
        self.assertEqual(source.get("server_url"), SERVER_URL)
        repository = source.get("repository") or ""
        self.assertEqual(repository.rstrip("/"), REPO_URL)
        self.assertTrue(repository.startswith("https://"))

    def test_security_scheme_matches_the_specification(self):
        security = self.contract.get("security")
        self.assertIsInstance(security, dict, "contract.security must be an object")
        self.assertEqual(security.get("scheme_name"), "bearerToken")
        self.assertEqual(security.get("type"), "http")
        self.assertEqual(security.get("scheme"), "Bearer")
        self.assertEqual(security.get("bearer_format"), "JWT")

    def test_contract_names_exactly_the_five_operations(self):
        operations = self.contract.get("operations")
        self.assertIsInstance(operations, dict)
        self.assertEqual(
            sorted(operations),
            sorted(OPERATION_IDS),
            "contract must name exactly the five operationIds in scope, spelled "
            "as in the specification",
        )

    def test_health_operation_overrides_the_security_requirement(self):
        entry = self.operation(OP_HEALTH)
        self.assertEqual(entry.get("method"), "GET")
        self.assertEqual(entry.get("path"), PATH_HEALTH)
        self.assertEqual(entry.get("spec_path_key"), PATH_HEALTH)
        self.assertEqual(entry.get("success_status"), 200)
        self.assertEqual(entry.get("response_schema"), "Health")
        self.assertIs(
            entry.get("authenticated"),
            False,
            "%s declares its own empty security requirement, so it is the one "
            "operation in scope that is not authenticated" % OP_HEALTH,
        )
        self.assertIsNone(entry.get("request_body"))
        self.assertEqual(entry.get("path_parameters"), [])
        self.assertEqual(entry.get("query_parameters"), [])
        self.assertEqual(entry.get("header_parameters"), [])

    def test_resolve_depot_components_operation(self):
        entry = self.operation(OP_RESOLVE)
        self.assertEqual(entry.get("method"), "POST")
        self.assertEqual(entry.get("path"), PATH_DEPOT_COMPONENTS)
        self.assertEqual(entry.get("spec_path_key"), PATH_DEPOT_COMPONENTS)
        self.assertEqual(entry.get("success_status"), 200)
        self.assertEqual(entry.get("response_schema"), "ResolvedComponentVersions")
        self.assertIs(entry.get("authenticated"), True)
        self.assertEqual(entry.get("path_parameters"), [])
        self.assertEqual(entry.get("query_parameters"), [])
        self.assertEqual(
            entry.get("header_parameters"),
            [],
            "%s declares no parameters at all" % OP_RESOLVE,
        )
        body = entry.get("request_body")
        self.assertIsInstance(body, dict, "%s declares a request body" % OP_RESOLVE)
        self.assertEqual(body.get("schema"), "DepotComponentsSpec")
        self.assertEqual(body.get("content_type"), "application/json")

    def test_perform_component_action_operation(self):
        entry = self.operation(OP_ACTION)
        self.assertEqual(entry.get("method"), "POST")
        self.assertEqual(entry.get("path"), PATH_COMPONENT)
        self.assertEqual(entry.get("spec_path_key"), PATH_COMPONENT)
        self.assertEqual(entry.get("success_status"), 202)
        self.assertEqual(entry.get("response_schema"), "Task")
        self.assertIs(entry.get("authenticated"), True)

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
            "the only header parameter declared on %s is X-Correlation-Id" % OP_ACTION,
        )
        self.assertIs(
            header_params[0].get("required"),
            False,
            "X-Correlation-Id is an optional header parameter",
        )
        self.assertEqual(header_params[0].get("type"), "string")

        body = entry.get("request_body")
        self.assertIsInstance(body, dict, "%s declares a request body" % OP_ACTION)
        self.assertEqual(body.get("schema"), "ComponentUpgradeSpec")
        self.assertEqual(body.get("content_type"), "application/json")

    def test_perform_component_action_query_parameter(self):
        query = self.operation(OP_ACTION).get("query_parameters")
        self.assertIsInstance(query, list)
        self.assertEqual(len(query), 1, "%s declares one query parameter" % OP_ACTION)
        item = query[0]
        self.assertEqual(item.get("name"), "action")
        self.assertIs(item.get("required"), True)
        self.assertEqual(item.get("type"), "string")
        self.assertEqual(
            item.get("source"),
            "parameters",
            "action is declared in the operation's parameters list",
        )
        self.assertIsNone(
            item.get("fixed_value"),
            "the caller chooses the action, so no value is fixed by the spec",
        )
        self.assertEqual(
            item.get("enum"),
            ACTION_ENUM,
            "the action enum must be listed in specification order",
        )

    def test_get_task_operation(self):
        entry = self.operation(OP_GET_TASK)
        self.assertEqual(entry.get("method"), "GET")
        self.assertEqual(entry.get("path"), PATH_TASK)
        self.assertEqual(entry.get("spec_path_key"), PATH_TASK)
        self.assertEqual(entry.get("success_status"), 200)
        self.assertEqual(entry.get("response_schema"), "Task")
        self.assertIs(entry.get("authenticated"), True)
        self.assertIsNone(entry.get("request_body"))
        self.assertEqual(entry.get("header_parameters"), [])
        self.assertEqual(entry.get("query_parameters"), [])

        path_params = entry.get("path_parameters")
        self.assertIsInstance(path_params, list)
        self.assertEqual(len(path_params), 1)
        self.assertEqual(path_params[0].get("name"), "taskId")
        self.assertTrue(path_params[0].get("required"))
        self.assertEqual(path_params[0].get("type"), "string")
        self.assertEqual(path_params[0].get("format"), "uuid")

    def test_retry_task_operation_splits_its_path_key(self):
        entry = self.operation(OP_RETRY_TASK)
        self.assertEqual(entry.get("method"), "POST")
        self.assertEqual(
            entry.get("spec_path_key"),
            PATH_TASK + "?action=retry",
            "%s is keyed under paths: by a template that already carries its "
            "query string" % OP_RETRY_TASK,
        )
        self.assertEqual(
            entry.get("path"),
            PATH_TASK,
            "'path' is the part of the key before the '?'",
        )
        self.assertEqual(entry.get("success_status"), 200)
        self.assertEqual(entry.get("response_schema"), "Task")
        self.assertIs(entry.get("authenticated"), True)
        self.assertIsNone(
            entry.get("request_body"), "%s declares no request body" % OP_RETRY_TASK
        )
        self.assertEqual(entry.get("header_parameters"), [])
        path_params = entry.get("path_parameters") or []
        self.assertEqual(len(path_params), 1)
        self.assertEqual(path_params[0].get("name"), "taskId")
        self.assertIs(path_params[0].get("required"), True)
        self.assertEqual(path_params[0].get("type"), "string")
        self.assertEqual(path_params[0].get("format"), "uuid")

        query = entry.get("query_parameters")
        self.assertIsInstance(query, list)
        self.assertEqual(len(query), 1)
        item = query[0]
        self.assertEqual(item.get("name"), "action")
        self.assertIs(item.get("required"), True)
        self.assertEqual(item.get("type"), "string")
        self.assertEqual(
            item.get("source"),
            "path_key",
            "action exists here only because it is baked into the paths: key",
        )
        self.assertEqual(
            item.get("fixed_value"),
            "retry",
            "the value is fixed by the specification, not chosen by the caller",
        )
        self.assertIsNone(item.get("enum"))

    def test_task_status_enum(self):
        statuses = self.contract.get("task_status")
        self.assertIsInstance(statuses, dict, "contract.task_status must be an object")
        self.assertEqual(
            statuses.get("all"),
            TASK_STATUS_ENUM,
            "task_status.all must list the TaskStatus enum in specification order",
        )
        self.assertEqual(set(statuses.get("terminal") or []), TERMINAL_STATUSES)
        self.assertEqual(set(statuses.get("successful") or []), SUCCESSFUL_STATUSES)


class ContractSchemasTest(unittest.TestCase):
    """The schemas block must describe the request bodies and their $refs."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_json(CONTRACT_PATH, "docs/contract.json")

    def test_names_exactly_the_reachable_schemas(self):
        schemas = self.contract.get("schemas")
        self.assertIsInstance(schemas, dict, "contract.schemas must be an object")
        self.assertEqual(
            set(schemas),
            EXPECTED_SCHEMA_NAMES,
            "schemas must hold the in-scope request-body schemas plus every "
            "schema reachable from them by $ref, and nothing else",
        )

    def test_each_schema_matches_the_specification(self):
        schemas = self.contract["schemas"]
        for name in sorted(EXPECTED_SCHEMA_NAMES):
            expected_required, expected_props = EXPECTED_SCHEMAS[name]
            entry = schemas[name]
            with self.subTest(schema=name):
                self.assertIsInstance(entry, dict)
                self.assertEqual(entry.get("type"), "object")
                self.assertEqual(
                    entry.get("required"),
                    expected_required,
                    "%s.required must list the spec's required entries in order"
                    % name,
                )
                properties = entry.get("properties")
                self.assertIsInstance(properties, list, "%s.properties" % name)
                self.assertEqual(
                    [item.get("name") for item in properties],
                    [prop[0] for prop in expected_props],
                    "%s.properties must be in specification order" % name,
                )
                for item, (prop, kind, ref, items) in zip(properties, expected_props):
                    self.assertEqual(item.get("type"), kind, "%s.%s type" % (name, prop))
                    self.assertEqual(
                        item.get("schema"), ref, "%s.%s schema" % (name, prop)
                    )
                    if items is None:
                        self.assertIsNone(
                            item.get("items"), "%s.%s items" % (name, prop)
                        )
                    else:
                        got = item.get("items")
                        self.assertIsInstance(got, dict, "%s.%s items" % (name, prop))
                        self.assertEqual(got.get("type"), items["type"])
                        self.assertEqual(got.get("schema"), items["schema"])

    def test_declared_formats(self):
        schemas = self.contract["schemas"]
        declared = {
            ("ComponentUpgradeSpec", "correlationId"): "uuid",
            ("DepotSpec", "url"): "uri",
        }
        for schema_name, schema_entry in schemas.items():
            for item in schema_entry["properties"]:
                key = (schema_name, item["name"])
                self.assertEqual(
                    item.get("format"),
                    declared.get(key),
                    "%s.%s must record its declared format, or null when the "
                    "specification declares none" % key,
                )


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
        repository = entry.get("repository") or ""
        self.assertTrue(repository.startswith("https://"))
        self.assertEqual(repository.rstrip("/"), REPO_URL)
        self.assertEqual(
            sorted(entry.get("operation_ids") or []),
            sorted(OPERATION_IDS),
            "every operationId used must be recorded",
        )

    def assert_digest(self, value, length, guard, label, description):
        self.assertIsInstance(value, str, "%s must be a string" % label)
        normalised = value.strip().lower()
        self.assertTrue(
            re.fullmatch("[0-9a-f]{%d}" % length, normalised),
            "%s must be %d lowercase hex characters, got %r"
            % (label, length, value),
        )
        self.assertEqual(
            hashlib.sha256(normalised.encode("ascii")).hexdigest(),
            guard,
            "%s does not match the specification file at the recorded commit; "
            "%s" % (label, description),
        )

    def test_records_content_digests_of_the_retrieved_spec(self):
        entry = self.entry()
        self.assert_digest(
            entry.get("spec_blob_sha"),
            40,
            SPEC_BLOB_SHA_GUARD,
            "spec_blob_sha",
            "it must be the git blob sha of the specification file, computed "
            "from the bytes actually fetched",
        )
        self.assert_digest(
            entry.get("spec_sha256"),
            64,
            SPEC_SHA256_GUARD,
            "spec_sha256",
            "it must be the SHA-256 of the specification file's raw bytes",
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


class StandardLibraryOnlyTest(unittest.TestCase):
    def test_implementation_imports_only_the_standard_library_and_local_package(self):
        stdlib = os.path.realpath(sysconfig.get_path("stdlib"))
        third_party = {
            os.path.realpath(path)
            for path in (sysconfig.get_path("purelib"), sysconfig.get_path("platlib"))
            if path
        }

        def under(path, root):
            try:
                return os.path.commonpath((path, root)) == root
            except ValueError:
                return False

        for filename in ("contract.py", "client.py"):
            path = os.path.join(SRC_DIR, "vcf_lcm", filename)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".", 1)[0])

            for name in sorted(imported - {"vcf_lcm"}):
                with self.subTest(file=filename, module=name):
                    spec = importlib.util.find_spec(name)
                    self.assertIsNotNone(spec, "%s imports unavailable module %s" % (filename, name))
                    origin = getattr(spec, "origin", None)
                    if origin in ("built-in", "frozen"):
                        continue
                    self.assertIsNotNone(origin, "%s is not a standard-library module" % name)
                    resolved = os.path.realpath(origin)
                    self.assertTrue(under(resolved, stdlib), "%s is not in the standard library" % name)
                    self.assertFalse(
                        any(under(resolved, root) for root in third_party),
                        "%s imports third-party module %s" % (filename, name),
                    )


# ===========================================================================
# 2. wire behaviour against the contract-pinned loopback mock
# ===========================================================================


class RecordingTokenProvider:
    """Hands out access tokens and counts how often it was asked.

    A run that keeps asking for a fresh token is a run stuck in a refresh loop,
    so the provider gives up rather than letting the suite hang.
    """

    MAX_CALLS = 32

    def __init__(self, tokens):
        self._tokens = list(tokens)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls > self.MAX_CALLS:
            raise RuntimeError(
                "the token provider has been called %d times; a rejected "
                "request must be replayed at most once" % self.calls
            )
        return self._tokens[min(self.calls - 1, len(self._tokens) - 1)]


class MockServer:
    """Runs .protected/lcm_mock_server.py on loopback and exposes its log."""

    def __init__(self, expire_after=0, contract_path=CONTRACT_PATH):
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
                "--expire-after",
                str(expire_after),
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
        _, self.base_url, self.token, self.rotated_token = line.split(" ", 3)

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

    expire_after = 0

    def setUp(self):
        import vcf_lcm

        self.vcf_lcm = vcf_lcm
        self.contract = load_json(CONTRACT_PATH, "docs/contract.json")
        self.server = MockServer(expire_after=self.expire_after)
        self.addCleanup(self.server.stop)
        self.provider = RecordingTokenProvider(
            [self.server.token, self.server.rotated_token]
        )
        self.client = self.make_client(self.provider)

    def make_client(self, provider):
        return self.vcf_lcm.SddcLcmClient(
            self.server.base_url,
            provider,
            contract_path=CONTRACT_PATH,
            poll_interval=0.01,
            timeout=10.0,
        )

    # -- helpers ------------------------------------------------------------
    def requests(self):
        return self.server.requests()

    def contracted_matchers(self):
        matchers = []
        for operation_id, entry in self.contract["operations"].items():
            pattern = re.sub(r"\{(\w+)\}", "[^/]+", entry["path"])
            matchers.append((operation_id, re.compile("^" + pattern + "$")))
        return matchers

    def assert_only_contracted_paths(self, entries):
        matchers = self.contracted_matchers()
        templates = ", ".join(
            entry["path"] for entry in self.contract["operations"].values()
        )
        for entry in entries:
            self.assertTrue(
                any(regex.match(entry["path"]) for _, regex in matchers),
                "request %d went to %r, which no contracted operation serves "
                "(contracted templates: %s)" % (entry["seq"], entry["path"], templates),
            )

    def assert_bearer(self, entry, token):
        headers = entry["headers_lower"]
        self.assertIn(
            "authorization",
            headers,
            "request %d (%s) has no Authorization header"
            % (entry["seq"], entry["path"]),
        )
        self.assertEqual(
            headers["authorization"],
            "Bearer " + token,
            "the contract's security scheme is a JWT bearer token",
        )

    def header_names(self, entry):
        return {name.lower() for name, _ in entry["headers"]}

    def call_with_deadline(self, func, seconds=30.0):
        """Run ``func`` on a watchdog so a retry loop fails instead of hanging."""
        box = {}

        def runner():
            try:
                box["value"] = func()
            except BaseException as exc:  # re-raised on the calling thread
                box["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(seconds)
        if thread.is_alive():
            self.fail(
                "the call did not return within %.0fs -- a request rejected "
                "with 401 must be replayed at most once, not retried forever"
                % seconds
            )
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def summary(self, entries):
        return [(e["method"], e["path"], e["query"], e["status"]) for e in entries]

    def apply_kwargs(self, **overrides):
        kwargs = {
            "target_version": TARGET_VERSION,
            "depot_url": MANIFEST_URL,
        }
        kwargs.update(overrides)
        return kwargs


class ContractDrivenMutationTest(unittest.TestCase):
    """A fixed canonical contract cannot expose hard-coded target metadata."""

    def test_method_target_parameters_and_auth_scheme_come_from_the_contract(self):
        import vcf_lcm

        contract = copy.deepcopy(load_json(CONTRACT_PATH, "docs/contract.json"))
        contract["security"]["scheme"] = "Token"

        health = contract["operations"][OP_HEALTH]
        health["method"] = "POST"
        health["path"] = "/contract-driven/health"
        health["spec_path_key"] = health["path"]

        action = contract["operations"][OP_ACTION]
        action["method"] = "PUT"
        action["path"] = "/contract-driven/components/{assetId}"
        action["spec_path_key"] = action["path"]
        action["path_parameters"][0]["name"] = "assetId"
        action["query_parameters"][0]["name"] = "verb"

        get_task = contract["operations"][OP_GET_TASK]
        get_task["path"] = "/contract-driven/jobs/{jobId}"
        get_task["spec_path_key"] = get_task["path"]
        get_task["path_parameters"][0]["name"] = "jobId"

        retry = contract["operations"][OP_RETRY_TASK]
        retry["method"] = "PATCH"
        retry["path"] = get_task["path"]
        retry["spec_path_key"] = retry["path"] + "?mode=resume"
        retry["path_parameters"][0]["name"] = "jobId"
        retry["query_parameters"][0]["name"] = "mode"
        retry["query_parameters"][0]["fixed_value"] = "resume"

        handle = tempfile.NamedTemporaryFile(
            mode="w", prefix="lcm-mutated-contract-", suffix=".json", delete=False
        )
        try:
            json.dump(contract, handle)
        finally:
            handle.close()
        self.addCleanup(os.unlink, handle.name)

        server = MockServer(contract_path=handle.name)
        self.addCleanup(server.stop)
        provider = RecordingTokenProvider([server.token])
        client = vcf_lcm.SddcLcmClient(
            server.base_url,
            provider,
            contract=contract,
            poll_interval=0.0,
            timeout=10.0,
        )

        self.assertEqual(client.get_health().get("status"), "HEALTHY")
        accepted = client.perform_component_action(
            COMPONENT_RETRY, "apply", TARGET_VERSION, MANIFEST_URL
        )
        self.assertEqual(accepted.get("id"), TASK_RETRY)
        with self.assertRaises(vcf_lcm.TaskFailedError):
            client.await_task(TASK_RETRY)
        client.retry_task(TASK_RETRY)

        entries = server.requests()
        self.assertEqual(
            [(e["method"], e["target"]) for e in entries],
            [
                ("POST", "/contract-driven/health"),
                (
                    "PUT",
                    "/contract-driven/components/%s?verb=apply" % COMPONENT_RETRY,
                ),
                ("GET", "/contract-driven/jobs/%s" % TASK_RETRY),
                ("GET", "/contract-driven/jobs/%s" % TASK_RETRY),
                (
                    "PATCH",
                    "/contract-driven/jobs/%s?mode=resume" % TASK_RETRY,
                ),
            ],
        )
        self.assertEqual(entries[1]["headers_lower"].get("authorization"),
                         "Token " + server.token)


class ExpiringTokenWorkflowTest(WireShapeTestCase):
    """The headline scenario: the token expires while the task is polled."""

    expire_after = 3

    def test_run_survives_a_mid_run_token_expiry(self):
        health = self.client.get_health()
        self.assertEqual(health.get("status"), "HEALTHY")

        resolved = self.client.resolve_depot_components(
            DEPOT_FQDN,
            DEPOT_CERT,
            [("vcf-ops", TARGET_VERSION), ("nsx", None)],
        )
        self.assertEqual(
            [item["component"] for item in resolved["componentVersions"]],
            ["vcf-ops", "nsx"],
        )
        self.assertEqual(
            resolved["componentVersions"][1]["version"],
            "9.1.0.0000.24177344",
            "the component sent without a version resolves to the depot's latest",
        )

        terminal = self.client.apply_component_upgrade(
            COMPONENT_OK,
            perform_backup=True,
            correlation_id=CORRELATION_ID,
            **self.apply_kwargs()
        )
        self.assertEqual(terminal.get("id"), TASK_OK)
        self.assertEqual(terminal.get("status"), "SUCCEEDED")

        entries = self.requests()
        self.assert_only_contracted_paths(entries)
        self.assertEqual(
            self.summary(entries),
            [
                ("GET", PATH_HEALTH, "", 200),
                ("POST", PATH_DEPOT_COMPONENTS, "", 200),
                ("POST", "/v1/components/%s" % COMPONENT_OK, "action=apply", 202),
                ("GET", "/v1/tasks/%s" % TASK_OK, "", 200),
                ("GET", "/v1/tasks/%s" % TASK_OK, "", 401),
                ("GET", "/v1/tasks/%s" % TASK_OK, "", 200),
                ("GET", "/v1/tasks/%s" % TASK_OK, "", 200),
            ],
            "expected: health, resolve, start the upgrade, poll; the fourth "
            "authenticated request is rejected because the token expired, and "
            "that one request is replayed with a fresh token",
        )

    def test_the_rejected_request_is_replayed_verbatim(self):
        self.client.get_health()
        self.client.resolve_depot_components(
            DEPOT_FQDN, DEPOT_CERT, [("vcf-ops", TARGET_VERSION), ("nsx", None)]
        )
        self.client.apply_component_upgrade(
            COMPONENT_OK, correlation_id=CORRELATION_ID, **self.apply_kwargs()
        )

        entries = self.requests()
        rejected = [e for e in entries if e["status"] == 401]
        self.assertEqual(
            len(rejected), 1, "exactly one request should have hit the expired token"
        )
        rejected = rejected[0]
        replay = entries[entries.index(rejected) + 1]

        self.assertEqual(
            (replay["method"], replay["target"], replay["body_raw"]),
            (rejected["method"], rejected["target"], rejected["body_raw"]),
            "the replay must be the same request, byte for byte, not a "
            "differently-built one",
        )
        self.assert_bearer(rejected, self.server.token)
        self.assert_bearer(replay, self.server.rotated_token)
        self.assertEqual(replay["status"], 200)

    def test_token_is_fetched_once_and_refreshed_once(self):
        self.client.get_health()
        self.client.resolve_depot_components(
            DEPOT_FQDN, DEPOT_CERT, [("vcf-ops", TARGET_VERSION)]
        )
        self.client.apply_component_upgrade(COMPONENT_OK, **self.apply_kwargs())
        self.assertEqual(
            self.provider.calls,
            2,
            "the token provider is called once up front and once more to "
            "refresh the expired token -- not before every request",
        )

    def test_accepted_work_is_not_reissued(self):
        self.client.get_health()
        self.client.resolve_depot_components(
            DEPOT_FQDN, DEPOT_CERT, [("vcf-ops", TARGET_VERSION)]
        )
        self.client.apply_component_upgrade(COMPONENT_OK, **self.apply_kwargs())

        entries = self.requests()
        starts = [
            e
            for e in entries
            if e["method"] == "POST" and e["path"].startswith("/v1/components/")
        ]
        self.assertEqual(
            len(starts),
            1,
            "the upgrade must be started exactly once; refreshing the token "
            "must not re-run the workflow from the top",
        )
        polled = {e["path"] for e in entries if e["path"].startswith("/v1/tasks/")}
        self.assertEqual(
            polled,
            {"/v1/tasks/%s" % TASK_OK},
            "polling must resume on the same task id after the refresh",
        )


class UnauthenticatedOperationTest(WireShapeTestCase):
    def test_health_carries_no_authorization_and_costs_no_token(self):
        self.client.get_health()
        entries = self.requests()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["method"], "GET")
        self.assertEqual(entry["path"], PATH_HEALTH)
        self.assertEqual(entry["query"], "")
        self.assertNotIn(
            "authorization",
            self.header_names(entry),
            "the specification gives %s an empty security requirement, so the "
            "request must not present a bearer token" % OP_HEALTH,
        )
        self.assertEqual(
            self.provider.calls,
            0,
            "an unauthenticated operation must not cause a token to be fetched",
        )
        self.assertEqual(entry["body_raw"], "")
        self.assertNotIn("content-type", self.header_names(entry))


class RequestBodyShapeTest(WireShapeTestCase):
    def test_resolve_body_carries_the_supplied_values(self):
        self.client.resolve_depot_components(
            DEPOT_FQDN,
            DEPOT_CERT,
            [("vcf-ops", TARGET_VERSION), ("nsx", None)],
            version="9.1.0.0",
        )
        entry = self.requests()[0]
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], PATH_DEPOT_COMPONENTS)
        self.assertEqual(
            entry["body_json"],
            {
                "fleetDepotSpec": {"fqdn": DEPOT_FQDN, "certificate": DEPOT_CERT},
                "version": "9.1.0.0",
                "componentVersions": [
                    {"component": "vcf-ops", "version": TARGET_VERSION},
                    {"component": "nsx"},
                ],
            },
            "the body is the DepotComponentsSpec the caller asked for -- the "
            "component with no version carries no version property",
        )
        headers = entry["headers_lower"]
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(
            headers.get("content-length"),
            str(len(entry["body_raw"].encode("utf-8"))),
            "Content-Length must match the body actually sent",
        )

    def test_apply_body_carries_the_supplied_values(self):
        self.client.perform_component_action(
            COMPONENT_OK,
            "apply",
            TARGET_VERSION,
            MANIFEST_URL,
            depot_certificates=[DEPOT_CERT],
            perform_backup=True,
            correlation_id=CORRELATION_ID,
        )
        entry = self.requests()[0]
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], "/v1/components/%s" % COMPONENT_OK)
        self.assertEqual(entry["query"], "action=apply")
        self.assertEqual(
            entry["body_json"],
            {
                "componentSpec": {
                    "software": {"version": TARGET_VERSION},
                    "depot": {"url": MANIFEST_URL, "certificate": [DEPOT_CERT]},
                },
                "lcmPlatformSpec": {"performBackup": True},
                "correlationId": CORRELATION_ID,
            },
        )
        self.assertEqual(
            entry["headers_lower"].get("x-correlation-id"),
            CORRELATION_ID,
            "a supplied correlation id sets the optional header too",
        )


class OptionalFieldOmissionTest(WireShapeTestCase):
    def test_unset_body_properties_are_omitted_not_sent_empty(self):
        self.client.perform_component_action(
            COMPONENT_OK, "apply", TARGET_VERSION, MANIFEST_URL
        )
        entry = self.requests()[0]
        self.assertEqual(
            entry["body_json"],
            {
                "componentSpec": {
                    "software": {"version": TARGET_VERSION},
                    "depot": {"url": MANIFEST_URL},
                }
            },
            "with nothing optional supplied the body carries only what the "
            "specification requires",
        )
        for absent in ("lcmPlatformSpec", "correlationId", "certificate", "null"):
            self.assertNotIn(
                absent,
                entry["body_raw"],
                "%r must not appear in the serialised body; an unset optional "
                "field is omitted, not sent empty" % absent,
            )

    def test_unset_nested_object_is_omitted_whole(self):
        self.client.perform_component_action(
            COMPONENT_OK, "apply", TARGET_VERSION, MANIFEST_URL, perform_backup=None
        )
        body = self.requests()[0]["body_json"]
        self.assertNotIn(
            "lcmPlatformSpec",
            body,
            "the platform spec object requires performBackup, so with no "
            "backup preference the whole object is omitted -- not sent as {}",
        )

    def test_false_is_a_value_and_is_sent(self):
        self.client.perform_component_action(
            COMPONENT_OK, "apply", TARGET_VERSION, MANIFEST_URL, perform_backup=False
        )
        body = self.requests()[0]["body_json"]
        self.assertEqual(
            body.get("lcmPlatformSpec"),
            {"performBackup": False},
            "False is a supplied value; only None means the property is unset",
        )

    def test_empty_string_is_a_value_and_is_sent(self):
        self.client.resolve_depot_components(
            DEPOT_FQDN, DEPOT_CERT, [("nsx", "")], version=""
        )
        body = self.requests()[0]["body_json"]
        self.assertEqual(body.get("version"), "")
        self.assertEqual(body["componentVersions"][0].get("version"), "")

    def test_unset_header_is_absent_not_empty(self):
        self.client.perform_component_action(
            COMPONENT_OK, "apply", TARGET_VERSION, MANIFEST_URL, perform_backup=True
        )
        entry = self.requests()[0]
        self.assertNotIn(
            "x-correlation-id",
            self.header_names(entry),
            "with no correlation id supplied the X-Correlation-Id header must "
            "be absent from the request, not present with an empty value",
        )

    def test_unset_optional_version_leaves_no_trace(self):
        self.client.resolve_depot_components(
            DEPOT_FQDN, DEPOT_CERT, [("nsx", None)]
        )
        entry = self.requests()[0]
        self.assertEqual(
            entry["body_json"],
            {
                "fleetDepotSpec": {"fqdn": DEPOT_FQDN, "certificate": DEPOT_CERT},
                "componentVersions": [{"component": "nsx"}],
            },
        )
        self.assertNotIn(
            "version",
            entry["body_raw"],
            "neither the request-level version nor the component version was "
            "supplied, so the string 'version' must not appear at all",
        )


class NoBodyOperationsTest(WireShapeTestCase):
    """Operations whose contract entry has request_body: null send no payload."""

    #: (method, path prefix) of the operations that do declare a request body.
    WITH_BODY = (
        ("POST", PATH_DEPOT_COMPONENTS),
        ("POST", "/v1/components/"),
    )

    def declares_a_body(self, entry):
        return any(
            entry["method"] == method and entry["path"].startswith(prefix)
            for method, prefix in self.WITH_BODY
        )

    def test_bodyless_operations_carry_no_payload(self):
        self.client.get_health()
        with self.assertRaises(self.vcf_lcm.TaskFailedError):
            self.client.apply_component_upgrade(COMPONENT_RETRY, **self.apply_kwargs())
        self.client.retry_task(TASK_RETRY)

        checked = 0
        for entry in self.requests():
            if self.declares_a_body(entry):
                continue
            checked += 1
            with self.subTest(seq=entry["seq"], path=entry["path"]):
                self.assertEqual(
                    entry["body_raw"],
                    "",
                    "%s %s carried a body but its operation declares none"
                    % (entry["method"], entry["path"]),
                )
                self.assertNotIn(
                    "content-type",
                    self.header_names(entry),
                    "%s %s sent a Content-Type header with no body"
                    % (entry["method"], entry["path"]),
                )
        self.assertGreaterEqual(
            checked, 4, "the health read, the task polls and the retry POST"
        )


class RetryTaskTest(WireShapeTestCase):
    def test_failed_task_is_resumed_not_restarted(self):
        with self.assertRaises(self.vcf_lcm.TaskFailedError) as caught:
            self.client.apply_component_upgrade(COMPONENT_RETRY, **self.apply_kwargs())
        failed = caught.exception.task
        self.assertIsInstance(failed, dict, "TaskFailedError must carry the task")
        self.assertEqual(failed.get("id"), TASK_RETRY)
        self.assertEqual(failed.get("status"), "FAILED")
        self.assertIs(failed.get("retriable"), True)

        self.client.retry_task(TASK_RETRY)
        terminal = self.client.await_task(TASK_RETRY, poll_interval=0.01, timeout=10.0)
        self.assertEqual(terminal.get("status"), "SUCCEEDED")

        entries = self.requests()
        self.assert_only_contracted_paths(entries)
        starts = [
            e
            for e in entries
            if e["method"] == "POST" and e["path"].startswith("/v1/components/")
        ]
        self.assertEqual(
            len(starts),
            1,
            "retrying a failed task must resume it, not start the upgrade again",
        )
        retries = [
            e
            for e in entries
            if e["method"] == "POST" and e["path"] == "/v1/tasks/%s" % TASK_RETRY
        ]
        self.assertEqual(len(retries), 1)
        self.assertEqual(
            retries[0]["query"],
            "action=retry",
            "the retry query value is fixed by the specification's paths: key",
        )
        self.assertEqual(retries[0]["status"], 200)

    def test_stalled_task_times_out_after_one_immediate_read(self):
        with self.assertRaises(self.vcf_lcm.TaskTimeoutError) as caught:
            self.client.apply_component_upgrade(
                COMPONENT_STALL, poll_interval=60.0, timeout=0.0, **self.apply_kwargs()
            )
        self.assertEqual(caught.exception.task.get("status"), "RUNNING")
        entries = self.requests()
        self.assert_only_contracted_paths(entries)
        polls = [e for e in entries if e["path"] == "/v1/tasks/%s" % TASK_STALL]
        self.assertEqual(
            len(polls),
            1,
            "await_task must read immediately and evaluate the timeout after "
            "that non-terminal read, without sleeping first",
        )
        starts = [
            e
            for e in entries
            if e["method"] == "POST" and e["path"].startswith("/v1/components/")
        ]
        self.assertEqual(len(starts), 1)


class TerminalOutcomeTest(WireShapeTestCase):
    def test_canceled_task_is_terminal_but_not_successful(self):
        with self.assertRaises(self.vcf_lcm.TaskFailedError) as caught:
            self.client.apply_component_upgrade(COMPONENT_CANCEL, **self.apply_kwargs())
        task = caught.exception.task
        self.assertEqual(task.get("id"), TASK_CANCEL)
        self.assertEqual(task.get("status"), "CANCELED")
        polls = [
            e for e in self.requests() if e["path"] == "/v1/tasks/%s" % TASK_CANCEL
        ]
        self.assertEqual(len(polls), 2)


class ErrorHandlingTest(WireShapeTestCase):
    def test_not_found_surfaces_as_api_error_without_refreshing(self):
        with self.assertRaises(self.vcf_lcm.LcmApiError) as caught:
            self.client.get_task(UNKNOWN_TASK)
        error = caught.exception
        self.assertEqual(getattr(error, "status_code", None), 404)
        payload = getattr(error, "payload", None)
        self.assertIsInstance(payload, dict, "the ErrorResponse body must be decoded")
        self.assertEqual(payload.get("code"), "NOT_FOUND")
        self.assertEqual(
            self.provider.calls, 1, "only a 401 may trigger a token refresh"
        )

    def test_a_second_401_is_not_retried_again(self):
        provider = RecordingTokenProvider(["not-the-right-token"])
        client = self.make_client(provider)
        with self.assertRaises(self.vcf_lcm.TokenRefreshError) as caught:
            self.call_with_deadline(
                lambda: client.resolve_depot_components(
                    DEPOT_FQDN, DEPOT_CERT, [("vcf-ops", TARGET_VERSION)]
                )
            )
        self.assertEqual(getattr(caught.exception, "status_code", None), 401)
        self.assertEqual(getattr(caught.exception, "payload", {}).get("code"),
                         "UNAUTHORIZED")
        self.assertTrue(
            isinstance(caught.exception, self.vcf_lcm.LcmApiError),
            "TokenRefreshError is an LcmApiError",
        )
        self.assertEqual(
            provider.calls, 2, "one initial fetch and exactly one refresh attempt"
        )
        entries = self.requests()
        self.assertEqual(
            [e["status"] for e in entries],
            [401, 401],
            "the request is replayed once and then given up on",
        )
        self.assertEqual(entries[0]["body_raw"], entries[1]["body_raw"])

    def test_unauthenticated_401_is_an_api_error_without_a_refresh(self):
        payload = json.dumps({"code": "UNAUTHORIZED"}).encode("utf-8")

        class UnauthorizedHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = HTTPServer(("127.0.0.1", 0), UnauthorizedHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        provider = RecordingTokenProvider(["must-not-be-fetched"])
        client = self.vcf_lcm.SddcLcmClient(
            "http://127.0.0.1:%d" % httpd.server_address[1],
            provider,
            contract=self.contract,
        )
        try:
            with self.assertRaises(self.vcf_lcm.LcmApiError) as caught:
                client.get_health()
        finally:
            httpd.shutdown()
            thread.join(10)
            httpd.server_close()

        self.assertNotIsInstance(caught.exception, self.vcf_lcm.TokenRefreshError)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.payload.get("code"), "UNAUTHORIZED")
        self.assertEqual(provider.calls, 0)


class ContractDrivesTheClientTest(WireShapeTestCase):
    def test_targets_are_built_from_the_contract(self):
        operations = self.contract["operations"]
        self.client.perform_component_action(
            COMPONENT_OK, "apply", TARGET_VERSION, MANIFEST_URL
        )
        entry = self.requests()[0]
        action = operations[OP_ACTION]
        self.assertEqual(entry["method"], action["method"])
        self.assertEqual(
            entry["path"], action["path"].replace("{componentId}", COMPONENT_OK)
        )
        self.assertEqual(
            entry["query"],
            "%s=apply" % action["query_parameters"][0]["name"],
            "the query parameter name comes from the contract",
        )

    def test_fixed_query_value_comes_from_the_contract(self):
        with self.assertRaises(self.vcf_lcm.TaskFailedError):
            self.client.apply_component_upgrade(COMPONENT_RETRY, **self.apply_kwargs())
        self.client.retry_task(TASK_RETRY)
        retry = self.contract["operations"][OP_RETRY_TASK]
        fixed = retry["query_parameters"][0]
        entry = [
            e
            for e in self.requests()
            if e["method"] == "POST" and e["path"].startswith("/v1/tasks/")
        ][0]
        self.assertEqual(
            entry["target"],
            "%s?%s=%s"
            % (
                retry["path"].replace("{taskId}", TASK_RETRY),
                fixed["name"],
                fixed["fixed_value"],
            ),
        )

    def test_only_contracted_operations_are_ever_called(self):
        self.client.get_health()
        self.client.apply_component_upgrade(COMPONENT_OK, **self.apply_kwargs())
        entries = self.requests()
        self.assert_only_contracted_paths(entries)
        self.assertEqual(
            [e for e in entries if e["status"] in (404, 405)],
            [],
            "no request may probe a path the contract does not name",
        )


if __name__ == "__main__":
    unittest.main()
