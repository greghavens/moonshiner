#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0229."""

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLIENT_PATH = SRC / "vcf_sddc_lcm" / "client.py"
INIT_PATH = SRC / "vcf_sddc_lcm" / "__init__.py"
PROJECT_PATH = ROOT / "pyproject.toml"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
SPEC_URL_PREFIX = "https://github.com/vmware/vcf-api-specs/blob/"
OPERATION_IDS = [
    "getTasks",
    "generateComponentSupportBundle",
    "getTask",
    "getComponentSupportBundles",
]
ROUTES = [
    ("GET", "/v1/tasks"),
    ("POST", "/v1/components/{componentId}/support-bundles"),
    ("GET", "/v1/tasks/{taskId}"),
    ("GET", "/v1/components/{componentId}/support-bundles"),
]
SPEC_LINES = [1020, 816, 1155, 774]
TASK_QUERY = [
    "status",
    "type",
    "createdBy",
    "name",
    "description",
    "startTimeGt",
    "startTimeLt",
    "updateTimeGt",
    "updateTimeLt",
    "endTimeGt",
    "endTimeLt",
    "resourceId",
    "resourceType",
    "includeSystemTasks",
    "pageNumber",
    "pageSize",
]
SENT_QUERY = {"type", "resourceId", "resourceType", "pageNumber", "pageSize"}
UNSET_QUERY = [name for name in TASK_QUERY if name not in SENT_QUERY]
TASK_STATUS_ENUM = [
    "PENDING",
    "SCHEDULED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELED",
]
BUNDLE_KEYS = ["id", "createdTimestamp", "size", "name", "url"]
RESULT_KEYS = ["taskId", "correlationId", "status", "created", "supportBundle"]
PAGE_SIZE = 2
CORRELATION_HEADER = "x-correlation-id"
PROXY_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# contract and provenance
# --------------------------------------------------------------------------


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repository") == "vmware/vcf-api-specs", "repository changed")
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.4", "OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "VCF API version changed")
    require(
        contract.get("security", {}).get("scheme") == "bearerToken"
        and contract["security"].get("httpScheme") == "Bearer",
        "contract security projection changed",
    )

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract operationIds changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations] == ROUTES,
        "contract routes changed",
    )
    get_tasks, generate, get_task, list_bundles = operations

    require(get_tasks.get("requestBody") is False, "getTasks must be bodyless")
    parameters = get_tasks.get("parameters", [])
    require(
        [item.get("name") for item in parameters] == TASK_QUERY,
        "getTasks parameter projection changed",
    )
    require(
        all(
            item.get("in") == "query" and item.get("required") is False
            for item in parameters
        ),
        "every getTasks parameter must remain an optional query member",
    )
    require(
        parameters[TASK_QUERY.index("includeSystemTasks")].get("schema")
        == {"type": "boolean", "default": False},
        "includeSystemTasks default projection changed",
    )
    wire = get_tasks.get("focusedWireProfile", {})
    require(
        wire.get("firstPageMembers") == ["type", "resourceId", "resourceType", "pageSize"],
        "first page profile changed",
    )
    require(
        wire.get("laterPageMembers")
        == ["type", "resourceId", "resourceType", "pageNumber", "pageSize"],
        "later page profile changed",
    )
    require(wire.get("unsetMembers") == UNSET_QUERY, "unset-member profile changed")
    require(wire.get("unsetBehavior") == "omit", "unset behavior must be omit")
    require(
        wire.get("filterValues")
        == {"type": "SUPPORT_BUNDLE", "resourceType": "COMPONENT"},
        "task filter projection changed",
    )
    require(
        get_tasks.get("responses", {}).get("200", {}).get("schema")
        == "PageOfTaskSummary",
        "getTasks response projection changed",
    )

    require(
        [
            (item.get("name"), item.get("in"), item.get("required"))
            for item in generate.get("parameters", [])
        ]
        == [
            ("X-Correlation-Id", "header", False),
            ("componentId", "path", True),
        ],
        "generate parameter projection changed",
    )
    require(
        generate.get("requestBody")
        == {
            "required": False,
            "contentType": "application/json",
            "schema": "ComponentSupportBundleSpec",
        },
        "generate request body projection changed",
    )
    generate_wire = generate.get("focusedWireProfile", {})
    require(
        generate_wire.get("unsetMembers") == ["lookBackWindow"],
        "generate unset-member profile changed",
    )
    require(generate_wire.get("unsetBehavior") == "omit", "generate must omit unset members")
    require(generate_wire.get("emptyBody") == "{}", "empty generate body projection changed")
    require(
        generate_wire.get("idempotencyKeyHeader") == "X-Correlation-Id",
        "correlation header projection changed",
    )
    require(
        generate.get("responses", {}).get("202", {}).get("schema") == "Task",
        "generate accepted-response projection changed",
    )

    require(get_task.get("requestBody") is False, "getTask must be bodyless")
    require(
        [
            (item.get("name"), item.get("in"), item.get("required"))
            for item in get_task.get("parameters", [])
        ]
        == [("taskId", "path", True)],
        "getTask parameter projection changed",
    )
    require(list_bundles.get("requestBody") is False, "bundle listing must be bodyless")
    require(
        [
            (item.get("name"), item.get("in"), item.get("required"))
            for item in list_bundles.get("parameters", [])
        ]
        == [("componentId", "path", True)],
        "bundle listing parameter projection changed",
    )
    require(
        all(
            item.get("name") != "X-Correlation-Id"
            for item in list_bundles.get("parameters", [])
        ),
        "bundle listing must not declare a correlation header",
    )

    schemas = contract.get("schemas", {})
    require(
        schemas.get("TaskStatus", {}).get("enum") == TASK_STATUS_ENUM,
        "TaskStatus enum projection changed",
    )
    require(
        list(schemas.get("ComponentSupportBundleSpec", {}).get("properties", {}))
        == ["lookBackWindow"],
        "ComponentSupportBundleSpec projection changed",
    )
    require(
        "required" not in schemas.get("ComponentSupportBundleSpec", {}),
        "ComponentSupportBundleSpec has no required members in the specification",
    )
    require(
        list(schemas.get("SupportBundle", {}).get("properties", {})) == BUNDLE_KEYS,
        "SupportBundle projection changed",
    )
    require(
        schemas.get("TaskSummary", {}).get("required") == ["id"]
        and schemas.get("Task", {}).get("required") == ["id"],
        "task required-field projection changed",
    )
    for name in ("TaskSummary", "Task"):
        require(
            "correlationId" in schemas.get(name, {}).get("properties", {}),
            f"{name} must project correlationId",
        )
    require(
        "additionalDetails" in schemas.get("Task", {}).get("properties", {}),
        "Task must project additionalDetails",
    )
    require(
        list(schemas.get("PageMetadata", {}).get("properties", {}))
        == ["pageNumber", "pageSize", "totalElements", "totalPages"],
        "PageMetadata projection changed",
    )


def verify_sources() -> None:
    sources = load_json(SOURCES_PATH)
    require(sources.get("repository") == "vmware/vcf-api-specs", "source repository changed")
    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("specVersion") == "9.1.0.0", "source spec version changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    spec_url = sources.get("specUrl", "")
    require(
        spec_url == f"{SPEC_URL_PREFIX}{COMMIT}/{SPEC_PATH}",
        "official source URL must pin the immutable commit",
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
            (operation_id, method, path, line, COMMIT, SPEC_PATH)
            for (operation_id, (method, path), line) in zip(
                OPERATION_IDS, ROUTES, SPEC_LINES
            )
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource") is False,
        "a documentation page must not be the contract source",
    )


# --------------------------------------------------------------------------
# package shape
# --------------------------------------------------------------------------


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_package_shape() -> None:
    require(CLIENT_PATH.is_file(), "src/vcf_sddc_lcm/client.py is missing")
    require(INIT_PATH.is_file(), "protected package initializer is missing")
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    require(
        project.get("project", {}).get("dependencies") == [],
        "dependencies must be empty",
    )
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
    folded = source.casefold()
    require("notimplementederror" not in folded, "client workflow is still a stub")
    for required in ("/v1/tasks", "support-bundles", "x-correlation-id", "authorization"):
        require(required in folded, f"client does not implement required surface: {required}")

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold()
        in {".whl", ".egg", ".zip", ".so", ".dll", ".dylib", ".pyc"}
    ]
    require(not vendored, "the package must not vendor dependencies or binary artifacts")


# --------------------------------------------------------------------------
# runtime scenario
# --------------------------------------------------------------------------


def make_scenario() -> dict[str, Any]:
    marker = secrets.token_hex(6)
    component_id = str(uuid.uuid4())
    tasks = [
        {
            "id": str(uuid.uuid4()),
            "name": f"decoy-support-bundle-{marker}-{index}",
            "status": "SUCCEEDED" if index % 2 == 0 else "FAILED",
            "type": "SUPPORT_BUNDLE",
            "createdBy": "operator",
            "resourceId": component_id,
            "resourceType": "COMPONENT",
            "createTime": f"2026-07-0{index + 1}T08:00:00.000Z",
            "correlationId": f"decoy-{marker}-{index}",
            "retriable": False,
            "cancellable": False,
            "additionalDetails": {"supportBundleId": f"absent-{marker}-{index}"},
        }
        for index in range(4)
    ]
    return {
        "accessToken": "tok-" + secrets.token_urlsafe(28),
        "componentId": component_id,
        "otherComponentId": str(uuid.uuid4()),
        "bundleMarker": marker,
        "tasks": tasks,
        "firstCorrelationId": f"ensure-{marker}-alpha",
        "secondCorrelationId": f"ensure-{marker}-beta",
    }


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
) -> None:
    try:
        action()
    except error_type:
        return
    except Exception as error:
        raise VerificationError(
            f"{label} raised {type(error).__name__}, not {error_type.__name__}"
        ) from None
    raise VerificationError(f"{label} did not raise {error_type.__name__}")


class ScriptedServer(ThreadingHTTPServer):
    """Tiny loopback server for deterministic malformed-response cases."""

    daemon_threads = True

    def __init__(self, responses: list[tuple[int | None, Any]]) -> None:
        super().__init__(("127.0.0.1", 0), ScriptedHandler)
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.response_lock = threading.Lock()


class ScriptedHandler(BaseHTTPRequestHandler):
    server: ScriptedServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()

    def _serve(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(max(length, 0))
        with self.server.response_lock:
            self.server.requests.append(
                {"method": self.command, "target": self.path, "body": body}
            )
            if self.server.responses:
                status, payload = self.server.responses.pop(0)
            else:
                status, payload = 500, {"errorCode": "SCRIPT_EXHAUSTED"}

        if status is None:
            # Returning without a status line deterministically exercises a dropped
            # transport response (RemoteDisconnected for urllib/http.client).
            self.close_connection = True
            return
        raw = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.wfile.flush()


def scripted_call(
    client_type: type[Any],
    error_type: type[BaseException],
    responses: list[tuple[int | None, Any]],
    label: str,
    *,
    expect_error: bool = True,
    component_id: str = "component-validation",
    correlation_id: str = "correlation-validation",
) -> tuple[Any, list[dict[str, Any]]]:
    """Run one public workflow against a fresh, entirely local response script."""
    server = ScriptedServer(responses)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    saved_proxies = {
        name: os.environ.pop(name) for name in PROXY_NAMES if name in os.environ
    }
    result: Any = None
    try:
        client = client_type(
            f"http://127.0.0.1:{server.server_address[1]}",
            "validation-access-token",
            timeout=2.0,
        )
        action = lambda: client.ensure_support_bundle(
            component_id, correlation_id, page_size=2
        )
        if expect_error:
            assert_client_error(error_type, action, label)
        else:
            try:
                result = action()
            except Exception as error:
                raise VerificationError(
                    f"{label} raised {type(error).__name__}: {error}"
                ) from None
    finally:
        os.environ.update(saved_proxies)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return result, list(server.requests)


def task_page(
    elements: Any,
    *,
    page_number: Any = 0,
    page_size: Any = 2,
    total_elements: Any | None = None,
    total_pages: Any = 1,
) -> dict[str, Any]:
    if total_elements is None:
        total_elements = len(elements) if isinstance(elements, list) else 0
    return {
        "elements": elements,
        "pageMetadata": {
            "pageNumber": page_number,
            "pageSize": page_size,
            "totalElements": total_elements,
            "totalPages": total_pages,
        },
    }


def resolved_task(
    correlation_id: str = "correlation-validation",
    *,
    task_id: str = "task-validation",
    status: str = "SUCCEEDED",
    bundle_id: Any = "bundle-validation",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "correlationId": correlation_id,
        "status": status,
        "additionalDetails": {"supportBundleId": bundle_id},
    }


def support_bundle(bundle_id: str = "bundle-validation") -> dict[str, Any]:
    return {
        "id": bundle_id,
        "createdTimestamp": "2026-07-20T10:00:00.000Z",
        "size": 8192,
        "name": "validation.tgz",
        "url": f"https://vmsp.example.com/bundles/{bundle_id}",
    }


def import_client() -> tuple[type[Any], type[BaseException]]:
    sys.path.insert(0, str(SRC))
    sys.dont_write_bytecode = True
    try:
        package = importlib.import_module("vcf_sddc_lcm")
    finally:
        sys.path.pop(0)
    require(
        getattr(package, "__all__", None) == ["SddcLcmClient", "SddcLcmError"],
        "package exports changed",
    )
    client_type = getattr(package, "SddcLcmClient", None)
    error_type = getattr(package, "SddcLcmError", None)
    require(isinstance(client_type, type), "SddcLcmClient is not a class")
    require(
        isinstance(error_type, type) and issubclass(error_type, RuntimeError),
        "SddcLcmError must derive from RuntimeError",
    )
    return client_type, error_type


def verify_response_validation() -> None:
    """Exercise required failure handling without relying on implementation internals."""
    client_type, error_type = import_client()
    correlation_id = "correlation-validation"
    accepted = {"id": "task-validation", "correlationId": correlation_id}
    good_task = resolved_task(correlation_id)
    good_bundle = support_bundle()

    assert_client_error(
        error_type,
        lambda: client_type("not-an-absolute-url", "access").ensure_support_bundle(
            "component", correlation_id
        ),
        "invalid transport URL",
    )

    for label, response in [
        ("HTTP failure", (500, {"errorCode": "FAULT"})),
        ("malformed JSON", (200, b"{")),
        ("non-UTF-8 JSON", (200, b"\xff")),
        ("dropped response", (None, b"")),
        ("wrong getTasks success status", (202, task_page([]))),
    ]:
        _, requests = scripted_call(
            client_type, error_type, [response], label
        )
        require(len(requests) == 1, f"{label} was not rejected at getTasks")

    invalid_pages: list[tuple[str, Any]] = [
        ("non-object task page", []),
        ("non-list task elements", task_page({"id": "bad"})),
        (
            "non-object page metadata",
            {"elements": [], "pageMetadata": []},
        ),
        (
            "missing page metadata member",
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
        ("negative total elements", task_page([], total_elements=-1)),
        ("zero total pages", task_page([], total_pages=0)),
        ("wrong echoed page number", task_page([], page_number=1, total_pages=2)),
        ("wrong echoed page size", task_page([], page_size=1)),
        (
            "overfull task page",
            task_page(
                [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                page_size=2,
                total_elements=3,
                total_pages=2,
            ),
        ),
        ("non-object task summary", task_page(["bad"])),
        ("blank required task-summary id", task_page([{"id": " "}])),
    ]
    valid_tail = [
        (202, accepted),
        (200, good_task),
        (200, [good_bundle]),
    ]
    for label, page in invalid_pages:
        _, requests = scripted_call(
            client_type,
            error_type,
            [(200, page), *valid_tail],
            label,
        )
        require(len(requests) == 1, f"{label} was not rejected on its page")

    # A non-last page must advance, while the element count is never a shortcut
    # for the advertised last page.
    non_last_empty = task_page(
        [], page_number=0, total_elements=0, total_pages=2
    )
    _, requests = scripted_call(
        client_type,
        error_type,
        [
            (200, non_last_empty),
            (200, task_page([], page_number=1, total_elements=0, total_pages=2)),
            *valid_tail,
        ],
        "empty non-last page",
    )
    require(len(requests) == 1, "empty non-last page did not fail before advancing")

    first_page = task_page(
        [{"id": "decoy", "correlationId": "other"}],
        page_number=0,
        total_elements=1,
        total_pages=2,
    )
    matching_page = task_page(
        [{"id": "task-validation", "correlationId": correlation_id}],
        page_number=1,
        total_elements=1,
        total_pages=2,
    )
    adopted, requests = scripted_call(
        client_type,
        error_type,
        [
            (200, first_page),
            (200, matching_page),
            (200, good_task),
            (200, [good_bundle]),
        ],
        "advertised last-page reconciliation",
        expect_error=False,
    )
    check_result(
        adopted,
        correlation_id=correlation_id,
        created=False,
        label="advertised last-page reconciliation",
    )
    require(len(requests) == 4, "reconciliation stopped before the advertised last page")

    changed_totals_page = task_page(
        [], page_number=1, total_elements=2, total_pages=2
    )
    _, requests = scripted_call(
        client_type,
        error_type,
        [
            (200, first_page),
            (200, changed_totals_page),
            *valid_tail,
        ],
        "changing pagination totals",
    )
    require(len(requests) == 2, "changing pagination totals were not rejected")

    empty_page = (200, task_page([]))
    invalid_generates: list[tuple[str, int, Any]] = [
        ("wrong generate success status", 200, accepted),
        ("non-object accepted task", 202, []),
        ("blank accepted task id", 202, {"id": " "}),
        (
            "foreign accepted-task correlation ID",
            202,
            {"id": "task-validation", "correlationId": "foreign"},
        ),
    ]
    for label, status, response in invalid_generates:
        _, requests = scripted_call(
            client_type,
            error_type,
            [empty_page, (status, response), (200, good_task), (200, [good_bundle])],
            label,
        )
        require(len(requests) == 2, f"{label} was not rejected at generate")

    invalid_tasks: list[tuple[str, int, Any]] = [
        ("wrong getTask success status", 202, good_task),
        ("non-object resolved task", 200, []),
        ("mismatched resolved task id", 200, resolved_task(task_id="other")),
        (
            "mismatched resolved correlation ID",
            200,
            resolved_task("foreign"),
        ),
        ("unknown resolved task status", 200, resolved_task(status="UNKNOWN")),
        ("unsuccessful resolved task", 200, resolved_task(status="FAILED")),
        (
            "missing task additionalDetails",
            200,
            {
                "id": "task-validation",
                "correlationId": correlation_id,
                "status": "SUCCEEDED",
            },
        ),
        ("blank support bundle id", 200, resolved_task(bundle_id=" ")),
    ]
    for label, status, response in invalid_tasks:
        _, requests = scripted_call(
            client_type,
            error_type,
            [empty_page, (202, accepted), (status, response), (200, [good_bundle])],
            label,
        )
        require(len(requests) == 3, f"{label} was not rejected at getTask")

    invalid_listings: list[tuple[str, int, Any]] = [
        ("wrong bundle-list success status", 202, [good_bundle]),
        ("non-array bundle listing", 200, {}),
        ("malformed bundle-list entry", 200, [good_bundle, "bad"]),
        ("missing selected bundle", 200, [support_bundle("other")]),
        ("duplicate selected bundle", 200, [good_bundle, dict(good_bundle)]),
    ]
    for label, status, response in invalid_listings:
        _, requests = scripted_call(
            client_type,
            error_type,
            [empty_page, (202, accepted), (200, good_task), (status, response)],
            label,
        )
        require(len(requests) == 4, f"{label} was not rejected at bundle listing")

    # All SupportBundle members are optional in the pinned schema and therefore
    # must be projected as None instead of disappearing from the result.
    minimal, requests = scripted_call(
        client_type,
        error_type,
        [empty_page, (202, accepted), (200, good_task), (200, [{"id": "bundle-validation"}])],
        "minimal optional bundle projection",
        expect_error=False,
    )
    checked = check_result(
        minimal,
        correlation_id=correlation_id,
        created=True,
        label="minimal optional bundle projection",
    )
    require(
        checked["supportBundle"]
        == {
            "id": "bundle-validation",
            "createdTimestamp": None,
            "size": None,
            "name": None,
            "url": None,
        },
        "missing optional bundle members were not preserved as None",
    )
    require(len(requests) == 4, "minimal bundle workflow made extra requests")


def check_result(
    result: Any,
    *,
    correlation_id: str,
    created: bool,
    label: str,
) -> dict[str, Any]:
    require(isinstance(result, dict), f"{label} must return a dictionary")
    require(list(result) == RESULT_KEYS, f"{label} result keys or key order changed")
    require(
        result["correlationId"] == correlation_id,
        f"{label} returned the wrong correlation ID",
    )
    require(result["created"] is created, f"{label} reported the wrong creation flag")
    require(result["status"] == "SUCCEEDED", f"{label} returned the wrong task status")
    require(
        isinstance(result["taskId"], str) and result["taskId"],
        f"{label} returned a blank task ID",
    )
    bundle = result["supportBundle"]
    require(isinstance(bundle, dict), f"{label} must return a bundle dictionary")
    require(list(bundle) == BUNDLE_KEYS, f"{label} bundle keys or key order changed")
    require(
        isinstance(bundle["id"], str) and bundle["id"],
        f"{label} returned a blank bundle ID",
    )
    return result


def verify_runtime() -> None:
    client_type, error_type = import_client()
    for label, arguments in [
        ("blank base_url", ("", "access")),
        ("blank access_token", ("http://127.0.0.1:1", " ")),
    ]:
        assert_client_error(error_type, lambda args=arguments: client_type(*args), label)
    assert_client_error(
        error_type,
        lambda: client_type("http://127.0.0.1:1", "access", timeout=0),
        "nonpositive timeout",
    )

    scenario = make_scenario()
    component_id = scenario["componentId"]
    first_key = scenario["firstCorrelationId"]
    second_key = scenario["secondCorrelationId"]

    with tempfile.TemporaryDirectory(prefix="vcf91-0229-") as temporary:
        temp = Path(temporary)
        port_file = temp / "port.txt"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        summary_file = temp / "summary.json"
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
                str(summary_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(port_file, server)
            saved_proxies = {
                name: os.environ.pop(name) for name in PROXY_NAMES if name in os.environ
            }
            try:
                client = client_type(
                    f"http://127.0.0.1:{port}",
                    scenario["accessToken"],
                    timeout=5.0,
                )
                for label, kwargs in [
                    ("blank component_id", {"component_id": "", "correlation_id": first_key}),
                    ("blank correlation_id", {"component_id": component_id, "correlation_id": " "}),
                ]:
                    assert_client_error(
                        error_type,
                        lambda kw=kwargs: client.ensure_support_bundle(**kw),
                        label,
                    )
                for invalid in (False, 0, -3, 1.5, "2"):
                    assert_client_error(
                        error_type,
                        lambda value=invalid: client.ensure_support_bundle(
                            component_id, first_key, look_back_window=value
                        ),
                        f"invalid look_back_window {invalid!r}",
                    )
                    assert_client_error(
                        error_type,
                        lambda value=invalid: client.ensure_support_bundle(
                            component_id, first_key, page_size=value
                        ),
                        f"invalid page_size {invalid!r}",
                    )
                assert_client_error(
                    error_type,
                    lambda: client.ensure_support_bundle(
                        component_id, first_key, page_size=51
                    ),
                    "page_size above the documented maximum",
                )

                def run(label: str, **kwargs: Any) -> Any:
                    try:
                        return client.ensure_support_bundle(**kwargs)
                    except Exception as error:  # surfaces the mock's rejection reason
                        raise VerificationError(
                            f"{label} raised {type(error).__name__}: {error}"
                        ) from None

                cold = run(
                    "first attempt",
                    component_id=component_id,
                    correlation_id=first_key,
                    page_size=PAGE_SIZE,
                )
                retry = run(
                    "retry",
                    component_id=component_id,
                    correlation_id=first_key,
                    page_size=PAGE_SIZE,
                )
                windowed = run(
                    "windowed run",
                    component_id=component_id,
                    correlation_id=second_key,
                    look_back_window=6,
                    page_size=PAGE_SIZE,
                )
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
        require(summary_file.is_file(), "loopback mock did not publish its effect summary")
        summary = load_json(summary_file)

    check_result(cold, correlation_id=first_key, created=True, label="first attempt")
    check_result(retry, correlation_id=first_key, created=False, label="retry")
    check_result(windowed, correlation_id=second_key, created=True, label="windowed run")

    verify_idempotent_effect(cold, retry, windowed, summary)
    verify_wire(requests, scenario, port)


def verify_idempotent_effect(
    cold: dict[str, Any],
    retry: dict[str, Any],
    windowed: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    require(retry is not cold, "the retry reused a cached result dictionary")
    require(
        retry["supportBundle"] is not cold["supportBundle"],
        "the retry reused a cached support-bundle dictionary",
    )
    require(
        retry["taskId"] == cold["taskId"],
        "the retry adopted a different task instead of the existing one",
    )
    require(
        retry["supportBundle"] == cold["supportBundle"],
        "the retry returned a different support bundle",
    )
    require(
        windowed["taskId"] != cold["taskId"],
        "a distinct correlation ID must produce a distinct task",
    )
    require(
        windowed["supportBundle"]["id"] != cold["supportBundle"]["id"],
        "a distinct correlation ID must produce a distinct bundle",
    )
    require(
        summary.get("duplicateAttempts") == 0,
        "the service rejected a duplicate generate request; the retry was not safe",
    )
    require(
        summary.get("createdBundleCount") == 2,
        "exactly two support bundles must exist: the retry duplicated the effect",
    )
    require(
        summary.get("bundleIds")
        == [cold["supportBundle"]["id"], windowed["supportBundle"]["id"]],
        "the generated bundle set does not match the returned bundles",
    )
    require(summary.get("taskCount") == 6, "the seeded task set changed unexpectedly")


def verify_wire(
    requests: list[dict[str, Any]], scenario: dict[str, Any], port: int
) -> None:
    component_id = scenario["componentId"]
    first_key = scenario["firstCorrelationId"]
    second_key = scenario["secondCorrelationId"]
    bundles_path = f"/v1/components/{component_id}/support-bundles"

    require(
        [request.get("sequence") for request in requests]
        == list(range(1, len(requests) + 1)),
        "request log sequence changed",
    )
    require(
        all(request.get("responseStatus") in (200, 202) for request in requests),
        "the mock rejected a request; the wire shape or call order is wrong",
    )
    require(
        all(request.get("operationId") in OPERATION_IDS for request in requests),
        "an operation outside the focused contract was contacted",
    )

    observed = [request.get("operationId") for request in requests]
    expected = (
        # cold run: two task pages, no match, generate, resolve, list
        ["getTasks", "getTasks", "generateComponentSupportBundle", "getTask",
         "getComponentSupportBundles"]
        # retry: three task pages, the adopted task is on the last one, no generate
        + ["getTasks", "getTasks", "getTasks", "getTask", "getComponentSupportBundles"]
        # windowed run: three task pages, no match, generate, resolve, list
        + ["getTasks", "getTasks", "getTasks", "generateComponentSupportBundle",
           "getTask", "getComponentSupportBundles"]
    )
    require(
        observed == expected,
        "operation sequence changed: the reconcile-before-mutate order is wrong",
    )

    generates = [
        request
        for request in requests
        if request.get("operationId") == "generateComponentSupportBundle"
    ]
    require(
        len(generates) == 2,
        "the mutating operation must be issued exactly once per correlation ID",
    )

    for request in requests:
        require(
            one_header(request, "host") == f"127.0.0.1:{port}",
            "request escaped the loopback mock",
        )
        require(
            one_header(request, "authorization") == f"Bearer {scenario['accessToken']}",
            "bearer token changed",
        )
        require(one_header(request, "accept") == "application/json", "Accept changed")

    # ---- getTasks: exact pagination targets and omission of every unset member
    task_requests = [
        request for request in requests if request.get("operationId") == "getTasks"
    ]
    require(len(task_requests) == 8, "getTasks request count changed")
    page_numbers = [0, 1, 0, 1, 2, 0, 1, 2]
    prefix = (
        f"type=SUPPORT_BUNDLE&resourceId={component_id}&resourceType=COMPONENT"
    )
    for index, (request, page) in enumerate(zip(task_requests, page_numbers)):
        require(request.get("method") == "GET", f"getTasks {index} method changed")
        require(request.get("bodyLength") == 0, f"getTasks {index} must be bodyless")
        headers = request.get("headerValues", {})
        require("content-type" not in headers, f"getTasks {index} sent Content-Type")
        require("content-length" not in headers, f"getTasks {index} sent Content-Length")
        require(
            CORRELATION_HEADER not in headers,
            f"getTasks {index} sent a correlation header the contract does not declare",
        )
        suffix = f"&pageSize={PAGE_SIZE}" if page == 0 else f"&pageNumber={page}&pageSize={PAGE_SIZE}"
        require(
            request.get("rawTarget") == f"/v1/tasks?{prefix}{suffix}",
            f"getTasks {index} raw target or query order changed",
        )
        query = request.get("query", {})
        allowed = SENT_QUERY if page else SENT_QUERY - {"pageNumber"}
        require(set(query) == allowed, f"getTasks {index} sent an unset query member")
        require(
            all(len(values) == 1 and values[0] != "" for values in query.values()),
            f"getTasks {index} sent an empty or repeated query value",
        )
        for omitted in UNSET_QUERY:
            require(
                omitted not in query,
                f"getTasks {index} sent unset optional field {omitted}",
            )

    # ---- generate: exact body bytes prove omission rather than an empty value
    for index, (request, key, body) in enumerate(
        zip(generates, [first_key, second_key], ["{}", '{"lookBackWindow":6}'])
    ):
        require(request.get("method") == "POST", f"generate {index} method changed")
        require(
            request.get("rawTarget") == bundles_path,
            f"generate {index} raw target changed",
        )
        require(request.get("rawQuery") == "", f"generate {index} must omit its query")
        require(request.get("query") == {}, f"generate {index} query must be absent")
        require(
            one_header(request, CORRELATION_HEADER) == key,
            f"generate {index} correlation header changed",
        )
        content_type = one_header(request, "content-type")
        require(
            content_type.split(";", 1)[0].strip().casefold() == "application/json",
            f"generate {index} Content-Type must be application/json",
        )
        require(
            request.get("body") == body,
            f"generate {index} body bytes changed; an unset optional member must be "
            "omitted, never sent as null, zero, blank, or an explicit default",
        )
        expected_length = len(body.encode("utf-8"))
        require(
            request.get("bodyLength") == expected_length,
            f"generate {index} body length changed",
        )
        require(
            one_header(request, "content-length") == str(expected_length),
            f"generate {index} Content-Length changed",
        )
    require(
        "lookBackWindow" not in generates[0].get("body", ""),
        "the unset optional body member must be absent from the wire",
    )

    # ---- getTask and the bundle listing: bodyless, queryless, no extra header
    resolves = [
        request for request in requests if request.get("operationId") == "getTask"
    ]
    require(len(resolves) == 3, "getTask request count changed")
    for index, request in enumerate(resolves):
        require(request.get("method") == "GET", f"getTask {index} method changed")
        require(request.get("rawQuery") == "", f"getTask {index} must omit its query")
        require(request.get("bodyLength") == 0, f"getTask {index} must be bodyless")
        headers = request.get("headerValues", {})
        require("content-type" not in headers, f"getTask {index} sent Content-Type")
        require(
            CORRELATION_HEADER not in headers,
            f"getTask {index} sent an undeclared correlation header",
        )
        require(
            request.get("path", "").startswith("/v1/tasks/"),
            f"getTask {index} path template changed",
        )

    listings = [
        request
        for request in requests
        if request.get("operationId") == "getComponentSupportBundles"
    ]
    require(len(listings) == 3, "bundle listing request count changed")
    for index, request in enumerate(listings):
        require(request.get("method") == "GET", f"listing {index} method changed")
        require(
            request.get("rawTarget") == bundles_path,
            f"listing {index} raw target changed",
        )
        require(request.get("rawQuery") == "", f"listing {index} must omit its query")
        require(request.get("bodyLength") == 0, f"listing {index} must be bodyless")
        headers = request.get("headerValues", {})
        require("content-type" not in headers, f"listing {index} sent Content-Type")
        require(
            CORRELATION_HEADER not in headers,
            f"listing {index} sent an undeclared correlation header",
        )


def main() -> int:
    try:
        verify_contract()
        verify_sources()
        verify_package_shape()
        verify_runtime()
        verify_response_validation()
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
