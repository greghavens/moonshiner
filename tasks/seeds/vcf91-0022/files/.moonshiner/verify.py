#!/usr/bin/env python3
"""Protected verifier for the resilient, sorted VCF domain snapshot client."""

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
EXPECTED_CONTRACT_SHA256 = (
    "a8924d45cefd3254345707b346b795d1817012609c76eae47e0b8f1546b44812"
)
EXPECTED_SOURCES_SHA256 = (
    "39a94a0f2493322133c0bcdcf3a58445df3431d5f8a50876332128f7c7551688"
)
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = (
    "specifications/sddc-manager/sddc-manager-openapi.json"
)
EXPECTED_ROUTES = [
    ("createToken", "POST", "/v1/tokens"),
    (
        "refreshAccessToken",
        "PATCH",
        "/v1/tokens/access-token/refresh",
    ),
    ("getDomains", "GET", "/v1/domains"),
]

DOMAINS = [
    {
        "id": "domain-z",
        "name": "Zulu",
        "type": "VI",
        "status": "ACTIVE",
        "details": {"region": "west", "sequence": 6},
    },
    {
        "id": "domain-b",
        "name": "alpha",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": False,
    },
    {
        "id": "domain-a",
        "name": "Alpha",
        "type": "MANAGEMENT",
        "status": "ACTIVE",
        "isManagementSsoDomain": True,
    },
    {
        "id": "domain-c",
        "name": "charlie",
        "type": "VI",
        "status": "EXPANDING",
    },
    {
        "id": "domain-d",
        "name": "Bravo",
        "type": "VI",
        "status": "ACTIVE",
    },
    {
        "id": "domain-e",
        "name": "echo",
        "type": "VI",
        "status": "ACTIVE",
    },
]


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


def verify_protected_contract() -> None:
    require(
        hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
        == EXPECTED_CONTRACT_SHA256,
        "docs/contract.json does not match the protected extraction",
    )
    require(
        hashlib.sha256(SOURCES_PATH.read_bytes()).hexdigest()
        == EXPECTED_SOURCES_SHA256,
        "docs/official_sources.json does not match protected provenance",
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
    require(
        actual_routes == EXPECTED_ROUTES,
        "contract must contain exactly the three task operations",
    )
    source_routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in sources.get("operations", [])
    ]
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
        list(operations["createToken"]["responses"]) == ["201", "400", "500"],
        "createToken responses changed from the pinned projection",
    )
    require(
        operations["refreshAccessToken"]["request"]["schema"]
        == {
            "type": "string",
            "description": "ID of the refresh token",
        },
        "refreshAccessToken request is not the OpenAPI JSON string",
    )
    require(
        operations["refreshAccessToken"]["responses"]["200"]["schema"]
        == {"type": "string"},
        "refreshAccessToken success response is not the OpenAPI JSON string",
    )
    require(
        [item["name"] for item in operations["getDomains"]["query_parameters"]]
        == [
            "type",
            "name",
            "vcFqdn",
            "vcInstanceId",
            "isManagementSsoDomain",
            "pageNumber",
            "pageSize",
            "useCache",
        ],
        "getDomains parameters changed from the pinned specification",
    )
    require(
        operations["getDomains"]["responses"]["200"]["schema_ref"]
        == "#/components/schemas/PageOfDomain",
        "getDomains success schema changed from the pinned specification",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_domain_snapshot"
    require(package.is_dir(), "vcf_domain_snapshot package is missing")
    paths = sorted(package.rglob("*.py"))
    require(bool(paths), "vcf_domain_snapshot contains no Python source")
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
                    root in stdlib or root == "vcf_domain_snapshot",
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
        return importlib.import_module("vcf_domain_snapshot")
    finally:
        sys.path.pop(0)


def verify_local_validation(module: Any) -> None:
    bad_origins = [
        "ftp://127.0.0.1",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/v1",
        "http://127.0.0.1/?query=yes",
        "http://127.0.0.1/#fragment",
    ]
    for origin in bad_origins:
        try:
            module.SddcManagerClient(origin, "user", "password")
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid base_url raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(f"invalid base_url was accepted: {origin}")

    for timeout in [0, -1, math.inf, math.nan, True]:
        try:
            module.SddcManagerClient(
                "http://127.0.0.1:1",
                "user",
                "password",
                timeout=timeout,
            )
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid timeout raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(f"invalid timeout was accepted: {timeout!r}")

    for username, password in [("", "password"), ("user", "")]:
        try:
            module.SddcManagerClient(
                "http://127.0.0.1:1",
                username,
                password,
            )
        except ValueError:
            pass
        else:
            raise VerificationFailure("empty credentials must be rejected")

    client = module.SddcManagerClient(
        "http://127.0.0.1:1",
        "user",
        "password",
        timeout=0.01,
    )
    for page_size in [0, -1, True, 2**31, "2"]:
        try:
            client.list_domains(page_size=page_size)
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid page_size {page_size!r} reached transport or raised "
                f"{type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(
                f"invalid page_size was accepted: {page_size!r}"
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


def run_acceptance(
    module: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0022-") as temp_name:
        temp = Path(temp_name)
        scenario_path = temp / "scenario.json"
        log_path = temp / "requests.jsonl"
        port_path = temp / "port"
        runtime = {
            "username": "inventory-" + secrets.token_hex(6),
            "password": secrets.token_urlsafe(18),
            "first_access_token": secrets.token_urlsafe(24),
            "refresh_token_id": secrets.token_urlsafe(20),
            "second_access_token": secrets.token_urlsafe(24),
        }
        scenario = {
            **runtime,
            "page_size": 2,
            "domains": DOMAINS,
        }
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
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(process, port_path)
            client = module.SddcManagerClient(
                f"http://127.0.0.1:{port}",
                runtime["username"],
                runtime["password"],
                timeout=2.0,
            )
            result = client.list_domains(page_size=2)
        finally:
            process.terminate()
            try:
                _stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate(timeout=5)
        require(
            process.returncode in {-15, 0},
            "loopback fixture failed: " + stderr.strip(),
        )
        require(log_path.exists(), "loopback fixture wrote no request log")
        try:
            requests = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationFailure("cannot read loopback request log") from exc
    return result, requests, runtime


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def expected_sorted_domains() -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
        name = item.get("name")
        identifier = item.get("id")
        return (
            name.casefold() if isinstance(name, str) else "",
            identifier if isinstance(identifier, str) else "",
            canonical(item),
        )

    return sorted(DOMAINS, key=sort_key)


def verify_result(result: Any, requests: list[dict[str, Any]]) -> None:
    require(isinstance(result, list), "list_domains must return a list")
    expected = expected_sorted_domains()
    require(
        result == expected,
        "list_domains must preserve every domain and sort the final collection",
    )

    served = [
        identifier
        for request in requests
        if request.get("operationId") == "getDomains"
        and request.get("responseStatus") == 200
        for identifier in request.get("servedElementIds", [])
    ]
    require(
        served
        == [
            "domain-b",
            "domain-z",
            "domain-a",
            "domain-c",
            "domain-e",
            "domain-d",
        ],
        "loopback fixture did not flip element order on every response",
    )
    require(
        [item["id"] for item in result] != served,
        "client returned response order instead of deterministic sort order",
    )
    require(
        sorted(canonical(item) for item in result)
        == sorted(canonical(item) for item in DOMAINS),
        "client projected, changed, duplicated, or dropped a Domain object",
    )


def verify_wire(
    requests: list[dict[str, Any]],
    runtime: dict[str, str],
) -> None:
    json_body = lambda value: json.dumps(  # noqa: E731
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    expected = [
        {
            "operationId": "createToken",
            "method": "POST",
            "rawTarget": "/v1/tokens",
            "queryPairs": [],
            "authorization": None,
            "contentType": "application/json",
            "body": json_body(
                {
                    "username": runtime["username"],
                    "password": runtime["password"],
                }
            ),
            "responseStatus": 201,
        },
        {
            "operationId": "getDomains",
            "method": "GET",
            "rawTarget": "/v1/domains?pageNumber=0&pageSize=2",
            "queryPairs": [["pageNumber", "0"], ["pageSize", "2"]],
            "authorization": "Bearer " + runtime["first_access_token"],
            "contentType": None,
            "body": "",
            "responseStatus": 200,
        },
        {
            "operationId": "getDomains",
            "method": "GET",
            "rawTarget": "/v1/domains?pageNumber=1&pageSize=2",
            "queryPairs": [["pageNumber", "1"], ["pageSize", "2"]],
            "authorization": "Bearer " + runtime["first_access_token"],
            "contentType": None,
            "body": "",
            "responseStatus": 401,
        },
        {
            "operationId": "refreshAccessToken",
            "method": "PATCH",
            "rawTarget": "/v1/tokens/access-token/refresh",
            "queryPairs": [],
            "authorization": None,
            "contentType": "application/json",
            "body": json_body(runtime["refresh_token_id"]),
            "responseStatus": 200,
        },
        {
            "operationId": "getDomains",
            "method": "GET",
            "rawTarget": "/v1/domains?pageNumber=1&pageSize=2",
            "queryPairs": [["pageNumber", "1"], ["pageSize", "2"]],
            "authorization": "Bearer " + runtime["second_access_token"],
            "contentType": None,
            "body": "",
            "responseStatus": 200,
        },
        {
            "operationId": "getDomains",
            "method": "GET",
            "rawTarget": "/v1/domains?pageNumber=2&pageSize=2",
            "queryPairs": [["pageNumber", "2"], ["pageSize", "2"]],
            "authorization": "Bearer " + runtime["second_access_token"],
            "contentType": None,
            "body": "",
            "responseStatus": 200,
        },
    ]
    require(
        len(requests) == len(expected),
        f"expected exactly {len(expected)} requests, got {len(requests)}",
    )
    for index, (actual, wanted) in enumerate(zip(requests, expected)):
        require(
            actual.get("accept") == "application/json",
            f"request {index} has the wrong Accept header",
        )
        for key, value in wanted.items():
            require(
                actual.get(key) == value,
                f"request {index} has wrong {key}",
            )

    page_zero_requests = [
        request
        for request in requests
        if request.get("rawTarget")
        == "/v1/domains?pageNumber=0&pageSize=2"
    ]
    require(
        len(page_zero_requests) == 1,
        "refresh must not restart pagination or refetch completed page 0",
    )
    require(
        sum(
            request.get("operationId") == "createToken"
            for request in requests
        )
        == 1,
        "createToken must not replace refreshAccessToken",
    )
    require(
        sum(
            request.get("operationId") == "refreshAccessToken"
            for request in requests
        )
        == 1,
        "access token must be refreshed exactly once",
    )
    query_names = {
        name
        for request in requests
        if request.get("operationId") == "getDomains"
        for name, _value in request.get("queryPairs", [])
    }
    require(
        query_names == {"pageNumber", "pageSize"},
        "unset optional getDomains parameters must be absent",
    )


def main() -> int:
    try:
        verify_protected_contract()
        verify_stdlib_only()
        module = import_package()
        verify_local_validation(module)
        result, requests, runtime = run_acceptance(module)
        verify_result(result, requests)
        verify_wire(requests, runtime)
    except VerificationFailure as exc:
        print(f"VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            "VERIFICATION FAILED: unexpected "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
