#!/usr/bin/env python3
"""Protected acceptance verifier for the VCF Automation client."""

from __future__ import annotations

import ast
import inspect
import json
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mock_vcfa import ContractMock


# Patch sleep while the package is imported as well as while the polling test
# runs.  This covers both ``import time`` and ``from time import sleep`` without
# making the verifier wait or relying on wall-clock timing.
_ORIGINAL_SLEEP = time.sleep
_SLEEP_CALLS = []


def _record_sleep(seconds):
    _SLEEP_CALLS.append(seconds)


time.sleep = _record_sleep
try:
    from vcf_automation import (
        ProtocolError,
        RequestFailed,
        VCFAutomationClient,
        VCFAutomationError,
    )
finally:
    time.sleep = _ORIGINAL_SLEEP


class ClientWireTests(unittest.TestCase):
    def assert_common_headers(self, request, token="fixture-token"):
        self.assertEqual(request["headers"].get("accept"), "application/json")
        self.assertEqual(request["headers"].get("authorization"), f"Bearer {token}")

    def assert_create_wire(self, request, expected_body, token="fixture-token"):
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["target"], "/deployment/api/resources")
        self.assertEqual(request["path"], "/deployment/api/resources")
        self.assertEqual(request["query"], "")
        self.assert_common_headers(request, token)
        self.assertEqual(request["headers"].get("content-type"), "application/json")
        self.assertEqual(int(request["headers"].get("content-length", "-1")), len(request["body"]))
        self.assertEqual(json.loads(request["body"].decode("utf-8")), expected_body)

    def assert_poll_wire(self, request, request_id="req-314", token="fixture-token"):
        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["target"], f"/deployment/api/requests/{request_id}")
        self.assertEqual(request["path"], f"/deployment/api/requests/{request_id}")
        self.assertEqual(request["query"], "")
        self.assertEqual(request["body"], b"")
        self.assert_common_headers(request, token)
        self.assertNotIn("content-type", request["headers"])

    def test_required_only_shape_omits_every_unset_optional_and_polls_to_success(self):
        _SLEEP_CALLS.clear()
        time.sleep = _record_sleep
        try:
            with ContractMock(["INPROGRESS", "COMPLETION", "SUCCESSFUL"]) as mock:
                client = VCFAutomationClient(mock.url + "/", "fixture-token")
                result = client.create_resource_and_wait("edge-cache", "Custom.Edge")
        finally:
            time.sleep = _ORIGINAL_SLEEP

        self.assertEqual(result["status"], "SUCCESSFUL")
        self.assertEqual(result, mock.responses[-1])
        self.assertEqual(_SLEEP_CALLS, [])
        self.assertEqual([request["method"] for request in mock.requests], ["POST", "GET", "GET", "GET"])
        self.assert_create_wire(
            mock.requests[0],
            {"name": "edge-cache", "type": "Custom.Edge"},
        )
        body = json.loads(mock.requests[0]["body"].decode("utf-8"))
        self.assertNotIn("deploymentId", body)
        self.assertNotIn("projectId", body)
        self.assertNotIn("properties", body)
        for request in mock.requests[1:]:
            self.assert_poll_wire(request)

    def test_set_optional_fields_use_documented_wire_names(self):
        with ContractMock(["SUCCESSFUL"], request_id="request-829") as mock:
            client = VCFAutomationClient(mock.url, "alternate-token")
            result = client.create_resource_and_wait(
                "database",
                "Custom.Database",
                deployment_id="dep-7",
                project_id="project-9",
                properties={"tier": "gold", "replicas": 2},
            )

        self.assertEqual(result["status"], "SUCCESSFUL")
        self.assertEqual(len(mock.requests), 2)
        self.assert_create_wire(
            mock.requests[0],
            {
                "name": "database",
                "type": "Custom.Database",
                "deploymentId": "dep-7",
                "projectId": "project-9",
                "properties": {"tier": "gold", "replicas": 2},
            },
            token="alternate-token",
        )
        self.assert_poll_wire(
            mock.requests[1], request_id="request-829", token="alternate-token"
        )

    def test_explicit_empty_optional_values_are_not_treated_as_unset(self):
        with ContractMock(["SUCCESSFUL"]) as mock:
            client = VCFAutomationClient(mock.url, "fixture-token")
            client.create_resource_and_wait(
                "empty-values",
                "Custom.Empty",
                deployment_id="",
                project_id="",
                properties={},
            )

        self.assert_create_wire(
            mock.requests[0],
            {
                "name": "empty-values",
                "type": "Custom.Empty",
                "deploymentId": "",
                "projectId": "",
                "properties": {},
            },
        )

    def test_every_documented_non_terminal_status_is_polled_and_sleep_is_between_polls(self):
        statuses = [
            "CREATED",
            "PENDING",
            "INITIALIZATION",
            "CHECKING_APPROVAL",
            "APPROVAL_PENDING",
            "USER_INTERACTION_PENDING",
            "INPROGRESS",
            "COMPLETION",
            "SUCCESSFUL",
        ]
        _SLEEP_CALLS.clear()
        time.sleep = _record_sleep
        try:
            with ContractMock(statuses) as mock:
                client = VCFAutomationClient(mock.url, "fixture-token")
                result = client.create_resource_and_wait(
                    "long-request", "Custom.Long", poll_interval=0.25
                )
        finally:
            time.sleep = _ORIGINAL_SLEEP

        self.assertEqual(result["status"], "SUCCESSFUL")
        self.assertEqual(
            [item["method"] for item in mock.requests],
            ["POST"] + ["GET"] * len(statuses),
        )
        self.assertEqual(_SLEEP_CALLS, [0.25] * (len(statuses) - 1))

    def test_negative_poll_interval_does_not_sleep(self):
        _SLEEP_CALLS.clear()
        time.sleep = _record_sleep
        try:
            with ContractMock(["PENDING", "SUCCESSFUL"]) as mock:
                client = VCFAutomationClient(mock.url, "fixture-token")
                client.create_resource_and_wait(
                    "no-delay", "Custom.NoDelay", poll_interval=-0.5
                )
        finally:
            time.sleep = _ORIGINAL_SLEEP

        self.assertEqual(_SLEEP_CALLS, [])
        self.assertEqual([item["method"] for item in mock.requests], ["POST", "GET", "GET"])

    def test_every_unsuccessful_terminal_status_raises_with_response(self):
        for terminal_status in ("APPROVAL_REJECTED", "ABORTED", "FAILED"):
            with self.subTest(status=terminal_status):
                with ContractMock(["INPROGRESS", terminal_status]) as mock:
                    client = VCFAutomationClient(mock.url, "fixture-token")
                    with self.assertRaises(RequestFailed) as raised:
                        client.create_resource_and_wait("worker", "Custom.Worker")

                self.assertEqual(raised.exception.status, terminal_status)
                self.assertEqual(raised.exception.request, mock.responses[-1])
                self.assertEqual([item["method"] for item in mock.requests], ["POST", "GET", "GET"])


class PublicSurfaceTests(unittest.TestCase):
    def test_all_requested_public_names_are_exposed(self):
        self.assertIsInstance(VCFAutomationClient, type)
        self.assertIsInstance(VCFAutomationError, type)
        self.assertIsInstance(ProtocolError, type)
        self.assertIsInstance(RequestFailed, type)

    def test_create_and_wait_has_the_requested_call_signature(self):
        parameters = list(
            inspect.signature(VCFAutomationClient.create_resource_and_wait).parameters.values()
        )
        self.assertEqual(
            [parameter.name for parameter in parameters],
            [
                "self",
                "name",
                "resource_type",
                "deployment_id",
                "project_id",
                "properties",
                "poll_interval",
            ],
        )
        self.assertIn(
            parameters[0].kind,
            (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                for parameter in parameters[1:3]
            )
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in parameters[3:]
            )
        )
        self.assertEqual(
            [parameter.default for parameter in parameters[3:]],
            [None, None, None, 0.0],
        )

    def test_package_uses_only_stdlib_and_local_imports(self):
        package = ROOT / "vcf_automation"
        self.assertTrue(package.is_dir())
        for source_path in package.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            imported_roots = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.extend(alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.append(node.module.partition(".")[0])

            for imported_root in imported_roots:
                is_local = (
                    (ROOT / (imported_root + ".py")).is_file()
                    or (ROOT / imported_root).is_dir()
                )
                self.assertTrue(
                    imported_root in sys.stdlib_module_names or is_local,
                    f"{source_path.relative_to(ROOT)} imports non-stdlib module {imported_root!r}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
