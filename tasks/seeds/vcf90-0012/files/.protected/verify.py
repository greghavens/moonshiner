#!/usr/bin/env python3
"""Deterministic protected verifier for vcf90-0012.

Checks that docs/contract.json is still the 9.0.0.0 projection, that the client
package is stdlib-only, and that reconciled SSH rotation produces the exact
request wire shape and exactly one rotation effect across repeated attempts.
No live VMware endpoint is contacted.
"""

from __future__ import annotations

import ast
import importlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = "vcf_sddc_credentials"
CLIENT_PATH = SRC / PACKAGE / "client.py"
INIT_PATH = SRC / PACKAGE / "__init__.py"
PROJECT_PATH = ROOT / "pyproject.toml"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_sddc_manager.py"

COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
TAG = "9.0.0.0"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
OPERATION_IDS = [
    "createToken",
    "getCredentialsTasks",
    "updateOrRotatePasswords",
    "retryCredentialsTask",
]
ROUTES = [
    ("createToken", "POST", "/v1/tokens"),
    ("getCredentialsTasks", "GET", "/v1/credentials/tasks"),
    ("updateOrRotatePasswords", "PATCH", "/v1/credentials"),
    ("retryCredentialsTask", "PATCH", "/v1/credentials/tasks/{id}"),
]
POINTERS = {
    "createToken": "/paths/~1v1~1tokens/post/operationId",
    "getCredentialsTasks": "/paths/~1v1~1credentials~1tasks/get/operationId",
    "updateOrRotatePasswords": "/paths/~1v1~1credentials/patch/operationId",
    "retryCredentialsTask": "/paths/~1v1~1credentials~1tasks~1{id}/patch/operationId",
}
# Present in the 9.0.0.0 revision; the 9.1.0.0 revision widens this list.
RESOURCE_TYPES_9_0 = [
    "ESXI",
    "VCENTER",
    "PSC",
    "NSXT_MANAGER",
    "NSXT_EDGE",
    "NSX_ALB",
    "BACKUP",
]
UNSET_UPDATE_SPEC_MEMBERS = ["autoRotatePolicy"]
UNSET_RESOURCE_MEMBERS = ["resourceId"]
UNSET_CREDENTIAL_MEMBERS = ["password"]
UNSET_TOKEN_MEMBERS = ["apiKey", "idToken"]

SEED_STAMP = "2026-03-04T11:00:00.000Z"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# contract provenance
# --------------------------------------------------------------------------- #


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repository") == "vmware/vcf-api-specs", "repository changed")
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit is not the 9.0.0.0 tag")
    require(source.get("repositoryTag") == TAG, "contract tag changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == TAG, "contract API version is not 9.0.0.0")

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract must name exactly the four pinned operationIds",
    )
    require(
        [(i.get("operationId"), i.get("method"), i.get("path")) for i in operations] == ROUTES,
        "contract route projection changed",
    )
    by_id = {item["operationId"]: item for item in operations}
    require(
        by_id["createToken"]["requestBody"]["schema"] == "TokenCreationSpec",
        "createToken request schema changed",
    )
    require(
        by_id["getCredentialsTasks"]["requestBody"] is None,
        "getCredentialsTasks has no request body",
    )
    require(
        [p["name"] for p in by_id["getCredentialsTasks"]["parameters"]] == ["limit"]
        and by_id["getCredentialsTasks"]["parameters"][0]["required"] is False
        and by_id["getCredentialsTasks"]["parameters"][0]["schema"]
        == {"type": "integer", "format": "int32"},
        "limit parameter projection changed",
    )
    for operation_id in ("updateOrRotatePasswords", "retryCredentialsTask"):
        operation = by_id[operation_id]
        require(
            operation["requestBody"]
            == {
                "required": True,
                "contentType": "application/json",
                "schema": "CredentialsUpdateSpec",
            },
            f"{operation_id} request body projection changed",
        )
        require(
            operation["responses"]["202"]["schema"] == "Task",
            f"{operation_id} must accept with 202 Task",
        )
    require(
        [p["name"] for p in by_id["retryCredentialsTask"]["parameters"]] == ["id"]
        and by_id["retryCredentialsTask"]["parameters"][0]["required"] is True,
        "retryCredentialsTask id parameter projection changed",
    )

    schemas = contract.get("schemas", {})
    update_spec = schemas.get("CredentialsUpdateSpec", {})
    require(
        sorted(update_spec.get("required", [])) == ["elements", "operationType"],
        "CredentialsUpdateSpec required members changed",
    )
    require(
        "autoRotatePolicy" in update_spec.get("properties", {}),
        "autoRotatePolicy must stay in the projection as an optional member",
    )
    resource = schemas.get("ResourceCredentials", {})
    require(
        sorted(resource.get("required", [])) == ["credentials", "resourceType"],
        "ResourceCredentials required members changed",
    )
    require(
        resource.get("properties", {}).get("resourceType", {}).get("enumeratedInExample")
        == RESOURCE_TYPES_9_0,
        "resourceType values must match the 9.0.0.0 revision exactly",
    )
    base = schemas.get("BaseCredential", {})
    require(base.get("required") == ["username"], "BaseCredential required members changed")
    require(
        set(base.get("properties", {}))
        == {"credentialType", "accountType", "username", "password"},
        "BaseCredential properties changed",
    )
    token_spec = schemas.get("TokenCreationSpec", {})
    require(token_spec.get("required") == [], "TokenCreationSpec members are all optional")
    require(
        set(token_spec.get("properties", {}))
        == {"username", "password", "apiKey", "idToken"},
        "TokenCreationSpec properties changed",
    )
    sub_task = schemas.get("CredentialsSubTask", {}).get("properties", {})
    require(
        {"entityType", "resourceName", "username", "credentialType"} <= set(sub_task),
        "CredentialsSubTask must project the identity members",
    )
    task_status = schemas.get("CredentialsTask", {}).get("properties", {}).get("status", {})
    require(
        task_status.get("enumeratedInExample")
        == [
            "PENDING",
            "IN_PROGRESS",
            "SUCCESSFUL",
            "FAILED",
            "USER_CANCELLED",
            "INCONSISTENT",
        ],
        "CredentialsTask status values changed",
    )

    require(sources.get("repository") == "vmware/vcf-api-specs", "source repository changed")
    require(sources.get("repositoryCommitSha") == COMMIT, "source commit is not the 9.0.0.0 tag")
    require(sources.get("repositoryTag") == TAG, "source tag changed")
    require(sources.get("specPath") == SPEC_PATH, "source spec path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "") and sources["specUrl"].endswith(SPEC_PATH),
        "official spec URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("specJsonPointer"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            (operation_id, method, path, POINTERS[operation_id], COMMIT, SPEC_PATH)
            for operation_id, method, path in ROUTES
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource") is False,
        "a documentation page must not be the contract source",
    )


# --------------------------------------------------------------------------- #
# package shape
# --------------------------------------------------------------------------- #


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_package_shape() -> None:
    require(CLIENT_PATH.is_file(), f"src/{PACKAGE}/client.py is missing")
    require(INIT_PATH.is_file(), "protected package initializer is missing")
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    require(project.get("project", {}).get("dependencies") == [], "dependencies must be empty")
    require(
        project.get("tool", {}).get("moonshiner", {}).get("stdlib-only") is True,
        "package must remain stdlib-only",
    )

    source = CLIENT_PATH.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(CLIENT_PATH))
    except SyntaxError as error:
        raise VerificationError(f"client.py is not valid Python: {error.msg}") from None
    roots = imported_roots(tree)
    third_party = roots - set(sys.stdlib_module_names) - {"__future__", PACKAGE}
    require(
        not third_party,
        "client imports non-stdlib modules: " + ", ".join(sorted(third_party)),
    )
    require("subprocess" not in roots, "client must not invoke external programs")
    require(
        "notimplementederror" not in source.casefold(),
        "client workflow is still a stub",
    )

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.casefold() in {".whl", ".egg", ".zip", ".so", ".dll", ".dylib", ".pyc"}
    ]
    require(not vendored, "the package must not vendor dependencies or binary artifacts")


# --------------------------------------------------------------------------- #
# scenario harness
# --------------------------------------------------------------------------- #


def seeded_task(
    task_id: str,
    status: str,
    entity_type: str,
    resource_name: str,
    username: str,
    *,
    credential_type: str = "SSH",
    task_type: str = "ROTATE",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "name": "Rotate Passwords",
        "type": task_type,
        "status": status,
        "creationTimestamp": SEED_STAMP,
        "isAutoRotate": False,
        "subTasks": [
            {
                "id": f"{task_id}-01",
                "name": "Rotate Password",
                "description": f"Rotate {credential_type} password for {resource_name}",
                "creationTimestamp": SEED_STAMP,
                "status": status,
                "entityType": entity_type,
                "resourceName": resource_name,
                "username": username,
                "credentialType": credential_type,
            }
        ],
    }


def rotate_body(resource_type: str, resource_name: str, account_username: str) -> str:
    return json.dumps(
        {
            "operationType": "ROTATE",
            "elements": [
                {
                    "resourceType": resource_type,
                    "resourceName": resource_name,
                    "credentials": [
                        {
                            "credentialType": "SSH",
                            "accountType": "USER",
                            "username": account_username,
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def wait_for_ready(ready_path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before readiness: " + (stderr or stdout or "no diagnostics")
            )
        if ready_path.is_file():
            try:
                ready = load_json(ready_path)
                require(ready.get("host") == "127.0.0.1", "mock is not loopback-only")
                require(ready.get("operationIds") == OPERATION_IDS, "mock operation set changed")
                port = ready.get("port")
                if isinstance(port, int) and 0 < port < 65536:
                    return port
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class Case:
    """One mock lifetime: seeded tasks, a sequence of rotate calls, a request log."""

    def __init__(self, name: str, seeded_tasks: list[dict[str, Any]]) -> None:
        self.name = name
        self.seeded_tasks = seeded_tasks
        self.identity = {
            "username": f"admin-{secrets.token_hex(4)}@vsphere.local",
            "password": secrets.token_urlsafe(18),
            "accessToken": secrets.token_urlsafe(24),
            "refreshTokenId": secrets.token_hex(8),
        }
        self.resource_name = f"esx-{secrets.token_hex(4)}.lab.local"
        self.account_username = "root"
        self.resource_type = "ESXI"
        self.requests: list[dict[str, Any]] = []
        self.outcomes: list[tuple[str, Any]] = []

    @property
    def expected_body(self) -> str:
        return rotate_body(self.resource_type, self.resource_name, self.account_username)

    def run(
        self,
        module: Any,
        calls: list[dict[str, Any]],
        *,
        client_password: str | None = None,
    ) -> None:
        invocations = [
            (
                (self.resource_type, self.resource_name, self.account_username),
                call,
            )
            for call in calls
        ]
        self.run_invocations(
            module,
            invocations,
            client_password=client_password,
        )

    def run_invocations(
        self,
        module: Any,
        invocations: list[tuple[tuple[Any, ...], dict[str, Any]]],
        *,
        client_password: str | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="vcf-sddc-verify-") as temporary:
            temp = Path(temporary)
            scenario_path = temp / "scenario.json"
            request_log = temp / "requests.jsonl"
            ready_path = temp / "ready.json"
            scenario_path.write_text(
                json.dumps({**self.identity, "credentialsTasks": self.seeded_tasks}),
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(MOCK_PATH),
                    "--contract",
                    str(CONTRACT_PATH),
                    "--scenario",
                    str(scenario_path),
                    "--request-log",
                    str(request_log),
                    "--ready",
                    str(ready_path),
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                port = wait_for_ready(ready_path, process)
                try:
                    client = module.SddcManagerClient(
                        f"http://127.0.0.1:{port}",
                        self.identity["username"],
                        (
                            self.identity["password"]
                            if client_password is None
                            else client_password
                        ),
                        timeout=5.0,
                    )
                except Exception as error:  # noqa: BLE001
                    raise VerificationError(
                        f"[{self.name}] client construction failed: "
                        f"{type(error).__name__}: {error}"
                    ) from None
                for arguments, options in invocations:
                    try:
                        result = client.rotate_ssh_password(*arguments, **options)
                        self.outcomes.append(("ok", result))
                    except Exception as error:  # noqa: BLE001
                        self.outcomes.append(("error", error))
            finally:
                stop_process(process)
            lines = request_log.read_text(encoding="utf-8").splitlines()
            self.requests = [json.loads(line) for line in lines if line.strip()]

    # -- shared assertions -------------------------------------------------- #

    def fail(self, message: str) -> None:
        raise VerificationError(f"[{self.name}] {message}")

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.fail(message)

    def operation_sequence(self) -> list[str | None]:
        return [request.get("operationId") for request in self.requests]

    def of(self, operation_id: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r.get("operationId") == operation_id]

    def rotation_effect_total(self) -> int:
        return self.requests[-1]["rotationEffectTotal"] if self.requests else 0

    def check_common(self) -> None:
        self.check(
            all(request.get("operationId") is not None for request in self.requests),
            "a route outside the focused contract was called",
        )
        self.check(
            [r.get("sequence") for r in self.requests] == list(range(1, len(self.requests) + 1)),
            "request sequence is not contiguous",
        )
        self.check(
            len(self.of("createToken")) == 1,
            "the access token must be obtained once and reused",
        )

        token_request = self.of("createToken")[0]
        headers = token_request["headerValues"]
        self.check(token_request["method"] == "POST", "createToken must use POST")
        self.check(token_request["rawTarget"] == "/v1/tokens", "createToken target changed")
        self.check("authorization" not in headers, "createToken must not send Authorization")
        self.check(
            headers.get("content-type") == ["application/json"],
            "createToken Content-Type changed",
        )
        self.check(headers.get("accept") == ["application/json"], "createToken Accept changed")
        expected_token_body = json.dumps(
            {"username": self.identity["username"], "password": self.identity["password"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.check(
            token_request["body"] == expected_token_body,
            "TokenCreationSpec body bytes changed",
        )
        expected_token_length = str(len(expected_token_body.encode("utf-8")))
        self.check(
            headers.get("content-length") == [expected_token_length],
            "createToken Content-Length value or multiplicity changed",
        )
        self.check(
            "transfer-encoding" not in headers,
            "createToken must use a fixed Content-Length",
        )
        decoded_token = json.loads(token_request["body"])
        self.check(
            all(name not in decoded_token for name in UNSET_TOKEN_MEMBERS),
            "unset TokenCreationSpec members were transmitted",
        )
        self.check(token_request["responseStatus"] == 201, "createToken did not return 201")

        for request in self.requests:
            if request["operationId"] == "createToken":
                continue
            self.check(
                request["headerValues"].get("authorization")
                == [f"Bearer {self.identity['accessToken']}"],
                f"{request['operationId']} Authorization value or multiplicity changed",
            )
            self.check(
                request["headerValues"].get("accept") == ["application/json"],
                f"{request['operationId']} Accept value or multiplicity changed",
            )
            self.check(
                "transfer-encoding" not in request["headerValues"],
                f"{request['operationId']} must use a fixed Content-Length",
            )

        for request in self.of("getCredentialsTasks"):
            self.check(request["method"] == "GET", "getCredentialsTasks must use GET")
            self.check(
                request["path"] == "/v1/credentials/tasks",
                "getCredentialsTasks path changed",
            )
            self.check(request["bodyLength"] == 0, "getCredentialsTasks must send no body")
            self.check(request["responseStatus"] == 200, "getCredentialsTasks was rejected")

    def check_update_spec(self, request: dict[str, Any]) -> None:
        expected = self.expected_body
        encoded = expected.encode("utf-8")
        self.check(request["method"] == "PATCH", f"{request['operationId']} must use PATCH")
        self.check(request["rawQuery"] == "", f"{request['operationId']} defines no query")
        self.check(
            request["body"] == expected,
            f"{request['operationId']} CredentialsUpdateSpec bytes changed",
        )
        self.check(
            request["bodyLength"] == len(encoded),
            f"{request['operationId']} body length changed",
        )
        self.check(
            request["headerValues"].get("content-type") == ["application/json"],
            f"{request['operationId']} Content-Type changed",
        )
        self.check(
            request["headerValues"].get("content-length") == [str(len(encoded))],
            f"{request['operationId']} Content-Length value or multiplicity changed",
        )
        self.check(request["responseStatus"] == 202, f"{request['operationId']} was rejected")

        decoded = json.loads(request["body"])
        self.check(
            set(decoded) == {"operationType", "elements"},
            "unset CredentialsUpdateSpec members were transmitted",
        )
        self.check(
            all(name not in decoded for name in UNSET_UPDATE_SPEC_MEMBERS),
            "autoRotatePolicy must be omitted when unset",
        )
        self.check(decoded["operationType"] == "ROTATE", "operationType must be ROTATE")
        self.check(len(decoded["elements"]) == 1, "exactly one resource must be addressed")
        element = decoded["elements"][0]
        self.check(
            set(element) == {"resourceType", "resourceName", "credentials"},
            "unset ResourceCredentials members were transmitted",
        )
        self.check(
            all(name not in element for name in UNSET_RESOURCE_MEMBERS),
            "resourceId must be omitted when unset",
        )
        self.check(len(element["credentials"]) == 1, "exactly one account must be addressed")
        credential = element["credentials"][0]
        self.check(
            set(credential) == {"credentialType", "accountType", "username"},
            "unset BaseCredential members were transmitted",
        )
        self.check(
            all(name not in credential for name in UNSET_CREDENTIAL_MEMBERS),
            "password must be omitted for a ROTATE, never sent blank or null",
        )

    def check_no_mutation(self) -> None:
        self.check(
            not self.of("updateOrRotatePasswords") and not self.of("retryCredentialsTask"),
            "an adopted or unsafe task state must not issue a mutating call",
        )
        self.check(self.rotation_effect_total() == 0, "no rotation effect may be started")

    def results(self) -> list[Any]:
        for kind, value in self.outcomes:
            if kind == "error":
                self.fail(f"rotation raised {type(value).__name__}: {value}")
        return [value for _, value in self.outcomes]

    def check_result(self, index: int, action: str, task_id: str) -> None:
        result = self.results()[index]
        self.check(isinstance(result, dict), "rotate_ssh_password must return a dictionary")
        self.check(
            set(result) == {"action", "taskId", "task"},
            f"result keys changed: {sorted(result)}",
        )
        self.check(
            result["action"] == action,
            f"expected action {action!r}, got {result.get('action')!r}",
        )
        self.check(
            result["taskId"] == task_id,
            f"expected taskId {task_id!r}, got {result.get('taskId')!r}",
        )
        self.check(isinstance(result["task"], dict), "result task must be a decoded object")
        self.check(
            result["task"].get("id") == task_id,
            "result task must be the task the action refers to",
        )


# --------------------------------------------------------------------------- #
# cases
# --------------------------------------------------------------------------- #


def case_retry_is_safe(module: Any) -> None:
    """The headline case: two identical attempts, exactly one rotation effect."""
    case = Case("retry-is-safe", [])
    case.run(module, [{}, {}])
    case.check_common()
    case.check(
        case.operation_sequence()
        == [
            "createToken",
            "getCredentialsTasks",
            "updateOrRotatePasswords",
            "getCredentialsTasks",
        ],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    submit = case.of("updateOrRotatePasswords")[0]
    case.check_update_spec(submit)
    case.check(
        submit["effect"]["duplicateIntents"] == [],
        "the first rotation must not collide with a live task",
    )
    case.check(
        case.rotation_effect_total() == 1,
        "the repeated attempt duplicated the rotation effect",
    )
    task_id = submit["effect"]["taskId"]
    case.check_result(0, "submitted", task_id)
    case.check_result(1, "in-flight", task_id)
    case.check(
        case.results()[0] is not case.results()[1],
        "each call must return a freshly built result",
    )


def case_resume_failed(module: Any) -> None:
    """A failed task is resumed in place, never restarted as a new rotation."""
    task_id = f"seed-{secrets.token_hex(6)}"
    case = Case("resume-failed", [])
    case.seeded_tasks = [
        seeded_task(task_id, "FAILED", "ESXI", case.resource_name, case.account_username)
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence()
        == ["createToken", "getCredentialsTasks", "retryCredentialsTask"],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    case.check(
        not case.of("updateOrRotatePasswords"),
        "a failed task must be resumed, not restarted with updateOrRotatePasswords",
    )
    retry = case.of("retryCredentialsTask")[0]
    case.check(
        retry["rawTarget"] == f"/v1/credentials/tasks/{task_id}",
        f"retry target changed: {retry['rawTarget']}",
    )
    case.check(
        retry["pathParams"] == {"id": task_id},
        "the failed task ID must be substituted into the path",
    )
    case.check_update_spec(retry)
    case.check(
        case.rotation_effect_total() == 0,
        "resuming a failed task must not start a second rotation effect",
    )
    case.check_result(0, "resumed", task_id)


def case_already_complete(module: Any) -> None:
    """A successful task is adopted, and an unset limit omits the query entirely."""
    task_id = f"seed-{secrets.token_hex(6)}"
    case = Case("already-complete", [])
    case.seeded_tasks = [
        seeded_task(task_id, "SUCCESSFUL", "ESXI", case.resource_name, case.account_username)
    ]
    case.run(module, [{"task_lookup_limit": None}])
    case.check_common()
    case.check(
        case.operation_sequence() == ["createToken", "getCredentialsTasks"],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    lookup = case.of("getCredentialsTasks")[0]
    case.check(
        lookup["rawTarget"] == "/v1/credentials/tasks",
        f"an unset limit must omit the query string entirely, got {lookup['rawTarget']!r}",
    )
    case.check(lookup["rawQuery"] == "", "an unset limit must not be sent blank or zero")
    case.check_no_mutation()
    case.check_result(0, "already-complete", task_id)


def case_bounded_lookup(module: Any) -> None:
    """A supplied limit is sent as the single defined query parameter."""
    task_id = f"seed-{secrets.token_hex(6)}"
    case = Case("bounded-lookup", [])
    case.seeded_tasks = [
        seeded_task(task_id, "PENDING", "ESXI", case.resource_name, case.account_username)
    ]
    case.run(module, [{"task_lookup_limit": 25}])
    case.check_common()
    lookup = case.of("getCredentialsTasks")[0]
    case.check(
        lookup["rawTarget"] == "/v1/credentials/tasks?limit=25",
        f"limit must be the only query parameter, got {lookup['rawTarget']!r}",
    )
    case.check_no_mutation()
    case.check_result(0, "in-flight", task_id)


def case_inconsistent_refuses(module: Any) -> None:
    """An INCONSISTENT task is not guessed at: it must raise, not mutate."""
    task_id = f"seed-{secrets.token_hex(6)}"
    case = Case("inconsistent-refuses", [])
    case.seeded_tasks = [
        seeded_task(task_id, "INCONSISTENT", "ESXI", case.resource_name, case.account_username)
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence() == ["createToken", "getCredentialsTasks"],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    case.check_no_mutation()
    kind, value = case.outcomes[0]
    case.check(kind == "error", "an INCONSISTENT prior task must raise SddcManagerError")
    case.check(
        isinstance(value, module.SddcManagerError),
        f"expected SddcManagerError, got {type(value).__name__}",
    )
    message = str(value)
    case.check(
        case.identity["password"] not in message
        and case.identity["accessToken"] not in message,
        "errors must not disclose the password or access token",
    )


def case_matching_is_precise(module: Any) -> None:
    """Near-miss tasks are not adoptable: only the exact account matches."""
    case = Case("matching-is-precise", [])
    case.seeded_tasks = [
        seeded_task(
            f"seed-{secrets.token_hex(6)}",
            "IN_PROGRESS",
            "ESXI",
            f"other-{secrets.token_hex(3)}.lab.local",
            case.account_username,
        ),
        seeded_task(
            f"seed-{secrets.token_hex(6)}",
            "IN_PROGRESS",
            "ESXI",
            case.resource_name,
            "vcf-service-account",
        ),
        seeded_task(
            f"seed-{secrets.token_hex(6)}",
            "IN_PROGRESS",
            "VCENTER",
            case.resource_name,
            case.account_username,
        ),
        seeded_task(
            f"seed-{secrets.token_hex(6)}",
            "IN_PROGRESS",
            "ESXI",
            case.resource_name,
            case.account_username,
            credential_type="API",
        ),
        seeded_task(
            f"seed-{secrets.token_hex(6)}",
            "IN_PROGRESS",
            "ESXI",
            case.resource_name,
            case.account_username,
            task_type="UPDATE",
        ),
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence()
        == ["createToken", "getCredentialsTasks", "updateOrRotatePasswords"],
        f"a near-miss task must not be adopted: {case.operation_sequence()}",
    )
    submit = case.of("updateOrRotatePasswords")[0]
    case.check_update_spec(submit)
    case.check(
        submit["effect"]["duplicateIntents"] == [],
        "no near-miss task should have counted as this rotation",
    )
    case.check(case.rotation_effect_total() == 1, "exactly one rotation must be started")
    case.check_result(0, "submitted", submit["effect"]["taskId"])


def case_cancelled_restarts(module: Any) -> None:
    """A cancelled task left no live effect, so a fresh rotation is correct."""
    task_id = f"seed-{secrets.token_hex(6)}"
    case = Case("cancelled-restarts", [])
    case.seeded_tasks = [
        seeded_task(task_id, "USER_CANCELLED", "ESXI", case.resource_name, case.account_username)
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence()
        == ["createToken", "getCredentialsTasks", "updateOrRotatePasswords"],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    submit = case.of("updateOrRotatePasswords")[0]
    case.check_update_spec(submit)
    case.check(
        not case.of("retryCredentialsTask"),
        "a cancelled task is not a failed task and must not be retried in place",
    )
    case.check(case.rotation_effect_total() == 1, "exactly one rotation must be started")
    case.check_result(0, "submitted", submit["effect"]["taskId"])


def case_9_0_resource_value_and_utf8_payload(module: Any) -> None:
    """A non-ESXI 9.0 enum value and Unicode runtime strings stay exact on wire."""
    case = Case("9-0-resource-value-and-utf8-payload", [])
    case.resource_type = "BACKUP"
    case.resource_name = f"sauvegarde-雪-{secrets.token_hex(3)}"
    case.account_username = "opérateur"
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence()
        == ["createToken", "getCredentialsTasks", "updateOrRotatePasswords"],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    submit = case.of("updateOrRotatePasswords")[0]
    case.check_update_spec(submit)
    case.check(case.rotation_effect_total() == 1, "exactly one rotation must be started")
    case.check_result(0, "submitted", submit["effect"]["taskId"])


def case_first_matching_task_wins(module: Any) -> None:
    """Returned ordering is authoritative when more than one task matches."""
    first_id = f"first-{secrets.token_hex(6)}"
    second_id = f"second-{secrets.token_hex(6)}"
    case = Case("first-matching-task-wins", [])
    case.seeded_tasks = [
        seeded_task(
            first_id,
            "SUCCESSFUL",
            "ESXI",
            case.resource_name,
            case.account_username,
        ),
        seeded_task(
            second_id,
            "FAILED",
            "ESXI",
            case.resource_name,
            case.account_username,
        ),
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence() == ["createToken", "getCredentialsTasks"],
        f"the first matching task was not selected: {case.operation_sequence()}",
    )
    case.check_no_mutation()
    case.check_result(0, "already-complete", first_id)


def case_retry_id_is_percent_encoded(module: Any) -> None:
    """A failed task ID is encoded as one path segment and decoded by the service."""
    task_id = f"failed/task ?#% é-{secrets.token_hex(3)}"
    case = Case("retry-id-is-percent-encoded", [])
    case.seeded_tasks = [
        seeded_task(task_id, "FAILED", "ESXI", case.resource_name, case.account_username)
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence()
        == ["createToken", "getCredentialsTasks", "retryCredentialsTask"],
        f"unexpected call sequence: {case.operation_sequence()}",
    )
    retry = case.of("retryCredentialsTask")[0]
    encoded_id = quote(task_id, safe="")
    case.check(
        retry["rawTarget"] == f"/v1/credentials/tasks/{encoded_id}",
        f"retry task ID was not encoded as one path segment: {retry['rawTarget']!r}",
    )
    case.check(
        retry["pathParams"] == {"id": task_id},
        "the service did not recover the original task ID from the encoded path",
    )
    case.check_update_spec(retry)
    case.check(case.rotation_effect_total() == 0, "retry must not start a new effect")
    case.check_result(0, "resumed", task_id)


def case_malformed_task_refuses(module: Any) -> None:
    """Malformed reconciliation data must not be interpreted as no prior task."""
    case = Case("malformed-task-refuses", [])
    case.seeded_tasks = [
        {
            "id": f"malformed-{secrets.token_hex(6)}",
            "name": "Rotate Passwords",
            "type": "ROTATE",
            "status": "IN_PROGRESS",
            "creationTimestamp": SEED_STAMP,
            "subTasks": "not-an-array",
        }
    ]
    case.run(module, [{}])
    case.check_common()
    case.check(
        case.operation_sequence() == ["createToken", "getCredentialsTasks"],
        f"malformed task data triggered another rotation: {case.operation_sequence()}",
    )
    case.check_no_mutation()
    kind, value = case.outcomes[0]
    case.check(kind == "error", "malformed task data must raise SddcManagerError")
    case.check(
        isinstance(value, module.SddcManagerError),
        f"expected SddcManagerError, got {type(value).__name__}",
    )


def case_unknown_status_redacts_secrets(module: Any) -> None:
    """Unresolved server-controlled values cannot disclose stored credentials."""
    case = Case("unknown-status-redacts-secrets", [])
    task_id = f"seed-{secrets.token_hex(6)}"
    case.seeded_tasks = [
        seeded_task(
            task_id,
            case.identity["accessToken"],
            "ESXI",
            case.resource_name,
            case.account_username,
        )
    ]
    case.run(module, [{}])
    case.check_common()
    case.check_no_mutation()
    kind, value = case.outcomes[0]
    case.check(kind == "error", "an unknown prior status must raise SddcManagerError")
    case.check(
        isinstance(value, module.SddcManagerError),
        f"expected SddcManagerError, got {type(value).__name__}",
    )
    message = str(value)
    case.check(
        case.identity["password"] not in message
        and case.identity["accessToken"] not in message,
        "errors must not disclose the password or access token",
    )


def case_http_error_redacts_password(module: Any) -> None:
    """Arbitrary error text from the service must not be echoed with a password."""
    case = Case("http-error-redacts-password", [])
    rejected_password = secrets.token_urlsafe(19)
    case.run(module, [{}], client_password=rejected_password)
    case.check(
        case.operation_sequence() == ["createToken"],
        f"authentication failure made unexpected calls: {case.operation_sequence()}",
    )
    token_request = case.of("createToken")[0]
    case.check(token_request["responseStatus"] == 400, "invalid credentials were accepted")
    kind, value = case.outcomes[0]
    case.check(kind == "error", "an HTTP failure must raise SddcManagerError")
    case.check(
        isinstance(value, module.SddcManagerError),
        f"expected SddcManagerError, got {type(value).__name__}",
    )
    message = str(value)
    case.check(
        rejected_password not in message
        and case.identity["accessToken"] not in message,
        "an HTTP error disclosed a password or access token",
    )


def case_invalid_inputs_do_not_call_network(module: Any) -> None:
    """All documented call inputs are rejected before token acquisition."""
    case = Case("invalid-inputs-do-not-call-network", [])
    invocations = [
        (("HCX_MANAGER", case.resource_name, case.account_username), {}),
        (("VSP", case.resource_name, case.account_username), {}),
        (("ESXI", "   ", case.account_username), {}),
        (("ESXI", case.resource_name, "\t"), {}),
        (("ESXI", case.resource_name, case.account_username), {"task_lookup_limit": 0}),
        (("ESXI", case.resource_name, case.account_username), {"task_lookup_limit": 1001}),
        (("ESXI", case.resource_name, case.account_username), {"task_lookup_limit": True}),
        (("ESXI", case.resource_name, case.account_username), {"task_lookup_limit": 1.5}),
        (("ESXI", case.resource_name, case.account_username), {"task_lookup_limit": "10"}),
    ]
    case.run_invocations(module, invocations)
    case.check(case.requests == [], "invalid call input touched the network")
    case.check(len(case.outcomes) == len(invocations), "not every invalid input was exercised")
    for kind, value in case.outcomes:
        case.check(kind == "error", "invalid call input was accepted")
        case.check(
            isinstance(value, module.SddcManagerError),
            f"expected SddcManagerError, got {type(value).__name__}",
        )


def case_invalid_constructor_inputs(module: Any) -> None:
    """Constructor validation follows the documented URL, text, and timeout bounds."""
    secret = secrets.token_urlsafe(19)
    attempts = [
        (("relative/path", "admin", secret), {}),
        (("ftp://manager.example", "admin", secret), {}),
        (("https://manager.example?mode=test", "admin", secret), {}),
        (("https://manager.example#section", "admin", secret), {}),
        (("https://manager.example", "  ", secret), {}),
        (("https://manager.example", "admin", "\n"), {}),
        (("https://manager.example", "admin", secret), {"timeout": True}),
        (("https://manager.example", "admin", secret), {"timeout": 0}),
        (("https://manager.example", "admin", secret), {"timeout": float("inf")}),
        (("https://manager.example", "admin", secret), {"timeout": float("nan")}),
        (("https://manager.example", "admin", secret), {"timeout": "10"}),
    ]
    for arguments, options in attempts:
        try:
            module.SddcManagerClient(*arguments, **options)
        except Exception as error:  # noqa: BLE001
            require(
                isinstance(error, module.SddcManagerError),
                f"constructor raised {type(error).__name__}, expected SddcManagerError",
            )
            require(secret not in str(error), "constructor error disclosed the password")
        else:
            raise VerificationError("constructor accepted an invalid documented input")


CASES = (
    case_retry_is_safe,
    case_resume_failed,
    case_already_complete,
    case_bounded_lookup,
    case_inconsistent_refuses,
    case_matching_is_precise,
    case_cancelled_restarts,
    case_9_0_resource_value_and_utf8_payload,
    case_first_matching_task_wins,
    case_retry_id_is_percent_encoded,
    case_malformed_task_refuses,
    case_unknown_status_redacts_secrets,
    case_http_error_redacts_password,
    case_invalid_inputs_do_not_call_network,
    case_invalid_constructor_inputs,
)


def main() -> int:
    try:
        verify_contract()
        verify_package_shape()
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        try:
            module = importlib.import_module(PACKAGE)
        except Exception as error:  # noqa: BLE001
            raise VerificationError(
                f"package import failed: {type(error).__name__}: {error}"
            ) from None
        require(
            hasattr(module, "SddcManagerClient") and hasattr(module, "SddcManagerError"),
            "the package must export SddcManagerClient and SddcManagerError",
        )
        require(
            issubclass(module.SddcManagerError, Exception),
            "SddcManagerError must be an exception type",
        )
        for case in CASES:
            case(module)
    except VerificationError as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print(
        "VERIFICATION PASSED: reconciled ROTATE keeps one effect across repeated attempts"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
