#!/usr/bin/env python3
"""Protected acceptance verifier for the VCF Logs client exercise."""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mock_vcf_logs import MockVcfLogs  # noqa: E402
from vcf_logs import LogsApiError, LogsClient, collect_event_queries  # noqa: E402


EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = (
    "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
)
EXPECTED_OPERATION_IDS = {"POST_sessions", "GET_events-+path"}


class ContractTests(unittest.TestCase):
    def test_pinned_official_9_0_contract(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text())
        sources = json.loads((ROOT / "docs" / "official_sources.json").read_text())

        self.assertEqual(contract["source"]["tag"], "9.0.0.0")
        self.assertEqual(contract["source"]["commitSha"], EXPECTED_COMMIT)
        self.assertEqual(contract["source"]["path"], EXPECTED_SPEC_PATH)
        self.assertEqual(set(contract["operations"]), EXPECTED_OPERATION_IDS)
        self.assertEqual(
            (
                contract["operations"]["POST_sessions"]["method"],
                contract["operations"]["POST_sessions"]["path"],
            ),
            ("POST", "/sessions"),
        )
        self.assertEqual(
            (
                contract["operations"]["GET_events-+path"]["method"],
                contract["operations"]["GET_events-+path"]["path"],
            ),
            ("GET", "/events/{+path}"),
        )
        self.assertEqual(sources["tag"], "9.0.0.0")
        self.assertEqual(sources["commitSha"], EXPECTED_COMMIT)
        self.assertEqual(sources["specPath"], EXPECTED_SPEC_PATH)
        self.assertEqual(
            {entry["operationId"] for entry in sources["operations"]},
            EXPECTED_OPERATION_IDS,
        )
        self.assertEqual(MockVcfLogs.contract_operation_ids, EXPECTED_OPERATION_IDS)

        query_parameters = contract["operations"]["GET_events-+path"]["parameters"]
        self.assertEqual(
            [parameter["name"] for parameter in query_parameters],
            [
                "+path",
                "limit",
                "timeout",
                "view",
                "content-pack-fields",
                "order-by-direction",
            ],
        )

    def test_package_imports_only_stdlib_or_itself(self) -> None:
        package = ROOT / "src" / "vcf_logs"
        for source_path in sorted(package.rglob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
            for node in ast.walk(tree):
                imported: list[str] = []
                if isinstance(node, ast.Import):
                    imported = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported = [node.module.split(".", 1)[0]]
                for module in imported:
                    self.assertTrue(
                        module == "vcf_logs" or module in sys.stdlib_module_names,
                        f"{source_path.relative_to(ROOT)} imports non-stdlib {module!r}",
                    )

    def test_mock_rejects_an_operation_outside_the_contract(self) -> None:
        with MockVcfLogs() as service:
            with self.assertRaises(HTTPError) as caught:
                build_opener(ProxyHandler({})).open(
                    service.origin + "/api/v2/datasets", timeout=2
                )
            error = caught.exception
            try:
                self.assertEqual(error.code, 404)
            finally:
                error.close()


class ClientTests(unittest.TestCase):
    def test_non_expiry_http_error_is_not_refreshed(self) -> None:
        with MockVcfLogs() as service:
            client = LogsClient(service.origin, "admin", "s3cret")
            client._session_id = "stale-session"
            with self.assertRaises(LogsApiError) as caught:
                client.query_events("text/CONTAINS denied")
            requests = service.requests

        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["method"], "GET")
        self.assertEqual(requests[0]["authorization"], "Bearer stale-session")

    def test_reserved_path_and_query_values_are_encoded(self) -> None:
        with MockVcfLogs() as service:
            client = LogsClient(
                service.origin,
                "directory-user",
                "p@ssword",
                provider="ActiveDirectory",
            )
            response = client.query_events(
                "text/CONTAINS a+b?c#d/field/EXISTS",
                content_pack_fields=["pack+one", "pack&two"],
            )
            requests = service.requests

        self.assertEqual(response["events"][0]["text"], "a+b?c#d event")
        self.assertEqual([request["method"] for request in requests], ["POST", "GET"])
        self.assertEqual(
            json.loads(requests[0]["body"]),
            {
                "username": "directory-user",
                "password": "p@ssword",
                "provider": "ActiveDirectory",
            },
        )
        target = urlsplit(requests[1]["path"])
        self.assertEqual(
            target.path,
            "/api/v2/events/text/CONTAINS%20a%2Bb%3Fc%23d/field/EXISTS",
        )
        self.assertEqual(
            parse_qs(target.query, keep_blank_values=True),
            {"content-pack-fields": ["pack+one", "pack&two"]},
        )

    def test_expired_session_refreshes_failed_query_without_losing_results(self) -> None:
        with MockVcfLogs() as service:
            client = LogsClient(service.origin, "admin", "s3cret")
            responses = collect_event_queries(
                client,
                [
                    {
                        "path": "text/CONTAINS error/timestamp/LAST 60000",
                        "limit": 2,
                        "timeout": 45000,
                        "view": "SIMPLE",
                        "content_pack_fields": ["base", "ops pack"],
                        "order_by_direction": "ASC",
                    },
                    {"path": "text/CONTAINS warning/timestamp/LAST 60000"},
                ],
            )
            requests = service.requests

        self.assertEqual(responses[0]["results"][0]["text"], "error event")
        self.assertEqual(responses[1]["events"][0]["text"], "warning event")
        self.assertEqual(
            [
                (request["method"], urlsplit(request["path"]).path)
                for request in requests
            ],
            [
                ("POST", "/api/v2/sessions"),
                (
                    "GET",
                    "/api/v2/events/text/CONTAINS%20error/timestamp/LAST%2060000",
                ),
                (
                    "GET",
                    "/api/v2/events/text/CONTAINS%20warning/timestamp/LAST%2060000",
                ),
                ("POST", "/api/v2/sessions"),
                (
                    "GET",
                    "/api/v2/events/text/CONTAINS%20warning/timestamp/LAST%2060000",
                ),
            ],
        )
        self.assertEqual(
            parse_qs(urlsplit(requests[1]["path"]).query, keep_blank_values=True),
            {
                "limit": ["2"],
                "timeout": ["45000"],
                "view": ["SIMPLE"],
                "content-pack-fields": ["base", "ops pack"],
                "order-by-direction": ["ASC"],
            },
        )

        for position in (0, 3):
            request = requests[position]
            self.assertEqual(
                json.loads(request["body"]),
                {"username": "admin", "password": "s3cret", "provider": "Local"},
            )
            self.assertEqual(request["content_type"], "application/json")
            self.assertIsNone(request["authorization"])

        self.assertEqual(
            [requests[position]["authorization"] for position in (1, 2, 4)],
            ["Bearer session-1", "Bearer session-1", "Bearer session-2"],
        )

        # None-valued optionals for the second query must not become empty or
        # default-valued query entries on either the failed or retried request.
        for position in (2, 4):
            self.assertNotIn("?", requests[position]["path"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
