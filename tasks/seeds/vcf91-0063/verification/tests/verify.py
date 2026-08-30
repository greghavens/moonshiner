"""Protected acceptance verifier for the NSX Policy partial-rollout task."""

from __future__ import annotations

import base64
import json
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nsx_partial_rollout import apply_firewall_change  # noqa: E402


COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
EXPECTED_OPERATION_IDS = [
    "PatchGroupForDomain",
    "PatchSecurityPolicyForDomain",
]


def compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def read_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class RunningMock:
    def __init__(self, temporary: Path, username: str, password: str) -> None:
        self.port_path = temporary / "port"
        self.log_path = temporary / "requests.jsonl"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(ROOT / "tools" / "mock_nsx_policy.py"),
                str(ROOT / "docs" / "contract.json"),
                str(self.port_path),
                str(self.log_path),
                username,
                password,
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.port_path.exists():
                self.port = int(self.port_path.read_text(encoding="ascii"))
                self.base_url = f"http://127.0.0.1:{self.port}"
                return
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate(timeout=1)
                raise AssertionError(
                    f"mock exited early ({self.process.returncode}): "
                    f"{stdout} {stderr}"
                )
            time.sleep(0.01)
        self.stop()
        raise AssertionError("mock did not publish its loopback port")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


class ContractProvenanceTests(unittest.TestCase):
    def test_protected_contract_is_the_pinned_spec_projection(self) -> None:
        contract = json.loads(
            (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
        )
        sources = json.loads(
            (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
        )

        self.assertEqual(contract["swagger"], "2.0")
        self.assertEqual(contract["info"]["title"], "NSX Policy API")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(contract["basePath"], "/policy/api/v1")
        self.assertEqual(contract["security"], [{"BasicAuth": []}])
        self.assertEqual(
            contract["securityDefinitions"]["BasicAuth"]["type"],
            "basic",
        )
        self.assertEqual(
            contract["derived_from"]["repository_commit_sha"],
            COMMIT,
        )
        self.assertEqual(contract["derived_from"]["spec_path"], SPEC_PATH)
        self.assertEqual(sources["repository_commit_sha"], COMMIT)
        self.assertEqual(sources["spec_path"], SPEC_PATH)
        self.assertEqual(
            [operation["operationId"] for operation in contract["operations"]],
            EXPECTED_OPERATION_IDS,
        )
        self.assertEqual(
            [operation["operationId"] for operation in sources["operations"]],
            EXPECTED_OPERATION_IDS,
        )
        for operation in sources["operations"]:
            self.assertEqual(operation["repository_commit_sha"], COMMIT)
            self.assertEqual(operation["spec_path"], SPEC_PATH)


class PartialRolloutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_context = tempfile.TemporaryDirectory()
        self.temporary = Path(self.temporary_context.name)
        nonce = secrets.token_hex(5)
        self.username = f"api-user-{nonce}"
        self.password = f"p@ss:{secrets.token_urlsafe(10)}"
        self.domain_id = f"prod blue+é-{nonce}"
        self.group_id = f"source #{nonce}"
        self.policy_id = f"allow?api={nonce}"
        self.destination_path = (
            f"/infra/domains/default/groups/application-{nonce}"
        )
        self.plan = {
            "group": {
                "display_name": f"source-é-{nonce}",
                "ip_addresses": [
                    "192.0.2.10",
                    "198.51.100.0/28",
                ],
                "description": None,
            },
            "policy": {
                "display_name": f"application-policy-{nonce}",
                "rule_display_name": f"allow-source-{nonce}",
                "destination_group_path": self.destination_path,
                "sequence_number": 1200,
                "rule_sequence_number": 10,
                "description": None,
                "rule_notes": None,
            },
        }
        self.mock = RunningMock(
            self.temporary,
            self.username,
            self.password,
        )

    def tearDown(self) -> None:
        self.mock.stop()
        self.temporary_context.cleanup()

    def expected_group_body(self) -> dict[str, object]:
        return {
            "resource_type": "Group",
            "display_name": self.plan["group"]["display_name"],
            "expression": [
                {
                    "resource_type": "IPAddressExpression",
                    "ip_addresses": self.plan["group"]["ip_addresses"],
                }
            ],
        }

    def expected_policy_body(self) -> dict[str, object]:
        source_path = (
            f"/infra/domains/{self.domain_id}/groups/{self.group_id}"
        )
        return {
            "resource_type": "SecurityPolicy",
            "display_name": self.plan["policy"]["display_name"],
            "category": "Application",
            "sequence_number": 1200,
            "stateful": True,
            "rules": [
                {
                    "resource_type": "Rule",
                    "display_name": self.plan["policy"]["rule_display_name"],
                    "sequence_number": 10,
                    "source_groups": [source_path],
                    "destination_groups": [self.destination_path],
                    "services": ["ANY"],
                    "scope": ["ANY"],
                    "action": "ALLOW",
                    "direction": "IN_OUT",
                }
            ],
        }

    def invoke(self, report_path: Path, *, password: str | None = None) -> dict:
        return apply_firewall_change(
            self.mock.base_url,
            self.username,
            self.password if password is None else password,
            self.domain_id,
            self.group_id,
            self.policy_id,
            self.plan,
            report_path,
            timeout=2.0,
        )

    def test_later_failure_preserves_and_reports_the_committed_group(self) -> None:
        report_path = self.temporary / "nested" / "rollout.json"
        report = self.invoke(report_path)

        expected_report = {
            "status": "partial_failure",
            "succeeded": 1,
            "failed": 1,
            "steps": [
                {
                    "name": "source-group",
                    "operationId": "PatchGroupForDomain",
                    "status": "succeeded",
                },
                {
                    "name": "security-policy",
                    "operationId": "PatchSecurityPolicyForDomain",
                    "status": "failed",
                    "http_status": 503,
                    "error_code": 73001,
                },
            ],
        }
        self.assertEqual(report, expected_report)
        self.assertEqual(
            report_path.read_bytes(),
            compact_json(expected_report) + b"\n",
        )

        entries = read_log(self.mock.log_path)
        self.assertEqual(len(entries), 2, entries)
        self.assertEqual(
            [entry["operationId"] for entry in entries],
            EXPECTED_OPERATION_IDS,
        )
        self.assertEqual(
            [entry["request_number"] for entry in entries],
            [1, 2],
        )
        self.assertEqual(
            [entry["status"] for entry in entries],
            [200, 503],
        )

        encoded_domain = quote(self.domain_id, safe="")
        encoded_group = quote(self.group_id, safe="")
        encoded_policy = quote(self.policy_id, safe="")
        self.assertEqual(
            entries[0]["target"],
            "/policy/api/v1/infra/domains/"
            f"{encoded_domain}/groups/{encoded_group}",
        )
        self.assertEqual(
            entries[1]["target"],
            "/policy/api/v1/infra/domains/"
            f"{encoded_domain}/security-policies/{encoded_policy}",
        )
        for entry in entries:
            self.assertEqual(entry["method"], "PATCH")
            self.assertEqual(entry["accept"], "application/json")
            self.assertEqual(entry["content_type"], "application/json")
            expected_auth = "Basic " + base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            self.assertEqual(entry["authorization"], expected_auth)
            self.assertNotIn("?", str(entry["target"]).split(encoded_policy)[-1])

        group_bytes = compact_json(self.expected_group_body())
        policy_bytes = compact_json(self.expected_policy_body())
        self.assertEqual(
            base64.b64decode(entries[0]["body_base64"]),
            group_bytes,
        )
        self.assertEqual(
            base64.b64decode(entries[1]["body_base64"]),
            policy_bytes,
        )
        self.assertEqual(entries[0]["content_length"], str(len(group_bytes)))
        self.assertEqual(entries[1]["content_length"], str(len(policy_bytes)))

        group_body = json.loads(group_bytes)
        policy_body = json.loads(policy_bytes)
        forbidden_group = {
            "description",
            "tags",
            "extended_expression",
            "group_type",
            "id",
            "path",
            "_revision",
            "state",
        }
        forbidden_policy = {
            "description",
            "scope",
            "tags",
            "locked",
            "tcp_strict",
            "id",
            "path",
            "_revision",
        }
        forbidden_rule = {
            "notes",
            "destinations_excluded",
            "sources_excluded",
            "disabled",
            "logged",
            "ip_protocol",
            "profiles",
            "service_entries",
            "tag",
            "id",
            "path",
            "_revision",
        }
        self.assertTrue(forbidden_group.isdisjoint(group_body))
        self.assertTrue(forbidden_policy.isdisjoint(policy_body))
        self.assertTrue(
            forbidden_rule.isdisjoint(policy_body["rules"][0])
        )

    def test_first_http_failure_stops_and_reports_only_that_step(self) -> None:
        report_path = self.temporary / "first-failure.json"
        report = self.invoke(report_path, password="wrong-password")
        expected = {
            "status": "failed",
            "succeeded": 0,
            "failed": 1,
            "steps": [
                {
                    "name": "source-group",
                    "operationId": "PatchGroupForDomain",
                    "status": "failed",
                    "http_status": 403,
                    "error_code": 40301,
                }
            ],
        }
        self.assertEqual(report, expected)
        self.assertEqual(report_path.read_bytes(), compact_json(expected) + b"\n")
        entries = read_log(self.mock.log_path)
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["operationId"], "PatchGroupForDomain")

    def test_unknown_or_read_only_plan_field_fails_before_http(self) -> None:
        report_path = self.temporary / "must-not-exist.json"
        invalid = {
            "group": dict(self.plan["group"], _revision=7),
            "policy": dict(self.plan["policy"]),
        }
        with self.assertRaises((TypeError, ValueError)):
            apply_firewall_change(
                self.mock.base_url,
                self.username,
                self.password,
                self.domain_id,
                self.group_id,
                self.policy_id,
                invalid,
                report_path,
                timeout=2.0,
            )
        self.assertFalse(report_path.exists())
        self.assertEqual(read_log(self.mock.log_path), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
