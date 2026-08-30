"""Protected acceptance verification for generation-safe NSX credential rotation."""

from __future__ import annotations

import base64
import json
import math
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote

from nsx_rotating_client import (
    CredentialRetirementError,
    NsxApiError,
    NsxPolicyClient,
    ProtocolError,
    RotationResult,
    RotationTimeout,
)


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "mock_nsx_policy.py"
OPERATION_ID = "ListTier1"


def read_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def arrivals(path: Path) -> list[dict[str, object]]:
    return [
        record
        for record in read_json_lines(path)
        if record.get("event") == "arrival"
    ]


def wait_for(
    predicate: object,
    *,
    timeout: float = 3.0,
    message: str = "condition was not observed",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


class CallThread:
    def __init__(self, target: object):
        self.result: object | None = None
        self.error: BaseException | None = None
        self.done = threading.Event()

        def run() -> None:
            try:
                if not callable(target):
                    raise TypeError("thread target is not callable")
                self.result = target()
            except BaseException as error:  # surfaced by join_result
                self.error = error
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def join_result(self, timeout: float = 5.0) -> object:
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError("worker thread did not finish")
        if self.error is not None:
            raise self.error
        return self.result


class MockProcess:
    def __init__(self, scenario: dict[str, object]) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="nsx-rotation-")
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


def credentials(nonce: str) -> tuple[dict[str, str], dict[str, str]]:
    old = {
        "username": f"inventory-{nonce}",
        "password": f"old:secret:{nonce}",
    }
    new = {
        "username": f"inventory-{nonce}",
        "password": f"new:secret:{nonce}",
    }
    return old, new


def authorization(value: dict[str, str]) -> str:
    payload = f"{value['username']}:{value['password']}".encode("utf-8")
    return "Basic " + base64.b64encode(payload).decode("ascii")


def response(cursor: str | None, body: object, status: int = 200) -> dict[str, object]:
    return {"cursor": cursor, "status": status, "body": body}


def scenario(
    old: dict[str, str],
    new: dict[str, str],
    responses: list[dict[str, object]],
    *,
    old_cursor: str | None = None,
    new_cursor: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "credentials": {"old": old, "new": new},
        "responses": responses,
    }
    if old_cursor is not None or new_cursor is not None:
        value["gate"] = {
            "old_cursor": old_cursor,
            "new_cursor": new_cursor,
        }
    return value


class ContractAndValidationTests(unittest.TestCase):
    def test_01_provenance_and_operation_projection(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

        self.assertEqual(contract["swagger"], "2.0")
        self.assertEqual(contract["info"]["title"], "NSX Policy API")
        self.assertEqual(contract["info"]["version"], "9.1.0.0")
        self.assertEqual(contract["basePath"], "/policy/api/v1")
        self.assertEqual(list(contract["operations"]), [OPERATION_ID])
        operation = contract["operations"][OPERATION_ID]
        self.assertEqual(operation["operationId"], OPERATION_ID)
        self.assertEqual(operation["method"], "GET")
        self.assertEqual(operation["path"], "/infra/tier-1s")
        self.assertEqual(
            [parameter["name"] for parameter in operation["parameters"]],
            [
                "cursor",
                "include_mark_for_delete_objects",
                "included_fields",
                "page_size",
                "sort_ascending",
                "sort_by",
            ],
        )
        page_size = operation["parameters"][3]
        self.assertEqual(page_size["minimum"], 0)
        self.assertEqual(page_size["maximum"], 1000)
        self.assertEqual(operation["responses"]["200"]["schema_ref"],
                         "#/definitions/Tier1ListResult")

        expected_commit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
        expected_path = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
        expected_blob = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
        self.assertEqual(sources["repository_commit_sha"], expected_commit)
        self.assertEqual(sources["spec_path"], expected_path)
        self.assertEqual(sources["spec_blob_sha"], expected_blob)
        self.assertEqual(sources["license"], "Apache-2.0")
        self.assertEqual(contract["source"]["repository_commit_sha"],
                         expected_commit)
        self.assertEqual(contract["source"]["spec_path"], expected_path)
        self.assertEqual(contract["source"]["spec_blob_sha"], expected_blob)
        self.assertEqual(
            [entry["operationId"] for entry in sources["operations"]],
            [OPERATION_ID],
        )
        source_operation = sources["operations"][0]
        self.assertEqual(source_operation["repository_commit_sha"],
                         expected_commit)
        self.assertEqual(source_operation["spec_path"], expected_path)
        self.assertEqual(
            source_operation["yaml_pointer"],
            "#/paths/~1infra~1tier-1s/get",
        )

    def test_02_constructor_and_preflight_validation(self) -> None:
        invalid_origins = (
            "",
            "ftp://127.0.0.1",
            "http://",
            "http://user@127.0.0.1",
            "http://127.0.0.1/policy",
            "http://127.0.0.1?x=1",
            "http://127.0.0.1#fragment",
            "http://127.0.0.1:bad",
        )
        for origin in invalid_origins:
            with self.assertRaises(ValueError, msg=origin):
                NsxPolicyClient(origin, "svc", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1:1", "", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1:1", "svc:name", "secret")
        with self.assertRaises(ValueError):
            NsxPolicyClient("http://127.0.0.1:1", "svc", " ")
        with self.assertRaises(TypeError):
            NsxPolicyClient("http://127.0.0.1:1", "svc", "secret", timeout=True)
        for timeout in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                NsxPolicyClient(
                    "http://127.0.0.1:1", "svc", "secret", timeout=timeout
                )

        client = NsxPolicyClient("http://127.0.0.1:1", "svc", "secret")
        self.assertEqual(client.credential_generation, 1)
        invalid_calls = (
            lambda: client.list_tier1s(cursor=""),
            lambda: client.list_tier1s(cursor="   "),
            lambda: client.list_tier1s(cursor=7),
            lambda: client.list_tier1s(page_size=True),
            lambda: client.list_tier1s(page_size=-1),
            lambda: client.list_tier1s(page_size=1001),
            lambda: client.list_tier1s(page_size=1.5),
            lambda: client.rotate_credentials("svc", "next", None),
            lambda: client.rotate_credentials(
                "svc", "next", lambda: None, drain_timeout=0
            ),
            lambda: client.rotate_credentials(
                "svc", "next", lambda: None, drain_timeout=math.inf
            ),
            lambda: client.rotate_credentials(
                "svc", "secret", lambda: None
            ),
        )
        for call in invalid_calls:
            with self.assertRaises((TypeError, ValueError)):
                call()
        self.assertEqual(
            client.credential_generation,
            1,
            "preflight failures must not publish a credential generation",
        )
        self.assertTrue(issubclass(NsxApiError, Exception))
        self.assertTrue(issubclass(ProtocolError, Exception))
        self.assertTrue(issubclass(RotationTimeout, Exception))
        self.assertTrue(issubclass(CredentialRetirementError, Exception))


class WireShapeTests(unittest.TestCase):
    def test_03_exact_query_headers_and_optional_omission(self) -> None:
        nonce = secrets.token_hex(8)
        old, new = credentials(nonce)
        cursor = f"next /?#% Δ:{nonce}"
        first_page = {
            "results": [
                {
                    "resource_type": "Tier1",
                    "id": f"first-{nonce}",
                    "display_name": "First",
                    "path": f"/infra/tier-1s/first-{nonce}",
                }
            ],
            "result_count": 1,
        }
        second_page = {
            "results": [],
            "cursor": f"done-{nonce}",
            "result_count": 0,
        }
        runtime = scenario(
            old,
            new,
            [response(None, first_page), response(cursor, second_page)],
        )

        with MockProcess(runtime) as mock:
            client = NsxPolicyClient(
                mock.base_url, old["username"], old["password"], timeout=2.0
            )
            self.assertEqual(client.list_tier1s(), first_page)
            self.assertEqual(
                client.list_tier1s(cursor=cursor, page_size=0),
                second_page,
            )

            records = arrivals(mock.log_file)
            self.assertEqual(len(records), 2, records)
            expected_authorization = authorization(old)
            expected_targets = [
                "/policy/api/v1/infra/tier-1s",
                (
                    "/policy/api/v1/infra/tier-1s?cursor="
                    f"{quote(cursor, safe='')}&page_size=0"
                ),
            ]
            for record, target in zip(records, expected_targets):
                self.assertEqual(record["operationId"], OPERATION_ID)
                self.assertEqual(record["method"], "GET")
                self.assertEqual(record["raw_target"], target)
                self.assertEqual(record["body_utf8"], "")
                headers = record["headers"]
                self.assertEqual(headers["authorization"],
                                 expected_authorization)
                self.assertEqual(headers["accept"], "application/json")
                self.assertNotIn("content-type", headers)
                self.assertNotIn("content-length", headers)

            self.assertNotIn("?", records[0]["raw_target"])
            for name in (
                "include_mark_for_delete_objects",
                "included_fields",
                "sort_ascending",
                "sort_by",
            ):
                for record in records:
                    self.assertNotIn(f"{name}=", record["raw_target"])
            self.assertNotIn("cursor=", records[0]["raw_target"])
            self.assertNotIn("page_size=", records[0]["raw_target"])

    def test_04_http_and_protocol_errors_are_single_attempt_and_sanitized(self) -> None:
        nonce = secrets.token_hex(8)
        old, new = credentials(nonce)
        malformed_cursor = f"malformed-{nonce}"
        runtime = scenario(
            old,
            new,
            [response(malformed_cursor, {"results": {}})],
        )

        with MockProcess(runtime) as mock:
            wrong_secret = f"wrong-secret-{nonce}"
            wrong = NsxPolicyClient(
                mock.base_url, old["username"], wrong_secret, timeout=2.0
            )
            with self.assertRaises(NsxApiError) as caught:
                wrong.list_tier1s(cursor=f"unauthorized-{nonce}")
            error = caught.exception
            self.assertEqual(error.status_code, 401)
            self.assertEqual(
                error.envelope,
                {
                    "error_code": 40165,
                    "error_message": "credential not accepted",
                    "module_name": "authentication",
                    "details": "Basic authentication failed.",
                },
            )
            self.assertEqual(error.error_code, 40165)
            self.assertEqual(error.error_message, "credential not accepted")
            self.assertEqual(error.module_name, "authentication")
            self.assertEqual(error.details, "Basic authentication failed.")
            self.assertNotIn(old["username"], str(error))
            self.assertNotIn(wrong_secret, str(error))
            self.assertNotIn("Basic ", str(error))

            valid = NsxPolicyClient(
                mock.base_url, old["username"], old["password"], timeout=2.0
            )
            with self.assertRaises(ProtocolError):
                valid.list_tier1s(cursor=malformed_cursor)

            self.assertEqual(
                len(arrivals(mock.log_file)),
                2,
                "neither HTTP nor protocol failures may be retried",
            )


class RotationTests(unittest.TestCase):
    def test_05_replacement_is_published_before_old_generation_drains(self) -> None:
        nonce = secrets.token_hex(8)
        old, new = credentials(nonce)
        old_cursor = f"old in flight / Δ-{nonce}"
        new_cursor = f"new during drain / Δ-{nonce}"
        old_page = {"results": [{"id": f"old-{nonce}"}]}
        new_page = {"results": [{"id": f"new-{nonce}"}]}
        runtime = scenario(
            old,
            new,
            [response(old_cursor, old_page), response(new_cursor, new_page)],
            old_cursor=old_cursor,
            new_cursor=new_cursor,
        )

        with MockProcess(runtime) as mock:
            client = NsxPolicyClient(
                mock.base_url, old["username"], old["password"], timeout=5.0
            )
            old_call = CallThread(
                lambda: client.list_tier1s(
                    cursor=old_cursor, page_size=17
                )
            )
            wait_for(
                lambda: any(
                    record.get("credential_label") == "old"
                    for record in arrivals(mock.log_file)
                ),
                message="old-generation request did not reach the mock",
            )

            callback_state = {"calls": 0, "saw_old_complete": False}

            def retire_old() -> None:
                callback_state["calls"] += 1
                records = read_json_lines(mock.log_file)
                old_arrivals = [
                    record
                    for record in records
                    if record.get("event") == "arrival"
                    and record.get("credential_label") == "old"
                ]
                if old_arrivals:
                    request_id = old_arrivals[0]["request_id"]
                    callback_state["saw_old_complete"] = any(
                        record.get("event") == "complete"
                        and record.get("request_id") == request_id
                        for record in records
                    )

            rotation = CallThread(
                lambda: client.rotate_credentials(
                    new["username"],
                    new["password"],
                    retire_old,
                    drain_timeout=3.0,
                )
            )
            wait_for(
                lambda: client.credential_generation == 2,
                message="replacement generation was not published",
            )
            self.assertFalse(rotation.done.is_set())
            self.assertEqual(callback_state["calls"], 0)

            new_result = client.list_tier1s(
                cursor=new_cursor, page_size=23
            )
            old_result = old_call.join_result()
            rotation_result = rotation.join_result()

            self.assertEqual(old_result, old_page)
            self.assertEqual(new_result, new_page)
            self.assertIsInstance(rotation_result, RotationResult)
            self.assertEqual(rotation_result.old_generation, 1)
            self.assertEqual(rotation_result.new_generation, 2)
            self.assertIs(rotation_result.retired, True)
            self.assertEqual(callback_state["calls"], 1)
            self.assertIs(callback_state["saw_old_complete"], True)

            records = arrivals(mock.log_file)
            self.assertEqual(len(records), 2, records)
            self.assertEqual(
                [record["credential_label"] for record in records],
                ["old", "new"],
            )
            self.assertEqual(
                records[0]["raw_target"],
                (
                    "/policy/api/v1/infra/tier-1s?cursor="
                    f"{quote(old_cursor, safe='')}&page_size=17"
                ),
            )
            self.assertEqual(
                records[1]["raw_target"],
                (
                    "/policy/api/v1/infra/tier-1s?cursor="
                    f"{quote(new_cursor, safe='')}&page_size=23"
                ),
            )
            self.assertEqual(
                records[0]["headers"]["authorization"], authorization(old)
            )
            self.assertEqual(
                records[1]["headers"]["authorization"], authorization(new)
            )

    def test_06_timeout_keeps_new_active_and_never_retires_in_use_old(self) -> None:
        nonce = secrets.token_hex(8)
        old, new = credentials(nonce)
        old_cursor = f"blocked-old-{nonce}"
        new_cursor = f"release-new-{nonce}"
        runtime = scenario(
            old,
            new,
            [
                response(old_cursor, {"results": [{"id": old_cursor}]}),
                response(new_cursor, {"results": [{"id": new_cursor}]}),
            ],
            old_cursor=old_cursor,
            new_cursor=new_cursor,
        )

        with MockProcess(runtime) as mock:
            client = NsxPolicyClient(
                mock.base_url, old["username"], old["password"], timeout=5.0
            )
            old_call = CallThread(
                lambda: client.list_tier1s(cursor=old_cursor)
            )
            wait_for(
                lambda: len(arrivals(mock.log_file)) == 1,
                message="blocked old request did not arrive",
            )

            callback_calls: list[str] = []
            with self.assertRaises(RotationTimeout) as caught:
                client.rotate_credentials(
                    new["username"],
                    new["password"],
                    lambda: callback_calls.append("retired"),
                    drain_timeout=0.08,
                )
            error = caught.exception
            self.assertEqual(error.old_generation, 1)
            self.assertEqual(error.new_generation, 2)
            self.assertGreater(error.pending_requests, 0)
            self.assertEqual(callback_calls, [])
            self.assertEqual(client.credential_generation, 2)

            new_page = client.list_tier1s(cursor=new_cursor)
            old_page = old_call.join_result()
            self.assertEqual(new_page["results"][0]["id"], new_cursor)
            self.assertEqual(old_page["results"][0]["id"], old_cursor)
            self.assertEqual(callback_calls, [])
            records = arrivals(mock.log_file)
            self.assertEqual(len(records), 2, records)
            self.assertEqual(
                [record["credential_label"] for record in records],
                ["old", "new"],
            )

    def test_07_retirement_failure_is_sanitized_and_does_not_roll_back(self) -> None:
        nonce = secrets.token_hex(8)
        old, new = credentials(nonce)
        client = NsxPolicyClient(
            "http://127.0.0.1:1",
            old["username"],
            old["password"],
        )

        def fail_retirement() -> None:
            raise RuntimeError(
                f"backend mentioned {old['password']} and {new['password']}"
            )

        with self.assertRaises(CredentialRetirementError) as caught:
            client.rotate_credentials(
                new["username"], new["password"], fail_retirement
            )
        error = caught.exception
        self.assertEqual(error.old_generation, 1)
        self.assertEqual(error.new_generation, 2)
        self.assertEqual(client.credential_generation, 2)
        self.assertNotIn(old["username"], str(error))
        self.assertNotIn(old["password"], str(error))
        self.assertNotIn(new["password"], str(error))
        self.assertNotIn("Basic ", str(error))


if __name__ == "__main__":
    unittest.main(verbosity=2)
