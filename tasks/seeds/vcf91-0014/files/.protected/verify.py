#!/usr/bin/env python3
"""Protected deterministic verifier for the stdlib SDDC Manager client."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = Path(__file__).with_name("mock_sddc_manager.py")
EXPECTED_CONTRACT_SHA256 = (
    "1710a6e94c65b1ef11db166e823f851d30d7a716d26831ef4c6e3e140533ca91"
)
EXPECTED_SOURCE_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
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
    ("getHosts", "GET", "/v1/hosts"),
]

EXPECTED_INVENTORY = {
    "domains": [
        {
            "id": "domain-b",
            "name": "Bravo-Compute",
            "type": "VI",
            "status": "ACTIVE",
        },
        {
            "id": "domain-a",
            "name": "Management",
            "type": "MANAGEMENT",
            "status": "ACTIVE",
        },
        {
            "id": "domain-c",
            "name": "Analytics",
            "type": "VI",
            "status": "EXPANDING",
        },
    ],
    "hosts": [
        {
            "id": "host-c",
            "fqdn": "esx03.example.test",
            "status": "ASSIGNED",
            "domain": {"id": "domain-b"},
        },
        {
            "id": "host-a",
            "fqdn": "esx01.example.test",
            "status": "ASSIGNED",
            "domain": {"id": "domain-a"},
        },
        {
            "id": "host-d",
            "fqdn": "esx04.example.test",
            "status": "UNASSIGNED_USEABLE",
            "domain": {"id": "domain-c"},
        },
        {
            "id": "host-b",
            "fqdn": "esx02.example.test",
            "status": "ASSIGNED",
            "domain": {"id": "domain-b"},
        },
    ],
}


class VerificationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(f"cannot read protected {path.name}: {exc}") from exc


def verify_protected_contract() -> None:
    contract_bytes = CONTRACT_PATH.read_bytes()
    digest = hashlib.sha256(contract_bytes).hexdigest()
    require(
        digest == EXPECTED_CONTRACT_SHA256,
        "docs/contract.json does not match the protected pinned contract",
    )

    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    require(
        sources.get("repository_commit_sha") == EXPECTED_SOURCE_SHA,
        "official_sources.json has the wrong repository commit",
    )
    require(
        sources.get("spec_path") == EXPECTED_SPEC_PATH,
        "official_sources.json has the wrong specification path",
    )
    expected_ids = [entry[0] for entry in EXPECTED_ROUTES]
    require(
        sources.get("operationIds") == expected_ids,
        "official_sources.json must name every operationId in contract order",
    )
    actual_routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in contract.get("operations", [])
    ]
    require(
        actual_routes == EXPECTED_ROUTES,
        "contract operations do not match the pinned OpenAPI extraction",
    )

    operations = {
        item["operationId"]: item for item in contract["operations"]
    }
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
        "getDomains optional parameters do not match the pinned specification",
    )
    require(
        [item["name"] for item in operations["getHosts"]["query_parameters"]]
        == [
            "pageSize",
            "pageNumber",
            "fqdn",
            "status",
            "domainId",
            "clusterId",
            "networkpoolId",
            "storageType",
            "datastoreName",
            "ipAddressVersionForVmotion",
            "isStandalone",
            "isLifecycleManaged",
            "isVsanWitnessHost",
            "size",
            "page",
        ],
        "getHosts optional parameters do not match the pinned specification",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_sddc"
    require(package.is_dir(), "vcf_sddc package is missing")
    python_files = sorted(package.rglob("*.py"))
    require(bool(python_files), "vcf_sddc contains no Python source")

    stdlib_names = set(sys.stdlib_module_names)
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            raise VerificationFailure(f"cannot parse {path.relative_to(ROOT)}: {exc}") from exc
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            for imported_root in roots:
                require(
                    imported_root in stdlib_names
                    or imported_root == "vcf_sddc",
                    f"{path.relative_to(ROOT)} imports non-stdlib module "
                    f"{imported_root!r}",
                )


def wait_for_port(process: subprocess.Popen[str], port_file: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            raise VerificationFailure(
                f"loopback fixture exited before startup: {stderr.strip()}"
            )
        try:
            value = port_file.read_text(encoding="ascii").strip()
            if value:
                return int(value)
        except (OSError, ValueError):
            pass
        time.sleep(0.02)
    raise VerificationFailure("loopback fixture did not publish its port")


def run_client() -> tuple[dict[str, object], list[dict[str, object]]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0014-") as temp_name:
        temp = Path(temp_name)
        log_path = temp / "requests.jsonl"
        port_path = temp / "port"
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
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
            sys.path.insert(0, str(ROOT))
            try:
                module = importlib.import_module("vcf_sddc")
            finally:
                sys.path.pop(0)
            client = module.SddcManagerClient(
                f"http://127.0.0.1:{port}",
                "svc-inventory",
                "fixture-password",
                timeout=2.0,
            )
            inventory = client.collect_inventory()
        finally:
            process.terminate()
            try:
                _stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate(timeout=5)

        require(
            process.returncode in {-15, 0},
            f"loopback fixture failed: {stderr.strip()}",
        )
        require(log_path.exists(), "loopback fixture did not create its request log")
        try:
            requests = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationFailure(f"cannot read fixture request log: {exc}") from exc
    return inventory, requests


def verify_inventory(inventory: object) -> None:
    require(
        inventory == EXPECTED_INVENTORY,
        "collect_inventory did not preserve every successful page and element",
    )
    require(
        isinstance(inventory, dict)
        and list(inventory) == ["domains", "hosts"],
        "collect_inventory must return domains followed by hosts",
    )


def verify_wire(requests: list[dict[str, object]]) -> None:
    expected = [
        {
            "operationId": "createToken",
            "method": "POST",
            "rawTarget": "/v1/tokens",
            "queryPairs": [],
            "authorization": None,
            "contentType": "application/json",
            "body": '{"username":"svc-inventory","password":"fixture-password"}',
            "responseStatus": 201,
        },
        {
            "operationId": "getDomains",
            "method": "GET",
            "rawTarget": "/v1/domains?pageSize=2",
            "queryPairs": [["pageSize", "2"]],
            "authorization": "Bearer fixture-access-1",
            "contentType": None,
            "body": "",
            "responseStatus": 200,
        },
        {
            "operationId": "getDomains",
            "method": "GET",
            "rawTarget": "/v1/domains?pageSize=2&pageNumber=1",
            "queryPairs": [["pageSize", "2"], ["pageNumber", "1"]],
            "authorization": "Bearer fixture-access-1",
            "contentType": None,
            "body": "",
            "responseStatus": 200,
        },
        {
            "operationId": "getHosts",
            "method": "GET",
            "rawTarget": "/v1/hosts?pageSize=2",
            "queryPairs": [["pageSize", "2"]],
            "authorization": "Bearer fixture-access-1",
            "contentType": None,
            "body": "",
            "responseStatus": 200,
        },
        {
            "operationId": "getHosts",
            "method": "GET",
            "rawTarget": "/v1/hosts?pageSize=2&pageNumber=1",
            "queryPairs": [["pageSize", "2"], ["pageNumber", "1"]],
            "authorization": "Bearer fixture-access-1",
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
            "body": '"fixture-refresh-1"',
            "responseStatus": 200,
        },
        {
            "operationId": "getHosts",
            "method": "GET",
            "rawTarget": "/v1/hosts?pageSize=2&pageNumber=1",
            "queryPairs": [["pageSize", "2"], ["pageNumber", "1"]],
            "authorization": "Bearer fixture-access-2",
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
                f"request {index} has wrong {key}: "
                f"expected {value!r}, got {actual.get(key)!r}",
            )

    query_names = {
        name
        for request in requests
        for name, _value in request.get("queryPairs", [])
    }
    require(
        query_names == {"pageSize", "pageNumber"},
        "unset optional query fields must be omitted rather than sent empty",
    )


def verify_page_size_validation() -> None:
    sys.path.insert(0, str(ROOT))
    try:
        module = importlib.import_module("vcf_sddc")
    finally:
        sys.path.pop(0)
    client = module.SddcManagerClient(
        "http://127.0.0.1:1",
        "unused",
        "unused",
        timeout=0.01,
    )
    try:
        client.collect_inventory(page_size=0)
    except (ValueError, module.SddcManagerError):
        return
    except Exception as exc:
        raise VerificationFailure(
            f"invalid page_size raised the wrong exception: {type(exc).__name__}"
        ) from exc
    raise VerificationFailure("page_size=0 must be rejected before network traffic")


def main() -> int:
    try:
        verify_protected_contract()
        verify_stdlib_only()
        verify_page_size_validation()
        inventory, requests = run_client()
        verify_inventory(inventory)
        verify_wire(requests)
    except VerificationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAIL: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("PASS: resilient SDDC Manager inventory wire contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
