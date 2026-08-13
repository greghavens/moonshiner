"""Deterministic verifier for the VCF Operations for Logs client task."""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import call, patch

from mock_vcf_logs import VCFLogsMock
from vcf_logs import VCFLogsAPIError, VCFLogsClient
import vcf_logs.client as client_module


ROOT = Path(__file__).resolve().parent
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = (
    "specifications/vcf-operations/"
    "vcf-operations-for-logs-openapi.json"
)
EXPECTED_OPERATIONS = {
    ("POST_deployment-join", "POST", "/deployment/join"),
    (
        "POST_deployment-waitUntilStarted",
        "POST",
        "/deployment/waitUntilStarted",
    ),
}


class ContractTests(unittest.TestCase):
    def test_contract_and_provenance_are_pinned_to_vcf_9_0(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text())
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text()
        )
        self.assertEqual(sources["tag"], "9.0.0.0")
        self.assertEqual(sources["commit"], EXPECTED_COMMIT)
        self.assertEqual(sources["specificationPath"], EXPECTED_SPEC)
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(contract["openapi"], "3.0.1")
        self.assertEqual(contract["servers"], [{"url": "/api/v2"}])

        contract_operations = {
            (item["operationId"], item["method"], item["path"])
            for item in contract["operations"]
        }
        source_operations = {
            (item["operationId"], item["method"], item["path"])
            for item in sources["operations"]
        }
        self.assertEqual(contract_operations, EXPECTED_OPERATIONS)
        self.assertEqual(source_operations, EXPECTED_OPERATIONS)

        join = next(
            item
            for item in contract["operations"]
            if item["operationId"] == "POST_deployment-join"
        )
        self.assertEqual(
            join["request"]["schema"]["required"], ["masterFQDN"]
        )
        self.assertEqual(
            set(join["request"]["schema"]["properties"]),
            {"masterFQDN", "masterPort", "acceptCert"},
        )

    def test_package_uses_only_standard_library_imports(self) -> None:
        for path in (ROOT / "vcf_logs").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    self.assertIn(
                        name,
                        sys.stdlib_module_names,
                        f"{path.name} imports non-stdlib module {name!r}",
                    )


class WireTests(unittest.TestCase):
    def test_join_omits_unset_options_and_polls_to_terminal_response(self) -> None:
        with VCFLogsMock(retryable_polls=2) as mock:
            client = VCFLogsClient(mock.base_url, poll_interval=0)
            result = client.join_cluster_and_wait("li-master.example.com")
            requests = mock.request_log

        self.assertEqual(
            result,
            {
                "masterAddress": "192.0.2.10",
                "workerAddress": "192.0.2.11",
                "workerPort": 16520,
                "workerToken": "worker-token-0086",
                "masterUiPort": 443,
            },
        )
        self.assertEqual(len(requests), 4)

        join = requests[0]
        self.assertEqual(join["method"], "POST")
        self.assertEqual(join["path"], "/api/v2/deployment/join")
        self.assertEqual(join["query"], "")
        self.assertEqual(
            json.loads(join["body"]),
            {"masterFQDN": "li-master.example.com"},
        )
        self.assertEqual(
            join["headers"].get("content-type"), "application/json"
        )
        self.assertEqual(int(join["headers"]["content-length"]), len(join["body"]))
        decoded = json.loads(join["body"])
        self.assertNotIn("masterPort", decoded)
        self.assertNotIn("acceptCert", decoded)

        for poll in requests[1:]:
            self.assertEqual(poll["method"], "POST")
            self.assertEqual(
                poll["path"], "/api/v2/deployment/waitUntilStarted"
            )
            self.assertEqual(poll["query"], "")
            self.assertEqual(poll["body"], b"")

    def test_explicit_false_and_port_are_preserved(self) -> None:
        with VCFLogsMock(retryable_polls=0) as mock:
            client = VCFLogsClient(mock.base_url, poll_interval=0)
            client.join_cluster_and_wait(
                "li-master.example.com", master_port=9543, accept_cert=False
            )
            join = mock.request_log[0]

        self.assertEqual(
            json.loads(join["body"]),
            {
                "masterFQDN": "li-master.example.com",
                "masterPort": 9543,
                "acceptCert": False,
            },
        )

    def test_non_successful_join_raises_api_error_without_polling(self) -> None:
        with VCFLogsMock(retryable_polls=0, join_status=409) as mock:
            client = VCFLogsClient(mock.base_url, poll_interval=0)
            with self.assertRaises(VCFLogsAPIError):
                client.join_cluster_and_wait("li-master.example.com")
            requests = mock.request_log

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["path"], "/api/v2/deployment/join")

    def test_non_retryable_status_failure_raises_without_another_poll(self) -> None:
        with VCFLogsMock(
            retryable_polls=0, poll_error_status=503
        ) as mock:
            client = VCFLogsClient(mock.base_url, poll_interval=0)
            with self.assertRaises(VCFLogsAPIError):
                client.join_cluster_and_wait("li-master.example.com")
            requests = mock.request_log

        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[1]["path"],
            "/api/v2/deployment/waitUntilStarted",
        )

    def test_poll_interval_is_used_only_between_retryable_polls(self) -> None:
        try:
            with patch("time.sleep") as sleep:
                reloaded = importlib.reload(client_module)
                with VCFLogsMock(retryable_polls=2) as mock:
                    client = reloaded.VCFLogsClient(
                        mock.base_url, poll_interval=0.25
                    )
                    client.join_cluster_and_wait("li-master.example.com")
                with VCFLogsMock(retryable_polls=1) as mock:
                    client = reloaded.VCFLogsClient(
                        mock.base_url, poll_interval=0
                    )
                    client.join_cluster_and_wait("li-master.example.com")
        finally:
            importlib.reload(client_module)

        self.assertEqual(sleep.call_args_list, [call(0.25), call(0.25)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
