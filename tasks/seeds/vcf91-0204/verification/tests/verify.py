#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0204."""

from __future__ import annotations

import ast
import importlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLIENT_PATH = SRC / "vcf_installer" / "client.py"
INIT_PATH = SRC / "vcf_installer" / "__init__.py"
PROJECT_PATH = ROOT / "pyproject.toml"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
OPERATION_IDS = ["getTasks", "refreshAccessToken"]
OPTIONAL_TASK_QUERY = [
    "limit",
    "taskStatus",
    "taskType",
    "resourceId",
    "resourceType",
    "completedAfter",
    "pageNumber",
    "pageSize",
    "orderDirection",
    "orderBy",
    "taskName",
    "doLiveRefresh",
]
ROW_KEYS = ["id", "name", "type", "status", "creationTimestamp"]


class VerificationError(AssertionError):
    pass


class ScriptedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, plans: list[dict[str, Any]]) -> None:
        super().__init__(("127.0.0.1", 0), ScriptedHandler)
        self.plans = plans
        self.requests: list[dict[str, Any]] = []


class ScriptedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ScriptedServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(max(length, 0))
        self.server.requests.append(
            {
                "method": self.command,
                "target": self.path,
                "headers": {
                    name.casefold(): self.headers.get_all(name) or []
                    for name in self.headers.keys()
                },
                "body": body,
            }
        )
        index = len(self.server.requests) - 1
        plan = (
            self.server.plans[index]
            if index < len(self.server.plans)
            else {"status": 500, "json": {"error": "unexpected request"}}
        )
        if plan.get("disconnect"):
            self.close_connection = True
            return

        status = int(plan.get("status", 200))
        if "raw" in plan:
            payload = plan["raw"]
            if not isinstance(payload, bytes):
                raise TypeError("scripted raw response must be bytes")
        else:
            payload = json.dumps(
                plan.get("json"), separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", str(plan.get("contentType", "application/json")))
        if "location" in plan:
            self.send_header("Location", str(plan["location"]))
        declared = int(plan.get("declaredLength", len(payload)))
        self.send_header("Content-Length", str(declared))
        self.send_header("Connection", "close")
        self.end_headers()
        if payload:
            self.wfile.write(payload)
            self.wfile.flush()
        self.close_connection = True


@contextmanager
def scripted_server(plans: list[dict[str, Any]]) -> Iterator[ScriptedServer]:
    server = ScriptedServer(plans)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    proxy_names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    saved_proxies = {
        name: os.environ.pop(name) for name in proxy_names if name in os.environ
    }
    try:
        yield server
    finally:
        os.environ.update(saved_proxies)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        require(not thread.is_alive(), "scripted loopback server did not stop")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repository") == "vmware/vcf-api-specs", "repository changed")
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "VCF API version changed")

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract operationIds changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations]
        == [
            ("GET", "/v1/tasks"),
            ("PATCH", "/v1/tokens/access-token/refresh"),
        ],
        "contract routes changed",
    )
    get_tasks, refresh = operations
    require(get_tasks.get("requestBody") is False, "getTasks must be bodyless")
    parameters = get_tasks.get("parameters", [])
    require(
        [item.get("name") for item in parameters] == OPTIONAL_TASK_QUERY,
        "getTasks parameter projection changed",
    )
    require(
        all(item.get("in") == "query" and item.get("required") is False for item in parameters),
        "every getTasks parameter must remain an optional query member",
    )
    require(
        parameters[-1].get("schema") == {"type": "boolean", "default": False},
        "doLiveRefresh default projection changed",
    )
    wire = get_tasks.get("focusedWireProfile", {})
    require(wire.get("firstPageMembers") == ["pageSize"], "first page profile changed")
    require(
        wire.get("laterPageMembers") == ["pageNumber", "pageSize"],
        "later page profile changed",
    )
    require(
        wire.get("unsetMembers")
        == [name for name in OPTIONAL_TASK_QUERY if name not in {"pageNumber", "pageSize"}],
        "unset-member profile changed",
    )
    require(wire.get("unsetBehavior") == "omit", "unset behavior must be omit")
    require(
        get_tasks.get("responses", {}).get("200", {}).get("schema") == "PageOfTask",
        "getTasks response projection changed",
    )

    require(refresh.get("parameters") == [], "refresh must have no parameters")
    require(
        refresh.get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": {
                "type": "string",
                "description": "ID of the refresh token",
            },
        },
        "refresh JSON-string request projection changed",
    )
    require(
        refresh.get("responses", {}).get("200", {}).get("schema")
        == {"type": "string"},
        "refresh response projection changed",
    )

    schemas = contract.get("schemas", {})
    require(
        schemas.get("Task", {}).get("required")
        == ["creationTimestamp", "id", "name", "status"],
        "Task required fields changed",
    )
    require(
        list(schemas.get("Task", {}).get("properties", {}))
        == [
            "id",
            "name",
            "localizableDescriptionPack",
            "type",
            "status",
            "creationTimestamp",
            "completionTimestamp",
            "subTasks",
            "errors",
            "resources",
            "resolutionStatus",
            "isCancellable",
            "isRetryable",
        ],
        "Task property projection changed",
    )
    require(
        list(schemas.get("PageMetadata", {}).get("properties", {}))
        == ["pageNumber", "pageSize", "totalElements", "totalPages"],
        "PageMetadata projection changed",
    )

    require(sources.get("repository") == "vmware/vcf-api-specs", "source repository changed")
    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
        "official source URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("specLine"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            ("getTasks", "GET", "/v1/tasks", 2041, COMMIT, SPEC_PATH),
            (
                "refreshAccessToken",
                "PATCH",
                "/v1/tokens/access-token/refresh",
                1048,
                COMMIT,
                SPEC_PATH,
            ),
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "a documentation page must not be the contract source",
    )


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_package_shape() -> None:
    require(CLIENT_PATH.is_file(), "src/vcf_installer/client.py is missing")
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
    third_party = imported_roots(tree) - set(sys.stdlib_module_names) - {"__future__"}
    require(not third_party, "client imports non-stdlib modules: " + ", ".join(sorted(third_party)))

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold()
        in {".whl", ".egg", ".zip", ".so", ".dll", ".dylib", ".pyc"}
    ]
    require(not vendored, "the package must not vendor dependencies or binary artifacts")


def make_scenario() -> tuple[dict[str, Any], list[dict[str, object]]]:
    marker = secrets.token_hex(6)
    timestamps = [
        "2026-07-14T15:00:04Z",
        "2026-07-14T15:00:01Z",
        "2026-07-14T15:00:03Z",
        "2026-07-14T15:00:01Z",
        "2026-07-14T15:00:02Z",
    ]
    tasks: list[dict[str, object]] = []
    for index, timestamp in enumerate(timestamps):
        task: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "name": f"installer-work-{marker}-{index}",
            "status": "SUCCESSFUL" if index % 2 == 0 else "IN_PROGRESS",
            "creationTimestamp": timestamp,
        }
        if index != 2:
            task["type"] = "VCF_INSTALLER_WORKFLOW"
        tasks.append(task)
    scenario = {
        "oldToken": "old-" + secrets.token_urlsafe(28),
        "newToken": "new-" + secrets.token_urlsafe(28),
        "refreshTokenId": 'refresh-quote"-slash\\-' + secrets.token_urlsafe(24),
        "pageSize": 2,
        "tasks": tasks,
    }
    expected = [
        {
            "id": item["id"],
            "name": item["name"],
            "type": item.get("type"),
            "status": item["status"],
            "creationTimestamp": item["creationTimestamp"],
        }
        for item in sorted(
            tasks,
            key=lambda item: (str(item["creationTimestamp"]), str(item["id"])),
        )
    ]
    return scenario, expected


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise VerificationError(
                "loopback mock exited during startup: " + (stderr or stdout).strip()
            )
        if port_file.is_file():
            value = port_file.read_text(encoding="utf-8").strip()
            if value:
                port = int(value)
                require(0 < port < 65536, "mock published an invalid port")
                return port
        time.sleep(0.04)
    raise VerificationError("loopback mock did not publish its port")


def read_log(path: Path) -> list[dict[str, Any]]:
    require(path.is_file(), "loopback mock did not create its request log")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_header(request: dict[str, Any], name: str) -> str:
    values = request.get("headerValues", {}).get(name.casefold(), [])
    require(len(values) == 1, f"{name} must occur exactly once")
    return values[0]


def assert_client_error(
    error_type: type[BaseException], action: Callable[[], object], label: str
) -> BaseException:
    try:
        action()
    except error_type as error:
        return error
    except Exception as error:
        raise VerificationError(
            f"{label} raised {type(error).__name__}, not {error_type.__name__}"
        ) from None
    raise VerificationError(f"{label} did not raise {error_type.__name__}")


def import_client() -> tuple[type[Any], type[BaseException]]:
    sys.path.insert(0, str(SRC))
    sys.dont_write_bytecode = True
    try:
        package = importlib.import_module("vcf_installer")
    finally:
        sys.path.pop(0)
    require(
        getattr(package, "__all__", None)
        == ["VcfInstallerClient", "VcfInstallerError"],
        "package exports changed",
    )
    client_type = getattr(package, "VcfInstallerClient", None)
    error_type = getattr(package, "VcfInstallerError", None)
    require(isinstance(client_type, type), "VcfInstallerClient is not a class")
    require(
        isinstance(error_type, type) and issubclass(error_type, RuntimeError),
        "VcfInstallerError must derive from RuntimeError",
    )
    return client_type, error_type


def verify_runtime() -> None:
    client_type, error_type = import_client()
    for label, arguments in [
        ("blank base_url", ("", "access", "refresh")),
        ("non-HTTP base_url", ("ftp://127.0.0.1", "access", "refresh")),
        ("credentialed base_url", ("http://user:pass@127.0.0.1", "access", "refresh")),
        ("queried base_url", ("http://127.0.0.1?x=1", "access", "refresh")),
        ("fragmented base_url", ("http://127.0.0.1#x", "access", "refresh")),
        ("malformed base_url", ("http://[broken", "access", "refresh")),
        ("invalid base_url port", ("http://127.0.0.1:not-a-port", "access", "refresh")),
        ("whitespace base_url", ("http://bad host", "access", "refresh")),
        ("blank access_token", ("http://127.0.0.1:1", " ", "refresh")),
        ("blank refresh_token_id", ("http://127.0.0.1:1", "access", "")),
    ]:
        assert_client_error(error_type, lambda args=arguments: client_type(*args), label)
    for invalid_timeout in (
        False,
        0,
        -1,
        float("nan"),
        float("inf"),
        "1",
    ):
        assert_client_error(
            error_type,
            lambda value=invalid_timeout: client_type(
                "http://127.0.0.1:1", "access", "refresh", timeout=value
            ),
            f"invalid timeout {invalid_timeout!r}",
        )

    scenario, expected_rows = make_scenario()
    with tempfile.TemporaryDirectory(prefix="vcf91-0204-") as temporary:
        temp = Path(temporary)
        port_file = temp / "port.txt"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        scenario_file.write_text(
            json.dumps(scenario, separators=(",", ":")), encoding="utf-8"
        )
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(request_log),
                str(CONTRACT_PATH),
                str(scenario_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(port_file, server)
            proxy_names = (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            )
            saved_proxies = {
                name: os.environ.pop(name) for name in proxy_names if name in os.environ
            }
            try:
                client = client_type(
                    f"http://127.0.0.1:{port}",
                    scenario["oldToken"],
                    scenario["refreshTokenId"],
                    timeout=5.0,
                )
                for invalid_size in (False, 0, -1, 1.5, 2_147_483_648):
                    assert_client_error(
                        error_type,
                        lambda size=invalid_size: client.list_tasks(page_size=size),
                        f"invalid page_size {invalid_size!r}",
                    )
                rows = client.list_tasks(page_size=scenario["pageSize"])
            finally:
                os.environ.update(saved_proxies)
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        requests = read_log(request_log)

    require(isinstance(rows, list), "list_tasks must return a list")
    require(len(rows) == 5, "list_tasks did not return all five tasks")
    for row in rows:
        require(isinstance(row, dict), "every result row must be a dictionary")
        require(set(row) == set(ROW_KEYS), "result row keys changed")
    require(rows == expected_rows, "tasks were lost, duplicated, mutated, or mis-sorted")
    serialized_rows = json.dumps(rows, separators=(",", ":"))
    for secret in (
        scenario["oldToken"],
        scenario["newToken"],
        scenario["refreshTokenId"],
    ):
        require(secret not in serialized_rows, "task results expose a token")

    require(len(requests) == 5, "wire request count must be exactly five")
    require(
        [request.get("sequence") for request in requests] == [1, 2, 3, 4, 5],
        "request log sequence changed",
    )
    require(
        [request.get("operationId") for request in requests]
        == [
            "getTasks",
            "getTasks",
            "refreshAccessToken",
            "getTasks",
            "getTasks",
        ],
        "operation sequence changed or an unlisted route was contacted",
    )
    require(
        [request.get("responseStatus") for request in requests]
        == [200, 401, 200, 200, 200],
        "mid-run expiry and resume status sequence changed",
    )
    expected_targets = [
        "/v1/tasks?pageSize=2",
        "/v1/tasks?pageNumber=1&pageSize=2",
        "/v1/tokens/access-token/refresh",
        "/v1/tasks?pageNumber=1&pageSize=2",
        "/v1/tasks?pageNumber=2&pageSize=2",
    ]
    require(
        [request.get("rawTarget") for request in requests] == expected_targets,
        "exact raw request targets changed",
    )
    require(
        requests[1].get("rawTarget") == requests[3].get("rawTarget"),
        "the interrupted page was not retried identically",
    )
    require(
        sum(request.get("rawTarget") == expected_targets[0] for request in requests) == 1,
        "completed page zero was replayed",
    )

    get_requests = [
        request for request in requests if request.get("operationId") == "getTasks"
    ]
    require(len(get_requests) == 4, "getTasks request count changed")
    for index, request in enumerate(get_requests):
        require(request.get("method") == "GET", f"getTasks {index} method changed")
        require(request.get("bodyLength") == 0, f"getTasks {index} must be bodyless")
        require(request.get("body") == "", f"getTasks {index} body must be empty")
        headers = request.get("headerValues", {})
        require("content-type" not in headers, f"getTasks {index} sent Content-Type")
        require(one_header(request, "accept") == "application/json", "Accept changed")
        require(
            one_header(request, "host") == f"127.0.0.1:{port}",
            "request escaped the loopback mock",
        )
        expected_token = scenario["oldToken"] if index < 2 else scenario["newToken"]
        require(
            one_header(request, "authorization") == f"Bearer {expected_token}",
            f"getTasks {index} bearer token changed",
        )
        query = request.get("query", {})
        allowed = {"pageSize"} if index == 0 else {"pageNumber", "pageSize"}
        require(set(query) == allowed, f"getTasks {index} sent an unset query member")
        require(
            all(len(values) == 1 and values[0] != "" for values in query.values()),
            f"getTasks {index} sent an empty or repeated query value",
        )
        for omitted in [
            name for name in OPTIONAL_TASK_QUERY if name not in {"pageNumber", "pageSize"}
        ]:
            require(omitted not in query, f"getTasks sent unset optional field {omitted}")

    refresh = requests[2]
    require(refresh.get("method") == "PATCH", "refresh method changed")
    require(refresh.get("rawQuery") == "", "refresh must omit its query delimiter")
    require(refresh.get("query") == {}, "refresh query must be absent")
    require(
        one_header(refresh, "authorization") == f"Bearer {scenario['oldToken']}",
        "refresh must use the expired bearer token",
    )
    require(one_header(refresh, "accept") == "application/json", "refresh Accept changed")
    content_type = one_header(refresh, "content-type")
    require(
        content_type.split(";", 1)[0].strip().casefold() == "application/json",
        "refresh Content-Type must be application/json",
    )
    expected_body = json.dumps(scenario["refreshTokenId"], ensure_ascii=False)
    require(refresh.get("body") == expected_body, "refresh JSON-string body bytes changed")
    expected_length = len(expected_body.encode("utf-8"))
    require(refresh.get("bodyLength") == expected_length, "refresh body length changed")
    require(
        one_header(refresh, "content-length") == str(expected_length),
        "refresh Content-Length changed",
    )


def sample_task(task_id: str = "task-a") -> dict[str, object]:
    return {
        "id": task_id,
        "name": "installer-work",
        "type": "VCF_INSTALLER_WORKFLOW",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-07-14T15:00:00Z",
    }


def task_page(
    elements: list[object],
    *,
    page_number: object = 0,
    page_size: object = 2,
    total_elements: object | None = None,
    total_pages: object | None = None,
) -> dict[str, object]:
    if total_elements is None:
        total_elements = len(elements)
    if total_pages is None:
        if isinstance(total_elements, int) and not isinstance(total_elements, bool):
            total_pages = (
                0
                if total_elements == 0
                else (total_elements + int(page_size) - 1) // int(page_size)
            )
        else:
            total_pages = 1
    return {
        "elements": elements,
        "pageMetadata": {
            "pageNumber": page_number,
            "pageSize": page_size,
            "totalElements": total_elements,
            "totalPages": total_pages,
        },
    }


def verify_failure_handling() -> None:
    client_type, error_type = import_client()
    old_token = "old-token-that-must-stay-secret"
    new_token = "new-token-that-must-stay-secret"
    refresh_id = "refresh-id-that-must-stay-secret"

    def expect_error(
        label: str,
        plans: list[dict[str, Any]],
        *,
        expected_requests: int = 1,
        page_size: int = 2,
    ) -> list[dict[str, Any]]:
        with scripted_server(plans) as server:
            port = int(server.server_address[1])
            client = client_type(
                f"http://127.0.0.1:{port}",
                old_token,
                refresh_id,
                timeout=2.0,
            )
            error = assert_client_error(
                error_type,
                lambda: client.list_tasks(page_size=page_size),
                label,
            )
            requests = list(server.requests)
        require(
            len(requests) == expected_requests,
            f"{label} made {len(requests)} requests, expected {expected_requests}",
        )
        rendered = str(error) + repr(error)
        for secret in (old_token, new_token, refresh_id):
            require(secret not in rendered, f"{label} exposed a token in its exception")
        return requests

    basic_bad_pages: list[tuple[str, object]] = [
        ("non-object page", []),
        ("null elements", {"elements": None, "pageMetadata": {}}),
        ("non-list elements", {"elements": {}, "pageMetadata": {}}),
        ("null pageMetadata", {"elements": [], "pageMetadata": None}),
        ("non-object pageMetadata", {"elements": [], "pageMetadata": []}),
        (
            "missing pagination member",
            {
                "elements": [],
                "pageMetadata": {
                    "pageNumber": 0,
                    "pageSize": 2,
                    "totalElements": 0,
                },
            },
        ),
        ("boolean page number", task_page([], page_number=False)),
        ("boolean metadata page size", task_page([], page_size=False, total_pages=0)),
        ("boolean total elements", task_page([], total_elements=False, total_pages=0)),
        ("boolean total pages", task_page([], total_elements=0, total_pages=False)),
        ("negative page number", task_page([], page_number=-1)),
        ("negative total elements", task_page([], total_elements=-1, total_pages=0)),
        ("negative total pages", task_page([], total_elements=0, total_pages=-1)),
        ("nonpositive metadata page size", task_page([], page_size=0, total_pages=0)),
        ("changed metadata page size", task_page([], page_size=3, total_pages=0)),
        (
            "incoherent pagination totals",
            task_page([sample_task()], total_elements=3, total_pages=1),
        ),
        (
            "unrequested page",
            task_page([sample_task()], page_number=1, total_elements=1, total_pages=1),
        ),
        (
            "overfull page",
            task_page(
                [sample_task("a"), sample_task("b"), sample_task("c")],
                total_elements=3,
                total_pages=2,
            ),
        ),
        (
            "empty nonterminal page",
            task_page([], total_elements=1, total_pages=1),
        ),
        (
            "more elements than declared",
            task_page(
                [sample_task("a"), sample_task("b")],
                total_elements=1,
                total_pages=1,
            ),
        ),
    ]
    for label, response in basic_bad_pages:
        expect_error(label, [{"json": response}])

    first_underfull = task_page(
        [sample_task("a")], total_elements=3, total_pages=2
    )
    expect_error(
        "changed pagination totals",
        [
            {"json": first_underfull},
            {
                "json": task_page(
                    [sample_task("b")],
                    page_number=1,
                    total_elements=4,
                    total_pages=2,
                )
            },
        ],
        expected_requests=2,
    )
    expect_error(
        "repeated page",
        [
            {"json": first_underfull},
            {
                "json": task_page(
                    [sample_task("b")],
                    page_number=0,
                    total_elements=3,
                    total_pages=2,
                )
            },
        ],
        expected_requests=2,
    )
    expect_error(
        "non-progressing final page",
        [
            {"json": first_underfull},
            {
                "json": task_page(
                    [sample_task("b")],
                    page_number=1,
                    total_elements=3,
                    total_pages=2,
                )
            },
        ],
        expected_requests=2,
    )

    malformed_tasks: list[tuple[str, object]] = [("non-object task", None)]
    for field, value in [
        ("id", None),
        ("name", " "),
        ("status", False),
        ("creationTimestamp", ""),
    ]:
        task = sample_task()
        if value is None:
            task.pop(field)
        else:
            task[field] = value
        malformed_tasks.append((f"invalid task {field}", task))
    null_type = sample_task()
    null_type["type"] = None
    malformed_tasks.append(("null task type", null_type))
    numeric_type = sample_task()
    numeric_type["type"] = 7
    malformed_tasks.append(("non-string task type", numeric_type))
    for label, task in malformed_tasks:
        expect_error(label, [{"json": task_page([task])}])
    expect_error(
        "duplicate task ID",
        [
            {
                "json": task_page(
                    [sample_task("duplicate"), sample_task("duplicate")]
                )
            }
        ],
    )

    with scripted_server(
        [
            {
                "json": task_page(
                    [sample_task("CaseSensitive"), sample_task("casesensitive")]
                )
            }
        ]
    ) as server:
        port = int(server.server_address[1])
        rows = client_type(
            f"http://127.0.0.1:{port}", old_token, refresh_id, timeout=2.0
        ).list_tasks(page_size=2)
    require(
        {row["id"] for row in rows} == {"CaseSensitive", "casesensitive"},
        "task IDs must be case-sensitive unique",
    )

    with scripted_server(
        [{"json": task_page([], page_size=101, total_elements=0, total_pages=0)}]
    ) as server:
        port = int(server.server_address[1])
        rows = client_type(
            f"http://127.0.0.1:{port}", old_token, refresh_id, timeout=2.0
        ).list_tasks(page_size=101)
        requests = list(server.requests)
    require(rows == [], "a valid int32 page_size above 100 must be supported")
    require(
        [request["target"] for request in requests] == ["/v1/tasks?pageSize=101"],
        "page_size 101 was not sent exactly",
    )

    expect_error("getTasks HTTP 500", [{"status": 500, "json": {}}])
    expect_error("invalid JSON", [{"raw": b"{"}])
    expect_error("invalid UTF-8 JSON", [{"raw": b"\xff"}])
    expect_error(
        "truncated transport response",
        [{"raw": b"{", "declaredLength": 20}],
    )
    expect_error("disconnected transport", [{"disconnect": True}])
    redirect_requests = expect_error(
        "redirect to an unlisted route",
        [
            {"status": 302, "location": "/outside", "json": {}},
            {"json": task_page([])},
        ],
    )
    require(
        [request["target"] for request in redirect_requests]
        == ["/v1/tasks?pageSize=2"],
        "client followed a redirect to an unlisted route",
    )

    page_zero = task_page(
        [sample_task("a"), sample_task("b")],
        total_elements=3,
        total_pages=2,
    )
    no_refresh_requests = expect_error(
        "non-401 page failure",
        [{"json": page_zero}, {"status": 403, "json": {}}],
        expected_requests=2,
    )
    require(
        [request["method"] for request in no_refresh_requests] == ["GET", "GET"],
        "client refreshed for a non-401 getTasks failure",
    )
    refresh_failure = expect_error(
        "refresh HTTP failure",
        [
            {"json": page_zero},
            {"status": 401, "json": {}},
            {"status": 401, "json": {}},
        ],
        expected_requests=3,
    )
    require(
        [request["method"] for request in refresh_failure] == ["GET", "GET", "PATCH"],
        "refreshAccessToken failure was retried or redirected",
    )
    for label, replacement in [
        ("blank refresh response", " "),
        ("non-string refresh response", {"accessToken": new_token}),
    ]:
        expect_error(
            label,
            [
                {"json": page_zero},
                {"status": 401, "json": {}},
                {"json": replacement},
            ],
            expected_requests=3,
        )
    expect_error(
        "malformed refresh JSON",
        [
            {"json": page_zero},
            {"status": 401, "json": {}},
            {"raw": b'"unterminated'},
        ],
        expected_requests=3,
    )
    second_401 = expect_error(
        "second getTasks 401",
        [
            {"json": page_zero},
            {"status": 401, "json": {}},
            {"json": new_token},
            {"status": 401, "json": {}},
        ],
        expected_requests=4,
    )
    require(
        [request["method"] for request in second_401]
        == ["GET", "GET", "PATCH", "GET"],
        "a second getTasks 401 triggered another refresh",
    )


def main() -> int:
    try:
        verify_contract()
        verify_package_shape()
        verify_runtime()
        verify_failure_handling()
    except VerificationError as error:
        print(f"FAIL: {error}")
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: verifier infrastructure error: {type(error).__name__}")
        return 1
    except Exception as error:
        print(f"FAIL: client raised unexpected {type(error).__name__}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
