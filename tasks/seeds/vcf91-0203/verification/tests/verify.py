#!/usr/bin/env python3
"""Deterministic protected verification for vcf91-0203."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from fixtures.contract_mock import ContractMock  # noqa: E402
from vcf_installer import (  # noqa: E402
    ApiError,
    BundleDownloadSpec,
    PollTimeoutError,
    ProtocolError,
    TaskFailedError,
    VcfInstallerClient,
)


class Verification(unittest.TestCase):
    def test_contract_and_provenance_are_pinned_to_the_official_spec(self) -> None:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )
        sha = "c3f3b52c845dd967cabbc21680e893292077d5ba"
        path = "specifications/vcf-installer/vcf-installer-openapi.json"
        expected = {
            "startBundleDownloadByID": ("PATCH", "/v1/bundles/{id}"),
            "getTask": ("GET", "/v1/tasks/{id}"),
        }
        self.assertEqual(contract["source"]["commit"], sha)
        self.assertEqual(contract["source"]["path"], path)
        self.assertEqual(contract["source"]["api_version"], "9.1.0.0")
        self.assertEqual(sources["repository_commit_sha"], sha)
        self.assertEqual(sources["spec_path"], path)
        self.assertEqual(
            {
                item["operationId"]: (item["method"], item["path"])
                for item in contract["operations"]
            },
            expected,
        )
        self.assertEqual(
            {item["operationId"] for item in sources["operations"]}, set(expected)
        )
        download = contract["schemas"]["BundleDownloadSpec"]
        self.assertEqual(download["required"], [])
        self.assertEqual(
            set(download["properties"]),
            {"scheduledTimestamp", "downloadNow", "cancelNow"},
        )

    def test_payload_maps_all_options_and_omits_only_none(self) -> None:
        self.assertEqual(
            BundleDownloadSpec(
                scheduled_timestamp="2026-08-03T12:30:00Z",
                download_now=False,
                cancel_now=True,
            ).to_payload(),
            {
                "bundleDownloadSpec": {
                    "scheduledTimestamp": "2026-08-03T12:30:00Z",
                    "downloadNow": False,
                    "cancelNow": True,
                }
            },
        )
        self.assertEqual(
            BundleDownloadSpec().to_payload(),
            {"bundleDownloadSpec": {}},
        )

    def test_download_is_encoded_exactly_and_polled_to_success(self) -> None:
        with ContractMock(
            (" pending ", "Queued", "  in progress  ", "  successful  ")
        ) as mock:
            client = VcfInstallerClient(mock.base_url)
            result = client.download_bundle_and_wait(
                "bundle/9.1 core",
                BundleDownloadSpec(download_now=True),
                poll_interval_seconds=0,
                timeout_seconds=2,
            )
            log = list(mock.request_log)

        self.assertEqual(
            result["status"].strip().upper().replace(" ", "_"), "SUCCESSFUL"
        )
        self.assertEqual(len(log), 5, "accepted work must be polled to terminal state")
        start = log[0]
        self.assertEqual(start.method, "PATCH")
        self.assertEqual(start.raw_path, "/v1/bundles/bundle%2F9.1%20core")
        self.assertEqual(start.query, "")
        self.assertEqual(start.headers.get("accept"), "application/json")
        self.assertEqual(start.headers.get("content-type"), "application/json")
        self.assertEqual(
            start.raw_body,
            b'{"bundleDownloadSpec":{"downloadNow":true}}',
            "unset optional fields must be absent, not null or empty",
        )
        for poll in log[1:]:
            self.assertEqual(poll.method, "GET")
            self.assertEqual(poll.raw_path, "/v1/tasks/task-bundle-download-91")
            self.assertEqual(poll.query, "")
            self.assertEqual(poll.raw_body, b"")
            self.assertEqual(poll.headers.get("accept"), "application/json")
            self.assertNotIn("content-type", poll.headers)

    def test_explicit_false_is_sent_and_task_id_is_encoded(self) -> None:
        task_id = "task/9.1 ready"
        with ContractMock(("SUCCESSFUL",), task_id=task_id) as mock:
            client = VcfInstallerClient(mock.base_url)
            accepted = client.start_bundle_download(
                "bundle-91", BundleDownloadSpec(download_now=False)
            )
            completed = client.get_task(accepted["id"])
            log = list(mock.request_log)

        self.assertEqual(completed["status"], "SUCCESSFUL")
        self.assertEqual(
            log[0].raw_body,
            b'{"bundleDownloadSpec":{"downloadNow":false}}',
        )
        self.assertEqual(log[1].method, "GET")
        self.assertEqual(log[1].raw_path, "/v1/tasks/task%2F9.1%20ready")
        self.assertEqual(log[1].raw_body, b"")
        self.assertNotIn("content-type", log[1].headers)

    def test_every_non_success_terminal_state_raises_and_stops(self) -> None:
        variants = {
            "FAILED": "Failed",
            "CANCELLED": " cancelled ",
            "COMPLETED_WITH_WARNING": "Completed With Warning",
            "SKIPPED": "skipped",
            "TIMED_OUT": "Timed Out",
        }
        for normalized, spelling in variants.items():
            with self.subTest(status=normalized):
                with ContractMock((spelling,)) as mock:
                    client = VcfInstallerClient(mock.base_url)
                    with self.assertRaises(TaskFailedError) as caught:
                        client.download_bundle_and_wait(
                            "bundle-91",
                            BundleDownloadSpec(download_now=True),
                            poll_interval_seconds=0,
                            timeout_seconds=2,
                        )
                    methods = [item.method for item in mock.request_log]
                self.assertEqual(caught.exception.task["status"], spelling)
                self.assertEqual(methods, ["PATCH", "GET"])

    def test_unknown_status_is_a_protocol_error(self) -> None:
        with ContractMock(("AWAITING_ORACLE",)) as mock:
            client = VcfInstallerClient(mock.base_url)
            with self.assertRaises(ProtocolError):
                client.download_bundle_and_wait(
                    "bundle-91",
                    BundleDownloadSpec(download_now=True),
                    poll_interval_seconds=0,
                    timeout_seconds=2,
                )
            methods = [item.method for item in mock.request_log]
        self.assertEqual(methods, ["PATCH", "GET"])

    def test_zero_timeout_does_not_issue_a_poll(self) -> None:
        with ContractMock(("QUEUED",)) as mock:
            client = VcfInstallerClient(mock.base_url)
            with self.assertRaises(PollTimeoutError):
                client.download_bundle_and_wait(
                    "bundle-91",
                    BundleDownloadSpec(download_now=True),
                    poll_interval_seconds=0,
                    timeout_seconds=0,
                )
            methods = [item.method for item in mock.request_log]
        self.assertEqual(methods, ["PATCH"])

    def test_non_2xx_and_invalid_json_are_api_errors(self) -> None:
        with ContractMock(start_response=(503, {"message": "unavailable"})) as mock:
            client = VcfInstallerClient(mock.base_url)
            with self.assertRaises(ApiError) as http_error:
                client.start_bundle_download(
                    "bundle-91", BundleDownloadSpec(download_now=True)
                )
        self.assertEqual(http_error.exception.status_code, 503)

        with ContractMock(
            (),
            task_responses=((200, b"{not-json"),),
        ) as mock:
            client = VcfInstallerClient(mock.base_url)
            with self.assertRaises(ApiError) as json_error:
                client.get_task("task-bundle-download-91")
        self.assertEqual(json_error.exception.status_code, 200)

    def test_malformed_successful_task_objects_are_protocol_errors(self) -> None:
        malformed = (
            ["not", "an", "object"],
            {"id": "task", "name": "Download", "status": "PENDING"},
            {
                "id": "task",
                "name": "Download",
                "status": 1,
                "creationTimestamp": "2026-08-03T00:00:00Z",
            },
            {
                "id": "  ",
                "name": "Download",
                "status": "PENDING",
                "creationTimestamp": "2026-08-03T00:00:00Z",
            },
        )
        for value in malformed:
            with self.subTest(value=value):
                with ContractMock(start_response=(202, value)) as mock:
                    client = VcfInstallerClient(mock.base_url)
                    with self.assertRaises(ProtocolError):
                        client.start_bundle_download(
                            "bundle-91", BundleDownloadSpec(download_now=True)
                        )

    def test_package_uses_only_the_standard_library(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["dependencies"], [])
        stdlib = set(sys.stdlib_module_names)
        for path in (ROOT / "src" / "vcf_installer").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".", 1)[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".", 1)[0]]
                else:
                    continue
                for root in roots:
                    self.assertIn(root, stdlib, f"non-stdlib import {root!r} in {path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
