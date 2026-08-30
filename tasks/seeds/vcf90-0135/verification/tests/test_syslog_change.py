from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from mock_vcf import ContractMock
from vcf_networks import SyslogClient, SyslogMapping, SyslogTarget


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"

OPERATION_IDS = [
    "addSyslogTarget",
    "updateSyslogMapping",
    "updateSyslogStatus",
]
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
COMMIT_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


EXPECTED_OPERATIONS = [
    {
        "operationId": "addSyslogTarget",
        "method": "post",
        "path": "/settings/syslog",
        "security": [{"ApiKeyAuth": []}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _ref("SyslogTarget")}},
        },
        "responses": {
            "201": {"description": "Created"},
            "401": {"description": "Unauthorized", "schema": _ref("ApiError")},
            "400": {"description": "Bad Request", "schema": _ref("ApiError")},
            "409": {"description": "Conflict", "schema": _ref("ApiError")},
            "403": {"description": "Forbidden", "schema": _ref("ApiError")},
            "500": {"description": "Internal Error", "schema": _ref("ApiError")},
        },
    },
    {
        "operationId": "updateSyslogMapping",
        "method": "post",
        "path": "/settings/syslog/mapping",
        "security": [{"ApiKeyAuth": []}],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "array",
                        "items": _ref("SyslogMapperRequest"),
                    }
                }
            },
        },
        "responses": {
            "201": {
                "description": "Success",
                "schema": _ref("SyslogMapperResponse"),
            },
            "400": {"description": "Bad Request", "schema": _ref("ApiError")},
            "401": {"description": "Unauthorized", "schema": _ref("ApiError")},
            "403": {"description": "Forbidden", "schema": _ref("ApiError")},
            "500": {"description": "Internal Error", "schema": _ref("ApiError")},
        },
    },
    {
        "operationId": "updateSyslogStatus",
        "method": "patch",
        "path": "/settings/syslog/status",
        "security": [{"ApiKeyAuth": []}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": _ref("SyslogStatus")}},
        },
        "responses": {
            "200": {"description": "Success", "schema": _ref("SyslogStatus")},
            "401": {"description": "Unauthorized", "schema": _ref("ApiError")},
            "403": {"description": "Forbidden", "schema": _ref("ApiError")},
            "500": {"description": "Internal Error", "schema": _ref("ApiError")},
        },
    },
]

EXPECTED_SCHEMAS = {
    "SyslogTarget": {
        "type": "object",
        "properties": {
            "ip_or_fqdn": {"type": "string"},
            "port": {"type": "integer"},
            "protocol": {"type": "string"},
            "nick_name": {"type": "string"},
            "collector_id": {"type": "string"},
        },
    },
    "SyslogMapperRequest": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "syslog_source": {"type": "string"},
                "collector_id": {"type": "string"},
                "syslog_ip": {"type": "string"},
            },
        },
    },
    "SyslogMapperResponse": {
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "syslog_source": {"type": "string"},
                        "collector_id": {"type": "string"},
                        "syslog_ip": {"type": "string"},
                    },
                },
            }
        },
    },
    "SyslogStatus": {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean"},
            "change_time": {"type": "integer", "format": "int64"},
            "message": {"type": "string"},
        },
    },
    "ApiError": {
        "type": "object",
        "properties": {
            "code": {"type": "integer", "format": "int32"},
            "message": {"type": "string"},
            "details": {"type": "array", "items": _ref("ErrorDetail")},
        },
    },
    "ErrorDetail": {
        "type": "object",
        "properties": {
            "code": {"type": "integer", "format": "int32"},
            "message": {"type": "string"},
            "target": {"type": "array", "items": {"type": "string"}},
        },
    },
}


class ContractDocumentationTests(unittest.TestCase):
    def test_contract_is_the_exact_tagged_spec_subset(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["openapi"], "3.0.1")
        self.assertEqual(
            contract["info"],
            {
                "title": "VMware Cloud Foundation Operations for Networks API Reference",
                "version": "9.0.0.0",
            },
        )
        self.assertEqual(contract["servers"], [{"url": "/api/ni"}])
        self.assertEqual(
            contract["securitySchemes"],
            {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "description": "API Key - NetworkInsight {token}",
                    "name": "Authorization",
                    "in": "header",
                }
            },
        )
        self.assertEqual(contract["operations"], EXPECTED_OPERATIONS)
        self.assertEqual(contract["components"]["schemas"], EXPECTED_SCHEMAS)
        self.assertEqual(set(contract), {"openapi", "info", "servers", "securitySchemes", "operations", "components"})

    def test_official_sources_are_pinned_and_complete(self) -> None:
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            sources,
            {
                "repository": "https://github.com/vmware/vcf-api-specs",
                "tag": "9.0.0.0",
                "commit_sha": COMMIT_SHA,
                "spec_path": SPEC_PATH,
                "license": "Apache-2.0",
                "operation_ids": OPERATION_IDS,
            },
        )


class SyslogIntegrationTests(unittest.TestCase):
    def _client(self, server: ContractMock) -> SyslogClient:
        return SyslogClient(server.base_url, "fixture-token", timeout=2)

    def test_later_failure_preserves_earlier_success_and_exact_wire_shape(self) -> None:
        with ContractMock(
            CONTRACT_PATH,
            fail_operation_id="updateSyslogStatus",
        ) as server:
            report = self._client(server).apply_change(
                SyslogTarget("syslog.example.test", 514, "UDP"),
                [SyslogMapping("platform", "syslog.example.test")],
                enabled=True,
            )

        self.assertFalse(report.success)
        self.assertEqual(
            [(step.operation_id, step.success, step.status_code) for step in report.steps],
            [
                ("addSyslogTarget", True, 201),
                ("updateSyslogMapping", True, 201),
                ("updateSyslogStatus", False, 500),
            ],
        )
        self.assertIsNone(report.steps[0].error)
        self.assertIsNone(report.steps[1].error)
        self.assertIn("forced failure for updateSyslogStatus", report.steps[2].error or "")

        self.assertEqual(
            [(entry["operation_id"], entry["method"], entry["path"], entry["query"]) for entry in server.request_log],
            [
                ("addSyslogTarget", "POST", "/api/ni/settings/syslog", ""),
                ("updateSyslogMapping", "POST", "/api/ni/settings/syslog/mapping", ""),
                ("updateSyslogStatus", "PATCH", "/api/ni/settings/syslog/status", ""),
            ],
        )
        self.assertEqual(
            [json.loads(entry["body"]) for entry in server.request_log],
            [
                {"ip_or_fqdn": "syslog.example.test", "port": 514, "protocol": "UDP"},
                [[{"syslog_source": "platform", "syslog_ip": "syslog.example.test"}]],
                {"enabled": True},
            ],
        )
        self._assert_common_headers(server.request_log)
        self._assert_no_empty_optional_values(
            [json.loads(entry["body"]) for entry in server.request_log]
        )

    def test_set_optional_fields_are_sent_and_success_is_reported(self) -> None:
        with ContractMock(CONTRACT_PATH) as server:
            report = self._client(server).apply_change(
                SyslogTarget(
                    "192.0.2.44",
                    6514,
                    "UDP",
                    nick_name="audit sink",
                    collector_id="collector-8",
                ),
                [SyslogMapping("collector", "192.0.2.44", collector_id="collector-8")],
                enabled=False,
            )

        self.assertTrue(report.success)
        self.assertEqual(len(report.steps), 3)
        self.assertTrue(all(step.success and step.error is None for step in report.steps))
        bodies = [json.loads(entry["body"]) for entry in server.request_log]
        self.assertEqual(
            bodies,
            [
                {
                    "ip_or_fqdn": "192.0.2.44",
                    "port": 6514,
                    "protocol": "UDP",
                    "nick_name": "audit sink",
                    "collector_id": "collector-8",
                },
                [[
                    {
                        "syslog_source": "collector",
                        "collector_id": "collector-8",
                        "syslog_ip": "192.0.2.44",
                    }
                ]],
                {"enabled": False},
            ],
        )

    def test_failure_stops_later_operations_without_rewriting_history(self) -> None:
        with ContractMock(
            CONTRACT_PATH,
            fail_operation_id="updateSyslogMapping",
        ) as server:
            report = self._client(server).apply_change(
                SyslogTarget("198.51.100.7", 514, "UDP"),
                [SyslogMapping("auditLogs", "198.51.100.7")],
                enabled=True,
            )

        self.assertEqual(
            [(step.operation_id, step.success) for step in report.steps],
            [("addSyslogTarget", True), ("updateSyslogMapping", False)],
        )
        self.assertEqual(
            [entry["operation_id"] for entry in server.request_log],
            ["addSyslogTarget", "updateSyslogMapping"],
        )

    def test_transport_failure_is_reported_and_stops_later_operations(self) -> None:
        with ContractMock(
            CONTRACT_PATH,
            disconnect_operation_id="addSyslogTarget",
        ) as server:
            report = self._client(server).apply_change(
                SyslogTarget("203.0.113.9", 514, "UDP"),
                [SyslogMapping("platform", "203.0.113.9")],
                enabled=True,
            )

        self.assertFalse(report.success)
        self.assertEqual(len(report.steps), 1)
        self.assertEqual(report.steps[0].operation_id, "addSyslogTarget")
        self.assertFalse(report.steps[0].success)
        self.assertIsNone(report.steps[0].status_code)
        self.assertIn("transport error", report.steps[0].error or "")
        self.assertEqual(
            [entry["operation_id"] for entry in server.request_log],
            ["addSyslogTarget"],
        )

    def test_mock_rejects_routes_not_named_by_contract(self) -> None:
        with ContractMock(CONTRACT_PATH) as server:
            with self.assertRaises(HTTPError) as caught:
                urlopen(server.base_url + "/api/ni/settings/syslog/unlisted", timeout=2)
            try:
                self.assertEqual(caught.exception.code, 404)
            finally:
                caught.exception.close()
            self.assertIsNone(server.request_log[0]["operation_id"])

    def _assert_common_headers(self, log: list[dict[str, object]]) -> None:
        for entry in log:
            headers = entry["headers"]
            assert isinstance(headers, dict)
            self.assertEqual(headers.get("authorization"), "NetworkInsight fixture-token")
            self.assertEqual(headers.get("accept"), "application/json")
            self.assertEqual(headers.get("content-type"), "application/json")

    def _assert_no_empty_optional_values(self, value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                self.assertIsNotNone(item)
                self.assertNotEqual(item, "")
                self._assert_no_empty_optional_values(item)
        elif isinstance(value, list):
            for item in value:
                self._assert_no_empty_optional_values(item)


class PackagingTests(unittest.TestCase):
    def test_package_imports_only_the_standard_library(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"vcf_networks"}
        for path in (ROOT / "vcf_networks").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"non-stdlib import {name} in {path}")

    def test_project_declares_no_runtime_dependencies(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", text)


if __name__ == "__main__":
    unittest.main()
