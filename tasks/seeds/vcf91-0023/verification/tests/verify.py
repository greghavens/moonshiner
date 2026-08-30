#!/usr/bin/env python3
"""Protected verifier for the complete, sorted VCF cluster inventory client."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import math
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
    "be706817fe2c8745f313d63f760d6740c02e8f4e673fb5fd6b86c9458fa52192"
)
EXPECTED_SOURCES_SHA256 = (
    "a4c2451b2ba8888ac1a327ae52e9aa159a0e5c1d7d27658c703c67a3f2a283f1"
)
EXPECTED_MOCK_SHA256 = (
    "91a4dd87459fc05bfa0dcf93df67e7e1a22b6b19248085ab0be5ce3519709453"
)
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_ROUTES = [("getClusters", "GET", "/v1/clusters")]
EXPECTED_QUERY_PARAMETERS = [
    "isStretched",
    "isImageBased",
    "domainId",
    "managedObjectReferenceId",
    "name",
    "isDefault",
    "isHciMeshEnabled",
    "pageSize",
    "pageNumber",
    "useCache",
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


def verify_protected_contract() -> None:
    protected_hashes = [
        (CONTRACT_PATH, EXPECTED_CONTRACT_SHA256),
        (SOURCES_PATH, EXPECTED_SOURCES_SHA256),
        (MOCK_PATH, EXPECTED_MOCK_SHA256),
    ]
    for path, expected in protected_hashes:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(
            actual == expected,
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
    routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in contract.get("operations", [])
    ]
    require(
        routes == EXPECTED_ROUTES,
        "contract must contain only the getClusters operation",
    )
    operation = contract["operations"][0]
    require(
        [item.get("name") for item in operation.get("query_parameters", [])]
        == EXPECTED_QUERY_PARAMETERS,
        "getClusters parameters changed from the pinned specification",
    )
    require(
        list(operation.get("responses", {})) == ["200", "400", "500"],
        "getClusters responses changed from the pinned specification",
    )
    require(
        operation["responses"]["200"].get("schema_ref")
        == "#/components/schemas/PageOfCluster",
        "getClusters success schema changed from the pinned specification",
    )
    schemas = contract.get("schemas", {})
    require(
        schemas.get("PageOfCluster", {})
        .get("properties", {})
        .get("elements", {})
        .get("items", {})
        .get("$ref")
        == "#/components/schemas/Cluster",
        "PageOfCluster element schema changed",
    )
    metadata_properties = set(
        schemas.get("PageMetadata", {}).get("properties", {})
    )
    require(
        metadata_properties
        == {"pageNumber", "pageSize", "totalElements", "totalPages"},
        "PageMetadata projection changed",
    )

    source_routes = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in sources.get("operations", [])
    ]
    require(
        source_routes == EXPECTED_ROUTES,
        "official_sources.json must name getClusters exactly once",
    )
    require(
        sources.get("repository", {}).get("commit_sha") == EXPECTED_COMMIT,
        "official_sources.json has the wrong repository commit",
    )
    require(
        sources.get("specification", {}).get("path") == EXPECTED_SPEC_PATH,
        "official_sources.json has the wrong specification path",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_cluster_inventory"
    require(package.is_dir(), "vcf_cluster_inventory package is missing")
    paths = sorted(package.rglob("*.py"))
    require(bool(paths), "vcf_cluster_inventory contains no Python source")
    stdlib = set(sys.stdlib_module_names)
    forbidden = {"http", "requests", "socket", "subprocess"}
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
                    root in stdlib or root == "vcf_cluster_inventory",
                    f"{path.relative_to(ROOT)} imports non-stdlib module "
                    f"{root!r}",
                )
                require(
                    root not in forbidden,
                    f"{path.relative_to(ROOT)} uses forbidden transport helper "
                    f"{root!r}",
                )


def import_package() -> Any:
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.import_module("vcf_cluster_inventory")
    finally:
        sys.path.pop(0)


def verify_local_validation(module: Any) -> None:
    bad_origins = [
        "ftp://127.0.0.1",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/v1",
        "http://127.0.0.1/?query=yes",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:70000",
    ]
    for origin in bad_origins:
        try:
            module.SddcManagerClient(origin, "token")
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid base_url raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure(f"invalid base_url was accepted: {origin}")

    for token in ["", "   "]:
        try:
            module.SddcManagerClient("http://127.0.0.1:1", token)
        except ValueError:
            pass
        except Exception as exc:
            raise VerificationFailure(
                f"invalid access_token raised {type(exc).__name__}"
            ) from exc
        else:
            raise VerificationFailure("a blank access_token was accepted")

    for timeout in [0, -1, math.inf, math.nan, True]:
        try:
            module.SddcManagerClient(
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
            raise VerificationFailure(f"invalid timeout was accepted: {timeout!r}")

    client = module.SddcManagerClient(
        "http://127.0.0.1:1",
        "token",
        timeout=0.1,
    )
    for page_size in [0, -1, True, 1.5, "2", INT32_MAX + 1]:
        try:
            client.list_clusters(page_size=page_size)
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


def cluster_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    name = item.get("name")
    cluster_id = item.get("id")
    canonical = json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        name.casefold() if isinstance(name, str) else "",
        cluster_id if isinstance(cluster_id, str) else "",
        canonical,
    )


def runtime_clusters(marker: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"cluster-z-{marker}",
            "name": "Zulu",
            "status": "ACTIVE",
            "runtimeMarker": f"{marker}-0",
        },
        {
            "id": f"cluster-b-{marker}",
            "name": "alpha",
            "status": "ACTIVE",
            "runtimeMarker": f"{marker}-1",
        },
        {
            "id": f"cluster-a-{marker}",
            "name": "Alpha",
            "status": "UPGRADING",
            "runtimeMarker": f"{marker}-2",
        },
        {
            "status": "CREATING",
            "runtimeMarker": f"{marker}-3",
            "details": {"site": "München"},
        },
        {
            "id": f"cluster-c-{marker}",
            "name": "charlie",
            "status": "ACTIVE",
            "runtimeMarker": f"{marker}-4",
        },
        {
            "id": f"cluster-d-{marker}",
            "name": "Bravo",
            "status": "ACTIVE",
            "runtimeMarker": f"{marker}-5",
            "domain": {"id": f"domain-{marker}", "name": "Management"},
        },
    ]


def start_fixture(
    *,
    access_token: str,
    clusters: list[dict[str, Any]],
    fault: str | None,
    temp_root: Path,
) -> tuple[subprocess.Popen[str], str, Path]:
    scenario_path = temp_root / "scenario.json"
    log_path = temp_root / "requests.jsonl"
    ready_path = temp_root / "ready.txt"
    scenario_path.write_text(
        json.dumps(
            {
                "access_token": access_token,
                "page_size": 2,
                "clusters": clusters,
                "fault": fault,
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
        raise VerificationFailure("cannot read fixture request log") from exc


def verify_complete_sorted_inventory(module: Any) -> None:
    marker = secrets.token_hex(8)
    access_token = "access-" + secrets.token_urlsafe(24)
    clusters = runtime_clusters(marker)
    expected = sorted(clusters, key=cluster_sort_key)
    with tempfile.TemporaryDirectory(prefix="vcf-cluster-inventory-") as raw:
        process, base_url, log_path = start_fixture(
            access_token=access_token,
            clusters=clusters,
            fault=None,
            temp_root=Path(raw),
        )
        try:
            client = module.SddcManagerClient(
                base_url + "/",
                access_token,
                timeout=2.0,
            )
            first = client.list_clusters(page_size=2)
            second = client.list_clusters(page_size=2)
        finally:
            stop_fixture(process)

        require(first == expected, "first inventory is incomplete or not sorted")
        require(second == expected, "flipped inventory is not stably sorted")
        require(first is not second, "each inventory call must return a new list")
        records = read_log(log_path)
        require(len(records) == 6, "complete retrieval must request three pages twice")
        expected_pages = [0, 1, 2, 0, 1, 2]
        for index, (record, page_number) in enumerate(
            zip(records, expected_pages, strict=True)
        ):
            prefix = f"request {index + 1}"
            require(
                record.get("operationId") == "getClusters",
                f"{prefix} used an operation outside the contract",
            )
            require(record.get("method") == "GET", f"{prefix} is not GET")
            require(
                record.get("rawTarget")
                == f"/v1/clusters?pageSize=2&pageNumber={page_number}",
                f"{prefix} has the wrong target",
            )
            require(
                record.get("queryPairs")
                == [["pageSize", "2"], ["pageNumber", str(page_number)]],
                f"{prefix} has unexpected query parameters",
            )
            require(
                record.get("accept") == "application/json",
                f"{prefix} has the wrong Accept header",
            )
            require(
                record.get("authorization") == "Bearer " + access_token,
                f"{prefix} has the wrong bearer authorization",
            )
            require(
                record.get("contentType") is None,
                f"{prefix} sent Content-Type on a bodyless GET",
            )
            require(record.get("body") == "", f"{prefix} sent a request body")
            require(
                record.get("responseStatus") == 200,
                f"{prefix} was rejected by the fixture",
            )


def verify_failure_mapping(module: Any) -> None:
    marker = secrets.token_hex(6)
    access_token = "access-" + secrets.token_urlsafe(18)
    clusters = runtime_clusters(marker)
    cases = [
        ("http_500", module.SddcManagerError, 500),
        ("unexpected_206", module.SddcManagerError, 206),
        ("bad_totals", module.ProtocolError, None),
    ]
    for fault, expected_type, expected_status in cases:
        with tempfile.TemporaryDirectory(prefix=f"vcf-{fault}-") as raw:
            process, base_url, log_path = start_fixture(
                access_token=access_token,
                clusters=clusters,
                fault=fault,
                temp_root=Path(raw),
            )
            try:
                client = module.SddcManagerClient(
                    base_url,
                    access_token,
                    timeout=2.0,
                )
                try:
                    client.list_clusters(page_size=2)
                except expected_type as exc:
                    require(
                        exc.operation_id == "getClusters",
                        f"{fault} exception lost the operationId",
                    )
                    if isinstance(exc, module.SddcManagerError):
                        require(
                            exc.status_code == expected_status,
                            f"{fault} exception has the wrong HTTP status",
                        )
                        require(
                            isinstance(exc.payload, dict),
                            f"{fault} exception lost its JSON payload",
                        )
                    rendered = str(exc) + repr(exc)
                    require(
                        access_token not in rendered,
                        f"{fault} exception exposed the bearer token",
                    )
                    require(
                        "FIXTURE_INTERNAL_ERROR" not in rendered,
                        f"{fault} exception exposed its response payload",
                    )
                except Exception as exc:
                    raise VerificationFailure(
                        f"{fault} raised {type(exc).__name__}"
                    ) from exc
                else:
                    raise VerificationFailure(f"{fault} did not raise")
            finally:
                stop_fixture(process)
            records = read_log(log_path)
            require(
                len(records) == 1,
                f"{fault} should stop after the first invalid response",
            )


def main() -> int:
    try:
        verify_protected_contract()
        verify_stdlib_only()
        module = import_package()
        verify_local_validation(module)
        verify_complete_sorted_inventory(module)
        verify_failure_mapping(module)
    except VerificationFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"FAIL: unexpected verifier error: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("PASS: complete, contract-correct, stable cluster inventory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
