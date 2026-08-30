"""Protected verification for the contract-pinned NSX IDFW evidence client."""

from __future__ import annotations

import base64
import json
import math
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote

from nsx_idfw_evidence import (
    FailureEvidence,
    NsxApiError,
    NsxPolicyClient,
    ProtocolError,
)


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "mock_nsx_policy.py"
COLLECTOR_OPERATION = "GetFirewallIdentityStoreEventLogServer"
EVENTS_OPERATION = "GetUserLoginEvents"


def json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class MockProcess:
    def __init__(self, scenario: dict[str, object]):
        self.tempdir = tempfile.TemporaryDirectory(prefix="nsx-idfw-evidence-")
        temp_path = Path(self.tempdir.name)
        self.port_file = temp_path / "port"
        self.log_file = temp_path / "requests.jsonl"
        self.scenario_file = temp_path / "scenario.json"
        self.scenario_file.write_text(
            json.dumps(scenario, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--port-file",
                str(self.port_file),
                "--log-file",
                str(self.log_file),
                "--scenario-file",
                str(self.scenario_file),
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


def session(
    session_id: str,
    user_id: str,
    source: str,
    login_time: int,
) -> dict[str, object]:
    return {
        "id": session_id,
        "domain_name": "corp.example",
        "user_name": "case-user",
        "user_id": user_id,
        "vm_ext_id": f"vm-{session_id}",
        "user_session_id": login_time % 1000,
        "login_time": login_time,
        "logout_time": login_time + 500,
        "session_source": source,
    }


class ContractTests(unittest.TestCase):
    def test_01_contract_and_provenance_are_exactly_pinned(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        operation_ids = [COLLECTOR_OPERATION, EVENTS_OPERATION]

        self.assertEqual(contract["swagger"], "2.0")
        self.assertEqual(contract["info"]["title"], "NSX Policy API")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(contract["basePath"], "/policy/api/v1")
        self.assertEqual(list(contract["operations"]), operation_ids)
        self.assertEqual(
            contract["operations"][COLLECTOR_OPERATION]["method"], "GET"
        )
        self.assertEqual(
            contract["operations"][COLLECTOR_OPERATION]["path"],
            "/infra/identity-firewall-stores/{identity-firewall-store-id}"
            "/event-log-servers/{event-log-server-id}",
        )
        self.assertEqual(
            [
                parameter["name"]
                for parameter in contract["operations"][COLLECTOR_OPERATION][
                    "parameters"
                ]
            ],
            [
                "identity-firewall-store-id",
                "event-log-server-id",
                "enforcement_point_path",
            ],
        )
        self.assertEqual(
            contract["operations"][EVENTS_OPERATION]["method"], "GET"
        )
        self.assertEqual(
            contract["operations"][EVENTS_OPERATION]["path"],
            "/infra/settings/firewall/idfw/user-stats/{user-id}",
        )
        self.assertEqual(
            [
                parameter["name"]
                for parameter in contract["operations"][EVENTS_OPERATION][
                    "parameters"
                ]
            ],
            ["user-id", "enforcement_point_path"],
        )
        for operation in contract["operations"].values():
            self.assertEqual(operation["successStatus"], 200)
            self.assertIsNone(operation["requestBody"])
            optional = operation["parameters"][-1]
            self.assertEqual(optional["name"], "enforcement_point_path")
            self.assertIs(optional["omitWhenUnset"], True)

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
        self.assertEqual(sources["operationIds"], operation_ids)
        self.assertEqual(
            [entry["operationId"] for entry in sources["operations"]],
            operation_ids,
        )
        self.assertEqual(
            contract["source"]["repository_commit_sha"],
            sources["repository_commit_sha"],
        )
        self.assertEqual(contract["source"]["spec_path"], sources["spec_path"])
        for operation in sources["operations"]:
            self.assertEqual(
                operation["repository_commit_sha"],
                sources["repository_commit_sha"],
            )
            self.assertEqual(operation["spec_path"], sources["spec_path"])

    def test_02_constructor_validation_and_public_types(self) -> None:
        invalid_constructors = (
            lambda: NsxPolicyClient("ftp://127.0.0.1", "svc", "secret"),
            lambda: NsxPolicyClient("http://user@127.0.0.1", "svc", "secret"),
            lambda: NsxPolicyClient("http://127.0.0.1/path", "svc", "secret"),
            lambda: NsxPolicyClient("http://127.0.0.1?x=1", "svc", "secret"),
            lambda: NsxPolicyClient("http://127.0.0.1:bad", "svc", "secret"),
            lambda: NsxPolicyClient("http://127.0.0.1", "svc:name", "secret"),
            lambda: NsxPolicyClient("http://127.0.0.1", "svc", ""),
            lambda: NsxPolicyClient(
                "http://127.0.0.1", "svc", "secret", timeout=math.inf
            ),
        )
        for construct in invalid_constructors:
            with self.assertRaises((TypeError, ValueError)):
                construct()
        self.assertTrue(issubclass(NsxApiError, Exception))
        self.assertTrue(issubclass(ProtocolError, Exception))


class EvidenceWireTests(unittest.TestCase):
    def test_03_failure_evidence_uses_both_exact_bodyless_gets(self) -> None:
        nonce = secrets.token_hex(8)
        store_id = f"corp/store ?#% Δ-{nonce}"
        server_id = f"collector/A+B %{nonce}"
        user_id = f"User/Case +% Δ-{nonce}"
        username = f"svc-{nonce}"
        password = f"pw:{nonce}:not-logged"
        evidence_message = f"event-log read denied [{nonce}]"

        relevant_new = session(f"b-{nonce}", user_id, "ELS", 1_900_000_000_000)
        relevant_tie = session(f"a-{nonce}", user_id, "ELS", 1_900_000_000_000)
        relevant_old = session(f"c-{nonce}", user_id, "ELS", 1_800_000_000_000)
        gi_session = session(f"gi-{nonce}", user_id, "GI", 2_000_000_000_000)
        gi_session.pop("logout_time")
        wrong_user = session(
            f"other-{nonce}", user_id.swapcase(), "ELS", 2_100_000_000_000
        )
        collector = {
            "resource_type": "IdentityFirewallStoreEventLogServer",
            "id": server_id,
            "host": "192.0.2.40",
            "status": {
                "status": "ERROR",
                "error_message": evidence_message,
                "last_polling_time": 1_900_000_001_000,
                "last_event_record_id": 421,
                "last_event_time_created": 1_899_999_000_000,
            },
        }
        stats = {
            "user_id": user_id,
            "active_sessions": [gi_session],
            "archived_sessions": [
                wrong_user,
                relevant_new,
                relevant_tie,
                relevant_old,
            ],
        }

        with MockProcess({"collector": collector, "user_stats": stats}) as mock:
            client = NsxPolicyClient(
                mock.base_url, username, password, timeout=2.0
            )
            invalid_calls = (
                lambda: client.collect_failure_evidence("", server_id, user_id),
                lambda: client.collect_failure_evidence(store_id, " ", user_id),
                lambda: client.collect_failure_evidence(store_id, server_id, None),
                lambda: client.collect_failure_evidence(
                    store_id,
                    server_id,
                    user_id,
                    enforcement_point_path=" ",
                ),
            )
            for invalid_call in invalid_calls:
                with self.assertRaises((TypeError, ValueError)):
                    invalid_call()
            self.assertEqual(
                json_lines(mock.log_file),
                [],
                "all local validation must precede the first connection",
            )

            result = client.collect_failure_evidence(
                store_id, server_id, user_id
            )
            self.assertIsInstance(result, FailureEvidence)
            self.assertEqual(result.identity_firewall_store_id, store_id)
            self.assertEqual(result.event_log_server_id, server_id)
            self.assertEqual(result.user_id, user_id)
            self.assertEqual(result.collector, collector)
            self.assertEqual(
                result.relevant_login_events,
                (relevant_tie, relevant_new, relevant_old),
            )
            self.assertIsInstance(result.relevant_login_events, tuple)
            self.assertEqual(result.evidence_message, evidence_message)

            records = json_lines(mock.log_file)
            self.assertEqual(len(records), 2, records)
            self.assertEqual(
                [record["operationId"] for record in records],
                [COLLECTOR_OPERATION, EVENTS_OPERATION],
            )
            expected_targets = [
                "/policy/api/v1/infra/identity-firewall-stores/"
                + quote(store_id, safe="")
                + "/event-log-servers/"
                + quote(server_id, safe=""),
                "/policy/api/v1/infra/settings/firewall/idfw/user-stats/"
                + quote(user_id, safe=""),
            ]
            expected_authorization = "Basic " + base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")
            for record, expected_target in zip(records, expected_targets):
                self.assertEqual(record["method"], "GET")
                self.assertEqual(record["raw_target"], expected_target)
                self.assertNotIn("?", record["raw_target"])
                self.assertNotIn("enforcement_point_path", record["raw_target"])
                self.assertEqual(record["body_utf8"], "")
                self.assertEqual(
                    record["headers"]["authorization"], expected_authorization
                )
                self.assertEqual(record["headers"]["accept"], "application/json")
                self.assertNotIn("content-type", record["headers"])
                self.assertNotIn("content-length", record["headers"])

    def test_04_set_optional_query_is_encoded_on_both_operations(self) -> None:
        nonce = secrets.token_hex(7)
        store_id = f"store/{nonce}"
        server_id = f"server {nonce}"
        user_id = f"user+{nonce}"
        enforcement_point_path = (
            f"/infra/sites/default/enforcement-points/edge A?x={nonce}&blue"
        )
        collector = {
            "id": server_id,
            "host": "192.0.2.41",
            "status": {
                "status": "OK",
                "error_message": f"ignored stale text {nonce}",
            },
        }
        stats = {
            "user_id": user_id,
            "active_sessions": [],
        }
        with MockProcess({"collector": collector, "user_stats": stats}) as mock:
            result = NsxPolicyClient(
                mock.base_url, "query-svc", f"pw-{nonce}"
            ).collect_failure_evidence(
                store_id,
                server_id,
                user_id,
                enforcement_point_path=enforcement_point_path,
            )
            self.assertEqual(result.relevant_login_events, ())
            self.assertIsNone(
                result.evidence_message,
                "OK and no events must not be turned into a guessed diagnosis",
            )
            records = json_lines(mock.log_file)
            self.assertEqual(len(records), 2, records)
            suffix = "?enforcement_point_path=" + quote(
                enforcement_point_path, safe=""
            )
            self.assertEqual(
                records[0]["raw_target"],
                "/policy/api/v1/infra/identity-firewall-stores/"
                + quote(store_id, safe="")
                + "/event-log-servers/"
                + quote(server_id, safe="")
                + suffix,
            )
            self.assertEqual(
                records[1]["raw_target"],
                "/policy/api/v1/infra/settings/firewall/idfw/user-stats/"
                + quote(user_id, safe="")
                + suffix,
            )
            for record in records:
                self.assertEqual(record["body_utf8"], "")
                self.assertNotIn("content-type", record["headers"])
                self.assertNotIn("content-length", record["headers"])

    def test_05_http_error_is_typed_complete_and_not_retried(self) -> None:
        nonce = secrets.token_hex(7)
        store_id = f"store-{nonce}"
        server_id = f"server-{nonce}"
        user_id = f"user-{nonce}"
        password = f"high-value-{nonce}"
        envelope = {
            "error_code": 73066,
            "error_message": f"IDFW statistics unavailable {nonce}",
            "module_name": "PolicyIdentity",
            "details": f"runtime detail {nonce}",
            "error_data": {"request": nonce},
            "related_errors": [
                {"error_code": 73067, "error_message": f"related {nonce}"}
            ],
        }
        scenario = {
            "collector": {
                "id": server_id,
                "host": "192.0.2.42",
                "status": {"status": "ERROR", "error_message": f"e-{nonce}"},
            },
            "events_http_status": 503,
            "user_stats": envelope,
        }
        with MockProcess(scenario) as mock:
            client = NsxPolicyClient(mock.base_url, f"svc-{nonce}", password)
            with self.assertRaises(NsxApiError) as raised:
                client.collect_failure_evidence(store_id, server_id, user_id)
            error = raised.exception
            self.assertEqual(error.status_code, 503)
            self.assertEqual(error.envelope, envelope)
            self.assertEqual(error.error_code, envelope["error_code"])
            self.assertEqual(error.error_message, envelope["error_message"])
            self.assertEqual(error.module_name, envelope["module_name"])
            self.assertEqual(error.details, envelope["details"])
            self.assertNotIn(password, str(error))
            self.assertNotIn("Basic ", str(error))
            self.assertEqual(
                [record["operationId"] for record in json_lines(mock.log_file)],
                [COLLECTOR_OPERATION, EVENTS_OPERATION],
                "an HTTP failure must not be retried",
            )

    def test_06_malformed_relevant_event_is_not_silently_used(self) -> None:
        nonce = secrets.token_hex(7)
        server_id = f"server-{nonce}"
        user_id = f"user-{nonce}"
        malformed = session(f"bad-{nonce}", user_id, "ELS", 100)
        malformed["login_time"] = True
        scenario = {
            "collector": {
                "id": server_id,
                "host": "192.0.2.43",
                "status": {"status": "ERROR"},
            },
            "user_stats": {
                "user_id": user_id,
                "active_sessions": [malformed],
                "archived_sessions": [],
            },
        }
        with MockProcess(scenario) as mock:
            client = NsxPolicyClient(mock.base_url, "svc", f"pw-{nonce}")
            with self.assertRaises(ProtocolError):
                client.collect_failure_evidence(
                    f"store-{nonce}", server_id, user_id
                )
            self.assertEqual(len(json_lines(mock.log_file)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
