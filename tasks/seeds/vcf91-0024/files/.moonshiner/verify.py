#!/usr/bin/env python3
"""Protected verifier for the VCF user-access reconciliation client."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "mock_sddc_manager.py"
INIT_PATH = ROOT / "vcf_user_access" / "__init__.py"
EXPECTED_CONTRACT_SHA256 = (
    "4cf4a64aef460a949e89dcce419941be0863aa6a55c5ec96c495fb90e2ec7b8a"
)
EXPECTED_SOURCES_SHA256 = (
    "505a4e24aa844d578decca9dff4e0a2903fd59014eac19758e2305851854c0f8"
)
EXPECTED_MOCK_SHA256 = (
    "427229cb7d7aab84de0047234c47a79663247a3e814393b647e06968da45276e"
)
EXPECTED_INIT_SHA256 = (
    "b72c246c0569b7fe02e6ec73ec68fd29b2aae9ccc8aa7b3c316acd870040bd11"
)
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = (
    "specifications/sddc-manager/sddc-manager-openapi.json"
)
EXPECTED_ROUTES = [
    ("getUsers", "GET", "/v1/users"),
    ("addUsers", "POST", "/v1/users"),
]
INT32_MAX = 2**31 - 1


class VerificationFailure(RuntimeError):
    pass


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


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_files() -> None:
    expected_hashes = {
        CONTRACT_PATH: EXPECTED_CONTRACT_SHA256,
        SOURCES_PATH: EXPECTED_SOURCES_SHA256,
        MOCK_PATH: EXPECTED_MOCK_SHA256,
        INIT_PATH: EXPECTED_INIT_SHA256,
    }
    for path, expected in expected_hashes.items():
        require(
            file_sha256(path) == expected,
            f"{path.relative_to(ROOT)} does not match the protected fixture",
        )

    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    derived = contract.get("derived_from", {})
    require(
        derived.get("repository_commit_sha") == EXPECTED_COMMIT,
        "contract repository commit is not pinned correctly",
    )
    require(
        derived.get("spec_path") == EXPECTED_SPEC_PATH,
        "contract specification path is not pinned correctly",
    )
    actual_routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in contract.get("operations", [])
    ]
    source_routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in sources.get("operations", [])
    ]
    require(
        actual_routes == EXPECTED_ROUTES,
        "contract must contain exactly the two task operations",
    )
    require(
        source_routes == EXPECTED_ROUTES,
        "official_sources.json must name every operationId exactly once",
    )
    require(
        sources.get("repository", {}).get("commit_sha") == EXPECTED_COMMIT,
        "official_sources.json has the wrong repository commit",
    )
    require(
        sources.get("specification", {}).get("path")
        == EXPECTED_SPEC_PATH,
        "official_sources.json has the wrong specification path",
    )

    operations = {
        item["operationId"]: item for item in contract["operations"]
    }
    require(
        list(operations["getUsers"]["responses"])
        == ["200", "400", "500", "401"],
        "getUsers responses changed from the pinned projection",
    )
    require(
        list(operations["addUsers"]["responses"])
        == ["201", "400", "500", "401"],
        "addUsers responses changed from the pinned projection",
    )
    require(
        operations["getUsers"]["responses"]["200"]["schema_ref"]
        == "#/components/schemas/PageOfUser",
        "getUsers success schema is not PageOfUser",
    )
    require(
        operations["addUsers"]["request"]["schema"]
        == {
            "type": "array",
            "description": "User data collection",
            "items_ref": "#/components/schemas/User",
        },
        "addUsers request is not the projected User array",
    )
    user_schema = contract.get("schemas", {}).get("User", {})
    require(
        user_schema.get("required") == ["name", "role", "type"],
        "the projected User required fields changed",
    )
    require(
        user_schema.get("properties", {}).get("role", {}).get("$ref")
        == "#/components/schemas/RoleReference",
        "the projected User role reference changed",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_user_access"
    require(package.is_dir(), "vcf_user_access package is missing")
    paths = sorted(package.rglob("*.py"))
    require(
        [path.name for path in paths] == ["__init__.py", "client.py"],
        "vcf_user_access must contain only the supplied Python modules",
    )
    stdlib = set(sys.stdlib_module_names)
    forbidden_stdlib = {"socket", "subprocess"}
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
                    root in stdlib or root == "vcf_user_access",
                    f"{path.relative_to(ROOT)} imports non-stdlib module "
                    f"{root!r}",
                )
                require(
                    root not in forbidden_stdlib,
                    f"{path.relative_to(ROOT)} uses forbidden transport helper "
                    f"{root!r}",
                )


def import_package() -> Any:
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("vcf_user_access")
    finally:
        sys.path.pop(0)


def expect_value_error(action: Any, label: str) -> None:
    try:
        action()
    except ValueError:
        return
    except Exception as exc:
        raise VerificationFailure(
            f"{label} raised {type(exc).__name__}, not ValueError"
        ) from exc
    raise VerificationFailure(f"{label} was accepted")


def verify_local_validation(module: Any) -> None:
    for origin in [
        "ftp://127.0.0.1",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/v1",
        "http://127.0.0.1/?query=yes",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:70000",
    ]:
        expect_value_error(
            lambda origin=origin: module.SddcManagerClient(origin, "token"),
            f"invalid base_url {origin!r}",
        )

    for token in ["", " ", "two words", "tab\ttoken", "line\ntoken"]:
        expect_value_error(
            lambda token=token: module.SddcManagerClient(
                "http://127.0.0.1:1",
                token,
            ),
            f"invalid access token {token!r}",
        )

    for timeout in [0, -1, math.inf, math.nan, True, "1"]:
        expect_value_error(
            lambda timeout=timeout: module.SddcManagerClient(
                "http://127.0.0.1:1",
                "token",
                timeout=timeout,
            ),
            f"invalid timeout {timeout!r}",
        )

    client = module.SddcManagerClient(
        "http://127.0.0.1:1",
        "token",
        timeout=0.01,
    )
    invalid_arguments = [
        ("", "corp.example", "USER", "role-a"),
        (" name", "corp.example", "USER", "role-a"),
        ("name", "", "USER", "role-a"),
        ("name", "corp.example ", "USER", "role-a"),
        ("name", "corp.example", "user", "role-a"),
        ("name", "corp.example", "ADMIN", "role-a"),
        ("name", "corp.example", "USER", ""),
        ("name", "corp.example", "USER", " role-a"),
        (1, "corp.example", "USER", "role-a"),
    ]
    for arguments in invalid_arguments:
        expect_value_error(
            lambda arguments=arguments: client.ensure_user_access(*arguments),
            f"invalid ensure_user_access arguments {arguments!r}",
        )


def wait_for_port(process: subprocess.Popen[str], port_file: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            raise VerificationFailure(
                "loopback fixture exited before publishing its port: "
                + stderr.strip()
            )
        try:
            text = port_file.read_text(encoding="ascii").strip()
            if text:
                return int(text)
        except (OSError, ValueError):
            pass
        time.sleep(0.02)
    raise VerificationFailure("loopback fixture did not publish its port")


def start_fixture(
    temp: Path,
    scenario: dict[str, Any],
) -> tuple[subprocess.Popen[str], str, Path]:
    scenario_path = temp / "scenario.json"
    log_path = temp / "requests.jsonl"
    port_path = temp / "port"
    scenario_path.write_text(
        json.dumps(scenario, ensure_ascii=False),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
            "--port-file",
            str(port_path),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    port = wait_for_port(process, port_path)
    return process, f"http://127.0.0.1:{port}", log_path


def stop_fixture(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        _stdout, stderr = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        _stdout, stderr = process.communicate(timeout=3)
    require(
        process.returncode in {0, -15},
        "loopback fixture failed: " + stderr.strip(),
    )


def read_log(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure("cannot read the fixture request log") from exc


def string_pair(value: Any) -> tuple[str, str]:
    text = value if isinstance(value, str) else ""
    return (text.casefold(), text)


def user_sort_key(user: dict[str, Any]) -> tuple[Any, ...]:
    role = user.get("role")
    role_id = role.get("id") if isinstance(role, dict) else ""
    canonical = json.dumps(
        user,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        *string_pair(user.get("domain")),
        *string_pair(user.get("name")),
        *string_pair(user.get("type")),
        *string_pair(role_id),
        *string_pair(user.get("id")),
        canonical,
    )


def base_scenario(
    *,
    fault: str = "none",
    drop_first_add: bool = True,
) -> dict[str, Any]:
    marker = secrets.token_hex(5)
    target = {
        "name": "svc-écho-" + marker,
        "domain": "corp.example",
        "type": "SERVICE",
        "role": {"id": "role-backup-" + marker},
    }
    return {
        "access_token": secrets.token_urlsafe(24),
        "assigned_id": "user-new-" + marker,
        "creation_timestamp": "2026-07-28T12:34:56Z",
        "drop_first_add": drop_first_add,
        "fault": fault,
        "target": target,
        "users": [
            {
                "id": "user-z-" + marker,
                "name": "Zulu",
                "domain": "west.example",
                "type": "USER",
                "role": {"id": "role-viewer"},
                "creationTimestamp": "2026-01-02T00:00:00Z",
                "fixtureMarker": {"position": 3},
            },
            {
                "id": "user-b-" + marker,
                "name": "alpha",
                "domain": "CORP.example",
                "type": "GROUP",
                "role": {"id": "role-operator"},
                "creationTimestamp": "2026-01-03T00:00:00Z",
            },
            {
                "id": "user-a-" + marker,
                "name": "Alpha",
                "domain": "corp.example",
                "type": "USER",
                "role": {"id": "role-admin"},
                "creationTimestamp": "2026-01-01T00:00:00Z",
            },
        ],
    }


def require_safe_error(
    error: BaseException,
    *,
    module: Any,
    operation_id: str,
    status_code: int | None,
    secrets_to_hide: list[str],
) -> None:
    require(
        isinstance(error, module.SddcManagerError),
        f"{operation_id} did not raise SddcManagerError",
    )
    require(
        getattr(error, "operation_id", None) == operation_id,
        f"{operation_id} error lost its operationId",
    )
    require(
        getattr(error, "status_code", object()) == status_code,
        f"{operation_id} error has the wrong status code",
    )
    rendered = str(error) + repr(error)
    for secret in secrets_to_hide:
        require(secret not in rendered, "exception text exposed sensitive data")


def verify_acceptance(module: Any) -> None:
    with tempfile.TemporaryDirectory(prefix="vcf91-0024-main-") as name:
        temp = Path(name)
        scenario = base_scenario()
        process, base_url, log_path = start_fixture(temp, scenario)
        try:
            client = module.SddcManagerClient(
                base_url + "/",
                scenario["access_token"],
                timeout=2.0,
            )
            first = client.list_users()
            second = client.list_users()
            expected_initial = sorted(
                scenario["users"],
                key=user_sort_key,
            )
            require(
                first == expected_initial and second == expected_initial,
                "list_users did not return intact, deterministic sorted output",
            )

            target = scenario["target"]
            reconciled = client.ensure_user_access(
                target["name"],
                target["domain"],
                target["type"],
                target["role"]["id"],
            )
            created = {
                "id": scenario["assigned_id"],
                **target,
                "creationTimestamp": scenario["creation_timestamp"],
            }
            expected_after = sorted(
                [*scenario["users"], created],
                key=user_sort_key,
            )
            require(
                reconciled == expected_after,
                "ambiguous addUsers outcome was not reconciled correctly",
            )

            retried = client.ensure_user_access(
                target["name"],
                target["domain"],
                target["type"],
                target["role"]["id"],
            )
            require(
                retried == expected_after,
                "caller retry did not return the existing sorted grant",
            )

            try:
                client.ensure_user_access(
                    "alpha",
                    "corp.example",
                    "USER",
                    "role-other",
                )
            except module.AccessConflictError as exc:
                require_safe_error(
                    exc,
                    module=module,
                    operation_id="addUsers",
                    status_code=None,
                    secrets_to_hide=[scenario["access_token"]],
                )
            except Exception as exc:
                raise VerificationFailure(
                    "role conflict raised the wrong exception type"
                ) from exc
            else:
                raise VerificationFailure(
                    "role conflict did not raise AccessConflictError"
                )

            bad_token = secrets.token_urlsafe(19)
            bad_client = module.SddcManagerClient(base_url, bad_token)
            try:
                bad_client.list_users()
            except Exception as exc:
                require_safe_error(
                    exc,
                    module=module,
                    operation_id="getUsers",
                    status_code=400,
                    secrets_to_hide=[
                        bad_token,
                        "BAD_GET_USERS",
                        "getUsers request did not match the contract",
                    ],
                )
                require(
                    isinstance(getattr(exc, "payload", None), dict),
                    "HTTP error did not preserve decoded JSON payload",
                )
            else:
                raise VerificationFailure("HTTP error was treated as success")
        finally:
            stop_fixture(process)

        log = read_log(log_path)
        require(len(log) == 8, "unexpected number of fixture requests")
        require(
            [item.get("operationId") for item in log]
            == [
                "getUsers",
                "getUsers",
                "getUsers",
                "addUsers",
                "getUsers",
                "getUsers",
                "getUsers",
                "getUsers",
            ],
            "operation order does not implement read-before-write reconciliation",
        )
        posts = [item for item in log if item.get("method") == "POST"]
        require(len(posts) == 1, "the grant mutation was duplicated")
        post = posts[0]
        expected_body = json.dumps(
            [scenario["target"]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        require(
            post.get("rawTarget") == "/v1/users"
            and post.get("accept") == "application/json"
            and post.get("contentType") == "application/json"
            and post.get("authorization")
            == "Bearer " + scenario["access_token"]
            and post.get("body") == expected_body
            and post.get("effectApplied") is True
            and post.get("connectionDropped") is True,
            "addUsers wire request or ambiguous-outcome fixture was incorrect",
        )
        for index, entry in enumerate(log):
            if entry.get("method") != "GET":
                continue
            require(
                entry.get("rawTarget") == "/v1/users"
                and entry.get("accept") == "application/json"
                and entry.get("contentType") is None
                and entry.get("body") == "",
                f"getUsers request {index} violated the wire contract",
            )

        served_orders = [
            entry["servedElementIds"]
            for entry in log
            if "servedElementIds" in entry
        ]
        require(
            len(served_orders) == 6
            and served_orders[0] == list(reversed(served_orders[1])),
            "the mock did not alternate collection response order",
        )


def verify_successful_add(module: Any) -> None:
    with tempfile.TemporaryDirectory(prefix="vcf91-0024-created-") as name:
        temp = Path(name)
        scenario = base_scenario(drop_first_add=False)
        process, base_url, log_path = start_fixture(temp, scenario)
        try:
            client = module.SddcManagerClient(
                base_url,
                scenario["access_token"],
            )
            target = scenario["target"]
            created = client.ensure_user_access(
                target["name"],
                target["domain"],
                target["type"],
                target["role"]["id"],
            )
            expected_user = {
                "id": scenario["assigned_id"],
                **target,
                "creationTimestamp": scenario["creation_timestamp"],
            }
            expected = sorted(
                [*scenario["users"], expected_user],
                key=user_sort_key,
            )
            require(
                created == expected,
                "HTTP 201 addUsers output was not validated and sorted",
            )
            repeated = client.ensure_user_access(
                target["name"],
                target["domain"],
                target["type"],
                target["role"]["id"],
            )
            require(
                repeated == expected,
                "successful addUsers was not safe on caller retry",
            )
        finally:
            stop_fixture(process)
        log = read_log(log_path)
        require(
            [(item.get("method"), item.get("status")) for item in log]
            == [("GET", 200), ("POST", 201), ("GET", 200)],
            "successful addUsers flow did not mutate exactly once",
        )


def verify_faults(module: Any) -> None:
    cases = [
        ("bad_page", module.ProtocolError, 200),
        ("get_201", module.SddcManagerError, 201),
    ]
    for fault, expected_type, status in cases:
        with tempfile.TemporaryDirectory(
            prefix=f"vcf91-0024-{fault}-"
        ) as name:
            temp = Path(name)
            scenario = base_scenario(
                fault=fault,
                drop_first_add=False,
            )
            process, base_url, log_path = start_fixture(temp, scenario)
            try:
                client = module.SddcManagerClient(
                    base_url,
                    scenario["access_token"],
                )
                try:
                    client.list_users()
                except expected_type as exc:
                    require_safe_error(
                        exc,
                        module=module,
                        operation_id="getUsers",
                        status_code=status,
                        secrets_to_hide=[scenario["access_token"]],
                    )
                except Exception as exc:
                    raise VerificationFailure(
                        f"{fault} raised {type(exc).__name__}"
                    ) from exc
                else:
                    raise VerificationFailure(f"{fault} was accepted")
            finally:
                stop_fixture(process)
            require(
                len(read_log(log_path)) == 1,
                f"{fault} caused an unexpected retry",
            )

    with tempfile.TemporaryDirectory(prefix="vcf91-0024-add400-") as name:
        temp = Path(name)
        scenario = base_scenario(
            fault="add_400",
            drop_first_add=False,
        )
        process, base_url, log_path = start_fixture(temp, scenario)
        try:
            client = module.SddcManagerClient(
                base_url,
                scenario["access_token"],
            )
            target = scenario["target"]
            try:
                client.ensure_user_access(
                    target["name"],
                    target["domain"],
                    target["type"],
                    target["role"]["id"],
                )
            except Exception as exc:
                require_safe_error(
                    exc,
                    module=module,
                    operation_id="addUsers",
                    status_code=400,
                    secrets_to_hide=[
                        scenario["access_token"],
                        "ADD_REJECTED",
                        "The requested grant was rejected",
                    ],
                )
            else:
                raise VerificationFailure("addUsers HTTP 400 was accepted")
        finally:
            stop_fixture(process)
        log = read_log(log_path)
        require(
            [(item.get("method"), item.get("status")) for item in log]
            == [("GET", 200), ("POST", 400)],
            "HTTP 4xx addUsers failure was retried or reconciled",
        )


def main() -> int:
    try:
        verify_protected_files()
        verify_stdlib_only()
        module = import_package()
        require(
            set(module.__all__)
            == {
                "AccessConflictError",
                "ProtocolError",
                "SddcManagerClient",
                "SddcManagerError",
            },
            "public package exports changed",
        )
        verify_local_validation(module)
        verify_acceptance(module)
        verify_successful_add(module)
        verify_faults(module)
    except VerificationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"FAIL: verifier crashed with {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("PASS: VCF user-access reconciliation is contract-correct and safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
