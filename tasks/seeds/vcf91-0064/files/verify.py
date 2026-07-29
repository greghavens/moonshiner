"""Protected acceptance verification for the contract-pinned Tier-1 guard."""

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

from nsx_tier1_guard import (
    NsxApiError,
    NsxPolicyClient,
    PrecheckFailed,
    ProtocolError,
    Tier1DescriptionPatch,
    UpdateResult,
)


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "mock_nsx_policy.py"
PRECHECK_OPERATION_ID = "GetTier1State"
MUTATION_OPERATION_ID = "PatchTier1"


def read_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class MockProcess:
    def __init__(self, scenarios: dict[str, object]) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="nsx-tier1-guard-")
        temp_path = Path(self.tempdir.name)
        self.port_file = temp_path / "port"
        self.log_file = temp_path / "requests.jsonl"
        self.scenario_file = temp_path / "scenarios.json"
        self.scenario_file.write_text(
            json.dumps(
                {"scenarios": scenarios},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
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


class ContractAndModelTests(unittest.TestCase):
    def test_01_provenance_and_operation_projection(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        operation_ids = [PRECHECK_OPERATION_ID, MUTATION_OPERATION_ID]

        self.assertEqual(contract["swagger"], "2.0")
        self.assertEqual(contract["info"]["title"], "NSX Policy API")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(contract["basePath"], "/policy/api/v1")
        self.assertEqual(list(contract["operations"]), operation_ids)
        self.assertEqual(
            contract["operations"][PRECHECK_OPERATION_ID]["method"], "GET"
        )
        self.assertEqual(
            contract["operations"][PRECHECK_OPERATION_ID]["path"],
            "/infra/tier-1s/{tier-1-id}/state",
        )
        self.assertEqual(
            contract["operations"][MUTATION_OPERATION_ID]["method"], "PATCH"
        )
        self.assertEqual(
            contract["operations"][MUTATION_OPERATION_ID]["path"],
            "/infra/tier-1s/{tier-1-id}",
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
            operation_ids,
        )
        for entry in sources["operations"]:
            self.assertEqual(
                entry["repository_commit_sha"], sources["repository_commit_sha"]
            )
            self.assertEqual(entry["spec_path"], sources["spec_path"])

    def test_02_patch_model_and_constructor_validation(self) -> None:
        patch = Tier1DescriptionPatch("Approved maintenance change")
        self.assertEqual(
            patch.to_wire(),
            {
                "resource_type": "Tier1",
                "description": "Approved maintenance change",
            },
        )
        self.assertEqual(
            list(patch.to_wire()), ["resource_type", "description"]
        )
        with self.assertRaises(ValueError):
            Tier1DescriptionPatch("").to_wire()
        with self.assertRaises(ValueError):
            Tier1DescriptionPatch(" " * 4).to_wire()
        with self.assertRaises(ValueError):
            Tier1DescriptionPatch("d" * 1025).to_wire()
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1:bad", "svc", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://user@127.0.0.1", "svc", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1/path", "svc", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1:1", "svc:name", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient(
                "http://127.0.0.1:1", "svc", "secret", timeout=math.inf
            )
        self.assertTrue(issubclass(NsxApiError, Exception))
        self.assertTrue(issubclass(PrecheckFailed, Exception))
        self.assertTrue(issubclass(ProtocolError, Exception))


class WireAndGateTests(unittest.TestCase):
    def test_03_success_uses_exact_wire_shape_and_omits_unset_fields(self) -> None:
        nonce = secrets.token_hex(8)
        tier1_id = f"west/core ?#% Δ-{nonce}"
        description = f"approved change Δ {nonce}"
        username = f"svc-{nonce}"
        password = f"pw:{nonce}:not-logged"
        scenarios = {
            tier1_id: {
                "precheck": {
                    "status": 200,
                    "body": {
                        "tier1_state": {
                            "state": "success",
                            "details": [],
                        }
                    },
                },
                "mutation": {"status": 200, "body": None},
            }
        }

        with MockProcess(scenarios) as mock:
            client = NsxPolicyClient(
                mock.base_url, username, password, timeout=2.0
            )
            invalid_calls = (
                lambda: client.update_tier1_description_if_ready(
                    "", Tier1DescriptionPatch(description)
                ),
                lambda: client.update_tier1_description_if_ready(
                    tier1_id, object()
                ),
                lambda: client.update_tier1_description_if_ready(
                    tier1_id, Tier1DescriptionPatch(" ")
                ),
                lambda: client.update_tier1_description_if_ready(
                    tier1_id,
                    Tier1DescriptionPatch(description),
                    enforcement_point_path=" ",
                ),
                lambda: client.update_tier1_description_if_ready(
                    tier1_id,
                    Tier1DescriptionPatch(description),
                    source="live",
                ),
            )
            for invalid_call in invalid_calls:
                with self.assertRaises((TypeError, ValueError)):
                    invalid_call()
            self.assertEqual(
                read_json_lines(mock.log_file),
                [],
                "local validation errors must not open an HTTP connection",
            )

            result = client.update_tier1_description_if_ready(
                tier1_id,
                Tier1DescriptionPatch(description),
            )
            self.assertIsInstance(result, UpdateResult)
            self.assertEqual(result.tier1_id, tier1_id)
            self.assertEqual(result.precheck_state, "success")
            self.assertIs(result.changed, True)

            records = read_json_lines(mock.log_file)
            self.assertEqual(
                [record["operationId"] for record in records],
                [PRECHECK_OPERATION_ID, MUTATION_OPERATION_ID],
            )
            self.assertEqual(len(records), 2, records)
            encoded_id = quote(tier1_id, safe="")
            expected_precheck_target = (
                f"/policy/api/v1/infra/tier-1s/{encoded_id}"
                "/state?type=GATEWAY_STATE"
            )
            expected_mutation_target = (
                f"/policy/api/v1/infra/tier-1s/{encoded_id}"
            )
            expected_authorization = "Basic " + base64.b64encode(
                f"{username}:{password}".encode("utf-8")
            ).decode("ascii")

            precheck = records[0]
            self.assertEqual(precheck["method"], "GET")
            self.assertEqual(precheck["raw_target"], expected_precheck_target)
            self.assertEqual(precheck["body_utf8"], "")
            self.assertEqual(precheck["headers"]["authorization"], expected_authorization)
            self.assertEqual(precheck["headers"]["accept"], "application/json")
            self.assertNotIn("content-type", precheck["headers"])
            self.assertNotIn("content-length", precheck["headers"])
            self.assertNotIn("cursor=", precheck["raw_target"])
            self.assertNotIn("enforcement_point_path=", precheck["raw_target"])
            self.assertNotIn("included_fields=", precheck["raw_target"])
            self.assertNotIn("interface_path=", precheck["raw_target"])
            self.assertNotIn("page_size=", precheck["raw_target"])
            self.assertNotIn("sort_ascending=", precheck["raw_target"])
            self.assertNotIn("sort_by=", precheck["raw_target"])
            self.assertNotIn("source=", precheck["raw_target"])
            self.assertEqual(precheck["mutation_count"], 0)

            expected_body = json.dumps(
                {
                    "resource_type": "Tier1",
                    "description": description,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            mutation = records[1]
            self.assertEqual(mutation["method"], "PATCH")
            self.assertEqual(mutation["raw_target"], expected_mutation_target)
            self.assertNotIn("?", mutation["raw_target"])
            self.assertEqual(mutation["body_utf8"], expected_body)
            self.assertEqual(mutation["headers"]["authorization"], expected_authorization)
            self.assertEqual(mutation["headers"]["accept"], "application/json")
            self.assertEqual(mutation["headers"]["content-type"], "application/json")
            self.assertEqual(
                mutation["headers"]["content-length"],
                str(len(expected_body.encode("utf-8"))),
            )
            decoded_body = json.loads(mutation["body_utf8"])
            self.assertEqual(
                list(decoded_body), ["resource_type", "description"]
            )
            self.assertEqual(
                set(decoded_body), {"resource_type", "description"}
            )
            for optional_name in (
                "display_name",
                "route_advertisement_types",
                "tier0_path",
                "tags",
                "children",
                "arp_limit",
                "default_rule_logging",
                "dhcp_config_paths",
                "disable_firewall",
                "enable_standby_relocation",
                "failover_mode",
                "id",
                "_revision",
            ):
                self.assertNotIn(optional_name, decoded_body)
            self.assertEqual(mutation["mutation_count"], 1)

    def test_04_set_query_options_are_encoded_in_specification_order(self) -> None:
        nonce = secrets.token_hex(7)
        tier1_id = f"query/options {nonce}"
        enforcement_point_path = (
            f"/infra/sites/default/enforcement-points/edge A?{nonce}"
        )
        scenarios = {
            tier1_id: {
                "precheck": {
                    "status": 200,
                    "body": {"tier1_state": {"state": "success"}},
                },
                "mutation": {"status": 200, "body": None},
            }
        }
        with MockProcess(scenarios) as mock:
            client = NsxPolicyClient(mock.base_url, "query-svc", f"pw-{nonce}")
            client.update_tier1_description_if_ready(
                tier1_id,
                Tier1DescriptionPatch(f"query order {nonce}"),
                enforcement_point_path=enforcement_point_path,
                source="realtime",
            )
            records = read_json_lines(mock.log_file)
            self.assertEqual(len(records), 2, records)
            expected = (
                "/policy/api/v1/infra/tier-1s/"
                + quote(tier1_id, safe="")
                + "/state?enforcement_point_path="
                + quote(enforcement_point_path, safe="")
                + "&source=realtime&type=GATEWAY_STATE"
            )
            self.assertEqual(records[0]["raw_target"], expected)
            self.assertNotIn("+", records[0]["raw_target"])
            self.assertEqual(records[0]["operationId"], PRECHECK_OPERATION_ID)
            self.assertEqual(records[1]["operationId"], MUTATION_OPERATION_ID)

    def test_05_every_precheck_failure_blocks_the_mutation(self) -> None:
        nonce = secrets.token_hex(8)
        state_failure_id = f"state-failure/{nonce}"
        malformed_id = f"malformed/{nonce}"
        http_failure_id = f"http-failure/{nonce}"
        failure_message = f"realization blocked {nonce}"
        scenarios = {
            state_failure_id: {
                "precheck": {
                    "status": 200,
                    "body": {
                        "tier1_state": {
                            "state": "error",
                            "failure_code": 9407,
                            "failure_message": failure_message,
                        }
                    },
                }
            },
            malformed_id: {
                "precheck": {
                    "status": 200,
                    "body": {"tier1_status": {}},
                }
            },
            http_failure_id: {
                "precheck": {
                    "status": 503,
                    "body": {
                        "error_code": 50364,
                        "error_message": f"precheck unavailable {nonce}",
                        "module_name": "contract-mock",
                        "details": "the guarded mutation must not run",
                    },
                }
            },
        }

        with MockProcess(scenarios) as mock:
            username = f"blocked-svc-{nonce}"
            password = f"blocked-password-{nonce}"
            client = NsxPolicyClient(mock.base_url, username, password)
            patch = Tier1DescriptionPatch(f"must never be sent {nonce}")

            with self.assertRaises(PrecheckFailed) as blocked:
                client.update_tier1_description_if_ready(state_failure_id, patch)
            self.assertEqual(blocked.exception.tier1_id, state_failure_id)
            self.assertEqual(blocked.exception.state, "error")
            self.assertEqual(blocked.exception.failure_code, 9407)
            self.assertEqual(
                blocked.exception.failure_message, failure_message
            )

            with self.assertRaises(ProtocolError):
                client.update_tier1_description_if_ready(malformed_id, patch)

            with self.assertRaises(NsxApiError) as unavailable:
                client.update_tier1_description_if_ready(http_failure_id, patch)
            api_error = unavailable.exception
            self.assertEqual(api_error.status_code, 503)
            self.assertEqual(api_error.error_code, 50364)
            self.assertEqual(
                api_error.error_message, f"precheck unavailable {nonce}"
            )
            self.assertEqual(api_error.module_name, "contract-mock")
            self.assertEqual(
                api_error.details, "the guarded mutation must not run"
            )
            self.assertEqual(api_error.envelope["error_code"], 50364)

            for error in (blocked.exception, api_error):
                rendered = str(error)
                self.assertNotIn(username, rendered)
                self.assertNotIn(password, rendered)
                self.assertNotIn("Basic ", rendered)

            records = read_json_lines(mock.log_file)
            self.assertEqual(len(records), 3, records)
            self.assertEqual(
                [record["operationId"] for record in records],
                [PRECHECK_OPERATION_ID] * 3,
                "a failed precheck must not be followed by PatchTier1",
            )
            self.assertTrue(all(record["method"] == "GET" for record in records))
            self.assertTrue(
                all(record["mutation_count"] == 0 for record in records),
                "no failed precheck may change mock state",
            )
            self.assertFalse(
                any(
                    record["operationId"] == MUTATION_OPERATION_ID
                    for record in records
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
