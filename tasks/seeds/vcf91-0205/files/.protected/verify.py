#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0205."""

from __future__ import annotations

import ast
import importlib
import json
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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
OPERATION_IDS = ["getTasks"]
QUERY_PARAMETERS = [
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
UNSET_PARAMETERS = [
    name for name in QUERY_PARAMETERS if name not in {"pageNumber", "pageSize"}
]
ROW_KEYS = ["id", "name", "type", "status", "creationTimestamp"]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class ScriptedState:
    """Deterministic loopback responses for negative client cases."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()


class ScriptedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(("127.0.0.1", 0), ScriptedHandler)
        self.state = ScriptedState(responses)


class ScriptedHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ScriptedServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))
        header_values: dict[str, list[str]] = {}
        for name in self.headers.keys():
            header_values[name.lower()] = self.headers.get_all(name) or []
        with self.server.state.lock:
            index = len(self.server.state.requests)
            self.server.state.requests.append(
                {
                    "method": self.command,
                    "rawTarget": self.path,
                    "path": urlsplit(self.path).path,
                    "headerValues": header_values,
                    "bodyLength": len(body),
                }
            )
        if index < len(self.server.state.responses):
            response = self.server.state.responses[index]
        else:
            response = json_response(
                {"errorCode": "UNEXPECTED_REQUEST", "message": "too many requests"},
                status=500,
            )
        payload = response["body"]
        self.send_response(response["status"])
        content_type = response.get("contentType")
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        for name, value in response.get("headers", {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()


class ScriptedEndpoint:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.server = ScriptedServer(responses)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

    def __enter__(self) -> "ScriptedEndpoint":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self.server.state.lock:
            return list(self.server.state.requests)


def json_response(
    value: Any,
    *,
    status: int = 200,
    content_type: str | None = "application/json",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "contentType": content_type,
        "headers": headers or {},
        "body": json.dumps(value, separators=(",", ":")).encode("utf-8"),
    }


def raw_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str | None = "application/json",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "contentType": content_type,
        "headers": headers or {},
        "body": body,
    }


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
        "contract must name exactly getTasks",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations]
        == [("GET", "/v1/tasks")],
        "contract route changed",
    )
    operation = operations[0]
    require(operation.get("requestBody") is False, "getTasks must be bodyless")
    parameters = operation.get("parameters", [])
    require(
        [item.get("name") for item in parameters] == QUERY_PARAMETERS,
        "getTasks parameter projection changed",
    )
    require(
        all(
            item.get("in") == "query" and item.get("required") is False
            for item in parameters
        ),
        "all getTasks parameters must remain optional query members",
    )
    require(
        parameters[-1].get("schema") == {"type": "boolean", "default": False},
        "doLiveRefresh default projection changed",
    )
    wire = operation.get("focusedWireProfile", {})
    require(wire.get("firstPageMembers") == ["pageSize"], "first-page profile changed")
    require(
        wire.get("laterPageMembers") == ["pageNumber", "pageSize"],
        "later-page profile changed",
    )
    require(wire.get("unsetMembers") == UNSET_PARAMETERS, "unset profile changed")
    require(wire.get("unsetBehavior") == "omit", "unset behavior must be omit")
    require(
        operation.get("responses", {}).get("200", {}).get("schema") == "PageOfTask",
        "getTasks response projection changed",
    )

    schemas = contract.get("schemas", {})
    require(
        schemas.get("Task", {}).get("required")
        == ["creationTimestamp", "id", "name", "status"],
        "Task required fields changed",
    )
    require(
        list(schemas.get("PageMetadata", {}).get("properties", {}))
        == ["pageNumber", "pageSize", "totalElements", "totalPages"],
        "PageMetadata projection changed",
    )
    page_schema = schemas.get("PageOfTask", {}).get("properties", {})
    require(
        page_schema.get("elements", {}).get("items", {}).get("$ref") == "Task"
        and page_schema.get("pageMetadata", {}).get("$ref") == "PageMetadata",
        "PageOfTask projection changed",
    )

    require(sources.get("repository") == "vmware/vcf-api-specs", "source repository changed")
    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source spec path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
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
            (
                "getTasks",
                "GET",
                "/v1/tasks",
                "/paths/~1v1~1tasks/get/operationId",
                COMMIT,
                SPEC_PATH,
            )
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
    require(
        not third_party,
        "client imports non-stdlib modules: " + ", ".join(sorted(third_party)),
    )
    require("subprocess" not in imported_roots(tree), "client must not invoke external programs")
    require(
        "notimplementederror" not in source.casefold(),
        "client workflow is still a stub",
    )

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold()
        in {".whl", ".egg", ".zip", ".so", ".dll", ".dylib", ".pyc"}
    ]
    require(not vendored, "the package must not vendor dependencies or binary artifacts")


def make_scenario() -> tuple[dict[str, Any], list[dict[str, object]]]:
    marker = secrets.token_hex(8)
    ids = ["zeta", "bravo", "alpha", "Alpha", "middle"]
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
            "id": f"{marker}-{ids[index]}",
            "name": f"installer-work-{marker}-{index}",
            "status": "SUCCESSFUL" if index % 2 == 0 else "IN_PROGRESS",
            "creationTimestamp": timestamp,
            "completionTimestamp": "2026-07-14T16:00:00Z",
        }
        if index != 2:
            task["type"] = "VCF_INSTALLER_WORKFLOW"
        tasks.append(task)
    scenario = {
        "accessToken": "access-" + secrets.token_urlsafe(30),
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


def wait_for_ready(ready_path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 8.0
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
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run_case() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, object]]]:
    scenario, expected = make_scenario()
    with tempfile.TemporaryDirectory(prefix="vcf-installer-verify-") as temporary:
        temp = Path(temporary)
        scenario_path = temp / "scenario.json"
        request_log = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
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
            if str(SRC) not in sys.path:
                sys.path.insert(0, str(SRC))
            module = importlib.import_module("vcf_installer")
            client = module.VcfInstallerClient(
                f"http://127.0.0.1:{port}",
                scenario["accessToken"],
                timeout=3.0,
            )
            result = client.list_tasks(page_size=scenario["pageSize"])
        except Exception as error:
            raise VerificationError(
                f"client failed protected collection: {type(error).__name__}: {error}"
            ) from None
        finally:
            stop_process(process)

        lines = request_log.read_text(encoding="utf-8").splitlines()
        requests = [json.loads(line) for line in lines if line.strip()]
    require(isinstance(result, list), "list_tasks must return a list")
    require(result == expected, "result is incomplete, malformed, or not stably sorted")
    require(
        all(isinstance(row, dict) and list(row) == ROW_KEYS for row in result),
        "each result row must contain exactly the required projection in order",
    )
    return requests, scenario, expected


def verify_wire(requests: list[dict[str, Any]], scenario: dict[str, Any]) -> None:
    require(len(requests) == 3, "getTasks must use exactly three page requests")
    expected_targets = [
        "/v1/tasks?pageSize=2",
        "/v1/tasks?pageNumber=1&pageSize=2",
        "/v1/tasks?pageNumber=2&pageSize=2",
    ]
    for index, (request, expected_target) in enumerate(zip(requests, expected_targets)):
        require(request.get("sequence") == index + 1, "request sequence is not contiguous")
        require(request.get("operationId") == "getTasks", "an unapproved operation was called")
        require(request.get("method") == "GET", "getTasks must use GET")
        require(request.get("path") == "/v1/tasks", "getTasks path changed")
        require(request.get("rawTarget") == expected_target, "raw query shape or order changed")
        require(request.get("responseStatus") == 200, "a page request was rejected")
        require(request.get("bodyLength") == 0 and request.get("body") == "", "GET must be bodyless")

        headers = request.get("headerValues", {})
        require(
            headers.get("authorization") == [f"Bearer {scenario['accessToken']}"],
            "Authorization header value or multiplicity changed",
        )
        require(
            headers.get("accept") == ["application/json"],
            "Accept header value or multiplicity changed",
        )
        require("content-type" not in headers, "bodyless GET must omit Content-Type")
        require("content-length" not in headers, "bodyless GET must omit Content-Length")
        require(
            "transfer-encoding" not in headers,
            "bodyless GET must omit Transfer-Encoding",
        )

        query = request.get("query", {})
        expected_query = {"pageSize": ["2"]}
        if index:
            expected_query = {"pageNumber": [str(index)], "pageSize": ["2"]}
        require(query == expected_query, "query members or values changed")
        require(
            all(name not in query for name in UNSET_PARAMETERS),
            "an unset optional query member was transmitted",
        )
    require("pageNumber" not in requests[0].get("query", {}), "first pageNumber must be omitted")


def expect_vcf_error(
    module: Any,
    callback: Any,
    label: str,
    *,
    secret: str | None = None,
) -> Exception:
    try:
        callback()
    except Exception as error:
        require(
            isinstance(error, module.VcfInstallerError),
            f"{label} raised {type(error).__name__}, not VcfInstallerError",
        )
        if secret is not None:
            require(secret not in str(error), f"{label} exposed the access token")
        return error
    raise VerificationError(f"{label} did not raise VcfInstallerError")


def scripted_error_case(
    module: Any,
    responses: list[dict[str, Any]],
    label: str,
    *,
    token: str = "scripted-access-token",
) -> list[dict[str, Any]]:
    with ScriptedEndpoint(responses) as endpoint:
        client = module.VcfInstallerClient(endpoint.base_url, token, timeout=2.0)
        expect_vcf_error(
            module,
            lambda: client.list_tasks(page_size=2),
            label,
            secret=token,
        )
        requests = endpoint.requests
    return requests


def task_record(task_id: str) -> dict[str, object]:
    return {
        "id": task_id,
        "name": f"task-{task_id}",
        "type": "INSTALL_WORKFLOW",
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-07-14T15:00:00Z",
        "ignored": "must not be projected",
    }


def task_page(
    elements: Any,
    *,
    page_number: Any = 0,
    page_size: Any = 2,
    total_elements: Any = 1,
    total_pages: Any = 1,
) -> dict[str, Any]:
    return {
        "elements": elements,
        "pageMetadata": {
            "pageNumber": page_number,
            "pageSize": page_size,
            "totalElements": total_elements,
            "totalPages": total_pages,
        },
    }


def verify_invalid_inputs(module: Any) -> None:
    client_type = module.VcfInstallerClient
    token = "constructor-secret"
    constructor_cases = [
        ("blank base_url", lambda: client_type(" ", token)),
        ("non-HTTP base_url", lambda: client_type("ftp://127.0.0.1", token)),
        ("credentialed base_url", lambda: client_type("http://user@127.0.0.1", token)),
        ("base_url path", lambda: client_type("http://127.0.0.1/prefix", token)),
        ("base_url query", lambda: client_type("http://127.0.0.1?x=1", token)),
        ("invalid base_url port", lambda: client_type("http://127.0.0.1:not-a-port", token)),
        ("blank access_token", lambda: client_type("http://127.0.0.1", "\t")),
        ("header-breaking access_token", lambda: client_type("http://127.0.0.1", "a\nb")),
        ("boolean timeout", lambda: client_type("http://127.0.0.1", token, timeout=True)),
        ("zero timeout", lambda: client_type("http://127.0.0.1", token, timeout=0)),
        ("infinite timeout", lambda: client_type("http://127.0.0.1", token, timeout=float("inf"))),
        ("string timeout", lambda: client_type("http://127.0.0.1", token, timeout="1")),
    ]
    for label, callback in constructor_cases:
        expect_vcf_error(module, callback, label, secret=token)

    with ScriptedEndpoint([]) as endpoint:
        client = client_type(endpoint.base_url, token, timeout=2)
        for value in [True, False, 0, -1, 101, 2.0, "2", None]:
            expect_vcf_error(
                module,
                lambda value=value: client.list_tasks(page_size=value),
                f"invalid page_size {value!r}",
                secret=token,
            )
        require(not endpoint.requests, "invalid page_size performed network I/O")


def verify_http_and_transport_failures(module: Any) -> None:
    token = "secret-must-never-appear"
    requests = scripted_error_case(
        module,
        [
            json_response(
                {"errorCode": "FAILED", "message": token},
                status=500,
            )
        ],
        "HTTP failure",
        token=token,
    )
    require(len(requests) == 1, "HTTP failure caused an unexpected retry")

    valid_empty = task_page(
        [], page_number=0, page_size=2, total_elements=0, total_pages=0
    )
    require(
        len(
            scripted_error_case(
                module,
                [json_response(valid_empty, status=201)],
                "non-contract success status",
            )
        )
        == 1,
        "non-200 status caused an unexpected retry",
    )
    scripted_error_case(
        module,
        [json_response(valid_empty, content_type="text/plain")],
        "wrong response media type",
    )
    scripted_error_case(
        module,
        [raw_response(b"{not-json")],
        "malformed JSON",
    )
    scripted_error_case(
        module,
        [
            raw_response(
                b'{"elements":[],"pageMetadata":{"pageNumber":0,'
                b'"pageSize":2,"totalElements":0,"totalPages":0},"bad":NaN}'
            )
        ],
        "non-standard JSON constant",
    )

    with ScriptedEndpoint(
        [
            raw_response(
                b"redirect",
                status=302,
                content_type="text/plain",
                headers={"Location": "/outside-focused-contract"},
            ),
            json_response(valid_empty),
        ]
    ) as endpoint:
        client = module.VcfInstallerClient(endpoint.base_url, token, timeout=2)
        expect_vcf_error(
            module,
            lambda: client.list_tasks(page_size=2),
            "redirect response",
            secret=token,
        )
        requests = endpoint.requests
    require(len(requests) == 1, "client followed a redirect outside getTasks")
    require(
        requests[0]["rawTarget"] == "/v1/tasks?pageSize=2",
        "redirect test did not begin at getTasks",
    )

    transport_client = module.VcfInstallerClient(
        "http://127.0.0.1:0", token, timeout=0.25
    )
    expect_vcf_error(
        module,
        lambda: transport_client.list_tasks(page_size=2),
        "transport failure",
        secret=token,
    )


def verify_page_failures(module: Any) -> None:
    first = task_record("first")
    second = task_record("second")
    third = task_record("third")
    one_request_cases = [
        ("non-object page", []),
        (
            "missing elements",
            {
                "pageMetadata": {
                    "pageNumber": 0,
                    "pageSize": 2,
                    "totalElements": 0,
                    "totalPages": 0,
                }
            },
        ),
        ("non-list elements", task_page({}, total_elements=0, total_pages=0)),
        ("non-object pageMetadata", {"elements": [], "pageMetadata": []}),
        ("missing metadata integer", {"elements": [], "pageMetadata": {}}),
        ("boolean metadata integer", task_page([first], page_number=False)),
        ("string metadata integer", task_page([first], total_elements="1")),
        ("negative page number", task_page([first], page_number=-1)),
        ("negative total elements", task_page([], total_elements=-1, total_pages=0)),
        ("negative total pages", task_page([], total_elements=0, total_pages=-1)),
        ("nonpositive metadata page size", task_page([first], page_size=0)),
        ("changed metadata page size", task_page([first], page_size=1)),
        ("unrequested first page", task_page([first], page_number=1)),
        (
            "incoherent total pages",
            task_page([first, second], total_elements=3, total_pages=3),
        ),
        (
            "overfull page",
            task_page([first, second, third], total_elements=3, total_pages=2),
        ),
        ("empty nonterminal page", task_page([], total_elements=3, total_pages=2)),
        (
            "short nonterminal page",
            task_page([first], total_elements=3, total_pages=2),
        ),
        ("element overshoot", task_page([first, second], total_elements=1, total_pages=1)),
    ]
    for label, page in one_request_cases:
        requests = scripted_error_case(module, [json_response(page)], label)
        require(len(requests) == 1, f"{label} caused an unexpected request count")

    first_page = task_page(
        [first, second], total_elements=3, total_pages=2
    )
    multi_request_cases = [
        (
            "repeated or unrequested page",
            task_page([third], page_number=0, total_elements=3, total_pages=2),
        ),
        (
            "changed totals",
            task_page([third], page_number=1, total_elements=4, total_pages=2),
        ),
        (
            "incomplete final page",
            task_page([], page_number=1, total_elements=3, total_pages=2),
        ),
    ]
    for label, second_page in multi_request_cases:
        requests = scripted_error_case(
            module,
            [json_response(first_page), json_response(second_page)],
            label,
        )
        require(len(requests) == 2, f"{label} did not fail on its second page")


def verify_task_failures(module: Any) -> None:
    valid = task_record("valid")
    invalid_tasks: list[tuple[str, Any]] = [("non-object task", "task")]
    for field, value in [
        ("id", " "),
        ("name", None),
        ("status", ""),
        ("creationTimestamp", 0),
    ]:
        item = dict(valid)
        item[field] = value
        invalid_tasks.append((f"invalid Task.{field}", item))
    missing_id = dict(valid)
    del missing_id["id"]
    invalid_tasks.append(("missing Task.id", missing_id))
    invalid_type = dict(valid)
    invalid_type["type"] = None
    invalid_tasks.append(("invalid optional Task.type", invalid_type))

    for label, item in invalid_tasks:
        requests = scripted_error_case(
            module,
            [json_response(task_page([item]))],
            label,
        )
        require(len(requests) == 1, f"{label} caused an unexpected request count")

    duplicate = task_record("duplicate")
    requests = scripted_error_case(
        module,
        [
            json_response(
                task_page(
                    [duplicate, dict(duplicate)],
                    total_elements=2,
                    total_pages=1,
                )
            )
        ],
        "duplicate task IDs",
    )
    require(len(requests) == 1, "duplicate IDs caused an unexpected request count")


def verify_small_success_cases(module: Any) -> None:
    empty_page = task_page(
        [], page_number=0, page_size=2, total_elements=0, total_pages=0
    )
    with ScriptedEndpoint([json_response(empty_page)]) as endpoint:
        client = module.VcfInstallerClient(endpoint.base_url + "/", "valid-token", timeout=2)
        require(client.list_tasks(page_size=2) == [], "empty collection was not returned")
        requests = endpoint.requests
    require(len(requests) == 1, "empty collection did not stop after one page")
    require(
        requests[0]["rawTarget"] == "/v1/tasks?pageSize=2",
        "trailing base_url slash changed the raw target",
    )

    item = task_record("empty-type")
    item["type"] = ""
    with ScriptedEndpoint([json_response(task_page([item]))]) as endpoint:
        client = module.VcfInstallerClient(endpoint.base_url, "valid-token", timeout=2)
        result = client.list_tasks(page_size=2)
    require(
        result
        == [
            {
                "id": "empty-type",
                "name": "task-empty-type",
                "type": "",
                "status": "SUCCESSFUL",
                "creationTimestamp": "2026-07-14T15:00:00Z",
            }
        ],
        "optional type was overvalidated or projection changed",
    )


def main() -> int:
    try:
        verify_contract()
        verify_package_shape()
        requests, scenario, _expected = run_case()
        verify_wire(requests, scenario)
        module = importlib.import_module("vcf_installer")
        verify_invalid_inputs(module)
        verify_http_and_transport_failures(module)
        verify_page_failures(module)
        verify_task_failures(module)
        verify_small_success_cases(module)
    except VerificationError as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print("VERIFICATION PASSED: complete stable getTasks collection and exact wire shape")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
