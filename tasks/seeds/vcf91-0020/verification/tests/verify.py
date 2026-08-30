#!/usr/bin/env python3
"""Protected verifier for evidence-based VCF failure diagnosis."""

from __future__ import annotations

import ast
import gzip
import io
import json
import secrets
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mock_sddc_manager import CONTRACT, MockSddcManager  # noqa: E402
from vcf_failure_diagnostics import (  # noqa: E402
    EvidenceError,
    PollTimeoutError,
    ProtocolError,
    SddcManagerClient,
    SddcManagerError,
    SupportBundleSelection,
)


PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    {
        "operationId": "getTask",
        "method": "GET",
        "path": "/v1/tasks/{id}",
    },
    {
        "operationId": "getNotifications",
        "method": "GET",
        "path": "/v1/notifications",
    },
    {
        "operationId": "startSupportBundle",
        "method": "POST",
        "path": "/v1/system/support-bundles",
    },
    {
        "operationId": "getSupportBundleStatus",
        "method": "GET",
        "path": "/v1/system/support-bundles/{id}",
    },
    {
        "operationId": "exportSupportBundleByID",
        "method": "GET",
        "path": "/v1/system/support-bundles/{id}/data",
    },
]
CREATED = "2026-07-28T18:00:00.000Z"
COMPLETED = "2026-07-28T18:05:00.000Z"


def _add_tar_text(
    archive: tarfile.TarFile,
    name: str,
    text: str,
) -> None:
    raw = text.encode("utf-8")
    member = tarfile.TarInfo(name)
    member.size = len(raw)
    member.mtime = 0
    member.mode = 0o640
    archive.addfile(member, io.BytesIO(raw))


def make_support_bundle(
    *,
    task_id: str,
    reference_token: str,
    cause_code: str,
    cause: str,
    include_match: bool = True,
    duplicate_match: bool = False,
) -> bytes:
    """Create a deterministic in-memory tar.gz containing JSONL logs."""
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            unrelated = {
                "timestamp": "2026-07-28T18:01:00.000Z",
                "taskId": "another-task",
                "referenceToken": "another-reference",
                "causeCode": "IRRELEVANT",
                "cause": "This record must not be selected",
            }
            wrong_task = {
                "timestamp": "2026-07-28T18:02:00.000Z",
                "taskId": "wrong-task",
                "referenceToken": reference_token,
                "causeCode": "WRONG_TASK",
                "cause": "A shared token is not sufficient",
            }
            lines = [
                json.dumps(unrelated, separators=(",", ":")),
                json.dumps(wrong_task, separators=(",", ":")),
            ]
            if include_match:
                match = {
                    "timestamp": "2026-07-28T18:03:00.000Z",
                    "taskId": task_id,
                    "referenceToken": reference_token,
                    "causeCode": cause_code,
                    "cause": cause,
                }
                lines.append(json.dumps(match, separators=(",", ":")))
                if duplicate_match:
                    lines.append(json.dumps(match, separators=(",", ":")))
            _add_tar_text(
                archive,
                "var/log/vmware/vcf/operationsmanager/operationsmanager.log",
                "\n".join(lines) + "\n",
            )
            _add_tar_text(
                archive,
                "var/log/vmware/vcf/nsx/nsx-manager.log",
                json.dumps(
                    {
                        "taskId": "noise",
                        "referenceToken": reference_token,
                        "causeCode": "NOISE",
                        "cause": "Reference token alone must not match",
                    },
                    separators=(",", ":"),
                )
                + "\n",
            )
            _add_tar_text(
                archive,
                "manifest.txt",
                "support bundle fixture\n",
            )
    return output.getvalue()


class ContractTests(unittest.TestCase):
    def test_contract_and_provenance_pin_exact_spec_operations(self) -> None:
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(
                encoding="utf-8"
            )
        )
        expected_source = {
            "repository": "https://github.com/vmware/vcf-api-specs",
            "repository_commit_sha": PINNED_COMMIT,
            "license": "Apache-2.0",
            "spec_path": SPEC_PATH,
        }
        self.assertEqual(CONTRACT["openapi"], "3.0.1")
        self.assertEqual(CONTRACT["spec_version"], "9.1.0.0")
        self.assertEqual(CONTRACT["source"], expected_source)
        self.assertEqual(sources["repository"], expected_source["repository"])
        self.assertEqual(sources["repository_commit_sha"], PINNED_COMMIT)
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(sources["spec_path"], SPEC_PATH)
        self.assertIn(PINNED_COMMIT, sources["source_url"])
        self.assertTrue(sources["source_url"].endswith(SPEC_PATH))

        actual_operations = [
            {
                "operationId": item["operationId"],
                "method": item["method"],
                "path": item["path"],
            }
            for item in CONTRACT["operations"]
        ]
        source_operations = [
            {
                "operationId": item["operationId"],
                "method": item["method"],
                "path": item["path"],
            }
            for item in sources["operationIds"]
        ]
        self.assertEqual(actual_operations, EXPECTED_OPERATIONS)
        self.assertEqual(source_operations, EXPECTED_OPERATIONS)
        for operation in sources["operationIds"]:
            self.assertEqual(operation["repository_commit_sha"], PINNED_COMMIT)
            self.assertEqual(operation["spec_path"], SPEC_PATH)

        schemas = CONTRACT["schemas"]
        self.assertEqual(
            list(schemas["SupportBundleSpec"]["properties"]),
            ["options", "scope", "logs"],
        )
        self.assertEqual(
            list(schemas["SupportBundleScope"]["properties"]),
            ["includeFreeHosts", "domains"],
        )
        self.assertEqual(
            list(schemas["Domains"]["properties"]),
            ["domainName", "clusterNames"],
        )
        self.assertEqual(
            list(schemas["Logs"]["properties"]),
            [
                "vcLogs",
                "nsxLogs",
                "esxLogs",
                "hcxLogs",
                "wcpLogs",
                "sddcManagerLogs",
                "apiLogs",
                "systemDebugLogs",
                "vmScreenshots",
                "vraLogs",
                "vropsLogs",
                "vrliLogs",
                "vrslcmLogs",
                "automationLogs",
                "operationsLogs",
                "operationsForLogs",
                "lifecycleLogs",
                "vmsLogs",
            ],
        )
        self.assertEqual(
            CONTRACT["operations"][-1]["responses"]["200"]["mediaType"],
            "application/octet-stream",
        )

    def test_mock_serves_only_contract_named_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "requests.jsonl"
            with MockSddcManager("token", log_path) as server:
                self.assertEqual(
                    server.operation_ids,
                    frozenset(
                        operation["operationId"]
                        for operation in EXPECTED_OPERATIONS
                    ),
                )
                request = urllib.request.Request(
                    server.base_url + "/v1/system/health-summary",
                    method="GET",
                    headers={"Authorization": "Bearer token"},
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(caught.exception.code, 404)
                self.assertIsNone(server.read_request_log()[0]["operationId"])

    def test_package_is_stdlib_only_and_does_not_shell_out(self) -> None:
        source_path = ROOT / "vcf_failure_diagnostics" / "client.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        forbidden = {"httpx", "requests", "socket", "subprocess"}
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names = [node.module.split(".", 1)[0]]
            for name in names:
                self.assertIn(
                    name,
                    sys.stdlib_module_names,
                    f"non-stdlib import in client.py: {name}",
                )
                self.assertNotIn(
                    name,
                    forbidden,
                    f"forbidden transport/import in client.py: {name}",
                )

        vendored_suffixes = {".whl", ".egg", ".zip", ".nupkg"}
        vendored = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in vendored_suffixes
        ]
        self.assertEqual(vendored, [])


class DiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        suffix = secrets.token_hex(7)
        self.access_token = "access-" + secrets.token_urlsafe(14)
        self.task_id = "task/" + suffix + " failed"
        self.bundle_id = "bundle/" + secrets.token_hex(6) + " ready"
        self.resource_id = "resource-" + secrets.token_hex(8)
        self.reference_token = "reference-" + secrets.token_hex(9)
        self.domain_name = "domain-" + secrets.token_hex(5)
        self.cause_code = "NSX_CAUSE_" + secrets.token_hex(6).upper()
        self.cause = "Evidence cause " + secrets.token_urlsafe(13)
        self.log_path = (
            "var/log/vmware/vcf/operationsmanager/operationsmanager.log"
        )

    def task(self, *, status: str = "FAILED") -> dict[str, Any]:
        return {
            "id": self.task_id,
            "name": "Apply cluster configuration",
            "type": "CLUSTER_UPDATE",
            "status": status,
            "creationTimestamp": CREATED,
            "completionTimestamp": COMPLETED,
            "errors": [
                {
                    "errorCode": "OPERATION_FAILED",
                    "message": "The operation failed. Collect diagnostic evidence.",
                    "remediationMessage": "Review correlated events and logs.",
                    "referenceToken": self.reference_token,
                }
            ],
            "resources": [
                {
                    "resourceId": self.resource_id,
                    "fqdn": "nsx-manager.example.test",
                    "type": "NSXT_MANAGER",
                    "name": "nsx-manager",
                }
            ],
        }

    def notifications(
        self,
        *,
        include_relevant: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        before = {
            "type": "HEALTH",
            "severity": "WARNING",
            "message": {
                "id": "before-window",
                "localizedMessage": "Old event",
                "arguments": [],
            },
            "creationTimestamp": "2026-07-28T17:59:59.999Z",
            "resources": [
                {
                    "id": self.resource_id,
                    "type": "NSXT_MANAGER",
                    "name": "nsx-manager.example.test",
                }
            ],
        }
        other_resource = {
            "type": "HEALTH",
            "severity": "CRITICAL",
            "message": {
                "id": "other-resource",
                "localizedMessage": "Concurrent unrelated incident",
                "arguments": [],
            },
            "creationTimestamp": "2026-07-28T18:02:00.000Z",
            "resources": [
                {
                    "id": "resource-unrelated",
                    "type": "VCENTER",
                    "name": "vc.example.test",
                }
            ],
        }
        relevant = {
            "type": "CONNECTIVITY",
            "severity": "CRITICAL",
            "message": {
                "id": "nsx-connectivity-event",
                "localizedMessage": "A connectivity event requires log correlation.",
                "arguments": [self.task_id, self.reference_token],
            },
            "creationTimestamp": "2026-07-28T18:02:30.000Z",
            "expirationTimestamp": "2026-08-04T18:02:30.000Z",
            "resources": [
                {
                    "id": self.resource_id,
                    "type": "NSXT_MANAGER",
                    "name": "nsx-manager.example.test",
                }
            ],
            "domain": {
                "id": "domain-id",
                "name": self.domain_name,
            },
        }
        after = {
            "type": "HEALTH",
            "severity": "WARNING",
            "message": {
                "id": "after-window",
                "localizedMessage": "Later event",
                "arguments": [],
            },
            "creationTimestamp": "2026-07-28T18:05:00.001Z",
            "resources": [
                {
                    "id": self.resource_id,
                    "type": "NSXT_MANAGER",
                    "name": "nsx-manager.example.test",
                }
            ],
        }
        values = [before, other_resource]
        if include_relevant:
            values.append(relevant)
        values.append(after)
        return values, relevant if include_relevant else None

    def selection(self) -> SupportBundleSelection:
        return SupportBundleSelection(
            self.domain_name,
            sddc_manager_logs=True,
            nsx_logs=True,
        )

    def client(
        self,
        server: MockSddcManager,
        sleeps: list[float],
        *,
        max_polls: int = 4,
    ) -> SddcManagerClient:
        return SddcManagerClient(
            server.base_url,
            self.access_token,
            sleep=sleeps.append,
            poll_interval=0.25,
            max_polls=max_polls,
            timeout=2.0,
        )

    def script_success(
        self,
        server: MockSddcManager,
        *,
        status_responses: list[tuple[int, Any]] | None = None,
        archive: bytes | None = None,
        include_relevant_event: bool = True,
        acceptance_status: str = "PENDING",
    ) -> dict[str, Any] | None:
        events, relevant = self.notifications(
            include_relevant=include_relevant_event
        )
        server.script("getTask", [(200, self.task())])
        server.script("getNotifications", [(200, events)])
        server.script(
            "startSupportBundle",
            [
                (
                    202,
                    {
                        "id": self.bundle_id,
                        "status": acceptance_status,
                        "bundleAvailable": "false",
                    },
                )
            ],
        )
        if status_responses is None:
            status_responses = [
                (
                    200,
                    {
                        "id": self.bundle_id,
                        "status": "IN_PROGRESS",
                        "bundleAvailable": "false",
                    },
                ),
                (
                    200,
                    {
                        "id": self.bundle_id,
                        "status": "COMPLETED_WITH_SUCCESS",
                        "bundleAvailable": "true",
                        "bundleName": "support-bundle.tar.gz",
                    },
                ),
            ]
        server.script("getSupportBundleStatus", status_responses)
        if archive is None:
            archive = make_support_bundle(
                task_id=self.task_id,
                reference_token=self.reference_token,
                cause_code=self.cause_code,
                cause=self.cause,
            )
        server.script(
            "exportSupportBundleByID",
            [(200, archive, "application/octet-stream")],
        )
        return relevant

    def test_full_evidence_workflow_and_exact_wire_shape(self) -> None:
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "requests.jsonl"
            with MockSddcManager(self.access_token, log_path) as server:
                relevant = self.script_success(server)
                report = self.client(server, sleeps).diagnose_failed_task(
                    self.task_id,
                    self.selection(),
                )
                requests = server.read_request_log()

        self.assertEqual(
            report,
            {
                "taskId": self.task_id,
                "taskStatus": "FAILED",
                "referenceToken": self.reference_token,
                "supportBundleId": self.bundle_id,
                "rootCause": {
                    "code": self.cause_code,
                    "message": self.cause,
                    "logPath": self.log_path,
                },
                "events": [relevant],
            },
        )
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(
            [record["operationId"] for record in requests],
            [
                "getTask",
                "getNotifications",
                "startSupportBundle",
                "getSupportBundleStatus",
                "getSupportBundleStatus",
                "exportSupportBundleByID",
            ],
        )

        encoded_task = urllib.parse.quote(self.task_id, safe="")
        encoded_bundle = urllib.parse.quote(self.bundle_id, safe="")
        self.assertEqual(
            [record["target"] for record in requests],
            [
                f"/v1/tasks/{encoded_task}",
                "/v1/notifications",
                "/v1/system/support-bundles",
                f"/v1/system/support-bundles/{encoded_bundle}",
                f"/v1/system/support-bundles/{encoded_bundle}",
                f"/v1/system/support-bundles/{encoded_bundle}/data",
            ],
        )

        expected_body = {
            "scope": {
                "domains": [
                    {
                        "domainName": self.domain_name,
                    }
                ]
            },
            "logs": {
                "nsxLogs": True,
                "sddcManagerLogs": True,
            },
        }
        expected_raw = json.dumps(
            expected_body,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertEqual(requests[2]["body"], expected_raw)
        self.assertEqual(json.loads(requests[2]["body"]), expected_body)

        for index, record in enumerate(requests):
            self.assertEqual(record["query"], "")
            self.assertEqual(
                record["headers"].get("authorization"),
                f"Bearer {self.access_token}",
            )
            expected_accept = (
                "application/octet-stream"
                if index == len(requests) - 1
                else "application/json"
            )
            self.assertEqual(
                record["headers"].get("accept"),
                expected_accept,
            )
            if index == 2:
                self.assertEqual(
                    record["headers"].get("content-type"),
                    "application/json",
                )
            else:
                self.assertNotIn("content-type", record["headers"])
                self.assertEqual(record["body"], "")

        body = json.loads(requests[2]["body"])
        self.assertNotIn("options", body)
        self.assertNotIn("includeFreeHosts", body["scope"])
        self.assertNotIn("clusterNames", body["scope"]["domains"][0])
        self.assertEqual(
            set(body["logs"]),
            {"nsxLogs", "sddcManagerLogs"},
        )
        unset_log_fields = set(
            CONTRACT["schemas"]["Logs"]["properties"]
        ) - set(body["logs"])
        self.assertTrue(unset_log_fields)
        self.assertTrue(unset_log_fields.isdisjoint(body["logs"]))

    def test_selection_preserves_false_and_explicit_empty_list(self) -> None:
        selection = SupportBundleSelection(
            self.domain_name,
            cluster_names=[],
            sddc_manager_logs=False,
            api_logs=True,
            include_free_hosts=False,
            force=False,
            summary_report=False,
        )
        self.assertEqual(
            selection.to_wire(),
            {
                "options": {
                    "config": {"force": False},
                    "include": {"summaryReport": False},
                },
                "scope": {
                    "includeFreeHosts": False,
                    "domains": [
                        {
                            "domainName": self.domain_name,
                            "clusterNames": [],
                        }
                    ],
                },
                "logs": {
                    "sddcManagerLogs": False,
                    "apiLogs": True,
                },
            },
        )

    def test_acceptance_body_is_never_completion_evidence(self) -> None:
        sleeps: list[float] = []
        terminal = [
            (
                200,
                {
                    "id": self.bundle_id,
                    "status": "COMPLETED_WITH_SUCCESS",
                    "bundleAvailable": "true",
                },
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                self.script_success(
                    server,
                    status_responses=terminal,
                    acceptance_status="COMPLETED_WITH_SUCCESS",
                )
                self.client(server, sleeps).diagnose_failed_task(
                    self.task_id,
                    self.selection(),
                )
                operations = [
                    record["operationId"]
                    for record in server.read_request_log()
                ]
        self.assertEqual(operations.count("getSupportBundleStatus"), 1)
        self.assertEqual(operations[-1], "exportSupportBundleByID")
        self.assertEqual(sleeps, [])

    def test_poll_budget_exhaustion_never_downloads(self) -> None:
        sleeps: list[float] = []
        in_progress = [
            (
                200,
                {
                    "id": self.bundle_id,
                    "status": "IN_PROGRESS",
                },
            ),
            (
                200,
                {
                    "id": self.bundle_id,
                    "status": "PENDING",
                },
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                self.script_success(server, status_responses=in_progress)
                with self.assertRaises(PollTimeoutError) as caught:
                    self.client(
                        server,
                        sleeps,
                        max_polls=2,
                    ).diagnose_failed_task(
                        self.task_id,
                        self.selection(),
                    )
                operations = [
                    record["operationId"]
                    for record in server.read_request_log()
                ]
        self.assertEqual(caught.exception.bundle_id, self.bundle_id)
        self.assertEqual(caught.exception.max_polls, 2)
        self.assertEqual(caught.exception.last_status, "PENDING")
        self.assertEqual(sleeps, [0.25])
        self.assertNotIn("exportSupportBundleByID", operations)

    def test_non_failed_task_stops_before_events_or_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                server.script(
                    "getTask",
                    [(200, self.task(status="SUCCESSFUL"))],
                )
                with self.assertRaises(EvidenceError):
                    self.client(server, []).diagnose_failed_task(
                        self.task_id,
                        self.selection(),
                    )
                operations = [
                    record["operationId"]
                    for record in server.read_request_log()
                ]
        self.assertEqual(operations, ["getTask"])

    def test_no_correlated_event_stops_before_log_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                self.script_success(
                    server,
                    include_relevant_event=False,
                )
                with self.assertRaises(EvidenceError):
                    self.client(server, []).diagnose_failed_task(
                        self.task_id,
                        self.selection(),
                    )
                operations = [
                    record["operationId"]
                    for record in server.read_request_log()
                ]
        self.assertEqual(operations, ["getTask", "getNotifications"])

    def test_missing_or_ambiguous_log_match_is_not_guessed(self) -> None:
        for include_match, duplicate_match in ((False, False), (True, True)):
            with self.subTest(
                include_match=include_match,
                duplicate_match=duplicate_match,
            ):
                archive = make_support_bundle(
                    task_id=self.task_id,
                    reference_token=self.reference_token,
                    cause_code=self.cause_code,
                    cause=self.cause,
                    include_match=include_match,
                    duplicate_match=duplicate_match,
                )
                with tempfile.TemporaryDirectory() as directory:
                    with MockSddcManager(
                        self.access_token,
                        Path(directory) / "requests.jsonl",
                    ) as server:
                        self.script_success(
                            server,
                            status_responses=[
                                (
                                    200,
                                    {
                                        "id": self.bundle_id,
                                        "status": "COMPLETED_WITH_SUCCESS",
                                    },
                                )
                            ],
                            archive=archive,
                        )
                        with self.assertRaises(EvidenceError):
                            self.client(
                                server,
                                [],
                            ).diagnose_failed_task(
                                self.task_id,
                                self.selection(),
                            )

    def test_unknown_bundle_status_is_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                self.script_success(
                    server,
                    status_responses=[
                        (
                            200,
                            {
                                "id": self.bundle_id,
                                "status": "MYSTERY",
                            },
                        )
                    ],
                )
                with self.assertRaises(ProtocolError):
                    self.client(server, []).diagnose_failed_task(
                        self.task_id,
                        self.selection(),
                    )
                operations = [
                    record["operationId"]
                    for record in server.read_request_log()
                ]
        self.assertNotIn("exportSupportBundleByID", operations)

    def test_http_error_preserves_payload_without_leaking_token(self) -> None:
        payload = {
            "errorCode": "TASK_LOOKUP_FAILED",
            "message": "Task lookup failed",
            "referenceToken": "server-reference",
        }
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                server.script("getTask", [(500, payload)])
                with self.assertRaises(SddcManagerError) as caught:
                    self.client(server, []).diagnose_failed_task(
                        self.task_id,
                        self.selection(),
                    )
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.payload, payload)
        rendered = str(caught.exception) + repr(caught.exception)
        self.assertNotIn(self.access_token, rendered)

    def test_invalid_inputs_fail_before_network_traffic(self) -> None:
        with self.assertRaises(ValueError):
            SupportBundleSelection(" ")
        with self.assertRaises(TypeError):
            SupportBundleSelection(
                self.domain_name,
                nsx_logs=1,
            )
        with self.assertRaises(ValueError):
            SddcManagerClient(
                "http://user:pass@127.0.0.1",
                self.access_token,
            )

        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.access_token,
                Path(directory) / "requests.jsonl",
            ) as server:
                with self.assertRaises(ValueError):
                    self.client(server, []).diagnose_failed_task(
                        " ",
                        self.selection(),
                    )
                self.assertEqual(server.read_request_log(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
