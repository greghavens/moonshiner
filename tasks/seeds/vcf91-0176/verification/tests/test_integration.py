import json
import unittest

from tests.mock_log_management import contract_transport_mock
from vcf_operations import (
    LogForwarderUpdate,
    LogManagementClient,
    LogManagementError,
)


FORWARDER_ID = "archive/primary 01"
EXPECTED_PATH = "/api/v2/logs/forwarders/archive%2Fprimary%2001"
TOKEN = "fixture-jwt-token"


def make_update():
    return LogForwarderUpdate(
        certificate=None,
        connection_refresh_interval=0,
        constraints=None,
        enabled=True,
        forward_complementary_fields=None,
        host="syslog-archive.local",
        name="archive-primary",
        port=6514,
        protocol="SYSLOG",
        ssl_enabled=True,
        tags=None,
        transport_protocol="TCP",
        worker_count=0,
    )


EXPECTED_BODY = {
    "connectionRefreshInterval": 0,
    "enabled": True,
    "host": "syslog-archive.local",
    "name": "archive-primary",
    "port": 6514,
    "protocol": "SYSLOG",
    "sslEnabled": True,
    "transportProtocol": "TCP",
    "workerCount": 0,
}
EXPECTED_RAW_BODY = json.dumps(
    EXPECTED_BODY,
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")


class LogManagementIntegrationTests(unittest.TestCase):
    def test_wire_model_omits_none_and_preserves_explicit_falsy_values(self):
        update = LogForwarderUpdate(
            certificate="",
            connection_refresh_interval=0,
            constraints={},
            enabled=False,
            forward_complementary_fields=False,
            host="",
            name="",
            port=0,
            protocol=None,
            ssl_enabled=False,
            tags={},
            transport_protocol=None,
            worker_count=0,
        )
        self.assertEqual(
            update.to_wire(),
            {
                "certificate": "",
                "connectionRefreshInterval": 0,
                "constraints": {},
                "enabled": False,
                "forwardComplementaryFields": False,
                "host": "",
                "name": "",
                "port": 0,
                "sslEnabled": False,
                "tags": {},
                "workerCount": 0,
            },
        )

    def test_update_retries_identical_put_without_duplicate_effect(self):
        with contract_transport_mock(
            expected_token=TOKEN,
            drop_first_response=True,
        ) as mock:
            self.assertTrue(mock.base_url.startswith("http://127.0.0.1:"))
            client = LogManagementClient(
                mock.base_url + "/",
                TOKEN,
                timeout=1.0,
                max_attempts=2,
            )
            result = client.update_log_forwarder(FORWARDER_ID, make_update())

            self.assertEqual(result, {"id": FORWARDER_ID, **EXPECTED_BODY})
            self.assertEqual(mock.resource, result)
            self.assertEqual(mock.effect_count, 1)

            requests = mock.requests
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]["raw_body"], requests[1]["raw_body"])
            for request in requests:
                self.assertEqual(request["operationId"], "updateLogForwarder")
                self.assertEqual(request["method"], "PUT")
                self.assertEqual(request["raw_path"], EXPECTED_PATH)
                self.assertEqual(request["query"], "")
                self.assertEqual(request["headers"]["x-jwt-token"], TOKEN)
                self.assertEqual(
                    request["headers"]["content-type"],
                    "application/json",
                )
                self.assertEqual(request["headers"]["accept"], "application/json")
                self.assertEqual(
                    request["headers"]["content-length"],
                    str(len(EXPECTED_RAW_BODY)),
                )
                self.assertEqual(request["raw_body"], EXPECTED_RAW_BODY)
                self.assertEqual(request["body"], EXPECTED_BODY)
                for unset_name in (
                    "certificate",
                    "constraints",
                    "forwardComplementaryFields",
                    "id",
                    "tags",
                ):
                    self.assertNotIn(unset_name, request["body"])

    def test_http_failure_is_not_retried(self):
        with contract_transport_mock(
            expected_token=TOKEN,
            drop_first_response=False,
        ) as mock:
            client = LogManagementClient(
                mock.base_url,
                "wrong-token",
                timeout=1.0,
                max_attempts=3,
            )
            with self.assertRaises(LogManagementError) as caught:
                client.update_log_forwarder(FORWARDER_ID, make_update())
            self.assertEqual(caught.exception.status, 403)
            self.assertEqual(len(mock.requests), 1)
            self.assertEqual(mock.effect_count, 0)


if __name__ == "__main__":
    unittest.main()
