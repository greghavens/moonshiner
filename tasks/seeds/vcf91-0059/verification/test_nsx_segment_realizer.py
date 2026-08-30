"""Protected acceptance verifier for the NSX Policy segment realizer."""

from __future__ import annotations

import base64
import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from mock_nsx_policy import MockNsxPolicy
from nsx_segment_realizer import (
    NsxApiError,
    NsxPolicyClient,
    NsxProtocolError,
    RealizationFailed,
    RealizationTimeout,
    SegmentSpec,
    SpecError,
    realize_segment,
)


ROOT = Path(__file__).parent
COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"


class ContractProvenanceTests(unittest.TestCase):
    def test_contract_is_the_pinned_spec_reduction(self):
        contract = json.loads((ROOT / "docs" / "contract.json").read_text())
        sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())

        self.assertEqual(contract["source"]["commit_sha"], COMMIT)
        self.assertEqual(contract["source"]["path"], SPEC_PATH)
        self.assertEqual(sources["repository_commit_sha"], COMMIT)
        self.assertEqual(sources["spec_path"], SPEC_PATH)
        self.assertIn(f"/blob/{COMMIT}/{SPEC_PATH}", sources["spec_url"])

        operation_ids = {
            "CreateOrReplaceInfraSegment",
            "ReadIntentStatus",
        }
        self.assertEqual(set(contract["operations"]), operation_ids)
        self.assertEqual(
            {item["operationId"] for item in sources["operation_ids"]},
            operation_ids,
        )
        self.assertEqual(
            contract["schemas"]["ConsolidatedRealizedStatus"]["properties"][
                "publish_status"
            ]["enum"],
            ["UNAVAILABLE", "UNREALIZED", "REALIZED", "ERROR"],
        )
        self.assertEqual(
            contract["openapi"]["securityDefinitions"]["BasicAuth"]["type"],
            "basic",
        )

    def test_mock_serves_no_operation_outside_the_contract(self):
        with MockNsxPolicy([{"publish_status": "REALIZED"}]) as mock:
            self.assertEqual(
                mock.named_operations,
                {"CreateOrReplaceInfraSegment", "ReadIntentStatus"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(mock.base_url + "/policy/api/v1/infra")
            self.assertEqual(raised.exception.code, 404)
            raised.exception.close()
            self.assertEqual(mock.request_log[0]["method"], "GET")


class SegmentRealizerTests(unittest.TestCase):
    def _client(self, mock, sleeps, *, max_polls=5):
        return NsxPolicyClient(
            mock.base_url,
            "automation",
            "s3cr3t",
            sleep=sleeps.append,
            poll_interval=0.25,
            max_polls=max_polls,
        )

    def test_exact_wire_shape_and_poll_to_realized(self):
        statuses = [
            {
                "publish_status": "UNREALIZED",
                "consolidated_status": {"consolidated_status": "IN_PROGRESS"},
            },
            {
                "publish_status": "REALIZED",
                "consolidated_status": {"consolidated_status": "SUCCESS"},
            },
        ]
        sleeps = []
        with MockNsxPolicy(statuses) as mock:
            client = self._client(mock, sleeps)
            spec = SegmentSpec(
                "blue-apps",
                connectivity_path="/infra/tier-1s/app",
                subnets=[{"gateway_address": "10.24.0.1/24"}],
            )
            result = realize_segment(client, "blue-apps", spec)
            log = list(mock.request_log)

        self.assertEqual(result["segment"]["id"], "blue-apps")
        self.assertEqual(result["realization"]["publish_status"], "REALIZED")
        self.assertEqual(sleeps, [0.25])
        self.assertEqual([item["method"] for item in log], ["PUT", "GET", "GET"])
        self.assertEqual(
            log[0]["target"],
            "/policy/api/v1/infra/segments/blue-apps",
        )
        self.assertEqual(
            log[1]["target"],
            "/policy/api/v1/infra/realized-state/status"
            "?intent_path=%2Finfra%2Fsegments%2Fblue-apps",
        )
        self.assertEqual(log[2]["target"], log[1]["target"])

        expected_body = (
            b'{"display_name":"blue-apps",'
            b'"connectivity_path":"/infra/tier-1s/app",'
            b'"subnets":[{"gateway_address":"10.24.0.1/24"}]}'
        )
        self.assertEqual(log[0]["body"], expected_body)
        body_document = json.loads(log[0]["body"])
        self.assertEqual(
            body_document,
            {
                "display_name": "blue-apps",
                "connectivity_path": "/infra/tier-1s/app",
                "subnets": [{"gateway_address": "10.24.0.1/24"}],
            },
        )
        for omitted in (
            "description",
            "transport_zone_path",
            "vlan_ids",
            "admin_state",
            "replication_mode",
            "tags",
            "dhcp_ranges",
            "dhcp_config",
        ):
            self.assertNotIn(omitted, body_document)

        auth = "Basic " + base64.b64encode(b"automation:s3cr3t").decode("ascii")
        for request in log:
            self.assertEqual(request["headers"].get("authorization"), auth)
            self.assertEqual(request["headers"].get("accept"), "application/json")
        self.assertEqual(
            log[0]["headers"].get("content-type"),
            "application/json",
        )
        self.assertNotIn("content-type", log[1]["headers"])
        self.assertNotIn("content-type", log[2]["headers"])
        self.assertNotIn("include_enforced_status", log[1]["target"])
        self.assertNotIn("site_path", log[1]["target"])
        self.assertFalse(log[1]["target"].endswith("?"))
        self.assertEqual(log[1]["body"], b"")
        self.assertEqual(log[2]["body"], b"")

    def test_explicit_status_query_options_are_encoded_not_blank(self):
        with MockNsxPolicy([{"publish_status": "REALIZED"}]) as mock:
            client = self._client(mock, [])
            document = client.read_intent_status(
                "/infra/segments/blue-apps",
                include_enforced_status=False,
                site_path="/infra/sites/denver",
            )
            entry = mock.request_log[0]

        self.assertEqual(document["publish_status"], "REALIZED")
        self.assertEqual(
            entry["target"],
            "/policy/api/v1/infra/realized-state/status"
            "?intent_path=%2Finfra%2Fsegments%2Fblue-apps"
            "&include_enforced_status=false"
            "&site_path=%2Finfra%2Fsites%2Fdenver",
        )

    def test_error_is_terminal_and_carries_final_document(self):
        statuses = [
            {"publish_status": "UNAVAILABLE"},
            {
                "publish_status": "ERROR",
                "consolidated_status": {"consolidated_status": "ERROR"},
            },
        ]
        sleeps = []
        with MockNsxPolicy(statuses) as mock:
            client = self._client(mock, sleeps)
            with self.assertRaises(RealizationFailed) as raised:
                realize_segment(client, "broken", SegmentSpec("broken"))
            log = list(mock.request_log)

        self.assertEqual(raised.exception.document["publish_status"], "ERROR")
        self.assertEqual(raised.exception.intent_path, "/infra/segments/broken")
        self.assertEqual(sleeps, [0.25])
        self.assertEqual([entry["method"] for entry in log], ["PUT", "GET", "GET"])

    def test_poll_limit_does_not_sleep_after_last_poll(self):
        sleeps = []
        with MockNsxPolicy([{"publish_status": "UNREALIZED"}]) as mock:
            client = self._client(mock, sleeps, max_polls=2)
            with self.assertRaises(RealizationTimeout) as raised:
                client.wait_for_realization("/infra/segments/slow")
            log = list(mock.request_log)

        self.assertEqual(raised.exception.polls, 2)
        self.assertEqual(raised.exception.intent_path, "/infra/segments/slow")
        self.assertEqual(sleeps, [0.25])
        self.assertEqual([entry["method"] for entry in log], ["GET", "GET"])

    def test_unknown_status_is_protocol_error(self):
        sleeps = []
        with MockNsxPolicy([{"publish_status": "QUEUED"}]) as mock:
            client = self._client(mock, sleeps)
            with self.assertRaises(NsxProtocolError):
                client.wait_for_realization("/infra/segments/odd")

        self.assertEqual(sleeps, [])
        self.assertEqual(len(mock.request_log), 1)

    def test_api_error_is_typed_and_does_not_leak_credentials(self):
        with MockNsxPolicy([{"publish_status": "REALIZED"}]) as mock:
            client = NsxPolicyClient(
                mock.base_url + "/not-the-policy-root",
                "automation",
                "s3cr3t",
                sleep=[].append,
                poll_interval=0.25,
                max_polls=5,
            )
            with self.assertRaises(NsxApiError) as raised:
                client.create_or_replace_segment(
                    "blue-apps", SegmentSpec("blue-apps")
                )
            log = list(mock.request_log)

        self.assertEqual(len(log), 1)
        message = str(raised.exception)
        self.assertNotIn("s3cr3t", message)
        self.assertNotIn(
            base64.b64encode(b"automation:s3cr3t").decode("ascii"),
            message,
        )

    def test_read_only_or_unknown_subnet_fields_fail_before_http(self):
        bad_subnets = [
            {"gateway_address": "10.24.0.1/24", "network": "10.24.0.0/24"},
            {"gateway_address": "10.24.0.1/24", "dns_servers": ["10.0.0.2"]},
        ]
        for subnet in bad_subnets:
            with self.subTest(subnet=subnet):
                with MockNsxPolicy([{"publish_status": "REALIZED"}]) as mock:
                    client = self._client(mock, [])
                    with self.assertRaises(SpecError):
                        realize_segment(
                            client,
                            "blue-apps",
                            SegmentSpec("blue-apps", subnets=[subnet]),
                        )
                    self.assertEqual(mock.request_log, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
