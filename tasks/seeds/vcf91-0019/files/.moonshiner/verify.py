#!/usr/bin/env python3
"""Protected verifier for concurrency-safe VCF credential rotation."""

from __future__ import annotations

import ast
import json
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mock_sddc_manager import CONTRACT, MockSddcManager  # noqa: E402


PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    {
        "operationId": "updateOrRotatePasswords",
        "method": "PATCH",
        "path": "/v1/credentials",
    },
    {
        "operationId": "getCredentialsTask",
        "method": "GET",
        "path": "/v1/credentials/tasks/{id}",
    },
    {
        "operationId": "getCredentialTaskByResourceID",
        "method": "GET",
        "path": "/v1/credentials/tasks/{id}/resource-credentials",
    },
]
CREATED = "2026-07-28T18:00:00.000Z"


def credential_task(task_id: str, status: str, **extra: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": task_id,
        "name": "Credential update",
        "type": "UPDATE",
        "creationTimestamp": CREATED,
        "status": status,
        "subTasks": [],
        "errors": [],
        "isAutoRotate": False,
    }
    value.update(extra)
    return value


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 2.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class ContractTests(unittest.TestCase):
    def test_contract_is_the_exact_pinned_spec_projection(self) -> None:
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
            schemas["CredentialsUpdateSpec"]["required"],
            ["elements", "operationType"],
        )
        self.assertEqual(
            set(schemas["CredentialsUpdateSpec"]["properties"]),
            {"operationType", "elements", "autoRotatePolicy"},
        )
        self.assertEqual(
            schemas["ResourceCredentials"]["required"],
            ["credentials", "resourceType"],
        )
        self.assertEqual(
            set(schemas["BaseCredential"]["properties"]),
            {
                "credentialType",
                "accountType",
                "username",
                "password",
            },
        )
        self.assertEqual(
            schemas["CredentialsTask"]["status_example_values"],
            [
                "PENDING",
                "IN_PROGRESS",
                "SUCCESSFUL",
                "FAILED",
                "USER_CANCELLED",
                "INCONSISTENT",
            ],
        )

    def test_mock_serves_only_contract_operations(self) -> None:
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
                    server.base_url + "/v1/credentials/uncontracted",
                    data=b"{}",
                    method="PATCH",
                    headers={
                        "Authorization": "Bearer token",
                        "Content-Type": "application/json",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(caught.exception.code, 404)
                self.assertIsNone(server.read_request_log()[0]["operationId"])

    def test_package_is_stdlib_only(self) -> None:
        package = ROOT / "vcf_credential_rotation"
        self.assertEqual(
            {
                path.name
                for path in package.iterdir()
                if path.name != "__pycache__"
            },
            {"__init__.py", "client.py"},
        )
        source_path = package / "client.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        forbidden_transports = {"socket", "subprocess", "http", "requests"}
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
                    forbidden_transports,
                    f"forbidden transport/import in client.py: {name}",
                )

        vendored_suffixes = {".whl", ".egg", ".zip", ".nupkg"}
        vendored = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file() and path.suffix.lower() in vendored_suffixes
        ]
        self.assertEqual(vendored, [])


class RotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.token = "access-" + secrets.token_hex(9)
        self.old_secret = "old-" + secrets.token_urlsafe(15)
        self.new_secret = "new-" + secrets.token_urlsafe(15)
        self.resource_id = "resource-" + secrets.token_hex(8)
        self.username = "svc-" + secrets.token_hex(5)
        self.task_id = "rotation/" + secrets.token_hex(7) + " queued"

    def _client(
        self,
        server: MockSddcManager,
        *,
        sleep: Callable[[float], Any] = lambda _seconds: None,
        max_polls: int = 4,
    ):
        from vcf_credential_rotation import SddcManagerCredentialRotator

        return SddcManagerCredentialRotator(
            server.base_url,
            self.token,
            sleep=sleep,
            poll_interval=0.125,
            max_polls=max_polls,
            timeout=2.0,
        )

    def test_drain_blocks_patch_and_waiters_then_success_publishes(self) -> None:
        from vcf_credential_rotation import ManagedCredential

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "requests.jsonl"
            with MockSddcManager(self.token, log_path) as server:
                server.script(
                    "updateOrRotatePasswords",
                    [(202, credential_task(self.task_id, "SUCCESSFUL"))],
                )
                server.script(
                    "getCredentialsTask",
                    [
                        (
                            200,
                            credential_task(self.task_id, "IN_PROGRESS"),
                        ),
                        (
                            200,
                            credential_task(
                                self.task_id,
                                "SUCCESSFUL",
                                completionTimestamp=(
                                    "2026-07-28T18:00:03.000Z"
                                ),
                            ),
                        ),
                    ],
                )
                server.script(
                    "getCredentialTaskByResourceID",
                    [
                        (
                            200,
                            [
                                {
                                    "resourceId": self.resource_id,
                                    "resourceType": "VCENTER",
                                    "credentials": [
                                        {
                                            "username": "decoy-"
                                            + secrets.token_hex(4),
                                            "password": "decoy-"
                                            + secrets.token_urlsafe(8),
                                        },
                                        {
                                            "username": self.username,
                                            "password": self.new_secret,
                                        }
                                    ],
                                }
                            ],
                        )
                    ],
                )

                sleep_entered = threading.Event()
                allow_second_poll = threading.Event()
                observed_sleeps: list[float] = []

                def controlled_sleep(seconds: float) -> None:
                    observed_sleeps.append(seconds)
                    sleep_entered.set()
                    if not allow_second_poll.wait(2):
                        raise AssertionError("test did not release the poll")

                credential = ManagedCredential(self.old_secret)
                client = self._client(server, sleep=controlled_sleep)
                old_lease = credential.lease()
                self.assertEqual(old_lease.__enter__(), self.old_secret)

                rotation_result: list[dict[str, Any]] = []
                rotation_errors: list[BaseException] = []

                def run_rotation() -> None:
                    try:
                        rotation_result.append(
                            client.rotate(
                                credential,
                                resource_type="VCENTER",
                                username=self.username,
                                resource_id=self.resource_id,
                            )
                        )
                    except BaseException as error:
                        rotation_errors.append(error)

                rotation_thread = threading.Thread(
                    target=run_rotation,
                    name="protected-rotation",
                )
                rotation_thread.start()
                self.assertTrue(
                    wait_until(lambda: credential.is_rotating),
                    "rotation never closed the credential gate",
                )
                self.assertEqual(
                    server.read_request_log(),
                    [],
                    "PATCH was sent while an old-secret lease was active",
                )

                with self.assertRaises(RuntimeError):
                    client.rotate(
                        credential,
                        resource_type="VCENTER",
                        username=self.username,
                        resource_id=self.resource_id,
                    )

                waiter_started = threading.Event()
                waiter_finished = threading.Event()
                waiter_values: list[str] = []

                def acquire_after_rotation_started() -> None:
                    waiter_started.set()
                    with credential.lease() as secret:
                        waiter_values.append(secret)
                    waiter_finished.set()

                waiter_thread = threading.Thread(
                    target=acquire_after_rotation_started,
                    name="protected-waiter",
                )
                waiter_thread.start()
                self.assertTrue(waiter_started.wait(1))
                self.assertFalse(
                    waiter_finished.wait(0.05),
                    "a new lease escaped through the closed gate",
                )

                try:
                    old_lease.__exit__(None, None, None)
                    self.assertTrue(
                        sleep_entered.wait(2),
                        "rotation did not PATCH then immediately poll",
                    )
                    self.assertFalse(
                        waiter_finished.is_set(),
                        "new lease opened before the task completed",
                    )
                    self.assertTrue(credential.is_rotating)
                    allow_second_poll.set()
                    rotation_thread.join(timeout=2)
                    waiter_thread.join(timeout=2)
                finally:
                    allow_second_poll.set()
                    if rotation_thread.is_alive():
                        rotation_thread.join(timeout=2)
                    if waiter_thread.is_alive():
                        waiter_thread.join(timeout=2)

                self.assertFalse(rotation_thread.is_alive())
                self.assertFalse(waiter_thread.is_alive())
                self.assertEqual(rotation_errors, [])
                self.assertEqual(len(rotation_result), 1)
                self.assertEqual(
                    rotation_result[0]["status"],
                    "SUCCESSFUL",
                )
                self.assertEqual(waiter_values, [self.new_secret])
                self.assertEqual(observed_sleeps, [0.125])
                self.assertFalse(credential.is_rotating)

                requests = server.read_request_log()

        encoded_task_id = self.task_id.replace("/", "%2F").replace(" ", "%20")
        self.assertEqual(
            [
                (
                    item["operationId"],
                    item["method"],
                    item["target"],
                )
                for item in requests
            ],
            [
                (
                    "updateOrRotatePasswords",
                    "PATCH",
                    "/v1/credentials",
                ),
                (
                    "getCredentialsTask",
                    "GET",
                    f"/v1/credentials/tasks/{encoded_task_id}",
                ),
                (
                    "getCredentialsTask",
                    "GET",
                    f"/v1/credentials/tasks/{encoded_task_id}",
                ),
                (
                    "getCredentialTaskByResourceID",
                    "GET",
                    (
                        f"/v1/credentials/tasks/{encoded_task_id}"
                        "/resource-credentials"
                    ),
                ),
            ],
        )
        patch_request = requests[0]
        expected_body = {
            "operationType": "ROTATE",
            "elements": [
                {
                    "resourceId": self.resource_id,
                    "resourceType": "VCENTER",
                    "credentials": [
                        {
                            "username": self.username,
                        }
                    ],
                }
            ],
        }
        self.assertEqual(json.loads(patch_request["body"]), expected_body)
        self.assertEqual(patch_request["query"], "")
        self.assertEqual(
            patch_request["headers"]["authorization"],
            f"Bearer {self.token}",
        )
        self.assertEqual(
            patch_request["headers"]["accept"],
            "application/json",
        )
        self.assertEqual(
            patch_request["headers"]["content-type"],
            "application/json",
        )
        self.assertEqual(
            patch_request["headers"]["content-length"],
            str(len(patch_request["body"].encode("utf-8"))),
        )
        self.assertNotIn("autoRotatePolicy", expected_body)
        resource = expected_body["elements"][0]
        self.assertNotIn("resourceName", resource)
        credential_wire = resource["credentials"][0]
        self.assertNotIn("credentialType", credential_wire)
        self.assertNotIn("accountType", credential_wire)
        self.assertNotIn("password", credential_wire)
        for request in requests[1:]:
            self.assertEqual(
                request["headers"]["authorization"],
                f"Bearer {self.token}",
            )
            self.assertEqual(
                request["headers"]["accept"],
                "application/json",
            )
            self.assertNotIn("content-type", request["headers"])
            self.assertEqual(request["body"], "")
            self.assertEqual(request["query"], "")

    def test_failed_task_reopens_with_old_secret_and_preserves_task(
        self,
    ) -> None:
        from vcf_credential_rotation import (
            ManagedCredential,
            RotationFailedError,
        )

        task_id = "failure-" + secrets.token_hex(6)
        terminal = credential_task(
            task_id,
            "INCONSISTENT",
            errors=[
                {
                    "errorCode": "CREDENTIAL_STATE_INCONSISTENT",
                    "message": "Credential state differs across the resource",
                }
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.token,
                Path(directory) / "requests.jsonl",
            ) as server:
                server.script(
                    "updateOrRotatePasswords",
                    [(202, credential_task(task_id, "PENDING"))],
                )
                server.script("getCredentialsTask", [(200, terminal)])
                credential = ManagedCredential(self.old_secret)
                with self.assertRaises(RotationFailedError) as caught:
                    self._client(server).rotate(
                        credential,
                        resource_type="ESXI",
                        username=self.username,
                        resource_name="esx-" + secrets.token_hex(4),
                        credential_type="SSH",
                        account_type="USER",
                    )
                requests = server.read_request_log()

        self.assertEqual(caught.exception.task, terminal)
        self.assertFalse(credential.is_rotating)
        with credential.lease() as current:
            self.assertEqual(current, self.old_secret)
        body = json.loads(requests[0]["body"])
        self.assertEqual(body["operationType"], "ROTATE")
        resource = body["elements"][0]
        self.assertNotIn("resourceId", resource)
        self.assertEqual(resource["resourceType"], "ESXI")
        self.assertEqual(
            resource["credentials"][0]["credentialType"],
            "SSH",
        )
        self.assertEqual(
            resource["credentials"][0]["accountType"],
            "USER",
        )
        self.assertNotIn("password", resource["credentials"][0])
        self.assertNotIn("autoRotatePolicy", body)

    def test_http_error_does_not_retry_and_reopens_old_secret(self) -> None:
        from vcf_credential_rotation import (
            ManagedCredential,
            SddcManagerError,
        )

        payload = {
            "errorCode": "PASSWORD_POLICY_VIOLATION",
            "message": "The supplied password does not satisfy policy",
        }
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.token,
                Path(directory) / "requests.jsonl",
            ) as server:
                server.script(
                    "updateOrRotatePasswords",
                    [(400, payload)],
                )
                credential = ManagedCredential(self.old_secret)
                with self.assertRaises(SddcManagerError) as caught:
                    self._client(server).rotate(
                        credential,
                        resource_type="VCENTER",
                        username=self.username,
                        resource_id=self.resource_id,
                    )
                requests = server.read_request_log()

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.payload, payload)
        self.assertEqual(len(requests), 1)
        with credential.lease() as current:
            self.assertEqual(current, self.old_secret)

    def test_poll_budget_is_exact_and_keeps_old_secret(self) -> None:
        from vcf_credential_rotation import (
            ManagedCredential,
            RotationTimeoutError,
        )

        task_id = "timeout-" + secrets.token_hex(6)
        last_task = credential_task(task_id, "IN_PROGRESS")
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.token,
                Path(directory) / "requests.jsonl",
            ) as server:
                server.script(
                    "updateOrRotatePasswords",
                    [(202, credential_task(task_id, "PENDING"))],
                )
                server.script(
                    "getCredentialsTask",
                    [
                        (200, credential_task(task_id, "PENDING")),
                        (200, last_task),
                    ],
                )
                credential = ManagedCredential(self.old_secret)
                with self.assertRaises(RotationTimeoutError) as caught:
                    self._client(
                        server,
                        sleep=sleeps.append,
                        max_polls=2,
                    ).rotate(
                        credential,
                        resource_type="VCENTER",
                        username=self.username,
                        resource_id=self.resource_id,
                    )
                requests = server.read_request_log()

        self.assertEqual(caught.exception.task, last_task)
        self.assertEqual(sleeps, [0.125])
        self.assertEqual(
            [item["operationId"] for item in requests],
            [
                "updateOrRotatePasswords",
                "getCredentialsTask",
                "getCredentialsTask",
            ],
        )
        with credential.lease() as current:
            self.assertEqual(current, self.old_secret)

    def test_protocol_error_reopens_old_secret(self) -> None:
        from vcf_credential_rotation import ManagedCredential, ProtocolError

        task_id = "protocol-" + secrets.token_hex(6)
        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.token,
                Path(directory) / "requests.jsonl",
            ) as server:
                server.script(
                    "updateOrRotatePasswords",
                    [(202, credential_task(task_id, "SUCCESSFUL"))],
                )
                server.script(
                    "getCredentialsTask",
                    [(200, credential_task(task_id, "SUCCESSFUL"))],
                )
                server.script(
                    "getCredentialTaskByResourceID",
                    [
                        (
                            200,
                            [
                                {
                                    "resourceId": self.resource_id,
                                    "resourceType": "VCENTER",
                                    "credentials": [
                                        {
                                            "username": self.username,
                                            "password": "",
                                        }
                                    ],
                                }
                            ],
                        )
                    ],
                )
                credential = ManagedCredential(self.old_secret)
                with self.assertRaises(ProtocolError):
                    self._client(server).rotate(
                        credential,
                        resource_type="VCENTER",
                        username=self.username,
                        resource_id=self.resource_id,
                    )
                requests = server.read_request_log()

        self.assertEqual(len(requests), 3)
        with credential.lease() as current:
            self.assertEqual(current, self.old_secret)

    def test_local_validation_precedes_traffic(self) -> None:
        from vcf_credential_rotation import (
            ManagedCredential,
            SddcManagerCredentialRotator,
        )

        for invalid_secret in ("", None, 7):
            with self.subTest(initial_secret=invalid_secret):
                with self.assertRaises((TypeError, ValueError)):
                    ManagedCredential(invalid_secret)  # type: ignore[arg-type]

        bad_clients = [
            ("", self.token, {}),
            ("ftp://host", self.token, {}),
            ("http://user:pass@host", self.token, {}),
            ("http://host/path", self.token, {}),
            ("http://host?query=yes", self.token, {}),
            ("http://host/#fragment", self.token, {}),
            ("http://host", "", {}),
            ("http://host", self.token, {"max_polls": 0}),
            ("http://host", self.token, {"poll_interval": -1}),
            ("http://host", self.token, {"timeout": 0}),
        ]
        for base_url, token, options in bad_clients:
            with self.subTest(base_url=base_url, options=options):
                with self.assertRaises((TypeError, ValueError)):
                    SddcManagerCredentialRotator(
                        base_url,
                        token,
                        **options,
                    )

        with tempfile.TemporaryDirectory() as directory:
            with MockSddcManager(
                self.token,
                Path(directory) / "requests.jsonl",
            ) as server:
                credential = ManagedCredential(self.old_secret)
                client = self._client(server)
                invalid_calls = [
                    {"resource_id": None, "resource_name": None},
                    {
                        "resource_id": self.resource_id,
                        "resource_name": "also-set",
                    },
                    {"resource_id": "", "resource_name": None},
                    {"resource_id": self.resource_id, "credential_type": ""},
                    {"resource_id": self.resource_id, "account_type": ""},
                ]
                for overrides in invalid_calls:
                    arguments: dict[str, Any] = {
                        "resource_type": "VCENTER",
                        "username": self.username,
                        "resource_id": None,
                        "resource_name": None,
                    }
                    arguments.update(overrides)
                    with self.subTest(arguments=arguments):
                        with self.assertRaises((TypeError, ValueError)):
                            client.rotate(credential, **arguments)
                self.assertEqual(server.read_request_log(), [])
                self.assertFalse(credential.is_rotating)


if __name__ == "__main__":
    unittest.main(verbosity=2)
