"""Protected acceptance tests for the stdlib-only NSX Policy package."""

import json
from pathlib import Path
import unittest

from tests.mock_nsx import FRESH_TOKEN, OLD_TOKEN, ContractNsxMock, running_mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
SOURCES = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))


class TokenSequence:
    def __init__(self, *tokens):
        self.tokens = tokens
        self.calls = 0

    def __call__(self):
        index = min(self.calls, len(self.tokens) - 1)
        self.calls += 1
        return self.tokens[index]


class ContractMetadataTests(unittest.TestCase):
    def test_contract_is_pinned_to_vcf_91_specification(self):
        self.assertEqual(
            SOURCES["repository_commit_sha"],
            "c3f3b52c845dd967cabbc21680e893292077d5ba",
        )
        self.assertEqual(
            SOURCES["spec_path"],
            "specifications/nsx/openapi-2.0/nsx_policy_api.yaml",
        )
        self.assertEqual(CONTRACT["swagger"], "2.0")
        self.assertEqual(CONTRACT["base_path"], "/policy/api/v1")
        self.assertEqual(
            set(SOURCES["operation_ids"]),
            {"CreateOrReplaceInfraSegment", "ListAllInfraSegments"},
        )
        self.assertEqual(set(CONTRACT["operations"]), set(SOURCES["operation_ids"]))
        for source_operation in SOURCES["operations"]:
            operation = CONTRACT["operations"][source_operation["operationId"]]
            self.assertEqual(operation["method"], source_operation["method"])
            self.assertEqual(
                CONTRACT["base_path"] + operation["path"],
                source_operation["path"],
            )

    def test_mock_routes_only_contract_operations(self):
        mock = ContractNsxMock(CONTRACT)
        self.assertEqual(mock.allowed_operation_ids, frozenset(CONTRACT["operations"]))
        self.assertEqual(
            mock.operation_for("GET", "/policy/api/v1/infra/segments"),
            ("ListAllInfraSegments", None),
        )
        self.assertEqual(
            mock.operation_for("PUT", "/policy/api/v1/infra/segments/blue"),
            ("CreateOrReplaceInfraSegment", "blue"),
        )
        self.assertEqual(
            mock.operation_for("POST", "/policy/api/v1/infra/segments"),
            (None, None),
        )
        self.assertEqual(mock.operation_for("GET", "/oauth/token"), (None, None))


class NsxPolicyIntegrationTests(unittest.TestCase):
    def test_mid_run_expiry_retries_only_failed_put_and_sorts_every_list(self):
        from nsx_policy import NsxPolicyClient, sync_segments

        tokens = TokenSequence(OLD_TOKEN, FRESH_TOKEN)
        desired = [
            {
                "resource_type": "Segment",
                "id": "segment-zeta",
                "display_name": "Zeta application",
                "description": "accepted before token expiry",
            },
            {
                "resource_type": "Segment",
                "id": "segment-alpha",
                "display_name": "Alpha application",
                "description": "the failed request is replayed after refresh",
            },
            {
                "resource_type": "Segment",
                "id": "segment-mu",
                "display_name": "Mu application",
                "description": "work continues with the fresh token",
            },
        ]
        expected_ids = ["segment-alpha", "segment-mu", "segment-zeta"]

        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(origin, tokens, timeout=2.0)
            result_one = sync_segments(client, desired)
            result_two = client.list_segments()
            result_three = client.list_segments()

        self.assertEqual([segment["id"] for segment in result_one], expected_ids)
        self.assertEqual([segment["id"] for segment in result_two], expected_ids)
        self.assertEqual([segment["id"] for segment in result_three], expected_ids)
        self.assertEqual(tokens.calls, 2, "one initial token and one refresh")

        put_log = [
            event
            for event in mock.request_log
            if event["operation_id"] == "CreateOrReplaceInfraSegment"
        ]
        self.assertEqual(
            [event["path"].rsplit("/", 1)[-1] for event in put_log],
            ["segment-zeta", "segment-alpha", "segment-alpha", "segment-mu"],
            "the completed first PUT must not be replayed",
        )
        self.assertEqual([event["status"] for event in put_log], [200, 401, 200, 200])
        self.assertEqual(
            [event["authorization"] for event in put_log],
            [
                f"Bearer {OLD_TOKEN}",
                f"Bearer {OLD_TOKEN}",
                f"Bearer {FRESH_TOKEN}",
                f"Bearer {FRESH_TOKEN}",
            ],
        )
        self.assertEqual(put_log[1]["body"], put_log[2]["body"])
        self.assertEqual(put_log[0]["body"], desired[0])
        self.assertTrue(all("application/json" in event["accept"] for event in put_log))
        self.assertTrue(
            all("application/json" in event["content_type"] for event in put_log)
        )

        list_log = [
            event
            for event in mock.request_log
            if event["operation_id"] == "ListAllInfraSegments"
        ]
        self.assertEqual(len(list_log), 3)
        self.assertNotEqual(list_log[0]["response_ids"], list_log[1]["response_ids"])
        self.assertNotEqual(list_log[1]["response_ids"], list_log[2]["response_ids"])
        self.assertEqual(list_log[0]["response_ids"], list_log[2]["response_ids"])
        self.assertTrue(
            all(event["authorization"] == f"Bearer {FRESH_TOKEN}" for event in list_log)
        )

    def test_non_401_error_is_preserved_and_does_not_refresh(self):
        from nsx_policy import NsxPolicyClient, NsxPolicyError

        tokens = TokenSequence(FRESH_TOKEN)
        invalid = {"id": "broken", "display_name": "Missing resource type"}
        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(origin, tokens, timeout=2.0)
            with self.assertRaises(NsxPolicyError) as caught:
                client.upsert_segment(invalid)

        error = caught.exception
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.error_code, 400012)
        self.assertEqual(error.error_message, "Invalid Segment")
        self.assertIn("id must match", error.details)
        self.assertEqual(error.payload["module_name"], "policy")
        self.assertEqual(tokens.calls, 1)
        self.assertEqual(len(mock.request_log), 1)
        self.assertEqual(mock.request_log[0]["status"], 400)

    def test_path_id_is_percent_encoded_and_body_is_not_mutated(self):
        from nsx_policy import NsxPolicyClient

        tokens = TokenSequence(FRESH_TOKEN)
        segment = {
            "resource_type": "Segment",
            "id": "blue floor/edge",
            "display_name": "Blue floor edge",
        }
        original = dict(segment)
        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(origin, tokens, timeout=2.0)
            response = client.upsert_segment(segment)

        self.assertEqual(segment, original)
        self.assertEqual(response, original)
        self.assertEqual(
            mock.request_log[0]["path"],
            "/policy/api/v1/infra/segments/blue%20floor%2Fedge",
        )

    def test_refreshed_token_is_replayed_at_most_once(self):
        from nsx_policy import NsxPolicyClient, NsxPolicyError

        tokens = TokenSequence(OLD_TOKEN, OLD_TOKEN, OLD_TOKEN)
        first = {
            "resource_type": "Segment",
            "id": "first",
            "display_name": "First",
        }
        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(origin, tokens, timeout=2.0)
            client.upsert_segment(first)
            with self.assertRaises(NsxPolicyError) as caught:
                client.list_segments()

        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(tokens.calls, 2)
        list_attempts = [
            event
            for event in mock.request_log
            if event["operation_id"] == "ListAllInfraSegments"
        ]
        self.assertEqual(len(list_attempts), 2)
        self.assertEqual([event["status"] for event in list_attempts], [401, 401])

    def test_local_segment_id_validation_sends_no_http(self):
        from nsx_policy import NsxPolicyClient

        tokens = TokenSequence(FRESH_TOKEN)
        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(origin, tokens, timeout=2.0)
            with self.assertRaises((TypeError, ValueError)):
                client.upsert_segment({"resource_type": "Segment", "id": ""})

        self.assertEqual(tokens.calls, 0)
        self.assertEqual(mock.request_log, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
