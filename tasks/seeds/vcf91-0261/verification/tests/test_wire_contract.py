"""Protected verification for the VCF Operations alert-collection client.

Three things are checked:

1. ``docs/contract.json`` and ``docs/official_sources.json`` match the published
   specification ``specifications/vcf-operations/vcf-operations-openapi.json``
   in ``vmware/vcf-api-specs`` at the pinned commit.
2. The client, driven against a loopback mock pinned to that contract, puts the
   exact expected bytes on the wire -- including omitting optional fields the
   caller left unset rather than sending them empty.
3. A paginated collection is retrieved completely, deduplicated the way a
   shifting collection demands, and emitted in the contracted stable order.

No VMware endpoint is contacted.  Every request goes to 127.0.0.1.

PROTECTED: do not modify.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
CONTRACT_PATH = os.path.join(DOCS_DIR, "contract.json")
SOURCES_PATH = os.path.join(DOCS_DIR, "official_sources.json")

for path in (HERE, SRC_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

from vcfops_mock_server import (  # noqa: E402  (path is set up above)
    ALERT_ROWS,
    DUPLICATED_ALERT_ID,
    RESOURCE_IDS,
    UNUSED_RESOURCE_ID,
    MockServer,
)

# --- values pinned to the published specification ---------------------------

REPO_URL = "https://github.com/vmware/vcf-api-specs"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_LICENSE = "Apache-2.0"

# The task pins the exact commit at which the specification must be read.  The
# content digests below additionally pin the retrieved bytes.
SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
# The two content digests are the proof that the specification was really
# retrieved, so they are not written out here for the taking: what is pinned is
# the SHA-256 *of each expected digest string*.  Fetch the file and the digests
# fall out; they cannot be recalled from memory.
SPEC_BLOB_SHA_GUARD = (
    "f17e866f5e320064620263daffef44d55b63897b3bf627d5fd0db21fd6e7b4f5"
)
SPEC_SHA256_GUARD = (
    "81eb70308f1997202eccf274d10b2c65547b50204d5ad283b4627965a077df49"
)

OPENAPI_VERSION = "3.0.1"
INFO_VERSION = "9.1.0.0"
BASE_PATH = "/suite-api"

OP_ACQUIRE = "acquireToken"
OP_ALERTS = "getAlerts"
OP_RELEASE = "releaseToken"
OPERATION_IDS = (OP_ACQUIRE, OP_ALERTS, OP_RELEASE)

PATH_ACQUIRE = "/api/auth/token/acquire"
PATH_ALERTS = "/api/alerts"
PATH_RELEASE = "/api/auth/token/release"

SECURITY_SCHEME_NAME = "Token-based-authorization"
TOKEN_PREFIX = "OpsToken"
JSON_MEDIA_TYPE = "application/json"

EXPECTED_SECURITY_SCHEME = {
    "name": SECURITY_SCHEME_NAME,
    "type": "apiKey",
    "in": "header",
    "headerName": "Authorization",
    "tokenPrefix": TOKEN_PREFIX,
}

EXPECTED_OPERATIONS = {
    OP_ACQUIRE: {
        "method": "POST",
        "path": PATH_ACQUIRE,
        "security": [],
        "queryParameters": [],
        "requestBody": {
            "required": True,
            "contentType": JSON_MEDIA_TYPE,
            "schema": "username-password",
        },
        "responseSchema": "auth-token",
    },
    OP_ALERTS: {
        "method": "GET",
        "path": PATH_ALERTS,
        "security": [SECURITY_SCHEME_NAME],
        "queryParameters": [
            {
                "name": "id",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                },
            },
            {
                "name": "resourceId",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                },
            },
            {
                "name": "page",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "format": "int32", "default": 0},
            },
            {
                "name": "pageSize",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "format": "int32", "default": 1000},
            },
        ],
        "requestBody": None,
        "responseSchema": "alerts",
    },
    OP_RELEASE: {
        "method": "POST",
        "path": PATH_RELEASE,
        "security": [SECURITY_SCHEME_NAME],
        "queryParameters": [],
        "requestBody": None,
        "responseSchema": None,
    },
}

EXPECTED_SCHEMAS = {
    "username-password": {
        "type": "object",
        "required": ["password", "username"],
        "properties": {
            "authSource": {"type": "string"},
            "password": {"type": "string"},
            "username": {"type": "string"},
        },
    },
    "auth-token": {
        "type": "object",
        "required": ["token", "validity"],
        "properties": {
            "expiresAt": {"type": "string"},
            "roles": {"type": "array", "items": {"type": "string"}},
            "token": {"type": "string"},
            "validity": {"type": "integer", "format": "int64"},
        },
    },
    "alerts": {
        "type": "object",
        "properties": {
            "alerts": {"type": "array", "items": {"$ref": "alert"}},
            "links": {"type": "array", "items": {"$ref": "link"}},
            "pageInfo": {"$ref": "page-info"},
        },
    },
    "page-info": {
        "type": "object",
        "properties": {
            "page": {"type": "integer", "format": "int32", "minimum": 0},
            "pageSize": {"type": "integer", "format": "int32", "minimum": 1},
            "sortBy": {"type": "string"},
            "sortOrder": {"type": "string"},
            "totalCount": {"type": "integer", "format": "int32"},
        },
    },
    "link": {
        "type": "object",
        "required": ["href"],
        "properties": {
            "description": {"type": "string"},
            "href": {"type": "string"},
            "name": {"type": "string"},
            "rel": {
                "type": "string",
                "enum": ["NEXT", "PREVIOUS", "START", "END", "RELATED", "SELF"],
            },
        },
    },
}

# --- the run the suite drives ----------------------------------------------

USERNAME = "svc-fleet-collector"
PASSWORD = "Do-not-log-me-42"
AUTH_SOURCE = "Imported LDAP Server"
PAGE_SIZE = 10
FIRST_TOKEN = "opstoken-1"

DEADLINE_SECONDS = 60.0


def load_json(path, label):
    if not os.path.exists(path):
        raise AssertionError("%s is missing (expected at %s)" % (label, path))
    with open(path, encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise AssertionError("%s is not valid JSON: %s" % (label, exc))


def contract_document():
    return load_json(CONTRACT_PATH, "docs/contract.json")


def expected_alerts(rows):
    """Complete, deduplicated first-seen-wins, in the contracted order."""
    first_seen = {}
    for row in rows:
        first_seen.setdefault(row["alertId"], row)
    return sorted(
        first_seen.values(),
        key=lambda alert: (-alert["startTimeUTC"], alert["alertId"]),
    )


def with_deadline(func, seconds=DEADLINE_SECONDS):
    """Run ``func`` on a worker so a hung client fails instead of wedging."""
    box = {}

    def target():
        try:
            box["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - reported below
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise AssertionError(
            "the client did not finish within %.0f seconds" % seconds
        )
    if "error" in box:
        raise box["error"]
    return box.get("value")


class Run:
    """One client run against a fresh contract-pinned mock."""

    def __init__(self, action, contract=None, alerts=ALERT_ROWS,
                 page_windows=None):
        self.directory = tempfile.mkdtemp(prefix="vcfops-mock-")
        self.log_path = os.path.join(self.directory, "requests.jsonl")
        self.server = MockServer(
            contract if contract is not None else contract_document(),
            self.log_path,
            alerts=alerts,
            page_windows=page_windows,
        )
        self.error = None
        self.result = None
        try:
            try:
                self.result = with_deadline(lambda: action(self.server))
            except AssertionError:
                raise
            except Exception as exc:  # noqa: BLE001 - inspected by the tests
                self.error = exc
        finally:
            self.requests = self.server.requests()
            self.server.stop()

    def raise_if_failed(self):
        if self.error is not None:
            raise AssertionError(
                "the client raised %s: %s"
                % (type(self.error).__name__, self.error)
            )

    def by_operation(self, operation_id):
        return [
            entry for entry in self.requests if entry["operation_id"] == operation_id
        ]

    def summary(self):
        return [
            "%s %s -> %s" % (entry["method"], entry["target"], entry["status"])
            for entry in self.requests
        ]


def library_run(page_size=PAGE_SIZE, auth_source=None, resource_ids=None,
                alert_ids=None, contract=None, release=True):
    """Drive ``OperationsClient`` directly and return the alerts it produced."""

    def action(server):
        from vcfops_alerts import OperationsClient

        kwargs = {}
        if contract is not None:
            kwargs["contract"] = contract
        client = OperationsClient(
            server.base_url,
            username=USERNAME,
            password=PASSWORD,
            auth_source=auth_source,
            **kwargs
        )
        if release:
            with client:
                return client.fetch_alerts(
                    page_size=page_size,
                    resource_ids=resource_ids,
                    alert_ids=alert_ids,
                )
        return client.fetch_alerts(
            page_size=page_size, resource_ids=resource_ids, alert_ids=alert_ids
        )

    return action


class ContractDerivationTest(unittest.TestCase):
    """docs/contract.json says what the specification says."""

    @classmethod
    def setUpClass(cls):
        cls.contract = contract_document()

    def operations(self):
        operations = self.contract.get("operations")
        self.assertIsInstance(
            operations, dict, "contract['operations'] must be a JSON object"
        )
        return operations

    def operation(self, operation_id):
        operations = self.operations()
        self.assertIn(
            operation_id, operations, "the contract does not name %s" % operation_id
        )
        return operations[operation_id]

    def test_source_block_identifies_the_specification(self):
        source = self.contract.get("source")
        self.assertIsInstance(source, dict, "contract['source'] must be an object")
        self.assertEqual(source.get("repository"), REPO_URL)
        self.assertEqual(source.get("specPath"), SPEC_PATH)
        self.assertEqual(
            source.get("commit"),
            SPEC_COMMIT,
            "contract['source']['commit'] must be the revision the "
            "specification was read from",
        )
        self.assertEqual(
            source.get("openapi"),
            OPENAPI_VERSION,
            "the OpenAPI version of the document, from its `openapi` field",
        )
        self.assertEqual(
            source.get("infoVersion"),
            INFO_VERSION,
            "the API version of the document, from `info.version`",
        )

    def test_base_path_comes_from_the_servers_block(self):
        self.assertEqual(
            self.contract.get("basePath"),
            BASE_PATH,
            "the specification's servers[0].url is the base path every "
            "operation hangs off",
        )

    def test_security_scheme_matches_the_specification(self):
        self.assertEqual(
            self.contract.get("securityScheme"), EXPECTED_SECURITY_SCHEME
        )

    def test_contract_names_exactly_the_three_operations(self):
        self.assertEqual(sorted(self.operations()), sorted(OPERATION_IDS))

    def test_acquire_token_operation(self):
        operation = self.operation(OP_ACQUIRE)
        expected = EXPECTED_OPERATIONS[OP_ACQUIRE]
        self.assertEqual(operation.get("method"), expected["method"])
        self.assertEqual(operation.get("path"), expected["path"])
        self.assertEqual(operation.get("requestBody"), expected["requestBody"])
        self.assertEqual(operation.get("responseSchema"), expected["responseSchema"])
        self.assertEqual(operation.get("queryParameters"), [])

    def test_acquire_token_overrides_the_document_security_requirement(self):
        operation = self.operation(OP_ACQUIRE)
        self.assertEqual(
            operation.get("security"),
            [],
            "the specification gives acquireToken its own empty security "
            "requirement, overriding the document-wide one",
        )

    def test_get_alerts_operation(self):
        operation = self.operation(OP_ALERTS)
        expected = EXPECTED_OPERATIONS[OP_ALERTS]
        self.assertEqual(operation.get("method"), expected["method"])
        self.assertEqual(operation.get("path"), expected["path"])
        self.assertEqual(operation.get("security"), expected["security"])
        self.assertEqual(operation.get("requestBody"), None)
        self.assertEqual(operation.get("responseSchema"), expected["responseSchema"])

    def test_get_alerts_query_parameters(self):
        operation = self.operation(OP_ALERTS)
        self.assertEqual(
            operation.get("queryParameters"),
            EXPECTED_OPERATIONS[OP_ALERTS]["queryParameters"],
            "record the four query parameters in specification order, with "
            "their declared schemas and defaults",
        )

    def test_release_token_operation(self):
        operation = self.operation(OP_RELEASE)
        expected = EXPECTED_OPERATIONS[OP_RELEASE]
        self.assertEqual(operation.get("method"), expected["method"])
        self.assertEqual(operation.get("path"), expected["path"])
        self.assertEqual(operation.get("security"), expected["security"])
        self.assertEqual(operation.get("queryParameters"), [])
        self.assertEqual(operation.get("requestBody"), None)
        self.assertEqual(operation.get("responseSchema"), None)

    def test_contract_names_exactly_the_five_schemas(self):
        schemas = self.contract.get("schemas")
        self.assertIsInstance(schemas, dict, "contract['schemas'] must be an object")
        self.assertEqual(sorted(schemas), sorted(EXPECTED_SCHEMAS))

    def test_each_schema_matches_the_specification(self):
        schemas = self.contract.get("schemas") or {}
        for name, expected in sorted(EXPECTED_SCHEMAS.items()):
            with self.subTest(schema=name):
                self.assertEqual(
                    schemas.get(name),
                    expected,
                    "schema %r does not match the specification (descriptions "
                    "and xml annotations are dropped, everything else is kept)"
                    % name,
                )


class OfficialSourcesTest(unittest.TestCase):
    """docs/official_sources.json proves where the contract came from."""

    @classmethod
    def setUpClass(cls):
        cls.document = load_json(SOURCES_PATH, "docs/official_sources.json")
        cls.raw = open(SOURCES_PATH, encoding="utf-8").read()

    def entry(self):
        sources = self.document.get("sources")
        self.assertIsInstance(
            sources, list, "official_sources.json must have a 'sources' list"
        )
        self.assertEqual(len(sources), 1, "record exactly one source")
        self.assertIsInstance(sources[0], dict)
        return sources[0]

    def test_records_repository_licence_and_spec_path(self):
        entry = self.entry()
        self.assertEqual(entry.get("repository"), REPO_URL)
        self.assertEqual(entry.get("license"), SPEC_LICENSE)
        self.assertEqual(entry.get("specPath"), SPEC_PATH)

    def test_records_the_commit_the_specification_was_read_from(self):
        entry = self.entry()
        self.assertEqual(entry.get("commit"), SPEC_COMMIT)

    def test_records_every_operation_id_used(self):
        entry = self.entry()
        operation_ids = entry.get("operationIds")
        self.assertIsInstance(operation_ids, list)
        self.assertEqual(
            sorted(operation_ids),
            sorted(OPERATION_IDS),
            "record every operationId the contract names",
        )

    def assert_digest(self, value, length, guard, label):
        self.assertIsInstance(value, str, "%s must be a hex string" % label)
        normalised = value.strip().lower()
        self.assertEqual(
            len(normalised), length, "%s must be %d hex characters" % (label, length)
        )
        self.assertEqual(
            hashlib.sha256(normalised.encode("ascii")).hexdigest(),
            guard,
            "%s does not match the specification bytes; compute it over the "
            "file you actually fetched" % label,
        )

    def test_records_content_digests_of_the_retrieved_spec(self):
        entry = self.entry()
        self.assert_digest(entry.get("blobSha"), 40, SPEC_BLOB_SHA_GUARD, "blobSha")
        self.assert_digest(entry.get("sha256"), 64, SPEC_SHA256_GUARD, "sha256")

    def test_no_placeholder_hosts(self):
        for needle in (".invalid", "example.com", "TODO", "PLACEHOLDER"):
            self.assertNotIn(
                needle,
                self.raw,
                "official_sources.json must record real provenance, not %r"
                % needle,
            )


class ContractModuleTest(unittest.TestCase):
    """The package reads the contract instead of hard-coding the wire."""

    @classmethod
    def setUpClass(cls):
        from vcfops_alerts import load_contract

        cls.contract = load_contract()

    def test_load_contract_exposes_the_contracted_operations(self):
        self.assertEqual(self.contract.base_path, BASE_PATH)
        self.assertEqual(sorted(self.contract.operation_ids), sorted(OPERATION_IDS))
        alerts = self.contract.operation(OP_ALERTS)
        self.assertEqual(alerts.method, "GET")
        self.assertEqual(alerts.path, PATH_ALERTS)
        self.assertEqual(alerts.security, [SECURITY_SCHEME_NAME])
        acquire = self.contract.operation(OP_ACQUIRE)
        self.assertEqual(acquire.security, [])
        self.assertEqual(acquire.request_body["schema"], "username-password")

    def test_targets_are_built_from_the_contract(self):
        self.assertEqual(
            self.contract.target(OP_RELEASE), BASE_PATH + PATH_RELEASE
        )
        self.assertEqual(
            self.contract.target(OP_ALERTS, query=[("page", 0), ("pageSize", 10)]),
            BASE_PATH + PATH_ALERTS + "?page=0&pageSize=10",
        )


class WireShapeTestCase(unittest.TestCase):
    """Shared assertions over a recorded run."""

    def assert_only_contracted_paths(self, run):
        contracted = {
            BASE_PATH + PATH_ACQUIRE,
            BASE_PATH + PATH_ALERTS,
            BASE_PATH + PATH_RELEASE,
        }
        for entry in run.requests:
            self.assertIn(
                entry["path"],
                contracted,
                "unexpected request %s %s" % (entry["method"], entry["target"]),
            )

    def assert_all_accepted(self, run):
        for entry in run.requests:
            self.assertEqual(
                entry["status"],
                200,
                "the mock rejected %s %s: status %s"
                % (entry["method"], entry["target"], entry["status"]),
            )

    def assert_json_accept(self, entry):
        self.assertEqual(entry["headers"].get("accept"), JSON_MEDIA_TYPE)

    def assert_token_header(self, entry, token=FIRST_TOKEN):
        self.assertEqual(
            entry["headers"].get("authorization"),
            "%s %s" % (TOKEN_PREFIX, token),
            "authenticated operations carry the token issued by this run",
        )

    def assert_no_body(self, entry):
        self.assertIsNone(entry["body"], "this operation takes no request body")
        self.assertEqual(entry["body_length"], 0)
        self.assertIsNone(
            entry["headers"].get("content-type"),
            "a request with no body must not declare a Content-Type",
        )

    def query_names(self, entry):
        return [name for name, _ in entry["query_pairs"]]

    def query_values(self, entry, name):
        return [value for key, value in entry["query_pairs"] if key == name]


class UnfilteredRunTest(WireShapeTestCase):
    """The whole collection, no filters, no auth source."""

    @classmethod
    def setUpClass(cls):
        cls.session = Run(library_run())
        cls.expected = expected_alerts(ALERT_ROWS)

    def setUp(self):
        self.session.raise_if_failed()

    def test_only_contracted_paths_are_touched(self):
        self.assert_only_contracted_paths(self.session)

    def test_every_request_was_accepted_by_the_contract_pinned_mock(self):
        self.assert_all_accepted(self.session)

    def test_the_run_acquires_exactly_one_token_first(self):
        acquires = self.session.by_operation(OP_ACQUIRE)
        self.assertEqual(len(acquires), 1, self.session.summary())
        self.assertEqual(self.session.requests[0]["operation_id"], OP_ACQUIRE)

    def test_acquire_request_wire_shape(self):
        entry = self.session.by_operation(OP_ACQUIRE)[0]
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], BASE_PATH + PATH_ACQUIRE)
        self.assertEqual(entry["raw_query"], "", "acquireToken takes no query")
        self.assert_json_accept(entry)
        self.assertEqual(entry["headers"].get("content-type"), JSON_MEDIA_TYPE)
        self.assertIsNone(
            entry["headers"].get("authorization"),
            "acquireToken is unauthenticated in the specification",
        )
        self.assertEqual(
            entry["body_length"],
            len(entry["body"].encode("utf-8")),
            "Content-Length must match the bytes sent",
        )

    def test_unset_auth_source_is_omitted_not_sent_empty(self):
        entry = self.session.by_operation(OP_ACQUIRE)[0]
        body = json.loads(entry["body"])
        self.assertEqual(
            sorted(body),
            ["password", "username"],
            "authSource was not supplied, so it must not appear in the body",
        )
        self.assertEqual(body["username"], USERNAME)
        self.assertEqual(body["password"], PASSWORD)

    def test_pages_are_requested_in_order_and_exactly_once(self):
        pages = self.session.by_operation(OP_ALERTS)
        expected_pages = -(-len(ALERT_ROWS) // PAGE_SIZE)
        self.assertEqual(
            len(pages),
            expected_pages,
            "%d rows at pageSize %d is %d requests, no more and no fewer: %s"
            % (len(ALERT_ROWS), PAGE_SIZE, expected_pages, self.session.summary()),
        )
        self.assertEqual(
            [self.query_values(entry, "page") for entry in pages],
            [[str(index)] for index in range(expected_pages)],
        )

    def test_page_requests_send_page_and_pagesize_and_nothing_else(self):
        for entry in self.session.by_operation(OP_ALERTS):
            with self.subTest(target=entry["target"]):
                self.assertEqual(
                    sorted(self.query_names(entry)),
                    ["page", "pageSize"],
                    "no filter was requested, so id and resourceId must be "
                    "omitted entirely",
                )
                self.assertEqual(
                    self.query_values(entry, "pageSize"), [str(PAGE_SIZE)]
                )
                self.assertNotIn("=&", entry["raw_query"])
                self.assertFalse(entry["raw_query"].endswith("="))

    def test_page_requests_carry_the_token_and_no_body(self):
        for entry in self.session.by_operation(OP_ALERTS):
            with self.subTest(target=entry["target"]):
                self.assertEqual(entry["method"], "GET")
                self.assertEqual(entry["path"], BASE_PATH + PATH_ALERTS)
                self.assert_token_header(entry)
                self.assert_json_accept(entry)
                self.assert_no_body(entry)

    def test_the_token_is_released_once_after_the_last_page(self):
        releases = self.session.by_operation(OP_RELEASE)
        self.assertEqual(len(releases), 1, self.session.summary())
        self.assertEqual(
            self.session.requests[-1]["operation_id"],
            OP_RELEASE,
            "the session is released after the collection is read: %s"
            % self.session.summary(),
        )
        entry = releases[0]
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], BASE_PATH + PATH_RELEASE)
        self.assertEqual(entry["raw_query"], "")
        self.assert_token_header(entry)
        self.assert_no_body(entry)

    def test_the_collection_is_retrieved_completely(self):
        returned = self.session.result
        self.assertIsInstance(returned, list)
        self.assertEqual(
            [alert["alertId"] for alert in returned],
            [alert["alertId"] for alert in self.expected],
            "every distinct alert on every page, once each",
        )

    def test_the_duplicated_alert_keeps_the_row_from_the_earlier_page(self):
        returned = {alert["alertId"]: alert for alert in self.session.result}
        self.assertIn(DUPLICATED_ALERT_ID, returned)
        self.assertEqual(
            returned[DUPLICATED_ALERT_ID]["status"],
            "ACTIVE",
            "the alert was served twice; the first row seen wins",
        )

    def test_the_result_is_in_the_contracted_stable_order(self):
        self.assertEqual(self.session.result, self.expected)


class AuthSourceTest(WireShapeTestCase):
    """The optional body property is sent when set, omitted when not."""

    def test_auth_source_is_sent_when_supplied(self):
        run = Run(library_run(auth_source=AUTH_SOURCE))
        run.raise_if_failed()
        body = json.loads(run.by_operation(OP_ACQUIRE)[0]["body"])
        self.assertEqual(sorted(body), ["authSource", "password", "username"])
        self.assertEqual(body["authSource"], AUTH_SOURCE)

    def test_an_empty_auth_source_is_not_a_value(self):
        run = Run(library_run(auth_source=""))
        run.raise_if_failed()
        body = json.loads(run.by_operation(OP_ACQUIRE)[0]["body"])
        self.assertEqual(
            sorted(body),
            ["password", "username"],
            "an empty auth source is unset; it must not be sent as \"\"",
        )


class ResourceFilterTest(WireShapeTestCase):
    """A repeated array query parameter, and only when the caller filters."""

    @classmethod
    def setUpClass(cls):
        cls.filter_ids = [RESOURCE_IDS[1], RESOURCE_IDS[3]]
        cls.session = Run(library_run(resource_ids=cls.filter_ids))
        cls.matching = [
            row for row in ALERT_ROWS if row["resourceId"] in set(cls.filter_ids)
        ]

    def setUp(self):
        self.session.raise_if_failed()

    def test_the_filter_is_sent_as_repeated_query_parameters(self):
        pages = self.session.by_operation(OP_ALERTS)
        self.assertTrue(pages)
        for entry in pages:
            with self.subTest(target=entry["target"]):
                self.assertEqual(
                    self.query_values(entry, "resourceId"),
                    self.filter_ids,
                    "each value gets its own resourceId=... pair, in the "
                    "order the caller gave them",
                )
                self.assertEqual(
                    sorted(set(self.query_names(entry))),
                    ["page", "pageSize", "resourceId"],
                    "the alert id filter was not requested and must be omitted",
                )

    def test_the_filtered_collection_is_complete_and_ordered(self):
        self.assertEqual(self.session.result, expected_alerts(self.matching))

    def test_the_filtered_pages_are_still_walked_to_the_end(self):
        expected_pages = -(-len(self.matching) // PAGE_SIZE)
        self.assertEqual(len(self.session.by_operation(OP_ALERTS)), expected_pages)


class EmptyCollectionTest(WireShapeTestCase):
    """An empty collection costs exactly one page request."""

    @classmethod
    def setUpClass(cls):
        cls.session = Run(library_run(resource_ids=[UNUSED_RESOURCE_ID]))

    def test_no_matches_returns_nothing(self):
        self.session.raise_if_failed()
        self.assertEqual(self.session.result, [])

    def test_no_matches_costs_one_page_request(self):
        self.session.raise_if_failed()
        self.assertEqual(
            len(self.session.by_operation(OP_ALERTS)),
            1,
            "the first page reports a total of zero: %s" % self.session.summary(),
        )


class ShortIntermediatePageTest(WireShapeTestCase):
    """Only pageInfo, not a short batch, decides that the read is done."""

    @classmethod
    def setUpClass(cls):
        cls.rows = ALERT_ROWS[:21]
        cls.windows = [cls.rows[:5], cls.rows[5:15], cls.rows[15:]]
        cls.session = Run(
            library_run(page_size=PAGE_SIZE),
            alerts=cls.rows,
            page_windows=cls.windows,
        )

    def test_a_short_nonfinal_page_does_not_stop_the_read(self):
        self.session.raise_if_failed()
        pages = self.session.by_operation(OP_ALERTS)
        self.assertEqual(
            [self.query_values(entry, "page") for entry in pages],
            [["0"], ["1"], ["2"]],
            "pageInfo reports 21 rows, so all three pages must be read",
        )
        self.assertEqual(self.session.result, expected_alerts(self.rows))


class AlertIdFilterTest(WireShapeTestCase):
    """The other array parameter, so `id` is exercised too."""

    @classmethod
    def setUpClass(cls):
        cls.alert_ids = [
            ALERT_ROWS[3]["alertId"],
            ALERT_ROWS[21]["alertId"],
            ALERT_ROWS[40]["alertId"],
        ]
        cls.session = Run(library_run(alert_ids=cls.alert_ids))

    def test_alert_ids_are_sent_as_repeated_id_parameters(self):
        self.session.raise_if_failed()
        pages = self.session.by_operation(OP_ALERTS)
        self.assertEqual(len(pages), 1)
        self.assertEqual(self.query_values(pages[0], "id"), self.alert_ids)
        self.assertEqual(
            sorted(set(self.query_names(pages[0]))), ["id", "page", "pageSize"]
        )

    def test_only_the_requested_alerts_come_back(self):
        self.session.raise_if_failed()
        self.assertEqual(
            sorted(alert["alertId"] for alert in self.session.result),
            sorted(self.alert_ids),
        )


class ContractDriftTest(WireShapeTestCase):
    """The wire follows the contract, not string literals in the client."""

    def test_a_relocated_path_moves_the_request(self):
        from vcfops_alerts import load_contract
        from vcfops_alerts.errors import OperationsApiError

        pinned = contract_document()
        drifted = json.loads(json.dumps(pinned))
        drifted["operations"][OP_ALERTS]["path"] = "/api/alerts-relocated"

        directory = tempfile.mkdtemp(prefix="vcfops-drift-")
        drifted_path = os.path.join(directory, "contract.json")
        with open(drifted_path, "w", encoding="utf-8") as handle:
            json.dump(drifted, handle)

        run = Run(library_run(contract=load_contract(drifted_path)), contract=pinned)

        targeted = [
            entry
            for entry in run.requests
            if entry["path"] == BASE_PATH + "/api/alerts-relocated"
        ]
        self.assertTrue(
            targeted,
            "the client built its target from a literal instead of the "
            "contract it was given: %s" % run.summary(),
        )
        self.assertEqual(targeted[0]["status"], 404)
        self.assertIsNotNone(
            run.error, "a 404 from the appliance must not be swallowed"
        )
        self.assertIsInstance(run.error, OperationsApiError)
        self.assertEqual(getattr(run.error, "status", None), 404)

    def test_a_relocated_base_path_moves_the_request(self):
        from vcfops_alerts import load_contract
        from vcfops_alerts.errors import OperationsApiError

        pinned = contract_document()
        drifted = json.loads(json.dumps(pinned))
        drifted["basePath"] = "/operations-relocated"

        directory = tempfile.mkdtemp(prefix="vcfops-drift-")
        drifted_path = os.path.join(directory, "contract.json")
        with open(drifted_path, "w", encoding="utf-8") as handle:
            json.dump(drifted, handle)

        run = Run(library_run(contract=load_contract(drifted_path)), contract=pinned)
        self.assertTrue(run.requests, "the client made no request")
        self.assertEqual(
            run.requests[0]["path"],
            "/operations-relocated" + PATH_ACQUIRE,
            "the client built the base path from a literal",
        )
        self.assertEqual(run.requests[0]["status"], 404)
        self.assertIsInstance(run.error, OperationsApiError)

    def test_relocated_query_parameters_move_every_query_pair(self):
        from vcfops_alerts import load_contract

        drifted = json.loads(json.dumps(contract_document()))
        relocated_names = [
            "alert-key",
            "resource-key",
            "page-index",
            "page-limit",
        ]
        parameters = drifted["operations"][OP_ALERTS]["queryParameters"]
        for parameter, relocated in zip(parameters, relocated_names):
            parameter["name"] = relocated

        directory = tempfile.mkdtemp(prefix="vcfops-drift-")
        drifted_path = os.path.join(directory, "contract.json")
        with open(drifted_path, "w", encoding="utf-8") as handle:
            json.dump(drifted, handle)

        run = Run(
            library_run(
                contract=load_contract(drifted_path),
                alert_ids=[ALERT_ROWS[0]["alertId"]],
                resource_ids=[RESOURCE_IDS[0]],
            ),
            contract=drifted,
        )
        run.raise_if_failed()
        pages = run.by_operation(OP_ALERTS)
        self.assertTrue(pages, "the client made no getAlerts request")
        self.assertEqual(
            self.query_names(pages[0]),
            relocated_names,
            "the client did not get every query parameter name from the contract",
        )


class CommandLineTest(WireShapeTestCase):
    """`python3 -m vcfops_alerts` emits the collection in a stable order."""

    @classmethod
    def setUpClass(cls):
        cls.expected_document = (
            json.dumps(expected_alerts(ALERT_ROWS), indent=2, sort_keys=True) + "\n"
        )

    def invoke(self):
        outputs = {}

        def action(server):
            environment = dict(os.environ)
            environment["PYTHONPATH"] = SRC_DIR + os.pathsep + environment.get(
                "PYTHONPATH", ""
            )
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "vcfops_alerts",
                    "--base-url",
                    server.base_url,
                    "--username",
                    USERNAME,
                    "--password",
                    PASSWORD,
                    "--page-size",
                    str(PAGE_SIZE),
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=DEADLINE_SECONDS,
            )
            outputs["completed"] = completed
            return completed

        run = Run(action)
        completed = outputs["completed"]
        self.assertEqual(
            completed.returncode,
            0,
            "the command failed:\n%s\n%s" % (completed.stdout, completed.stderr),
        )
        return completed, run

    def test_the_command_emits_the_contracted_document(self):
        completed, _ = self.invoke()
        self.assertEqual(completed.stdout, self.expected_document)

    def test_the_command_walks_the_contracted_request_sequence(self):
        _, run = self.invoke()
        self.assertEqual(
            [entry["operation_id"] for entry in run.requests],
            [OP_ACQUIRE] + [OP_ALERTS] * 5 + [OP_RELEASE],
            run.summary(),
        )
        self.assert_all_accepted(run)

    def test_the_output_is_byte_identical_across_runs(self):
        first, _ = self.invoke()
        second, _ = self.invoke()
        self.assertEqual(first.stdout, second.stdout)

    def test_the_password_is_not_echoed(self):
        completed, _ = self.invoke()
        self.assertNotIn(PASSWORD, completed.stderr)
