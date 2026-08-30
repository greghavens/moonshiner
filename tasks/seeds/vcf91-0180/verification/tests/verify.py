"""Protected acceptance verifier for vcf91-0180."""

from __future__ import annotations

import ast
import copy
import json
import math
import secrets
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_vcf import MockVcfOperations  # noqa: E402
from vcf_ops_diagnosis import (  # noqa: E402
    Diagnosis,
    Evidence,
    LogManagementClient,
    LogManagementError,
)

OPERATION_ID = "executeLogSearchQuery_1"
SEARCH_PATH = "/api/v2/logs/search"
SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"


def success_response(hits):
    return {
        "events": {"hits": hits, "total": len(hits)},
        "timeTakenMillis": 3,
        "timedOut": False,
    }


def field(name, value):
    return {
        "internalName": name,
        "value": value,
        "valueType": "STRING",
    }


def hit(fields, timestamp, message):
    return {
        "msgContent": {
            "fields": [field(name, value) for name, value in fields],
            "ingestTimestamp": timestamp + 2,
            "logTimestamp": timestamp,
            "originalText": message,
        }
    }


def compact(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def expected_query(identity_name, identity_value, event_type, start, end):
    return {
        "bool": {
            "filter": [
                {"match_phrase": {identity_name: identity_value}},
                {"match_phrase": {"event_type": event_type}},
                {
                    "range": {
                        "timestamp": {
                            "gte": str(start),
                            "lte": str(end),
                        }
                    }
                },
            ]
        }
    }


def expected_diagnostic_body(identity_name, identity_value, event_type, start, end):
    return {
        "query": expected_query(
            identity_name, identity_value, event_type, start, end
        ),
        "size": 25,
        "sort": [{"timestamp": {"order": "asc"}}],
        "trackTotalHits": False,
    }


class ContractTests(unittest.TestCase):
    def test_pinned_spec_projection_and_provenance(self):
        contract = json.loads(
            (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
        )
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )

        self.assertEqual("pinned-openapi-specification", contract["source"]["kind"])
        self.assertEqual(SHA, contract["source"]["repositoryCommitSha"])
        self.assertEqual(SPEC_PATH, contract["source"]["specPath"])
        self.assertEqual("4ada16fa39ec345674de4126174de94ea70d23a0",
                         contract["source"]["specBlobSha"])
        self.assertEqual("Apache-2.0", contract["source"]["license"])
        self.assertEqual("3.0.1", contract["source"]["openapi"])
        self.assertEqual("9.1.0.0", contract["source"]["apiVersion"])

        self.assertEqual(1, len(contract["operations"]))
        operation = contract["operations"][0]
        self.assertEqual(OPERATION_ID, operation["operationId"])
        self.assertEqual("POST", operation["method"])
        self.assertEqual(SEARCH_PATH, operation["path"])
        self.assertEqual("QueryRequest", operation["requestBody"]["schema"])
        self.assertEqual("QueryResponse", operation["responses"]["200"]["schema"])
        self.assertNotIn("/api/v2/search",
                         [item["path"] for item in contract["operations"]])

        self.assertEqual(
            {
                "type": "apiKey",
                "in": "header",
                "name": "X-JWT-Token",
            },
            contract["securitySchemes"]["OPSTokenAuthorization"],
        )
        query_request = contract["schemas"]["QueryRequest"]
        self.assertEqual(
            [
                "aggregations",
                "from",
                "indices",
                "query",
                "scroll",
                "scrollSize",
                "size",
                "sort",
                "trackTotalHits",
            ],
            query_request["propertyOrder"],
        )
        self.assertEqual([], query_request["required"])
        self.assertEqual(20000, query_request["properties"]["from"]["maximum"])
        self.assertEqual(2000, query_request["properties"]["size"]["maximum"])

        self.assertEqual(SHA, sources["repositoryCommitSha"])
        self.assertEqual(SPEC_PATH, sources["specPath"])
        self.assertEqual([OPERATION_ID], sources["operationIds"])
        self.assertFalse(
            sources["derivation"]["documentationPageUsedAsContractSource"]
        )
        self.assertEqual(1, len(sources["operations"]))
        source_operation = sources["operations"][0]
        self.assertEqual(OPERATION_ID, source_operation["operationId"])
        self.assertEqual(SHA, source_operation["repositoryCommitSha"])
        self.assertEqual(SPEC_PATH, source_operation["specPath"])
        self.assertIn(SHA, sources["rawSpecUrl"])

    def test_mock_rejects_every_operation_not_named_by_contract(self):
        with MockVcfOperations({}) as mock:
            request = urllib.request.Request(
                mock.base_url + SEARCH_PATH, method="GET"
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(404, raised.exception.code)
            raised.exception.close()
            self.assertEqual(1, len(mock.requests))
            self.assertIsNone(mock.requests[0]["operationId"])


class ClientWireTests(unittest.TestCase):
    def test_search_omits_unset_options_but_preserves_zero_and_false(self):
        token = "jwt-" + secrets.token_urlsafe(18)
        scripts = {OPERATION_ID: [(200, success_response([]))]}
        with MockVcfOperations(scripts) as mock:
            client = LogManagementClient(mock.base_url, token)
            query = {"match_all": {}}
            before = copy.deepcopy(query)
            response = client.search(
                query,
                from_=0,
                track_total_hits=False,
            )

            self.assertEqual(before, query)
            self.assertEqual(0, response["events"]["total"])
            self.assertEqual(1, len(mock.requests))
            request = mock.requests[0]
            expected = {
                "from": 0,
                "query": {"match_all": {}},
                "trackTotalHits": False,
            }
            self.assertEqual(expected, request["body"])
            self.assertEqual(compact(expected), request["raw_body"])
            self.assertEqual("POST", request["method"])
            self.assertEqual(SEARCH_PATH, request["raw_target"])
            self.assertEqual(OPERATION_ID, request["operationId"])
            self.assertEqual([token], request["headers"].get("x-jwt-token"))
            self.assertEqual(
                ["application/json"], request["headers"].get("accept")
            )
            self.assertEqual(
                ["application/json"], request["headers"].get("content-type")
            )
            self.assertNotIn("authorization", request["headers"])
            for optional in (
                "aggregations",
                "indices",
                "scroll",
                "scrollSize",
                "size",
                "sort",
            ):
                self.assertNotIn(optional, request["body"])

    def test_search_response_failures_are_safe_errors(self):
        token = "jwt-" + secrets.token_urlsafe(18)
        responses = [
            (200, {"timedOut": True, "events": {"hits": [], "total": 0}}),
            (
                200,
                {
                    "timedOut": False,
                    "failureReason": "QUERY",
                    "failureMessage": "bad focused query",
                },
            ),
            (
                403,
                {
                    "errorCode": "SECURITY_ERROR",
                    "errorMessage": "token was rejected",
                    "errorDetails": {"internal": "raw-body-marker"},
                },
            ),
        ]
        with MockVcfOperations({OPERATION_ID: responses}) as mock:
            client = LogManagementClient(mock.base_url, token)
            for _ in responses:
                with self.assertRaises(LogManagementError) as raised:
                    client.search({"match_all": {}})
                text = str(raised.exception)
                self.assertNotIn(token, text)
                self.assertNotIn("raw-body-marker", text)

    def test_constructor_validation(self):
        token = "jwt-" + secrets.token_urlsafe(18)
        invalid = [
            ("relative", token, 5.0),
            ("ftp://127.0.0.1", token, 5.0),
            ("http://user@127.0.0.1", token, 5.0),
            ("http://127.0.0.1/not-root", token, 5.0),
            ("http://127.0.0.1?x=1", token, 5.0),
            ("http://127.0.0.1#frag", token, 5.0),
            ("http://127.0.0.1", " ", 5.0),
            ("http://127.0.0.1", "bad\nheader", 5.0),
            ("http://127.0.0.1", token, True),
            ("http://127.0.0.1", token, 0),
            ("http://127.0.0.1", token, math.inf),
        ]
        for base_url, candidate_token, timeout in invalid:
            with self.subTest(base_url=base_url, timeout=timeout):
                with self.assertRaises((TypeError, ValueError)):
                    LogManagementClient(
                        base_url, candidate_token, timeout=timeout
                    )


class DiagnosisTests(unittest.TestCase):
    def make_runtime_case(self):
        request_id = "req-" + secrets.token_hex(10)
        correlation_id = "corr-" + secrets.token_hex(12)
        component = "sddc-manager-" + secrets.token_hex(4)
        token = "jwt-" + secrets.token_urlsafe(18)
        start = 1784736000123
        end = start + 300000
        failed_at = start + 41000
        event_at = start + 53000
        failure_message = "deployment failed; inspect structured fields"
        event_message = "certificate validation event recorded"
        failure_hit = hit(
            [
                ("request_id", request_id),
                ("event_type", "DEPLOYMENT_FAILED"),
                ("correlation_id", correlation_id),
            ],
            failed_at,
            failure_message,
        )
        event_hit = hit(
            [
                ("correlation_id", correlation_id),
                ("event_type", "CERTIFICATE_VALIDATION_FAILED"),
                ("certificate_status", "EXPIRED"),
                ("component", component),
            ],
            event_at,
            event_message,
        )
        return {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "component": component,
            "token": token,
            "start": start,
            "end": end,
            "failed_at": failed_at,
            "event_at": event_at,
            "failure_message": failure_message,
            "event_message": event_message,
            "failure_hit": failure_hit,
            "event_hit": event_hit,
        }

    def test_diagnosis_uses_two_ordered_structured_evidence_searches(self):
        case = self.make_runtime_case()
        scripts = {
            OPERATION_ID: [
                (200, success_response([case["failure_hit"]])),
                (200, success_response([case["event_hit"]])),
            ]
        }
        with MockVcfOperations(scripts) as mock:
            client = LogManagementClient(mock.base_url, case["token"])
            diagnosis = client.diagnose_deployment_failure(
                case["request_id"], case["start"], case["end"]
            )

            self.assertIsInstance(diagnosis, Diagnosis)
            self.assertEqual("diagnosed", diagnosis.status)
            self.assertEqual(case["request_id"], diagnosis.request_id)
            self.assertEqual(case["correlation_id"], diagnosis.correlation_id)
            self.assertEqual("certificate_expired", diagnosis.cause)
            self.assertEqual(case["component"], diagnosis.component)
            self.assertEqual(
                (
                    Evidence(
                        "DEPLOYMENT_FAILED",
                        case["failed_at"],
                        case["failure_message"],
                    ),
                    Evidence(
                        "CERTIFICATE_VALIDATION_FAILED",
                        case["event_at"],
                        case["event_message"],
                    ),
                ),
                diagnosis.evidence,
            )

            self.assertEqual(2, len(mock.requests))
            expected_first = expected_diagnostic_body(
                "request_id",
                case["request_id"],
                "DEPLOYMENT_FAILED",
                case["start"],
                case["end"],
            )
            expected_second = expected_diagnostic_body(
                "correlation_id",
                case["correlation_id"],
                "CERTIFICATE_VALIDATION_FAILED",
                case["start"],
                case["end"],
            )
            self.assertEqual(
                [expected_first, expected_second],
                [request["body"] for request in mock.requests],
            )
            self.assertEqual(
                [compact(expected_first), compact(expected_second)],
                [request["raw_body"] for request in mock.requests],
            )
            for request in mock.requests:
                self.assertEqual("POST", request["method"])
                self.assertEqual(SEARCH_PATH, request["raw_target"])
                self.assertEqual("", request["query"])
                self.assertEqual(OPERATION_ID, request["operationId"])
                self.assertEqual(
                    [case["token"]],
                    request["headers"].get("x-jwt-token"),
                )
                self.assertNotIn("authorization", request["headers"])
                self.assertEqual(
                    ["query", "size", "sort", "trackTotalHits"],
                    list(request["body"]),
                )
                for omitted in (
                    "aggregations",
                    "from",
                    "indices",
                    "scroll",
                    "scrollSize",
                ):
                    self.assertNotIn(omitted, request["body"])

    def test_ambiguous_failure_log_stops_before_event_search(self):
        case = self.make_runtime_case()
        duplicate = copy.deepcopy(case["failure_hit"])
        duplicate["msgContent"]["logTimestamp"] += 1
        scripts = {
            OPERATION_ID: [
                (200, success_response([case["failure_hit"], duplicate]))
            ]
        }
        with MockVcfOperations(scripts) as mock:
            client = LogManagementClient(mock.base_url, case["token"])
            with self.assertRaises(LogManagementError):
                client.diagnose_deployment_failure(
                    case["request_id"], case["start"], case["end"]
                )
            self.assertEqual(1, len(mock.requests))

    def test_message_text_cannot_substitute_for_structured_event_evidence(self):
        case = self.make_runtime_case()
        inconsistent = hit(
            [
                ("correlation_id", case["correlation_id"]),
                ("event_type", "CERTIFICATE_VALIDATION_FAILED"),
                ("certificate_status", "VALID"),
                ("component", case["component"]),
            ],
            case["event_at"],
            "EXPIRED certificate for the component",
        )
        scripts = {
            OPERATION_ID: [
                (200, success_response([case["failure_hit"]])),
                (200, success_response([inconsistent])),
            ]
        }
        with MockVcfOperations(scripts) as mock:
            client = LogManagementClient(mock.base_url, case["token"])
            with self.assertRaises(LogManagementError):
                client.diagnose_deployment_failure(
                    case["request_id"], case["start"], case["end"]
                )
            self.assertEqual(2, len(mock.requests))

    def test_diagnosis_input_validation_makes_no_request(self):
        case = self.make_runtime_case()
        with MockVcfOperations({}) as mock:
            client = LogManagementClient(mock.base_url, case["token"])
            invalid = [
                (" ", case["start"], case["end"]),
                (" padded ", case["start"], case["end"]),
                (case["request_id"], True, case["end"]),
                (case["request_id"], -1, case["end"]),
                (case["request_id"], case["start"], False),
                (case["request_id"], case["start"], case["start"]),
                (case["request_id"], case["start"], case["start"] - 1),
            ]
            for request_id, start, end in invalid:
                with self.subTest(request_id=request_id, start=start, end=end):
                    with self.assertRaises((TypeError, ValueError)):
                        client.diagnose_deployment_failure(
                            request_id, start, end
                        )
            self.assertEqual([], mock.requests)


class PackagingTests(unittest.TestCase):
    def test_standard_library_only_and_empty_dependencies(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", pyproject)

        allowed_internal = {"vcf_ops_diagnosis"}
        for path in (ROOT / "vcf_ops_diagnosis").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    roots = [(node.module or "").split(".", 1)[0]]
                else:
                    continue
                for root in roots:
                    self.assertTrue(
                        root in sys.stdlib_module_names or root in allowed_internal,
                        f"non-stdlib import {root!r} in {path.name}",
                    )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
