"""Protected acceptance verification for the contract-pinned retry workflow."""

from __future__ import annotations

import base64
import json
import math
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote

from nsx_group_retry import (
    AmbiguousWriteError,
    GroupSpec,
    GroupTag,
    NsxApiError,
    NsxPolicyClient,
    ProtocolError,
    UpdateResult,
)


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "mock_nsx_policy.py"
OPERATION_ID = "UpdateGroupForDomain"


def read_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class MockProcess:
    def __init__(self, mode: str = "drop-after-commit") -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="nsx-group-retry-")
        temp_path = Path(self.tempdir.name)
        self.port_file = temp_path / "port"
        self.log_file = temp_path / "requests.jsonl"
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--port-file",
                str(self.port_file),
                "--log-file",
                str(self.log_file),
                "--mode",
                mode,
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.port_file.exists():
                port = int(self.port_file.read_text(encoding="ascii"))
                self.base_url = f"http://127.0.0.1:{port}"
                return
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate(timeout=1)
                self.tempdir.cleanup()
                raise AssertionError(
                    f"mock exited before startup\nstdout: {stdout}\nstderr: {stderr}"
                )
            time.sleep(0.02)
        self.close()
        raise AssertionError("mock did not publish its loopback port")

    def close(self) -> None:
        if getattr(self, "process", None) is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if getattr(self, "tempdir", None) is not None:
            self.tempdir.cleanup()

    def __enter__(self) -> "MockProcess":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ContractTests(unittest.TestCase):
    def test_01_provenance_and_model_projection(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["swagger"], "2.0")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(contract["basePath"], "/policy/api/v1")
        self.assertEqual(list(contract["operations"]), [OPERATION_ID])
        operation = contract["operations"][OPERATION_ID]
        self.assertEqual(operation["operationId"], OPERATION_ID)
        self.assertEqual(operation["method"], "PUT")
        self.assertEqual(
            operation["path"], "/infra/domains/{domain-id}/groups/{group-id}"
        )
        self.assertEqual(
            sources["repository_commit_sha"],
            "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
        )
        self.assertEqual(
            sources["spec_path"],
            "specifications/nsx/openapi-2.0/nsx_policy_api.yaml",
        )
        self.assertEqual(
            sources["spec_blob_sha"],
            "102d15fd342f6a45bb6d84a5b39a916c65929f4c",
        )
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(
            contract["source"]["repository_commit_sha"],
            sources["repository_commit_sha"],
        )
        self.assertEqual(contract["source"]["spec_path"], sources["spec_path"])
        self.assertEqual(
            contract["source"]["spec_blob_sha"], sources["spec_blob_sha"]
        )
        self.assertEqual(
            [entry["operationId"] for entry in sources["operations"]],
            [OPERATION_ID],
        )
        for entry in sources["operations"]:
            self.assertEqual(
                entry["repository_commit_sha"], sources["repository_commit_sha"]
            )
            self.assertEqual(entry["spec_path"], sources["spec_path"])

        unscoped = GroupTag("pci")
        self.assertEqual(unscoped.to_wire(), {"tag": "pci"})
        self.assertEqual(
            list(unscoped.to_wire()),
            ["tag"],
            "unset tag scope must be omitted, not emitted as an empty default",
        )
        detailed = GroupSpec(
            "payments",
            description="PCI workloads",
            tags=[GroupTag("payments", scope="app"), unscoped],
        )
        detailed_wire = detailed.to_wire()
        self.assertEqual(
            list(detailed_wire),
            ["resource_type", "display_name", "description", "tags"],
        )
        self.assertEqual(
            detailed_wire,
            {
                "resource_type": "Group",
                "display_name": "payments",
                "description": "PCI workloads",
                "tags": [
                    {"tag": "payments", "scope": "app"},
                    {"tag": "pci"},
                ],
            },
        )
        with self.assertRaises(ValueError):
            GroupSpec("payments", tags=[]).to_wire()
        with self.assertRaises(ValueError):
            GroupTag(" ").to_wire()
        with self.assertRaises(ValueError):
            GroupTag("pci", scope="s" * 129).to_wire()
        with self.assertRaises(ValueError):
            GroupSpec("n" * 256).to_wire()
        with self.assertRaises(ValueError):
            GroupSpec("payments", description="d" * 1025).to_wire()
        with self.assertRaises(ValueError):
            GroupSpec(
                "payments", tags=[GroupTag(str(index)) for index in range(31)]
            ).to_wire()
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1:bad", "svc", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient(
                "http://127.0.0.1:1", "svc", "secret", timeout=math.inf
            )
        with self.assertRaises(ValueError):
            NsxPolicyClient(
                "http://127.0.0.1:1", "svc", "secret", max_attempts=0
            )
        self.assertTrue(issubclass(AmbiguousWriteError, Exception))
        self.assertTrue(issubclass(NsxApiError, Exception))
        self.assertTrue(issubclass(ProtocolError, Exception))

    def test_02_lost_response_replays_identical_put_once(self) -> None:
        with MockProcess() as mock:
            username = "svc-rollout"
            password = "contract-secret-62"
            domain_id = "prod/west ?%"
            group_id = "payments/blue #1"
            display_name = "Payments Δ blue"

            client = NsxPolicyClient(
                mock.base_url,
                username,
                password,
                timeout=2.0,
                max_attempts=2,
            )

            for invalid_call in (
                lambda: client.update_group("", group_id, GroupSpec(display_name)),
                lambda: client.update_group(
                    domain_id, group_id, GroupSpec(" ", description=None)
                ),
                lambda: client.update_group(
                    domain_id, group_id, GroupSpec(display_name, description="")
                ),
            ):
                with self.assertRaises((TypeError, ValueError)):
                    invalid_call()
            self.assertEqual(
                read_json_lines(mock.log_file),
                [],
                "local validation errors must not open an HTTP connection",
            )

            result = client.update_group(
                domain_id,
                group_id,
                GroupSpec(display_name, description=None, tags=None),
            )
            self.assertIsInstance(result, UpdateResult)
            self.assertEqual(result.attempts, 2)
            self.assertEqual(result.ambiguous_retries, 1)
            self.assertEqual(result.group["resource_type"], "Group")
            self.assertEqual(result.group["display_name"], display_name)
            self.assertEqual(result.group["id"], group_id)

            records = read_json_lines(mock.log_file)
            self.assertEqual(len(records), 2, records)
            expected_target = (
                "/policy/api/v1/infra/domains/"
                + quote(domain_id, safe="")
                + "/groups/"
                + quote(group_id, safe="")
            )
            expected_body = json.dumps(
                {
                    "resource_type": "Group",
                    "display_name": display_name,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            expected_authorization = "Basic " + base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")

            for record in records:
                self.assertEqual(record["operationId"], OPERATION_ID)
                self.assertEqual(record["method"], "PUT")
                self.assertEqual(record["raw_target"], expected_target)
                self.assertNotIn("?", record["raw_target"])
                self.assertEqual(record["body_utf8"], expected_body)
                headers = record["headers"]
                self.assertEqual(headers["authorization"], expected_authorization)
                self.assertEqual(headers["accept"], "application/json")
                self.assertEqual(headers["content-type"], "application/json")
                self.assertNotIn("idempotency-key", headers)
                self.assertNotIn("x-idempotency-key", headers)
                self.assertEqual(
                    headers["content-length"],
                    str(len(expected_body.encode("utf-8"))),
                )
                decoded_body = json.loads(record["body_utf8"])
                self.assertEqual(
                    list(decoded_body), ["resource_type", "display_name"]
                )
                for optional_name in (
                    "description",
                    "tags",
                    "expression",
                    "extended_expression",
                    "group_type",
                ):
                    self.assertNotIn(optional_name, decoded_body)

            self.assertEqual(records[0]["attempt"], 1)
            self.assertEqual(records[0]["effect"], "created")
            self.assertEqual(records[0]["resource_count"], 1)
            self.assertIs(records[0]["response_dropped"], True)
            self.assertEqual(records[1]["attempt"], 2)
            self.assertEqual(records[1]["effect"], "unchanged")
            self.assertEqual(records[1]["resource_count"], 1)
            self.assertIs(records[1]["response_dropped"], False)
            self.assertEqual(records[0]["raw_target"], records[1]["raw_target"])
            self.assertEqual(records[0]["body_utf8"], records[1]["body_utf8"])
            self.assertEqual(records[0]["headers"], records[1]["headers"])

    def test_03_received_http_error_is_not_retried(self) -> None:
        with MockProcess(mode="http-error") as mock:
            client = NsxPolicyClient(
                mock.base_url,
                "svc-error-check",
                "error-secret-62",
                timeout=2.0,
                max_attempts=4,
            )
            with self.assertRaises(NsxApiError) as raised:
                client.update_group(
                    "default",
                    "http-error",
                    GroupSpec("HTTP error must not be replayed"),
                )
            error = raised.exception
            self.assertEqual(error.status_code, 503)
            self.assertEqual(error.error_code, 50362)
            self.assertEqual(error.error_message, "retry policy test")
            self.assertEqual(error.module_name, "contract-mock")
            self.assertEqual(
                error.details, "a received HTTP response must not be replayed"
            )
            self.assertEqual(error.envelope["error_code"], 50362)
            records = read_json_lines(mock.log_file)
            self.assertEqual(
                len(records),
                1,
                "a received HTTP error must be terminal even when attempts remain",
            )
            self.assertEqual(records[0]["status"], 503)
            self.assertEqual(records[0]["effect"], "none")
            self.assertEqual(records[0]["resource_count"], 0)

    def test_04_ambiguous_attempt_limit_is_reported(self) -> None:
        with MockProcess() as mock:
            client = NsxPolicyClient(
                mock.base_url,
                "svc-limit-check",
                "limit-secret-62",
                timeout=2.0,
                max_attempts=1,
            )
            with self.assertRaises(AmbiguousWriteError) as raised:
                client.update_group(
                    "default",
                    "attempt-limit",
                    GroupSpec("Attempt limit"),
                )
            self.assertEqual(raised.exception.attempts, 1)
            records = read_json_lines(mock.log_file)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["effect"], "created")
            self.assertEqual(records[0]["resource_count"], 1)
            self.assertIs(records[0]["response_dropped"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
