"""Protected verification for the vcenter_content client.

Asserts two things that no live endpoint is needed for:

  1. docs/contract.json and docs/official_sources.json still point at the
     vcenter.yaml blob published at tag 9.0.0.0 of vmware/vcf-api-specs.
  2. The requests vcenter_content puts on the wire match that contract exactly
     -- including that optional properties left unset are absent from the JSON
     body rather than sent as null or "".

Everything runs against a loopback mock. Run with:  python tests/verify_contract.py
"""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import os
import re
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock as unittest_mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mock import vcenter_mock  # noqa: E402
from vcenter_content import ContentLibraryClient, VCenterApiError  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
CONTRACT = json.load(open(os.path.join(DOCS, "contract.json"), encoding="utf-8"))
SOURCES = json.load(open(os.path.join(DOCS, "official_sources.json"), encoding="utf-8"))

SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_TAG = "9.0.0.0"
SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_COMMIT_9_1 = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"

# The operations this client is allowed to use, with the method and path each
# one carries in that spec revision.
EXPECTED_OPERATIONS = {
    "Cis.Session_create": ("POST", "/session"),
    "Content.Library.Item_create": ("POST", "/content/library/item"),
    "Content.Library.Item_get": ("GET", "/content/library/item/{libraryItemId}"),
}

# Content.Library.ItemModel property classification for the create operation.
CREATE_REQUIRED = {"library_id", "name"}
CREATE_OPTIONAL = {"description", "type"}

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

LIBRARY_ID = vcenter_mock.DEFAULT_LIBRARIES[0]
USERNAME = vcenter_mock.DEFAULT_USERNAME
PASSWORD = vcenter_mock.DEFAULT_PASSWORD


class Harness:
    """Runs the loopback mock for one scenario and exposes its request log."""

    def __init__(self, create_fault_count=1, session_fault_count=0):
        self.create_fault_count = create_fault_count
        self.session_fault_count = session_fault_count

    def __enter__(self):
        fd, self.log_path = tempfile.mkstemp(prefix="vcenter-mock-", suffix=".jsonl")
        os.close(fd)
        self.server, self.base_url = vcenter_mock.start(
            log_path=self.log_path,
            create_fault_count=self.create_fault_count,
            session_fault_count=self.session_fault_count,
        )
        self.state = self.server.state
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        os.unlink(self.log_path)
        return False

    def client(self, **kwargs):
        kwargs.setdefault("max_attempts", 3)
        kwargs.setdefault("backoff", 0.0)
        return ContentLibraryClient(self.base_url, USERNAME, PASSWORD, **kwargs)

    # -- request log ------------------------------------------------------
    def requests(self):
        with open(self.log_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def by_operation(self, operation_id):
        return [r for r in self.requests() if r["operation_id"] == operation_id]

    def creates(self):
        return self.by_operation("Content.Library.Item_create")

    def items(self):
        return self.state.items


class ContractProvenanceTest(unittest.TestCase):
    """docs/ must still describe the 9.0.0.0 specification blob."""

    def source(self):
        self.assertEqual(len(SOURCES["sources"]), 1, "expected exactly one source")
        return SOURCES["sources"][0]

    def test_source_is_the_pinned_spec_blob(self):
        src = self.source()
        self.assertEqual(src["path"], SPEC_PATH)
        self.assertEqual(src["tag"], SPEC_TAG)
        self.assertEqual(src["commit"], SPEC_COMMIT)
        self.assertEqual(src["repository"], "https://github.com/vmware/vcf-api-specs")
        self.assertEqual(src["info"]["version"], SPEC_TAG)

    def test_source_is_not_the_9_1_revision(self):
        src = self.source()
        self.assertNotEqual(
            src["commit"],
            SPEC_COMMIT_9_1,
            "contract must come from 9.0.0.0, not the 9.1 revision of the file",
        )
        self.assertNotIn("9.1", src["tag"])

    def test_source_names_every_operation_used(self):
        src = self.source()
        self.assertEqual(set(src["operation_ids"]), set(EXPECTED_OPERATIONS))
        recorded = {op["operation_id"]: (op["method"], op["path"])
                    for op in src["operations"]}
        self.assertEqual(recorded, EXPECTED_OPERATIONS)

    def test_contract_agrees_with_its_source_record(self):
        src = self.source()
        self.assertEqual(CONTRACT["source"]["commit"], src["commit"])
        self.assertEqual(CONTRACT["source"]["path"], src["path"])
        self.assertEqual(CONTRACT["source"]["tag"], SPEC_TAG)
        self.assertEqual(CONTRACT["source"]["spec_sha256"], src["sha256"])

    def test_contract_operations_match_the_spec(self):
        actual = {
            oid: (op["method"], op["path"])
            for oid, op in CONTRACT["operations"].items()
        }
        self.assertEqual(actual, EXPECTED_OPERATIONS)
        self.assertEqual(CONTRACT["server"]["base_path"], "/api")
        self.assertEqual(
            CONTRACT["security_schemes"]["api_key_auth"]["name"],
            "vmware-api-session-id",
        )

    def test_contract_records_the_create_field_classification(self):
        create = CONTRACT["schemas"]["Content.Library.ItemModel"]["create"]
        self.assertEqual(set(create["required"]), CREATE_REQUIRED)
        self.assertEqual(set(create["optional"]), CREATE_OPTIONAL)
        self.assertNotIn("id", create["required"] + create["optional"])
        self.assertIn("id", create["not_used"])

    def test_contract_records_the_idempotency_header(self):
        idem = CONTRACT["idempotency"]
        self.assertEqual(idem["operation_id"], "Content.Library.Item_create")
        self.assertEqual(idem["header"], "Client-Token")
        self.assertEqual(idem["format"], "uuid")


class ImplementationConstraintTest(unittest.TestCase):
    """The appliance build may import only this package and the stdlib."""

    def assert_stdlib_module(self, name):
        if name == "vcenter_content":
            return
        spec = importlib.util.find_spec(name)
        self.assertIsNotNone(spec, "imported module %r is not available" % name)
        origin = spec.origin
        if origin in {"built-in", "frozen"}:
            return
        self.assertIsNotNone(origin, "namespace package %r is not stdlib" % name)
        origin = os.path.realpath(origin)
        self.assertFalse(
            {"site-packages", "dist-packages"} & set(origin.split(os.sep)),
            "third-party import is not allowed: %s" % name,
        )
        stdlib_roots = {
            os.path.realpath(sysconfig.get_path(key))
            for key in ("stdlib", "platstdlib")
            if sysconfig.get_path(key)
        }
        self.assertTrue(
            any(os.path.commonpath([origin, root]) == root for root in stdlib_roots),
            "third-party import is not allowed: %s" % name,
        )

    def test_client_uses_only_the_python_standard_library(self):
        package = os.path.join(ROOT, "vcenter_content")
        imported = set()
        for directory, _, filenames in os.walk(package):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(directory, filename)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(
                            alias.name.split(".")[0] for alias in node.names
                        )
                    elif (
                        isinstance(node, ast.ImportFrom)
                        and node.level == 0
                        and node.module
                    ):
                        imported.add(node.module.split(".")[0])
        for name in sorted(imported):
            self.assert_stdlib_module(name)


class WireShapeTest(unittest.TestCase):
    """The bytes the client actually sends."""

    # -- shared assertions ------------------------------------------------
    def assert_nothing_off_contract(self, h):
        stray = [
            "%s %s" % (r["method"], r["path"])
            for r in h.requests()
            if r["operation_id"] is None
        ]
        self.assertEqual(stray, [], "requests outside the contract: %s" % stray)

    def assert_authenticated(self, h, records):
        sessions = h.by_operation("Cis.Session_create")
        self.assertEqual(
            len(sessions), 1, "expected exactly one Cis.Session_create per client"
        )
        session = sessions[0]
        self.assertEqual(session["path"], "/api/session")
        self.assertEqual(session["query"], {}, "session creation takes no query")
        self.assertEqual(session["body_raw"], "", "session creation takes no body")
        self.assertEqual(session["status"], 201)
        auth = session["headers"].get("authorization", "")
        self.assertTrue(auth.startswith("Basic "), "session must use HTTP Basic auth")
        self.assertEqual(
            base64.b64decode(auth[6:]).decode(), "%s:%s" % (USERNAME, PASSWORD)
        )
        token = next(iter(h.state.sessions))
        for r in records:
            self.assertEqual(
                r["headers"].get("vmware-api-session-id"),
                token,
                "%s must carry the session id issued by Cis.Session_create"
                % r["operation_id"],
            )
            self.assertNotIn(
                "authorization",
                r["headers"],
                "credentials must not be replayed once a session exists",
            )

    def assert_json_body(self, record):
        ctype = record["headers"].get("content-type", "")
        self.assertTrue(
            ctype.split(";")[0].strip() == "application/json",
            "Content-Type must be application/json, got %r" % ctype,
        )
        self.assertIsInstance(record["body_json"], dict)
        return record["body_json"]

    def assert_create_shape(self, record, expected_keys):
        """A create request must carry exactly `expected_keys` and no more."""
        self.assertEqual(record["method"], "POST")
        self.assertEqual(record["path"], "/api/content/library/item")
        self.assertEqual(record["query"], {}, "create takes no query parameters")
        body = self.assert_json_body(record)
        self.assertEqual(
            set(body),
            expected_keys,
            "create body properties must be exactly %s, got %s"
            % (sorted(expected_keys), sorted(body)),
        )
        for key, value in body.items():
            self.assertIsNotNone(
                value, "%r must be omitted when unset, not sent as null" % key
            )
        return body

    def assert_same_token(self, creates):
        tokens = [r["headers"].get("client-token") for r in creates]
        self.assertTrue(
            all(t is not None for t in tokens),
            "every create attempt must carry a Client-Token header",
        )
        self.assertEqual(
            len(set(tokens)),
            1,
            "a retry must reuse the original Client-Token, saw %s" % sorted(set(tokens)),
        )
        self.assertTrue(
            UUID_RE.match(tokens[0]), "Client-Token must be a UUID, got %r" % tokens[0]
        )
        return tokens[0]

    # -- scenarios --------------------------------------------------------
    def test_minimal_create_omits_unset_optional_properties(self):
        with Harness(create_fault_count=1) as h:
            item_id = h.client().ensure_library_item(
                library_id=LIBRARY_ID, name="ubuntu-24.04-server"
            )

            self.assertIsInstance(item_id, str)
            self.assertIn(item_id, h.items())
            self.assert_nothing_off_contract(h)

            creates = h.creates()
            self.assertEqual(
                len(creates),
                2,
                "one transient 503 should produce exactly one retry, saw %d attempt(s)"
                % len(creates),
            )
            self.assertEqual([r["status"] for r in creates], [503, 201])
            self.assert_authenticated(h, creates)
            self.assert_same_token(creates)

            for record in creates:
                body = self.assert_create_shape(record, CREATE_REQUIRED)
                self.assertEqual(body["library_id"], LIBRARY_ID)
                self.assertEqual(body["name"], "ubuntu-24.04-server")
                # Belt and braces: the raw bytes must not mention them either.
                self.assertNotIn("description", record["body_raw"])
                self.assertNotIn("type", record["body_raw"])

            self.assertEqual(
                creates[0]["body_raw"],
                creates[1]["body_raw"],
                "the retry must resend the original body verbatim",
            )

    def test_retry_does_not_duplicate_the_item(self):
        with Harness(create_fault_count=1) as h:
            item_id = h.client().ensure_library_item(
                library_id=LIBRARY_ID, name="rocky-9-minimal"
            )
            self.assertEqual(
                len(h.items()),
                1,
                "the retried create left %d items behind; the call is not idempotent"
                % len(h.items()),
            )
            self.assertEqual(list(h.items()), [item_id])
            self.assertEqual(
                list(h.state.tokens.values()),
                [item_id],
                "the server saw more than one distinct Client-Token",
            )

    def test_supplied_optional_properties_are_sent(self):
        with Harness(create_fault_count=0) as h:
            h.client().ensure_library_item(
                library_id=LIBRARY_ID,
                name="photon-5-ova",
                description="Golden image for the platform team",
                item_type="ovf",
            )
            self.assert_nothing_off_contract(h)
            creates = h.creates()
            self.assertEqual(len(creates), 1)
            body = self.assert_create_shape(
                creates[0], CREATE_REQUIRED | CREATE_OPTIONAL
            )
            self.assertEqual(body["description"], "Golden image for the platform team")
            self.assertEqual(
                body["type"],
                "ovf",
                "item_type maps onto the spec property name 'type'",
            )

    def test_empty_string_is_a_value_not_an_unset_property(self):
        with Harness(create_fault_count=0) as h:
            h.client().ensure_library_item(
                library_id=LIBRARY_ID,
                name="empty-optionals",
                description="",
                item_type="",
            )
            body = self.assert_create_shape(
                h.creates()[0], CREATE_REQUIRED | CREATE_OPTIONAL
            )
            self.assertEqual(
                body["description"],
                "",
                "an explicit empty string is a value and must be sent",
            )
            self.assertEqual(
                body["type"],
                "",
                "an explicit empty item_type maps to an explicit empty type",
            )

    def test_caller_supplied_token_is_used_verbatim(self):
        token = "b8a2a2e3-2314-43cd-a871-6ede0f429751"
        with Harness(create_fault_count=1) as h:
            h.client().ensure_library_item(
                library_id=LIBRARY_ID, name="pinned-token", client_token=token
            )
            creates = h.creates()
            self.assertEqual(len(creates), 2)
            self.assertEqual([r["headers"].get("client-token") for r in creates],
                             [token, token])

    def test_invalid_falsy_supplied_token_is_not_silently_replaced(self):
        with Harness(create_fault_count=0) as h:
            with self.assertRaises(VCenterApiError) as caught:
                h.client().ensure_library_item(
                    library_id=LIBRARY_ID, name="bad-token", client_token=""
                )
            self.assertEqual(caught.exception.status, 400)
            creates = h.creates()
            self.assertEqual(len(creates), 1)
            self.assertEqual(creates[0]["headers"].get("client-token"), "")

    def test_exhausted_retries_still_leave_one_item(self):
        with Harness(create_fault_count=5) as h:
            with self.assertRaises(VCenterApiError) as caught:
                h.client(max_attempts=3).ensure_library_item(
                    library_id=LIBRARY_ID, name="always-failing"
                )
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(caught.exception.error_type, "SERVICE_UNAVAILABLE")
            self.assertIsInstance(caught.exception.messages, list)
            self.assertTrue(caught.exception.messages)

            creates = h.creates()
            self.assertEqual(
                len(creates), 3, "max_attempts=3 means three create attempts"
            )
            self.assertEqual([r["status"] for r in creates], [503, 503, 503])
            self.assert_same_token(creates)
            self.assertEqual(
                len(h.items()),
                1,
                "every attempt shared one token, so at most one item may exist",
            )

    def test_client_errors_are_not_retried(self):
        with Harness(create_fault_count=0) as h:
            with self.assertRaises(VCenterApiError) as caught:
                h.client().ensure_library_item(
                    library_id="00000000-0000-4000-8000-000000000000", name="nowhere"
                )
            self.assertEqual(caught.exception.status, 404)
            self.assertEqual(caught.exception.error_type, "NOT_FOUND")
            self.assertIsInstance(caught.exception.messages, list)
            self.assertIn("default_message", caught.exception.messages[0])
            self.assertEqual(
                len(h.creates()), 1, "a 404 is terminal and must not be retried"
            )

    def test_bad_request_is_terminal_and_carries_the_error_body(self):
        with Harness(create_fault_count=0) as h:
            with self.assertRaises(VCenterApiError) as caught:
                h.client().ensure_library_item(library_id=LIBRARY_ID, name="")
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(caught.exception.error_type, "INVALID_ARGUMENT")
            self.assertIn("name may not be empty", str(caught.exception.messages))
            self.assertEqual(len(h.creates()), 1, "a 400 must not be retried")

    def test_authentication_is_lazy_and_401_is_terminal(self):
        with Harness(create_fault_count=0) as h:
            h.client()
            self.assertEqual(h.requests(), [], "constructing a client must do no I/O")

            client = ContentLibraryClient(
                h.base_url,
                USERNAME,
                PASSWORD + "-wrong",
                max_attempts=3,
                backoff=0.0,
            )
            with self.assertRaises(VCenterApiError) as caught:
                client.ensure_library_item(library_id=LIBRARY_ID, name="no-session")
            self.assertEqual(caught.exception.status, 401)
            self.assertEqual(caught.exception.error_type, "UNAUTHENTICATED")
            self.assertEqual(len(h.by_operation("Cis.Session_create")), 1)
            self.assertEqual(h.creates(), [])

    def test_session_503_is_retried_with_backoff(self):
        with Harness(create_fault_count=0, session_fault_count=1) as h:
            with unittest_mock.patch("vcenter_content.client.time.sleep") as sleep:
                item_id = h.client(backoff=0.125).ensure_library_item(
                    library_id=LIBRARY_ID, name="session-retry"
                )

            sessions = h.by_operation("Cis.Session_create")
            self.assertEqual([r["status"] for r in sessions], [503, 201])
            self.assertEqual(len(h.state.sessions), 1)
            self.assertEqual(len(h.creates()), 1)
            self.assertIn(item_id, h.items())
            sleep.assert_called_once_with(0.125)

    def test_exhausted_session_retries_raise_the_last_503(self):
        with Harness(create_fault_count=0, session_fault_count=5) as h:
            with self.assertRaises(VCenterApiError) as caught:
                h.client(max_attempts=3).ensure_library_item(
                    library_id=LIBRARY_ID, name="session-down"
                )
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(caught.exception.error_type, "SERVICE_UNAVAILABLE")
            self.assertEqual(
                [r["status"] for r in h.by_operation("Cis.Session_create")],
                [503, 503, 503],
            )
            self.assertEqual(h.creates(), [])
            self.assertEqual(h.state.sessions, set())

    def test_read_back_uses_the_get_operation(self):
        with Harness(create_fault_count=0) as h:
            client = h.client()
            item_id = client.ensure_library_item(
                library_id=LIBRARY_ID, name="read-me-back", description="round trip"
            )
            item = client.get_library_item(item_id)

            self.assert_nothing_off_contract(h)
            gets = h.by_operation("Content.Library.Item_get")
            self.assertEqual(len(gets), 1)
            self.assertEqual(gets[0]["method"], "GET")
            self.assertEqual(gets[0]["path"], "/api/content/library/item/" + item_id)
            self.assertEqual(gets[0]["query"], {})
            self.assertEqual(gets[0]["body_raw"], "", "GET must not carry a body")

            self.assertIsInstance(item, dict)
            self.assertEqual(item["id"], item_id)
            self.assertEqual(item["name"], "read-me-back")
            self.assertEqual(item["description"], "round trip")
            self.assertEqual(item["library_id"], LIBRARY_ID)

    def test_one_session_serves_the_whole_client(self):
        with Harness(create_fault_count=0) as h:
            client = h.client()
            first = client.ensure_library_item(library_id=LIBRARY_ID, name="alpha")
            second = client.ensure_library_item(library_id=LIBRARY_ID, name="beta")
            client.get_library_item(first)

            self.assert_nothing_off_contract(h)
            self.assertNotEqual(first, second)
            self.assertEqual(
                len(h.by_operation("Cis.Session_create")),
                1,
                "the session must be created once and reused",
            )
            self.assert_authenticated(
                h, h.creates() + h.by_operation("Content.Library.Item_get")
            )
            self.assertEqual(
                len({r["headers"]["client-token"] for r in h.creates()}),
                2,
                "two distinct create calls need two distinct Client-Tokens",
            )


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromTestCase(case)
        for case in (
            ContractProvenanceTest,
            ImplementationConstraintTest,
            WireShapeTest,
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
