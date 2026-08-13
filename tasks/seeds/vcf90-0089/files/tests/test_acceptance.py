from __future__ import annotations

import ast
import hashlib
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from tests.mock_server import ContractMock
from vcf_logs import VcfOperationsForLogsClient


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
TOKEN = "fixture-session-token"
WEBHOOK_ID = "8de56f4b-6977-4aa4-9808-ec7478cf8d5f"


class ContractProvenanceTests(unittest.TestCase):
    def test_contract_and_official_sources_are_pinned(self) -> None:
        self.assertEqual(
            hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
            "8dceee746d881c073921727be1fd8b9e2ecb0743d7998fa7d075ff7119bf0b20",
        )
        self.assertEqual(
            hashlib.sha256(SOURCES.read_bytes()).hexdigest(),
            "1df146702c5a1e95b1575d9ffcaa2317f7b7b312540a240fa31b69903571c310",
        )

        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES.read_text(encoding="utf-8"))
        operation_ids = [item["operationId"] for item in contract["operations"]]
        self.assertEqual(operation_ids, ["PUT_notification-webhook-webhookId"])
        self.assertEqual(operation_ids, sources["operationIds"])
        self.assertEqual(contract["source"]["tag"], "9.0.0.0")
        self.assertEqual(contract["source"]["commit"], sources["tagCommitSha"])
        self.assertEqual(contract["source"]["path"], sources["specPath"])
        self.assertNotIn("9.1", json.dumps(contract) + json.dumps(sources))

    def test_package_imports_only_standard_library_modules(self) -> None:
        imported_roots: set[str] = set()
        for source_path in (ROOT / "vcf_logs").glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.add(node.module.split(".", 1)[0])
        non_stdlib = sorted(imported_roots - sys.stdlib_module_names)
        self.assertEqual(non_stdlib, [], f"non-stdlib imports found: {non_stdlib}")


class WebhookUpdateTests(unittest.TestCase):
    def test_public_signatures_are_preserved(self) -> None:
        constructor = inspect.signature(VcfOperationsForLogsClient.__init__)
        self.assertEqual(
            list(constructor.parameters),
            ["self", "base_url", "token", "timeout"],
        )
        self.assertEqual(constructor.parameters["timeout"].default, 10.0)
        for name in ("self", "base_url", "token", "timeout"):
            self.assertEqual(
                constructor.parameters[name].kind,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )

        update = inspect.signature(VcfOperationsForLogsClient.update_webhook)
        self.assertEqual(
            list(update.parameters),
            [
                "self",
                "webhook_id",
                "proxy_id",
                "urls",
                "destination_app",
                "content_type",
                "payload",
                "name",
                "headers",
                "accept_cert",
                "send_individual_logs",
            ],
        )
        self.assertEqual(
            update.parameters["webhook_id"].kind,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for name in list(update.parameters)[2:]:
            self.assertEqual(
                update.parameters[name].kind,
                inspect.Parameter.KEYWORD_ONLY,
            )
            self.assertIsNone(update.parameters[name].default)

    def test_all_unset_values_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_log = Path(directory) / "requests.jsonl"
            with ContractMock(CONTRACT, request_log, TOKEN) as mock:
                client = VcfOperationsForLogsClient(mock.origin, TOKEN, timeout=2)
                response = client.update_webhook(WEBHOOK_ID)

                self.assertEqual(response, {"id": WEBHOOK_ID})
                record = json.loads(request_log.read_text(encoding="utf-8"))
                self.assertEqual(json.loads(record["body"]), {})

    def test_webhook_id_is_one_escaped_path_segment(self) -> None:
        webhook_id = "alerts/team one?active#blue"
        with tempfile.TemporaryDirectory() as directory:
            request_log = Path(directory) / "requests.jsonl"
            with ContractMock(CONTRACT, request_log, TOKEN) as mock:
                client = VcfOperationsForLogsClient(mock.origin, TOKEN, timeout=2)
                response = client.update_webhook(webhook_id, name="team alerts")

                self.assertEqual(response, {"id": webhook_id, "name": "team alerts"})
                record = json.loads(request_log.read_text(encoding="utf-8"))
                self.assertEqual(
                    record["path"],
                    "/api/v2/notification/webhook/" + quote(webhook_id, safe=""),
                )
                self.assertEqual(record["query"], "")

    def test_exact_wire_shape_and_retry_safe_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_log = Path(directory) / "requests.jsonl"
            with ContractMock(CONTRACT, request_log, TOKEN) as mock:
                client = VcfOperationsForLogsClient(mock.origin + "/", TOKEN, timeout=2)
                arguments = {
                    "urls": ["https://alerts.example.com/vcf"],
                    "destination_app": "custom",
                    "content_type": "JSON",
                    "accept_cert": False,
                }
                first = client.update_webhook(WEBHOOK_ID, **arguments)
                second = client.update_webhook(WEBHOOK_ID, **arguments)

                expected_body = {
                    "URLs": ["https://alerts.example.com/vcf"],
                    "destinationApp": "custom",
                    "contentType": "JSON",
                    "acceptCert": False,
                }
                self.assertEqual(first, {"id": WEBHOOK_ID, **expected_body})
                self.assertEqual(second, first)
                self.assertEqual(mock.state, {WEBHOOK_ID: expected_body})
                self.assertEqual(mock.effect_counts, {WEBHOOK_ID: 1})

                records = [
                    json.loads(line)
                    for line in request_log.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(len(records), 2)
                for record in records:
                    self.assertEqual(record["method"], "PUT")
                    self.assertEqual(
                        record["path"],
                        f"/api/v2/notification/webhook/{WEBHOOK_ID}",
                    )
                    self.assertEqual(record["query"], "")
                    self.assertEqual(record["headers"]["authorization"], f"Bearer {TOKEN}")
                    self.assertEqual(record["headers"]["accept"], "application/json")
                    self.assertEqual(record["headers"]["content-type"], "application/json")
                    self.assertNotIn("transfer-encoding", record["headers"])
                    self.assertEqual(
                        int(record["headers"]["content-length"]),
                        len(record["body"].encode("utf-8")),
                    )
                    self.assertEqual(json.loads(record["body"]), expected_body)
                    for omitted in (
                        "proxyId",
                        "payload",
                        "name",
                        "headers",
                        "sendIndividualLogs",
                    ):
                        self.assertNotIn(omitted, record["body"])

    def test_all_optional_values_use_the_contract_field_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_log = Path(directory) / "requests.jsonl"
            with ContractMock(CONTRACT, request_log, TOKEN) as mock:
                client = VcfOperationsForLogsClient(mock.origin, TOKEN, timeout=2)
                client.update_webhook(
                    WEBHOOK_ID,
                    proxy_id="760b1a86-e590-4890-939b-c504c491a072",
                    urls=["https://alerts.example.com/vcf"],
                    destination_app="custom",
                    content_type="JSON",
                    payload="{}",
                    name="VCF alerts",
                    headers='{"Action":"POST"}',
                    accept_cert=True,
                    send_individual_logs=False,
                )

                record = json.loads(request_log.read_text(encoding="utf-8"))
                self.assertEqual(
                    json.loads(record["body"]),
                    {
                        "proxyId": "760b1a86-e590-4890-939b-c504c491a072",
                        "URLs": ["https://alerts.example.com/vcf"],
                        "destinationApp": "custom",
                        "contentType": "JSON",
                        "payload": "{}",
                        "name": "VCF alerts",
                        "headers": '{"Action":"POST"}',
                        "acceptCert": True,
                        "sendIndividualLogs": False,
                    },
                )

    def test_mock_rejects_routes_not_named_by_the_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request_log = Path(directory) / "requests.jsonl"
            with ContractMock(CONTRACT, request_log, TOKEN) as mock:
                collection_request = Request(
                    mock.origin + "/api/v2/notification/webhook",
                    data=b"{}",
                    method="PUT",
                    headers={
                        "Authorization": f"Bearer {TOKEN}",
                        "Content-Type": "application/json",
                    },
                )
                with self.assertRaises(HTTPError) as collection_error:
                    urlopen(collection_request, timeout=2)
                self.assertEqual(collection_error.exception.code, 404)
                collection_error.exception.close()

                get_request = Request(
                    mock.origin + f"/api/v2/notification/webhook/{WEBHOOK_ID}",
                    method="GET",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                with self.assertRaises(HTTPError) as get_error:
                    urlopen(get_request, timeout=2)
                self.assertEqual(get_error.exception.code, 404)
                get_error.exception.close()


if __name__ == "__main__":
    unittest.main()
