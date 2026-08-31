"""Protected acceptance tests for the stdlib-only NSX Policy package."""

import json
from pathlib import Path
import unittest

from tests.mock_nsx import (
    BASIC_AUTHORIZATION,
    BASIC_PASSWORD,
    BASIC_USERNAME,
    ContractNsxMock,
    running_mock,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
SOURCES = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))


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
        self.assertEqual(CONTRACT["securityDefinitions"]["BasicAuth"], {"type": "basic"})
        self.assertEqual(CONTRACT["security"], [{"BasicAuth": []}])
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
    def test_upserts_in_order_without_replay_and_sorts_every_list(self):
        from nsx_policy import NsxPolicyClient, sync_segments

        desired = [
            {
                "resource_type": "Segment",
                "id": "segment-zeta",
                "display_name": "Zeta application",
                "description": "accepted first",
            },
            {
                "resource_type": "Segment",
                "id": "segment-alpha",
                "display_name": "Alpha application",
                "description": "accepted second",
            },
            {
                "resource_type": "Segment",
                "id": "segment-mu",
                "display_name": "Mu application",
                "description": "accepted third",
            },
        ]
        expected_ids = ["segment-alpha", "segment-mu", "segment-zeta"]

        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(
                origin, BASIC_USERNAME, BASIC_PASSWORD, timeout=2.0
            )
            result_one = sync_segments(client, desired)
            result_two = client.list_segments()
            result_three = client.list_segments()

        self.assertEqual([segment["id"] for segment in result_one], expected_ids)
        self.assertEqual([segment["id"] for segment in result_two], expected_ids)
        self.assertEqual([segment["id"] for segment in result_three], expected_ids)
        put_log = [
            event
            for event in mock.request_log
            if event["operation_id"] == "CreateOrReplaceInfraSegment"
        ]
        self.assertEqual(
            [event["path"].rsplit("/", 1)[-1] for event in put_log],
            ["segment-zeta", "segment-alpha", "segment-mu"],
            "the completed first PUT must not be replayed",
        )
        self.assertEqual([event["status"] for event in put_log], [200, 200, 200])
        self.assertTrue(
            all(event["authorization"] == BASIC_AUTHORIZATION for event in put_log)
        )
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
            all(event["authorization"] == BASIC_AUTHORIZATION for event in list_log)
        )

    def test_non_authentication_error_is_preserved(self):
        from nsx_policy import NsxPolicyClient, NsxPolicyError

        invalid = {"id": "broken", "display_name": "Missing resource type"}
        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(
                origin, BASIC_USERNAME, BASIC_PASSWORD, timeout=2.0
            )
            with self.assertRaises(NsxPolicyError) as caught:
                client.upsert_segment(invalid)

        error = caught.exception
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.error_code, 400012)
        self.assertEqual(error.error_message, "Invalid Segment")
        self.assertIn("id must match", error.details)
        self.assertEqual(error.payload["module_name"], "policy")
        self.assertEqual(len(mock.request_log), 1)
        self.assertEqual(mock.request_log[0]["status"], 400)

    def test_path_id_is_percent_encoded_and_body_is_not_mutated(self):
        from nsx_policy import NsxPolicyClient

        segment = {
            "resource_type": "Segment",
            "id": "blue floor/edge",
            "display_name": "Blue floor edge",
        }
        original = dict(segment)
        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(
                origin, BASIC_USERNAME, BASIC_PASSWORD, timeout=2.0
            )
            response = client.upsert_segment(segment)

        self.assertEqual(segment, original)
        self.assertEqual(response, original)
        self.assertEqual(
            mock.request_log[0]["path"],
            "/policy/api/v1/infra/segments/blue%20floor%2Fedge",
        )

    def test_rejected_basic_credentials_are_not_retried(self):
        from nsx_policy import NsxPolicyClient, NsxPolicyError

        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(origin, BASIC_USERNAME, "wrong", timeout=2.0)
            with self.assertRaises(NsxPolicyError) as caught:
                client.list_segments()

        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.error_code, 403)
        self.assertEqual(caught.exception.payload["module_name"], "common-services")
        list_attempts = [
            event
            for event in mock.request_log
            if event["operation_id"] == "ListAllInfraSegments"
        ]
        self.assertEqual(len(list_attempts), 1)
        self.assertEqual([event["status"] for event in list_attempts], [403])

    def test_local_segment_id_validation_sends_no_http(self):
        from nsx_policy import NsxPolicyClient

        with running_mock(CONTRACT) as (origin, mock):
            client = NsxPolicyClient(
                origin, BASIC_USERNAME, BASIC_PASSWORD, timeout=2.0
            )
            with self.assertRaises((TypeError, ValueError)):
                client.upsert_segment({"resource_type": "Segment", "id": ""})

        self.assertEqual(mock.request_log, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
