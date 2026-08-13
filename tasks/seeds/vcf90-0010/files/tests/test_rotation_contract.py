"""Protected verifier for the SDDC Manager credential rotation client.

Runs the client against the loopback mock and asserts the exact wire shape of
every request it made, including that unset optional fields are omitted rather
than sent empty. Deterministic: token expiry is counted in requests, task
progress is counted in polls, and no live VMware endpoint is contacted.

Run with:  python3 tests/test_rotation_contract.py
"""

import json
import os
import socket
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "mock"))

import sddc_manager_mock as mock  # noqa: E402

CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(REPO_ROOT, "docs", "official_sources.json")

SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_TAG = "9.0.0.0"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
OPERATION_IDS = [
    "createToken",
    "refreshAccessToken",
    "getCredentials",
    "updateOrRotatePasswords",
    "getCredentialsTask",
]

OPERATOR_USERNAME = "administrator@vsphere.local"
OPERATOR_PASSWORD = "VMw@re1!VMw@re1!"
PAGE_SIZE = 2
TASK_ID = "cred-task-0001"

# The full transcript a correct client produces. The access token authorizes
# two calls (mock.ACCESS_TOKEN_USES), so it expires while paging the inventory
# and again while polling the rotation task.
EXPECTED_TRANSCRIPT = [
    ("createToken", 201),
    ("getCredentials", 200),
    ("getCredentials", 200),
    ("getCredentials", 401),
    ("refreshAccessToken", 200),
    ("getCredentials", 200),
    ("updateOrRotatePasswords", 202),
    ("getCredentialsTask", 401),
    ("refreshAccessToken", 200),
    ("getCredentialsTask", 200),
    ("getCredentialsTask", 200),
]

EXPECTED_ROTATED_IDS = sorted([
    "0f1c7a3e-2b44-4c19-9a51-7d0a6c8e1b02",  # esxi-01 root
    "3ac58b17-9e6d-4f02-b8aa-52c1d3f74e88",  # esxi-03 root
    "77b2e940-1c53-4ab6-8e70-9f31d5c02a6b",  # esxi-02 root
    "c85f0d6a-4e21-47b9-93cc-0b7a12de5f40",  # esxi-04 root
    "e102a7bc-6d38-4f55-81ea-3b9c47d0e2a9",  # esxi-03 svc-vcf-monitor
])

# elements of the CredentialsUpdateSpec, normalised for comparison.
EXPECTED_ELEMENTS = [
    {
        "resourceId": "b21d9f60-5a77-42c8-8f3e-1c4b90ad7e11",
        "resourceName": "esxi-01.vcf.sddc.lab",
        "resourceType": "ESXI",
        "credentials": [
            {"username": "root", "credentialType": "SSH", "accountType": "SYSTEM"},
        ],
    },
    {
        "resourceId": "d4a138f5-7c92-4e10-b6d3-2a58e9107c3f",
        "resourceName": "esxi-02.vcf.sddc.lab",
        "resourceType": "ESXI",
        "credentials": [
            {"username": "root", "credentialType": "SSH", "accountType": "SYSTEM"},
        ],
    },
    {
        "resourceId": "6e07c4d2-31b8-49aa-9d55-8b2f0e6c17a4",
        "resourceName": "esxi-03.vcf.sddc.lab",
        "resourceType": "ESXI",
        "credentials": [
            {"username": "root", "credentialType": "SSH", "accountType": "SYSTEM"},
            {"username": "svc-vcf-monitor", "credentialType": "SSH",
             "accountType": "SERVICE"},
        ],
    },
    {
        "resourceId": "9f3b6c81-08de-4d47-a2b5-4c7e1f90b6d2",
        "resourceName": "esxi-04.vcf.sddc.lab",
        "resourceType": "ESXI",
        "credentials": [
            {"username": "root", "credentialType": "SSH", "accountType": "SYSTEM"},
        ],
    },
]


def walk_empty(node, path=""):
    """Dotted paths of every null or empty-string value in a decoded body."""
    if node is None or node == "":
        yield path or "<root>"
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from walk_empty(value, "%s.%s" % (path, key) if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_empty(value, "%s[%d]" % (path, index))


class LoopbackGuard:
    """Fail the run if anything resolves a host that is not loopback."""

    def __init__(self):
        self.hosts = set()
        self._real = socket.getaddrinfo

    def __enter__(self):
        def guarded(host, port, *args, **kwargs):
            self.hosts.add(host)
            return self._real(host, port, *args, **kwargs)

        socket.getaddrinfo = guarded
        return self

    def __exit__(self, *exc):
        socket.getaddrinfo = self._real
        return False

    def offenders(self):
        allowed = {"127.0.0.1", "::1", "localhost", None, ""}
        return sorted(h for h in self.hosts if h not in allowed)


_RUN = {}


def rotation_run():
    """Drive the client against the mock exactly once, and cache what happened."""
    if _RUN:
        return _RUN

    tmpdir = tempfile.mkdtemp(prefix="vcf-credrotate-")
    log_path = os.path.join(tmpdir, "requests.jsonl")
    server, base_url = mock.start_mock(log_path=log_path)

    result, error = None, None
    guard = LoopbackGuard()
    try:
        from vcf_credrotate import rotate_credentials
    except Exception as exc:
        error = exc
        rotate_credentials = None

    if rotate_credentials is not None:
        try:
            with guard:
                result = rotate_credentials(
                    base_url,
                    OPERATOR_USERNAME,
                    OPERATOR_PASSWORD,
                    resource_type="ESXI",
                    page_size=PAGE_SIZE,
                    poll_interval=0.0,
                )
        except Exception as exc:
            error = exc
    mock.stop_mock(server)

    with open(log_path, "r", encoding="utf-8") as fh:
        log = [json.loads(line) for line in fh if line.strip()]
    os.unlink(log_path)
    os.rmdir(tmpdir)

    _RUN.update(result=result, error=error, log=log, guard=guard, base_url=base_url)
    return _RUN


class RotationRun(unittest.TestCase):
    """Assertions over the single shared rotation run."""

    @classmethod
    def setUpClass(cls):
        run = rotation_run()
        cls.result = run["result"]
        cls.error = run["error"]
        cls.log = run["log"]
        cls.guard = run["guard"]
        cls.base_url = run["base_url"]

    def setUp(self):
        if self.error is not None:
            self.fail(
                "rotate_credentials raised %r after %d request(s): %s"
                % (self.error, len(self.log), self.summarise())
            )

    # -- helpers --------------------------------------------------------
    @classmethod
    def summarise(cls):
        return " | ".join(
            "%s %s%s -> %s" % (
                e["method"], e["path"],
                "?" + e["rawQuery"] if e["rawQuery"] else "", e["status"],
            )
            for e in cls.log
        ) or "(no requests reached the mock)"

    def calls(self, operation_id, status=None):
        return [
            e for e in self.log
            if e["operationId"] == operation_id
            and (status is None or e["status"] == status)
        ]


class TestProvenance(unittest.TestCase):
    """docs/ must stay pinned to the 9.0.0.0 specification."""

    def setUp(self):
        with open(CONTRACT_PATH, encoding="utf-8") as fh:
            self.contract = json.load(fh)
        with open(SOURCES_PATH, encoding="utf-8") as fh:
            self.sources = json.load(fh)

    def test_contract_pins_the_9_0_0_0_specification(self):
        origin = self.contract["derivedFrom"]
        self.assertEqual(origin["tag"], SPEC_TAG)
        self.assertEqual(origin["commit"], SPEC_COMMIT)
        self.assertEqual(origin["specPath"], SPEC_PATH)
        self.assertEqual(
            origin["infoVersion"], SPEC_TAG,
            "the contract must come from the 9.0.0.0 revision of the spec file",
        )

    def test_official_sources_records_spec_path_commit_and_operation_ids(self):
        source = self.sources["sources"][0]
        self.assertEqual(source["kind"], "openapi-specification")
        self.assertEqual(source["tag"], SPEC_TAG)
        self.assertEqual(source["commit"], SPEC_COMMIT)
        self.assertEqual(source["specPath"], SPEC_PATH)
        self.assertEqual(
            [o["operationId"] for o in source["operationIds"]], OPERATION_IDS
        )

    def test_contract_names_exactly_the_five_operations(self):
        self.assertEqual(
            [o["operationId"] for o in self.contract["operations"]], OPERATION_IDS
        )

    def test_contract_records_the_spec_derived_shapes(self):
        by_id = {o["operationId"]: o for o in self.contract["operations"]}

        refresh = by_id["refreshAccessToken"]
        self.assertEqual(refresh["method"], "PATCH")
        self.assertEqual(refresh["path"], "/v1/tokens/access-token/refresh")
        self.assertEqual(refresh["requestBody"]["schema"], {"kind": "string"})
        self.assertEqual(refresh["responses"]["200"]["schema"], {"kind": "string"})

        create = by_id["createToken"]
        self.assertEqual(create["method"], "POST")
        self.assertEqual(
            create["responses"]["201"]["schema"], {"kind": "ref", "model": "TokenPair"}
        )
        self.assertEqual(
            self.contract["models"]["TokenCreationSpec"]["required"], [],
            "every TokenCreationSpec property is optional in the 9.0.0.0 spec",
        )
        self.assertEqual(
            self.contract["models"]["TokenPair"]["properties"]["refreshToken"]["type"],
            {"kind": "ref", "model": "RefreshToken"},
        )

        rotate = by_id["updateOrRotatePasswords"]
        self.assertEqual(rotate["method"], "PATCH")
        self.assertEqual(rotate["path"], "/v1/credentials")
        self.assertEqual(
            rotate["responses"]["202"]["schema"], {"kind": "ref", "model": "Task"}
        )
        self.assertEqual(
            by_id["getCredentialsTask"]["responses"]["200"]["schema"],
            {"kind": "ref", "model": "CredentialsTask"},
        )

    def test_every_referenced_model_is_defined(self):
        models = self.contract["models"]
        referenced = set()

        def collect(node):
            if isinstance(node, dict):
                if node.get("kind") == "ref":
                    referenced.add(node["model"])
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)

        collect(self.contract)
        self.assertEqual(
            sorted(referenced - set(models)), [],
            "contract model references must be self-contained",
        )
        self.assertEqual(
            models["Task"]["required"],
            ["creationTimestamp", "id", "name", "status"],
        )
        self.assertEqual(
            models["Task"]["properties"]["id"]["type"], {"kind": "string"}
        )


class TestMockIsPinnedToContract(unittest.TestCase):
    """The mock serves only the operations the contract names."""

    @classmethod
    def setUpClass(cls):
        cls.server, cls.base_url = mock.start_mock()

    @classmethod
    def tearDownClass(cls):
        mock.stop_mock(cls.server)

    def request(self, method, path, body=None):
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_paths_outside_the_contract_are_not_served(self):
        for path in ("/v1/hosts", "/v1/domains", "/v1/credentials/tasks",
                     "/v1/tokens/refresh-token"):
            status, body = self.request("GET", path)
            self.assertEqual(status, 404, "%s should not be served: %s" % (path, body))

    def test_methods_outside_the_contract_are_not_served(self):
        status, body = self.request(
            "POST", "/v1/tokens/access-token/refresh", body="whatever"
        )
        self.assertEqual(
            status, 405,
            "refreshAccessToken is PATCH in the 9.0.0.0 spec; POST must be "
            "rejected, got %s %s" % (status, body),
        )

    def test_refresh_rejects_an_object_body(self):
        status, _ = self.request("POST", "/v1/tokens", body={
            "username": OPERATOR_USERNAME, "password": OPERATOR_PASSWORD})
        self.assertEqual(status, 201)
        status, body = self.request(
            "PATCH", "/v1/tokens/access-token/refresh",
            body={"refreshToken": mock.REFRESH_TOKEN_ID},
        )
        self.assertEqual(
            status, 400,
            "the refresh body is a bare JSON string in the spec, not an object",
        )
        self.assertEqual(body["errorCode"], "INVALID_REFRESH_TOKEN")


class TestRotationOutcome(RotationRun):

    def test_returns_the_rotated_credentials_and_task(self):
        self.assertIsInstance(self.result, dict)
        self.assertEqual(
            self.result.get("rotated_credential_ids"),
            EXPECTED_ROTATED_IDS,
            "the summary must list every and only rotated ESXI credential id "
            "in sorted order",
        )
        self.assertEqual(self.result.get("task_id"), TASK_ID)
        self.assertEqual(self.result.get("task_status"), "SUCCESSFUL")

    def test_reports_both_token_refreshes(self):
        self.assertEqual(
            self.result.get("token_refreshes"), 2,
            "the access token expires twice during this run",
        )


class TestRequestTranscript(RotationRun):

    def test_every_request_hit_a_contract_operation(self):
        stray = [
            "%s %s -> %s" % (e["method"], e["path"], e["status"])
            for e in self.log if e["operationId"] is None
        ]
        self.assertEqual(
            stray, [],
            "the client called paths or methods the contract does not name",
        )

    def test_transcript_matches_expected_operations_and_statuses(self):
        actual = [(e["operationId"], e["status"]) for e in self.log]
        self.assertEqual(
            actual, EXPECTED_TRANSCRIPT,
            "unexpected request sequence.\n  expected: %s\n  actual:   %s\n  raw: %s"
            % (EXPECTED_TRANSCRIPT, actual, self.summarise()),
        )

    def test_only_loopback_was_contacted(self):
        self.assertEqual(
            self.guard.offenders(), [],
            "the client resolved a non-loopback host; no live VMware endpoint "
            "may be contacted",
        )


class TestNoWorkIsLost(RotationRun):

    def test_rotation_is_submitted_exactly_once(self):
        self.assertEqual(
            len(self.calls("updateOrRotatePasswords")), 1,
            "the rotation must not be resubmitted after the token expires; the "
            "task created before the 401 is still the live one",
        )

    def test_authentication_happens_once_and_expiry_is_handled_by_refresh(self):
        self.assertEqual(
            len(self.calls("createToken")), 1,
            "re-authenticating with username and password discards the session; "
            "the refresh token exists for this",
        )
        self.assertEqual(len(self.calls("refreshAccessToken", 200)), 2)

    def test_inventory_pages_are_each_fetched_once(self):
        pages = [
            e["query"]["pageNumber"][0]
            for e in self.calls("getCredentials", 200)
        ]
        self.assertEqual(
            pages, ["0", "1", "2"],
            "paging must resume at the page that got the 401, not restart at 0",
        )

    def test_each_401_is_followed_by_a_refresh_then_an_identical_retry(self):
        indexes = [i for i, e in enumerate(self.log) if e["status"] == 401]
        self.assertEqual(len(indexes), 2, "expected exactly two expiry 401s")
        for i in indexes:
            self.assertLess(i + 2, len(self.log), "nothing followed the 401")
            refresh, retry, failed = self.log[i + 1], self.log[i + 2], self.log[i]
            self.assertEqual(
                refresh["operationId"], "refreshAccessToken",
                "a 401 must be answered by refreshing the access token",
            )
            self.assertEqual(refresh["status"], 200)
            for field in ("operationId", "method", "path", "query", "bodyRaw"):
                self.assertEqual(
                    retry[field], failed[field],
                    "the retry after refresh must repeat the same request; %s "
                    "differs" % field,
                )
            self.assertNotEqual(
                retry["headers"]["authorization"],
                failed["headers"]["authorization"],
                "the retry must carry the refreshed access token",
            )


class TestExpiryOnRotationSubmission(unittest.TestCase):
    """The generic 401 path must also cover the mutating authorized call."""

    def test_rotation_request_is_refreshed_and_replayed_unchanged(self):
        original_uses = mock.ACCESS_TOKEN_USES
        server = None
        try:
            # Three successful inventory pages consume the first token, placing
            # the deterministic expiry on updateOrRotatePasswords itself.
            mock.ACCESS_TOKEN_USES = 3
            server, base_url = mock.start_mock()
            from vcf_credrotate import rotate_credentials

            guard = LoopbackGuard()
            with guard:
                result = rotate_credentials(
                    base_url,
                    OPERATOR_USERNAME,
                    OPERATOR_PASSWORD,
                    resource_type="ESXI",
                    page_size=PAGE_SIZE,
                    poll_interval=0.0,
                )
            log = list(server.state.log)
        finally:
            if server is not None:
                mock.stop_mock(server)
            mock.ACCESS_TOKEN_USES = original_uses

        self.assertEqual(guard.offenders(), [])
        self.assertEqual(result["task_status"], "SUCCESSFUL")
        self.assertEqual(result["token_refreshes"], 1)
        self.assertEqual(
            [(entry["operationId"], entry["status"]) for entry in log],
            [
                ("createToken", 201),
                ("getCredentials", 200),
                ("getCredentials", 200),
                ("getCredentials", 200),
                ("updateOrRotatePasswords", 401),
                ("refreshAccessToken", 200),
                ("updateOrRotatePasswords", 202),
                ("getCredentialsTask", 200),
                ("getCredentialsTask", 200),
            ],
        )
        attempts = [
            entry for entry in log
            if entry["operationId"] == "updateOrRotatePasswords"
        ]
        self.assertEqual([entry["status"] for entry in attempts], [401, 202])
        for field in ("operationId", "method", "path", "query", "bodyRaw"):
            self.assertEqual(
                attempts[1][field], attempts[0][field],
                "rotation retry changed %s" % field,
            )
        self.assertNotEqual(
            attempts[1]["headers"]["authorization"],
            attempts[0]["headers"]["authorization"],
        )
        self.assertEqual(
            len([entry for entry in attempts if entry["status"] == 202]), 1,
            "only one rotation task may be created",
        )


class TestWireShape(RotationRun):

    def test_no_request_sends_a_null_or_empty_value(self):
        offences = []
        for entry in self.log:
            if entry["bodyIsJson"]:
                for path in walk_empty(entry["bodyJson"]):
                    offences.append("#%d %s body.%s" % (
                        entry["seq"], entry["operationId"], path))
            for name, values in entry["query"].items():
                if any(v == "" for v in values):
                    offences.append("#%d %s query.%s" % (
                        entry["seq"], entry["operationId"], name))
        self.assertEqual(
            sorted(offences), [],
            "unset optional fields must be omitted, not sent as null or empty",
        )

    def test_create_token_sends_only_the_fields_that_are_set(self):
        entry = self.calls("createToken")[0]
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], "/v1/tokens")
        self.assertEqual(entry["rawQuery"], "")
        self.assertIsNone(
            entry["headers"]["authorization"],
            "createToken is unauthenticated in the 9.0.0.0 spec",
        )
        self.assertEqual(
            (entry["headers"]["content-type"] or "").split(";")[0].strip(),
            "application/json",
        )
        self.assertEqual(
            entry["bodyJson"],
            {"username": OPERATOR_USERNAME, "password": OPERATOR_PASSWORD},
            "TokenCreationSpec.apiKey and .idToken are unset and must be omitted",
        )

    def test_refresh_sends_the_token_id_as_a_bare_json_string(self):
        for entry in self.calls("refreshAccessToken"):
            self.assertEqual(entry["method"], "PATCH", "the spec says PATCH, not POST")
            self.assertEqual(entry["path"], "/v1/tokens/access-token/refresh")
            self.assertEqual(entry["rawQuery"], "")
            self.assertIsNone(
                entry["headers"]["authorization"],
                "refreshAccessToken is unauthenticated in the pinned contract",
            )
            self.assertEqual(
                (entry["headers"]["content-type"] or "").split(";")[0].strip(),
                "application/json",
            )
            self.assertIsInstance(
                entry["bodyJson"], str,
                "the request body is the refresh token id as a bare JSON "
                "string, not an object wrapping it; got %r" % (entry["bodyRaw"],),
            )
            self.assertEqual(entry["bodyJson"], mock.REFRESH_TOKEN_ID)

    def test_get_credentials_sends_only_the_filters_that_are_set(self):
        entries = self.calls("getCredentials")
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertEqual(entry["method"], "GET")
            self.assertEqual(entry["path"], "/v1/credentials")
            self.assertEqual(entry["bodyRaw"], "")
            page = entry["query"].get("pageNumber", [None])[0]
            self.assertEqual(
                entry["query"],
                {"resourceType": ["ESXI"], "pageNumber": [page],
                 "pageSize": [str(PAGE_SIZE)]},
                "resourceName, resourceIp, domainName and accountType are unset "
                "and must not appear in the query string",
            )
            self.assertTrue(
                (entry["headers"]["authorization"] or "").startswith("Bearer "),
                "getCredentials is authorized with a bearer access token",
            )

    def test_rotation_spec_matches_the_credentials_update_schema(self):
        entry = self.calls("updateOrRotatePasswords")[0]
        self.assertEqual(entry["method"], "PATCH", "the spec says PATCH, not POST")
        self.assertEqual(entry["path"], "/v1/credentials")
        self.assertEqual(entry["rawQuery"], "")
        body = entry["bodyJson"]
        self.assertIsInstance(body, dict)
        self.assertEqual(
            sorted(body), ["elements", "operationType"],
            "autoRotatePolicy is unset and must be omitted from the body",
        )
        self.assertEqual(body["operationType"], "ROTATE")

        def normalise(element):
            copy = dict(element)
            copy["credentials"] = sorted(
                element.get("credentials") or [], key=lambda c: c.get("username", "")
            )
            return copy

        actual = sorted(
            (normalise(e) for e in body["elements"]),
            key=lambda e: e.get("resourceName", ""),
        )
        expected = sorted(
            (normalise(e) for e in EXPECTED_ELEMENTS),
            key=lambda e: e["resourceName"],
        )
        self.assertEqual(
            actual, expected,
            "one element per resource, carrying that resource's credentials, "
            "with password omitted because SDDC Manager generates it for ROTATE",
        )

    def test_task_is_polled_by_id_with_no_query_string(self):
        for entry in self.calls("getCredentialsTask"):
            self.assertEqual(entry["method"], "GET")
            self.assertEqual(entry["path"], "/v1/credentials/tasks/" + TASK_ID)
            self.assertEqual(entry["rawQuery"], "")
            self.assertEqual(entry["bodyRaw"], "")
            self.assertTrue(
                (entry["headers"]["authorization"] or "").startswith("Bearer ")
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
