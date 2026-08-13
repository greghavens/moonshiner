"""Protected verifier for the VCF Automation policy client.

Everything runs against the loopback mock in vcfa_mock.py, which is pinned to
docs/contract.json. No live VMware endpoint is contacted: the mock binds
127.0.0.1 on an ephemeral port and test_all_traffic_stayed_on_loopback proves
every request the client made went there.

The assertions are about the wire, not about the implementation: exact method,
exact path, exact query string, exact headers, and the exact JSON body -- in
particular that optional fields the caller left unset are absent from the body
rather than sent as null or as an empty value.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock as unittest_mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from vcfa_mock import LEASE_POLICY_TYPE_ID, POLICY_TYPES, MockVcfAutomation  # noqa: E402

from vcfa_policy import ApiError, PolicyClient, PolicyTypeNotFoundError  # noqa: E402

TOKEN = "unit-test-token"
POLICY_ID = "6f0a35c2-9b41-4d0e-8f1a-2c7e5b93ad10"
POLICY_NAME = "nightly-lease-cap"
DEFINITION = {"leaseGrace": 1, "leaseTermMax": 10, "leaseTotalTermMax": 100}
PROJECT_ID = "c1d9a0f4-3b77-4e21-9a55-08f6b2e14c3d"

REQUIRED_BODY_KEYS = {"id", "typeId", "name", "definition"}
OPTIONAL_BODY_KEYS = {
    "description",
    "enforcementType",
    "projectId",
    "orgId",
    "criteria",
    "scopeCriteria",
    "opaRegoCriteria",
}


def header(entry, name):
    """Case-insensitive header lookup, per RFC 9110."""
    wanted = name.lower()
    for key, value in entry["headers"]:
        if key.lower() == wanted:
            return value
    return None


def of_op(log, operation_id):
    return [e for e in log if e.get("operation_id") == operation_id]


class ContractTestCase(unittest.TestCase):
    maxDiff = None

    def make(self, fail_first_posts=None, **client_overrides):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log_path = os.path.join(tmp.name, "requests.jsonl")
        mock = MockVcfAutomation(
            token=TOKEN, fail_first_posts=fail_first_posts, log_path=log_path
        )
        mock.start()
        self.addCleanup(mock.stop)
        client_args = dict(
            timeout=10.0, max_attempts=4, backoff=lambda attempt: 0.0
        )
        client_args.update(client_overrides)
        client = PolicyClient(mock.base_url, TOKEN, **client_args)
        return mock, client

    def ensure(self, client, **overrides):
        kwargs = dict(
            policy_id=POLICY_ID,
            type_id=LEASE_POLICY_TYPE_ID,
            name=POLICY_NAME,
            definition=DEFINITION,
        )
        kwargs.update(overrides)
        return client.ensure_policy(**kwargs)


class TestRequestWireShape(ContractTestCase):
    def test_get_policy_type_returns_the_decoded_policy_type(self):
        mock, client = self.make()
        policy_type = client.get_policy_type(LEASE_POLICY_TYPE_ID)

        self.assertEqual(policy_type, POLICY_TYPES[LEASE_POLICY_TYPE_ID])
        calls = of_op(mock.read_log(), "getPolicyType")
        self.assertEqual(len(calls), 1)

    def test_policy_type_preflight_precedes_the_mutation(self):
        mock, client = self.make()
        self.ensure(client)
        log = mock.read_log()

        self.assertGreaterEqual(len(log), 2, "expected a preflight and a mutation")
        first = log[0]
        self.assertEqual(first["method"], "GET")
        self.assertEqual(first["path"], "/policy/api/policyTypes/" + LEASE_POLICY_TYPE_ID)
        self.assertEqual(first["query"], "", "getPolicyType documents no query parameters")
        self.assertEqual(first["operation_id"], "getPolicyType")
        self.assertEqual(first["body"], "", "a GET must not carry a body")
        self.assertEqual(header(first, "Authorization"), "Bearer " + TOKEN)
        self.assertEqual(header(first, "Accept"), "application/json")

        mutations = of_op(log, "createOrUpdatePolicy")
        self.assertTrue(mutations)
        self.assertLess(
            first["seq"],
            min(e["seq"] for e in mutations),
            "the policy type must be validated before anything is mutated",
        )

    def test_post_omits_optional_fields_the_caller_left_unset(self):
        mock, client = self.make()
        self.ensure(client)

        posts = of_op(mock.read_log(), "createOrUpdatePolicy")
        self.assertEqual(len(posts), 1)
        post = posts[0]

        self.assertEqual(post["method"], "POST")
        self.assertEqual(post["path"], "/policy/api/policies")
        self.assertEqual(
            post["query"], "", "dryRun and validationOnly were not requested; omit the query"
        )

        body = json.loads(post["body"])
        self.assertEqual(
            set(body),
            REQUIRED_BODY_KEYS,
            "only the fields the caller set may appear on the wire",
        )
        self.assertEqual(body["id"], POLICY_ID)
        self.assertEqual(body["typeId"], LEASE_POLICY_TYPE_ID)
        self.assertEqual(body["name"], POLICY_NAME)
        self.assertEqual(body["definition"], DEFINITION)

        for key in OPTIONAL_BODY_KEYS:
            self.assertNotIn(
                '"%s"' % key,
                post["body"],
                "unset optional field %r leaked onto the wire; it must be omitted, "
                "not sent as null or empty" % key,
            )
        self.assertNotIn("null", post["body"], "no field may be serialised as null")

    def test_post_headers_and_framing(self):
        mock, client = self.make()
        self.ensure(client)
        post = of_op(mock.read_log(), "createOrUpdatePolicy")[0]

        self.assertEqual(header(post, "Authorization"), "Bearer " + TOKEN)
        self.assertEqual(header(post, "Accept"), "application/json")
        content_type = header(post, "Content-Type")
        self.assertIsNotNone(content_type, "POST must declare a Content-Type")
        self.assertTrue(
            content_type.split(";")[0].strip().lower() == "application/json",
            "Content-Type must be application/json, got %r" % content_type,
        )
        self.assertEqual(
            int(header(post, "Content-Length")),
            post["body_bytes"],
            "Content-Length must match the encoded body",
        )
        self.assertEqual(
            post["body_bytes"],
            len(post["body"].encode("utf-8")),
            "the body must be UTF-8 encoded JSON",
        )

    def test_post_carries_optional_fields_when_the_caller_sets_them(self):
        mock, client = self.make()
        self.ensure(
            client,
            description="Cap nightly lab leases",
            enforcement_type="SOFT",
            project_id=PROJECT_ID,
            org_id="org-dedicated-lab",
        )
        post = of_op(mock.read_log(), "createOrUpdatePolicy")[0]
        body = json.loads(post["body"])

        self.assertEqual(
            set(body),
            REQUIRED_BODY_KEYS
            | {"description", "enforcementType", "projectId", "orgId"},
        )
        self.assertEqual(body["description"], "Cap nightly lab leases")
        self.assertEqual(body["enforcementType"], "SOFT")
        self.assertEqual(body["projectId"], PROJECT_ID)
        self.assertEqual(body["orgId"], "org-dedicated-lab")

    def test_base_url_with_a_trailing_slash_is_tolerated(self):
        mock, _ = self.make()
        client = PolicyClient(
            mock.base_url + "/",
            TOKEN,
            timeout=10.0,
            max_attempts=4,
            backoff=lambda attempt: 0.0,
        )

        stored = self.ensure(client)
        self.assertEqual(stored["id"], POLICY_ID)
        self.assertTrue(all(entry["status"] < 400 for entry in mock.read_log()))

    def test_read_back_uses_get_policy_without_a_query_string(self):
        mock, client = self.make()
        stored = self.ensure(client)

        gets = of_op(mock.read_log(), "getPolicy")
        self.assertGreaterEqual(len(gets), 1, "the stored policy must be read back")
        last = gets[-1]
        self.assertEqual(last["method"], "GET")
        self.assertEqual(last["path"], "/policy/api/policies/" + POLICY_ID)
        self.assertEqual(last["query"], "", "computeStats was not requested; omit the query")
        self.assertEqual(last["body"], "")
        self.assertEqual(header(last, "Authorization"), "Bearer " + TOKEN)

        self.assertIsInstance(stored, dict)
        self.assertEqual(stored["id"], POLICY_ID)
        self.assertEqual(stored["name"], POLICY_NAME)
        self.assertEqual(stored["definition"], DEFINITION)
        self.assertEqual(stored, mock.policies[POLICY_ID])


class TestRetrySafety(ContractTestCase):
    def test_accepted_success_is_read_back(self):
        mock, client = self.make(
            fail_first_posts=[{"status": 202, "apply": True, "success": True}]
        )
        stored = self.ensure(client)

        posts = of_op(mock.read_log(), "createOrUpdatePolicy")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["status"], 202)
        self.assertEqual(stored, mock.policies[POLICY_ID])

    def test_retry_after_a_lost_response_does_not_duplicate_the_policy(self):
        # The appliance applies the write, then the response is lost.
        mock, client = self.make(fail_first_posts=[{"status": 503, "apply": True}])
        stored = self.ensure(client)

        posts = of_op(mock.read_log(), "createOrUpdatePolicy")
        self.assertEqual(len(posts), 2, "a retryable 503 must be retried exactly once here")
        self.assertEqual(posts[0]["status"], 503)
        self.assertIn(posts[1]["status"], (200, 201))
        self.assertEqual(
            posts[0]["body"],
            posts[1]["body"],
            "the retry must resend the identical body, id included",
        )
        first_body = json.loads(posts[0]["body"])
        self.assertIn(
            "id",
            first_body,
            "the mutation must carry the caller's policy id; without it the "
            "appliance mints a fresh policy per attempt and the retry duplicates",
        )
        self.assertEqual(first_body["id"], POLICY_ID)

        self.assertEqual(
            len(mock.policies),
            1,
            "the retry created a second policy; the caller's id was not sent",
        )
        self.assertEqual(list(mock.policies), [POLICY_ID])
        self.assertEqual(stored["id"], POLICY_ID)

    def test_transport_failure_after_apply_is_retried_safely(self):
        mock, client = self.make(
            fail_first_posts=[{"status": 503, "apply": True, "disconnect": True}]
        )
        stored = self.ensure(client)

        posts = of_op(mock.read_log(), "createOrUpdatePolicy")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["status"], "connection-dropped")
        self.assertEqual(posts[1]["status"], 200)
        self.assertEqual(posts[0]["body"], posts[1]["body"])
        self.assertEqual(len(mock.policies), 1)
        self.assertEqual(stored["id"], POLICY_ID)

    def test_repeated_convergence_is_idempotent(self):
        mock, client = self.make()
        first = self.ensure(client)
        second = self.ensure(client)

        posts = of_op(mock.read_log(), "createOrUpdatePolicy")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["body"], posts[1]["body"])
        self.assertEqual(posts[0]["status"], 201, "first convergence creates")
        self.assertEqual(posts[1]["status"], 200, "second convergence updates in place")
        self.assertEqual(len(mock.policies), 1)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["createdAt"], second["createdAt"])

    def test_every_contract_retry_status_is_retried(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                mock, client = self.make(
                    fail_first_posts=[{"status": status, "apply": False}]
                )
                stored = self.ensure(client)

                posts = of_op(mock.read_log(), "createOrUpdatePolicy")
                self.assertEqual(len(posts), 2)
                self.assertEqual([entry["status"] for entry in posts], [status, 201])
                self.assertEqual(stored["id"], POLICY_ID)

    def test_contract_non_retry_statuses_are_not_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                mock, client = self.make(
                    fail_first_posts=[{"status": status, "apply": False}]
                )
                with self.assertRaises(ApiError) as caught:
                    self.ensure(client)

                self.assertEqual(caught.exception.status, status)
                self.assertEqual(caught.exception.method, "POST")
                self.assertEqual(caught.exception.path, "/policy/api/policies")
                posts = of_op(mock.read_log(), "createOrUpdatePolicy")
                self.assertEqual(len(posts), 1, "%s is not retryable" % status)
                self.assertEqual(mock.policies, {})

    def test_backoff_is_used_between_attempts(self):
        calls = []

        def backoff(attempt):
            calls.append(attempt)
            return attempt / 4.0

        mock, client = self.make(
            fail_first_posts=[{"status": 503, "apply": False}] * 2,
            backoff=backoff,
        )
        with unittest_mock.patch("vcfa_policy.client.time.sleep") as sleep:
            self.ensure(client)

        self.assertEqual(calls, [1, 2])
        self.assertEqual(
            sleep.call_args_list,
            [unittest_mock.call(0.25), unittest_mock.call(0.5)],
        )
        self.assertEqual(len(of_op(mock.read_log(), "createOrUpdatePolicy")), 3)

    def test_exhausted_retries_raise_the_last_api_error(self):
        mock, client = self.make(
            fail_first_posts=[{"status": 503, "apply": False}] * 6
        )
        with self.assertRaises(ApiError) as caught:
            self.ensure(client)

        self.assertEqual(caught.exception.status, 503)
        posts = of_op(mock.read_log(), "createOrUpdatePolicy")
        self.assertEqual(len(posts), 4, "max_attempts=4 means four POSTs, not more")

    def test_unknown_policy_type_aborts_before_mutating(self):
        mock, client = self.make()
        with self.assertRaises(PolicyTypeNotFoundError) as caught:
            self.ensure(client, type_id="com.vmware.policy.deployment.does-not-exist")

        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(
            of_op(mock.read_log(), "createOrUpdatePolicy"),
            [],
            "nothing may be mutated once the preflight fails",
        )
        self.assertEqual(mock.policies, {})

    def test_missing_policy_reads_back_as_none(self):
        mock, client = self.make()
        self.assertIsNone(client.get_policy("11111111-2222-3333-4444-555555555555"))

    def test_read_operations_surface_non_404_errors(self):
        for method_name, operation_id in (
            ("get_policy", "getPolicy"),
            ("get_policy_type", "getPolicyType"),
        ):
            with self.subTest(method=method_name):
                mock, _ = self.make()
                client = PolicyClient(
                    mock.base_url,
                    "wrong-token",
                    timeout=10.0,
                    max_attempts=4,
                    backoff=lambda attempt: 0.0,
                )
                with self.assertRaises(ApiError) as caught:
                    getattr(client, method_name)(LEASE_POLICY_TYPE_ID)

                self.assertEqual(caught.exception.status, 401)
                self.assertEqual(len(of_op(mock.read_log(), operation_id)), 1)

    def test_redirect_cannot_send_credentials_to_another_host(self):
        target, _ = self.make()
        mock, client = self.make(
            fail_first_posts=[
                {
                    "status": 302,
                    "apply": False,
                    "headers": {"Location": target.base_url + "/redirect-target"},
                }
            ]
        )

        with self.assertRaises(ApiError) as caught:
            self.ensure(client)

        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(target.read_log(), [], "the redirect target must not be contacted")
        self.assertEqual(len(of_op(mock.read_log(), "createOrUpdatePolicy")), 1)


class TestContractDiscipline(ContractTestCase):
    def test_client_only_calls_operations_the_contract_names(self):
        mock, client = self.make(fail_first_posts=[{"status": 503, "apply": True}])
        self.ensure(client, description="Cap nightly lab leases", project_id=PROJECT_ID)

        log = mock.read_log()
        self.assertTrue(log)
        for entry in log:
            self.assertIn(
                entry["operation_id"],
                mock.contract_operation_ids,
                "%s %s is not an operation named in docs/contract.json"
                % (entry["method"], entry["path"]),
            )
            self.assertNotEqual(entry["status"], 401, "every request must be authenticated")
            self.assertNotEqual(entry["status"], 405)

    def test_all_traffic_stayed_on_loopback(self):
        mock, client = self.make()
        self.ensure(client)
        expected = mock.base_url.split("//", 1)[1]
        for entry in mock.read_log():
            self.assertEqual(
                header(entry, "Host"),
                expected,
                "the client contacted something other than the loopback mock",
            )

    def test_contract_declares_its_documentation_provenance(self):
        with open(os.path.join(REPO_ROOT, "docs", "contract.json"), encoding="utf-8") as fh:
            contract = json.load(fh)
        self.assertEqual(contract["source_kind"], "reference-documentation")
        statement = contract["source_statement"].lower()
        self.assertIn("reference documentation", statement)
        self.assertIn("not from a published specification", statement)
        self.assertEqual(
            {op["operation_id"] for op in contract["operations"]},
            {"createOrUpdatePolicy", "getPolicy", "getPolicyType"},
        )

    def test_every_documented_operation_cites_a_fetched_source_page(self):
        path = os.path.join(REPO_ROOT, "docs", "official_sources.json")
        with open(path, encoding="utf-8") as fh:
            sources = json.load(fh)
        entries = sources["sources"]
        self.assertTrue(entries)
        cited = set()
        for entry in entries:
            self.assertTrue(entry["url"].startswith("https://developer.broadcom.com/xapis"))
            self.assertIn("operation", entry)
            self.assertTrue(entry["fetched_on"])
            self.assertTrue(entry["documents"])
            if entry["operation"]:
                cited.add(entry["operation"].split(" ", 1)[0])
        self.assertEqual(cited, {"createOrUpdatePolicy", "getPolicy", "getPolicyType"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
