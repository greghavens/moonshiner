#!/usr/bin/env python3
"""Protected verifier for complete, deterministic vCenter role pagination."""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import math
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "mock_vcenter.py"
EXPECTED_CONTRACT_SHA256 = (
    "c3cb3ea37fd6fa12a3715cae7e8decd9ec0f5d036375edde1b448f78989806b6"
)
EXPECTED_SOURCES_SHA256 = (
    "ba76cc6b337af4785a76377ccfe949dcbd2933ef7c63267269763fcf1fe8865d"
)
EXPECTED_MOCK_SHA256 = (
    "b36d4be881984582512ed7b22d594693d5082b56de6d4834288bf5e72c70447f"
)
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = (
    "specifications/vsphere/openapi/automation/vcenter.yaml"
)
EXPECTED_OPERATION = "Vcenter.Authorization.Roles_list"
EXPECTED_ROUTES = [
    (
        EXPECTED_OPERATION,
        "GET",
        "/api/vcenter/authorization/roles",
    )
]
EXPECTED_SOURCE_PARAMETERS = ["filter", "names", "privileges", "iterate"]
EXPECTED_QUERY_FIELDS = [
    "is_system",
    "names",
    "privileges",
    "page_size",
    "marker",
]
INT64_MAX = 2**63 - 1


class VerificationFailure(RuntimeError):
    """A deterministic verifier assertion failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(
            f"cannot read protected {path.relative_to(ROOT)}"
        ) from exc


def verify_protected_contract() -> None:
    protected_hashes = [
        (CONTRACT_PATH, EXPECTED_CONTRACT_SHA256),
        (SOURCES_PATH, EXPECTED_SOURCES_SHA256),
        (MOCK_PATH, EXPECTED_MOCK_SHA256),
    ]
    for path, expected in protected_hashes:
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise VerificationFailure(
                f"cannot read protected {path.relative_to(ROOT)}"
            ) from exc
        require(
            actual == expected,
            f"{path.relative_to(ROOT)} does not match the protected fixture",
        )

    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract.get("source", {})
    require(
        source.get("commitSha") == EXPECTED_COMMIT,
        "contract repository commit is not pinned correctly",
    )
    require(
        source.get("specPath") == EXPECTED_SPEC_PATH,
        "contract specification path is not pinned correctly",
    )
    require(
        source.get("openapi") == "3.0.3"
        and source.get("apiVersion") == "9.1.0.0",
        "contract does not identify the VCF 9.1 OpenAPI specification",
    )
    routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in contract.get("operations", [])
    ]
    require(
        routes == EXPECTED_ROUTES,
        "contract must contain only Vcenter.Authorization.Roles_list",
    )
    operation = contract["operations"][0]
    require(
        operation.get("specPathItem") == "/vcenter/authorization/roles",
        "contract path item changed from the pinned specification",
    )
    require(
        [item.get("name") for item in operation.get("parameters", [])]
        == EXPECTED_SOURCE_PARAMETERS,
        "source parameter projection changed from the pinned specification",
    )
    require(
        [
            item.get("name")
            for item in operation.get("effectiveQueryFields", [])
        ]
        == EXPECTED_QUERY_FIELDS,
        "effective query fields changed from the pinned specification",
    )
    require(
        operation.get("querySerializationOrder") == EXPECTED_QUERY_FIELDS,
        "contract query serialization order changed",
    )
    require(
        operation.get("requestBody") is False,
        "roles-list contract unexpectedly permits a request body",
    )
    require(
        list(operation.get("responses", {})) == [
            "200",
            "400",
            "401",
            "500",
        ],
        "roles-list response projection changed",
    )
    require(
        operation["responses"]["200"].get("schema")
        == "Vcenter.Authorization.Roles.ListResult",
        "roles-list success schema changed",
    )
    require(
        contract.get("securitySchemes", {})
        .get("api_key_auth", {})
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter API-key security projection changed",
    )
    schemas = contract.get("schemas", {})
    require(
        schemas.get("Vcenter.Authorization.Roles.ListResult", {}).get(
            "required"
        )
        == ["items"],
        "roles list-result required fields changed",
    )
    require(
        schemas.get("Vcenter.Authorization.Roles.ListItem", {}).get("required")
        == ["role", "info"],
        "role list-item required fields changed",
    )
    require(
        schemas.get("Vcenter.Authorization.Roles.Info", {}).get("required")
        == ["name", "description", "privileges", "system"],
        "role info required fields changed",
    )

    require(
        sources.get("repositoryCommitSha") == EXPECTED_COMMIT,
        "official_sources.json has the wrong repository commit",
    )
    require(
        sources.get("specPath") == EXPECTED_SPEC_PATH,
        "official_sources.json has the wrong specification path",
    )
    require(
        sources.get("operationIds") == [EXPECTED_OPERATION],
        "official_sources.json must name the exact operationId once",
    )
    source_routes = [
        (
            item.get("operationId"),
            item.get("method"),
            "/api" + str(item.get("path")),
        )
        for item in sources.get("operations", [])
    ]
    require(
        source_routes == EXPECTED_ROUTES,
        "official_sources.json operation provenance changed",
    )
    for item in sources["operations"]:
        require(
            item.get("repositoryCommitSha") == EXPECTED_COMMIT
            and item.get("specPath") == EXPECTED_SPEC_PATH,
            "each official source operation must repeat its immutable source",
        )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_vcenter_roles"
    require(package.is_dir(), "vcf_vcenter_roles package is missing")
    paths = sorted(package.rglob("*.py"))
    require(bool(paths), "vcf_vcenter_roles contains no Python source")
    stdlib = set(sys.stdlib_module_names)
    for path in paths:
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise VerificationFailure(
                f"cannot parse {path.relative_to(ROOT)}"
            ) from exc
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
            ):
                roots = [node.module.split(".", 1)[0]]
            for root in roots:
                require(
                    root in stdlib or root == "vcf_vcenter_roles",
                    f"{path.relative_to(ROOT)} imports non-stdlib module "
                    f"{root!r}",
                )


def import_package() -> Any:
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("vcf_vcenter_roles")
    finally:
        sys.path.pop(0)


def verify_public_api(module: Any) -> None:
    require(
        tuple(module.__all__)
        == ("ProtocolError", "VcenterError", "VcenterRoleClient"),
        "package public exports changed",
    )
    client_signature = inspect.signature(module.VcenterRoleClient)
    require(
        list(client_signature.parameters) == [
            "base_url",
            "session_token",
            "timeout",
        ],
        "VcenterRoleClient constructor signature changed",
    )
    list_signature = inspect.signature(module.VcenterRoleClient.list_roles)
    require(
        list(list_signature.parameters) == ["self", "page_size"],
        "list_roles signature changed",
    )


def verify_local_validation(module: Any) -> None:
    bad_origins: list[Any] = [
        "",
        123,
        "ftp://127.0.0.1",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1/?query=yes",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:70000",
    ]
    for origin in bad_origins:
        try:
            module.VcenterRoleClient(origin, "token")
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid base_url raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(f"invalid base_url was accepted: {origin!r}")

    for token in ["", "   ", None]:
        try:
            module.VcenterRoleClient("http://127.0.0.1:1", token)
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid session_token raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure("a blank session_token was accepted")

    for timeout in [0, -1, math.inf, math.nan, True, "1"]:
        try:
            module.VcenterRoleClient(
                "http://127.0.0.1:1",
                "token",
                timeout=timeout,
            )
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid timeout raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(
                f"invalid timeout was accepted: {timeout!r}"
            )

    client = module.VcenterRoleClient(
        "http://127.0.0.1:1",
        "token",
        timeout=0.1,
    )
    for page_size in [0, -1, True, 1.5, "2", INT64_MAX + 1]:
        try:
            client.list_roles(page_size=page_size)
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid page_size raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(
                f"invalid page_size was accepted: {page_size!r}"
            )


def role_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        item["info"]["name"],
        item["role"],
        json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def runtime_roles(marker: str) -> list[dict[str, Any]]:
    return [
        {
            "role": f"role-z-{marker}",
            "info": {
                "name": "Zulu",
                "description": "last by name",
                "privileges": ["VirtualMachine.Config.Settings"],
                "system": False,
            },
            "runtime": {"sequence": 0},
        },
        {
            "role": f"role-shared-{marker}",
            "info": {
                "name": "Alpha",
                "description": "tie z",
                "privileges": ["System.Read"],
                "system": True,
            },
            "runtime": {"sequence": 1, "site": "München"},
        },
        {
            "role": f"role-b-{marker}",
            "info": {
                "name": "Bravo",
                "description": "middle",
                "privileges": ["Datastore.Browse", "System.View"],
                "system": False,
            },
            "runtime": {"sequence": 2},
        },
        {
            "role": f"role-shared-{marker}",
            "info": {
                "name": "Alpha",
                "description": "tie a",
                "privileges": ["System.Read"],
                "system": True,
            },
            "runtime": {"sequence": 0, "site": "Zürich"},
        },
        {
            "role": f"role-a-{marker}",
            "info": {
                "name": "alpha",
                "description": "lowercase sorts separately",
                "privileges": [],
                "system": False,
            },
            "runtime": {"sequence": 4},
        },
        {
            "role": f"role-u-{marker}",
            "info": {
                "name": "Ångström",
                "description": "non-ASCII order",
                "privileges": ["System.Anonymous"],
                "system": False,
            },
            "runtime": {"sequence": 5},
        },
    ]


def start_fixture(
    *,
    session_token: str,
    page_size: int,
    roles: list[dict[str, Any]],
    markers: list[str],
    fault: str | None,
    error_secret: str,
    temp_root: Path,
) -> tuple[subprocess.Popen[str], str, Path]:
    scenario_path = temp_root / "scenario.json"
    log_path = temp_root / "requests.jsonl"
    ready_path = temp_root / "ready.txt"
    scenario_path.write_text(
        json.dumps(
            {
                "session_token": session_token,
                "page_size": page_size,
                "roles": roles,
                "markers": markers,
                "fault": fault,
                "error_secret": error_secret,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
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
            "--log",
            str(log_path),
            "--ready",
            str(ready_path),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ready_path.exists():
            base_url = ready_path.read_text(encoding="utf-8").strip()
            require(
                base_url.startswith("http://127.0.0.1:"),
                "fixture did not bind to loopback",
            )
            return process, base_url, log_path
        if process.poll() is not None:
            _stdout, stderr = process.communicate(timeout=1)
            raise VerificationFailure(
                "loopback fixture exited before readiness: " + stderr[-300:]
            )
        time.sleep(0.01)
    process.terminate()
    process.wait(timeout=2)
    raise VerificationFailure("loopback fixture did not become ready")


def stop_fixture(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure("cannot read the fixture request log") from exc


def verify_wire_entry(
    entry: dict[str, Any],
    *,
    target: str,
    session_token: str,
    query_pairs: list[list[str]],
    response_status: int,
) -> None:
    require(
        entry.get("operationId") == EXPECTED_OPERATION,
        "request reached an operation outside the pinned contract",
    )
    require(entry.get("method") == "GET", "collection request was not GET")
    require(
        entry.get("rawTarget") == target,
        "collection request raw target or query escaping is incorrect",
    )
    require(
        entry.get("queryPairs") == query_pairs,
        "collection request query fields or order are incorrect",
    )
    require(
        entry.get("accept") == "application/json",
        "collection request Accept header is incorrect",
    )
    require(
        entry.get("sessionToken") == session_token,
        "collection request session header is incorrect",
    )
    require(
        entry.get("contentType") is None
        and entry.get("contentLength") is None
        and entry.get("body") == "",
        "GET request must omit Content-Type, Content-Length, and body",
    )
    require(
        entry.get("responseStatus") == response_status,
        "fixture response status was unexpected",
    )
    forbidden = {
        "filter",
        "is_system",
        "names",
        "privileges",
        "iterate",
    }
    require(
        not forbidden.intersection(name for name, _value in query_pairs),
        "an unset optional filter was transmitted",
    )


def verify_complete_collection(module: Any) -> None:
    runtime_marker = secrets.token_hex(8)
    token = "session-" + secrets.token_urlsafe(24)
    error_secret = "error-" + secrets.token_hex(12)
    roles = runtime_roles(runtime_marker)
    markers = [
        f"{runtime_marker} next /?&+=雪",
        f"{runtime_marker}:final #[two]",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf-role-main-") as raw_temp:
        process, base_url, log_path = start_fixture(
            session_token=token,
            page_size=2,
            roles=roles,
            markers=markers,
            fault=None,
            error_secret=error_secret,
            temp_root=Path(raw_temp),
        )
        try:
            client = module.VcenterRoleClient(
                base_url + "/",
                token,
                timeout=2.0,
            )
            first = client.list_roles(page_size=2)
            second = client.list_roles(page_size=2)
        finally:
            stop_fixture(process)

        expected = sorted(roles, key=role_sort_key)
        require(first == expected, "first call did not return every sorted role")
        require(second == expected, "role order changed between collection calls")
        require(first is not second, "each collection call must return a new list")

        entries = read_log(log_path)
        base_target = "/api/vcenter/authorization/roles?page_size=2"
        page_targets = [
            base_target,
            base_target + "&marker=" + quote(markers[0], safe=""),
            base_target + "&marker=" + quote(markers[1], safe=""),
        ]
        expected_entries: list[tuple[str, list[list[str]]]] = [
            (
                page_targets[0],
                [["page_size", "2"]],
            ),
            (
                page_targets[1],
                [["page_size", "2"], ["marker", markers[0]]],
            ),
            (
                page_targets[2],
                [["page_size", "2"], ["marker", markers[1]]],
            ),
        ] * 2
        require(
            len(entries) == len(expected_entries),
            "collection did not request exactly every page twice",
        )
        for entry, (target, pairs) in zip(
            entries,
            expected_entries,
            strict=True,
        ):
            verify_wire_entry(
                entry,
                target=target,
                session_token=token,
                query_pairs=pairs,
                response_status=200,
            )


def verify_default_page_size(module: Any) -> None:
    runtime_marker = secrets.token_hex(8)
    token = "default-" + secrets.token_urlsafe(18)
    roles = runtime_roles(runtime_marker)[:1]
    with tempfile.TemporaryDirectory(prefix="vcf-role-default-") as raw_temp:
        process, base_url, log_path = start_fixture(
            session_token=token,
            page_size=200,
            roles=roles,
            markers=[],
            fault=None,
            error_secret="unused-" + secrets.token_hex(8),
            temp_root=Path(raw_temp),
        )
        try:
            result = module.VcenterRoleClient(
                base_url,
                token,
                timeout=2.0,
            ).list_roles()
        finally:
            stop_fixture(process)
        require(result == roles, "default-sized one-page collection changed")
        entries = read_log(log_path)
        require(len(entries) == 1, "default collection made extra requests")
        verify_wire_entry(
            entries[0],
            target="/api/vcenter/authorization/roles?page_size=200",
            session_token=token,
            query_pairs=[["page_size", "200"]],
            response_status=200,
        )


def verify_failure_surfaces(module: Any) -> None:
    marker = secrets.token_hex(8)
    roles = runtime_roles(marker)[:3]

    token = "http-" + secrets.token_urlsafe(18)
    error_secret = "payload-" + secrets.token_hex(14)
    with tempfile.TemporaryDirectory(prefix="vcf-role-http-") as raw_temp:
        process, base_url, log_path = start_fixture(
            session_token=token,
            page_size=2,
            roles=roles,
            markers=["next-" + marker],
            fault="http_500",
            error_secret=error_secret,
            temp_root=Path(raw_temp),
        )
        try:
            try:
                module.VcenterRoleClient(
                    base_url,
                    token,
                    timeout=2.0,
                ).list_roles(page_size=2)
            except module.VcenterError as exc:
                require(
                    exc.operation_id == EXPECTED_OPERATION,
                    "HTTP error lost its operationId",
                )
                require(exc.status_code == 500, "HTTP error lost its status")
                require(
                    isinstance(exc.payload, dict)
                    and exc.payload.get("secret") == error_secret,
                    "HTTP error did not retain the decoded JSON payload",
                )
                visible = str(exc) + repr(exc)
                require(
                    token not in visible and error_secret not in visible,
                    "HTTP error text exposed a credential or response payload",
                )
            except Exception as exc:
                raise VerificationFailure(
                    f"HTTP 500 raised {type(exc).__name__}"
                ) from exc
            else:
                raise VerificationFailure("HTTP 500 did not raise VcenterError")
        finally:
            stop_fixture(process)
        entries = read_log(log_path)
        require(len(entries) == 1, "HTTP failure triggered extra requests")
        verify_wire_entry(
            entries[0],
            target="/api/vcenter/authorization/roles?page_size=2",
            session_token=token,
            query_pairs=[["page_size", "2"]],
            response_status=500,
        )

    token = "status-" + secrets.token_urlsafe(18)
    with tempfile.TemporaryDirectory(prefix="vcf-role-status-") as raw_temp:
        process, base_url, _log_path = start_fixture(
            session_token=token,
            page_size=2,
            roles=roles,
            markers=["next-" + marker],
            fault="unexpected_204",
            error_secret="unused-" + marker,
            temp_root=Path(raw_temp),
        )
        try:
            try:
                module.VcenterRoleClient(
                    base_url,
                    token,
                    timeout=2.0,
                ).list_roles(page_size=2)
            except module.VcenterError as exc:
                require(
                    exc.operation_id == EXPECTED_OPERATION
                    and exc.status_code == 204,
                    "non-200 success status was not preserved",
                )
            except Exception as exc:
                raise VerificationFailure(
                    f"HTTP 204 raised {type(exc).__name__}"
                ) from exc
            else:
                raise VerificationFailure("HTTP 204 was accepted as success")
        finally:
            stop_fixture(process)

    token = "repeat-" + secrets.token_urlsafe(18)
    repeated = "repeat /" + marker
    with tempfile.TemporaryDirectory(prefix="vcf-role-repeat-") as raw_temp:
        process, base_url, log_path = start_fixture(
            session_token=token,
            page_size=2,
            roles=roles,
            markers=[repeated],
            fault="repeated_marker",
            error_secret="unused-" + marker,
            temp_root=Path(raw_temp),
        )
        try:
            try:
                module.VcenterRoleClient(
                    base_url,
                    token,
                    timeout=2.0,
                ).list_roles(page_size=2)
            except module.ProtocolError as exc:
                require(
                    exc.operation_id == EXPECTED_OPERATION,
                    "protocol error lost its operationId",
                )
            except Exception as exc:
                raise VerificationFailure(
                    f"repeated marker raised {type(exc).__name__}"
                ) from exc
            else:
                raise VerificationFailure(
                    "repeated marker returned a partial collection"
                )
        finally:
            stop_fixture(process)
        require(
            len(read_log(log_path)) == 2,
            "repeated-marker case did not stop at the invalid response",
        )

    token = "missing-" + secrets.token_urlsafe(18)
    with tempfile.TemporaryDirectory(prefix="vcf-role-missing-") as raw_temp:
        process, base_url, log_path = start_fixture(
            session_token=token,
            page_size=2,
            roles=roles,
            markers=["next-" + marker],
            fault="missing_items",
            error_secret="unused-" + marker,
            temp_root=Path(raw_temp),
        )
        try:
            try:
                module.VcenterRoleClient(
                    base_url,
                    token,
                    timeout=2.0,
                ).list_roles(page_size=2)
            except module.ProtocolError as exc:
                require(
                    exc.operation_id == EXPECTED_OPERATION,
                    "missing-items error lost its operationId",
                )
            except Exception as exc:
                raise VerificationFailure(
                    f"missing items raised {type(exc).__name__}"
                ) from exc
            else:
                raise VerificationFailure(
                    "missing items returned a partial collection"
                )
        finally:
            stop_fixture(process)
        require(
            len(read_log(log_path)) == 1,
            "missing-items case requested another page",
        )

    token = "malformed-" + secrets.token_urlsafe(18)
    malformed = [
        {
            "role": "",
            "info": {
                "name": "Broken",
                "description": "blank role",
                "privileges": ["System.Read"],
                "system": False,
            },
        }
    ]
    with tempfile.TemporaryDirectory(prefix="vcf-role-malformed-") as raw_temp:
        process, base_url, _log_path = start_fixture(
            session_token=token,
            page_size=2,
            roles=malformed,
            markers=[],
            fault=None,
            error_secret="unused-" + marker,
            temp_root=Path(raw_temp),
        )
        try:
            try:
                module.VcenterRoleClient(
                    base_url,
                    token,
                    timeout=2.0,
                ).list_roles(page_size=2)
            except module.ProtocolError:
                pass
            except Exception as exc:
                raise VerificationFailure(
                    f"malformed role raised {type(exc).__name__}"
                ) from exc
            else:
                raise VerificationFailure("malformed role item was accepted")
        finally:
            stop_fixture(process)

    transport_token = "transport-" + secrets.token_urlsafe(18)
    try:
        module.VcenterRoleClient(
            "http://127.0.0.1:1",
            transport_token,
            timeout=0.1,
        ).list_roles(page_size=2)
    except module.VcenterError as exc:
        require(
            exc.operation_id == EXPECTED_OPERATION,
            "transport error lost its operationId",
        )
        require(exc.status_code is None, "transport error has an HTTP status")
        visible = str(exc) + repr(exc)
        require(
            transport_token not in visible
            and "refused" not in visible.lower()
            and "urlopen error" not in visible.lower(),
            "transport error text exposed a credential or low-level exception",
        )
    except Exception as exc:
        raise VerificationFailure(
            f"transport failure raised {type(exc).__name__}"
        ) from exc
    else:
        raise VerificationFailure("transport failure did not raise VcenterError")


def main() -> int:
    try:
        verify_protected_contract()
        verify_stdlib_only()
        module = import_package()
        verify_public_api(module)
        verify_local_validation(module)
        verify_complete_collection(module)
        verify_default_page_size(module)
        verify_failure_surfaces(module)
    except VerificationFailure as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"verification failed with unexpected {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
