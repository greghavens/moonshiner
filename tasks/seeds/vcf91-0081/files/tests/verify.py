#!/usr/bin/env python3
"""Protected deterministic verifier for the single-file Java client."""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
CLIENT_PATH = ROOT / "NsxPolicyClient.java"
HARNESS_PATH = ROOT / "tests" / "TestMain.java"
MOCK_PATH = ROOT / "tests" / "mock_nsx.py"

SPEC_REPOSITORY = "https://github.com/vmware/vcf-api-specs"
SPEC_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_BLOB = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
OPERATION_ID = "ListAllInfraSegments"
LIST_PATH = "/policy/api/v1/infra/segments"


def fail(message: str) -> None:
    raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    source = contract.get("source")
    expected_source = {
        "repository": SPEC_REPOSITORY,
        "commit": SPEC_COMMIT,
        "blob_sha": SPEC_BLOB,
        "path": SPEC_PATH,
        "license": "Apache-2.0",
    }
    if source != expected_source:
        fail(f"unexpected contract source provenance: {source!r}")
    if contract.get("swagger") != "2.0":
        fail("contract must remain an OpenAPI 2.0 extraction")
    if contract.get("info") != {
        "title": "NSX Policy API",
        "version": "9.1.0.0",
    }:
        fail(f"unexpected contract info: {contract.get('info')!r}")
    if contract.get("basePath") != "/policy/api/v1":
        fail(f"unexpected basePath: {contract.get('basePath')!r}")
    if contract.get("security") != {
        "name": "BasicAuth",
        "type": "basic",
    }:
        fail(f"unexpected security extraction: {contract.get('security')!r}")

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        fail("contract must name exactly one operation")
    operation = operations[0]
    if (
        operation.get("operationId") != OPERATION_ID
        or operation.get("method") != "GET"
        or operation.get("path") != "/infra/segments"
        or operation.get("produces") != ["application/json"]
    ):
        fail(f"unexpected operation extraction: {operation!r}")

    parameters = operation.get("parameters")
    expected_parameters = [
        ("cursor", "string", None, None, None),
        ("include_mark_for_delete_objects", "boolean", False, None, None),
        ("included_fields", "string", None, None, None),
        ("page_size", "integer", 1000, 0, 1000),
        ("segment_type", "string", None, None, None),
        ("sort_ascending", "boolean", None, None, None),
        ("sort_by", "string", None, None, None),
    ]
    actual_parameters = []
    for parameter in parameters:
        if parameter.get("in") != "query" or parameter.get("required") is not False:
            fail(f"unexpected parameter location/required flag: {parameter!r}")
        actual_parameters.append(
            (
                parameter.get("name"),
                parameter.get("type"),
                parameter.get("default"),
                parameter.get("minimum"),
                parameter.get("maximum"),
            )
        )
    if actual_parameters != expected_parameters:
        fail(f"unexpected query contract: {actual_parameters!r}")
    segment_type = parameters[4]
    if segment_type.get("enum") != ["DVPortgroup", "ALL"]:
        fail(f"unexpected segment_type enum: {segment_type!r}")

    response = operation.get("responses", {}).get("200", {})
    if (
        response.get("schema") != "SegmentListResult"
        or response.get("required") != ["results"]
        or response.get("properties", {}).get("cursor", {}).get("type")
        != "string"
        or response.get("properties", {}).get("results", {}).get("items")
        != "Segment"
    ):
        fail(f"unexpected collection response extraction: {response!r}")

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    if (
        sources.get("repository") != SPEC_REPOSITORY
        or sources.get("repository_commit_sha") != SPEC_COMMIT
        or sources.get("spec_path") != SPEC_PATH
        or sources.get("spec_blob_sha") != SPEC_BLOB
        or sources.get("license") != "Apache-2.0"
        or sources.get("operationIds") != [OPERATION_ID]
        or sources.get("operations")
        != [
            {
                "operationId": OPERATION_ID,
                "method": "GET",
                "path": LIST_PATH,
            }
        ]
    ):
        fail(f"official_sources.json does not pin the contract: {sources!r}")


def compile_client(build_dir: Path) -> None:
    production_java = [
        path
        for path in ROOT.rglob("*.java")
        if "tests" not in path.relative_to(ROOT).parts
    ]
    if production_java != [CLIENT_PATH]:
        fail(
            "the production client must remain exactly one Java source file: "
            + repr([str(path.relative_to(ROOT)) for path in production_java])
        )

    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        fail("the verifier requires a Java 17+ JDK")
    completed = subprocess.run(
        [
            javac,
            "--release",
            "17",
            "-encoding",
            "UTF-8",
            "-d",
            str(build_dir),
            str(CLIENT_PATH),
            str(HARNESS_PATH),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
    )
    if completed.returncode != 0:
        fail(
            "javac failed:\n"
            + completed.stdout
            + completed.stderr
        )


@contextlib.contextmanager
def running_mock(
    scenario: str, temporary_root: Path
) -> Iterator[tuple[str, Path]]:
    scenario_dir = temporary_root / scenario
    scenario_dir.mkdir()
    log_path = scenario_dir / "requests.jsonl"
    ready_path = scenario_dir / "ready.txt"
    process = subprocess.Popen(
        [
            sys.executable,
            str(MOCK_PATH),
            "--contract",
            str(CONTRACT_PATH),
            "--scenario",
            scenario,
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
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                fail(
                    f"mock exited during startup for {scenario}:\n"
                    + stdout
                    + stderr
                )
            if time.monotonic() >= deadline:
                fail(f"mock did not become ready for {scenario}")
            time.sleep(0.02)
        yield ready_path.read_text(encoding="utf-8"), log_path
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=3)


def read_requests(log_path: Path) -> list[dict[str, object]]:
    if not log_path.exists():
        return []
    requests = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line:
            requests.append(json.loads(line))
    return requests


def run_harness(
    build_dir: Path,
    base_url: str,
    scenario: str,
) -> None:
    java = shutil.which("java")
    assert java is not None
    completed = subprocess.run(
        [java, "-cp", str(build_dir), "TestMain", base_url, scenario],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        fail(
            f"TestMain failed for {scenario}:\n"
            + completed.stdout
            + completed.stderr
        )
    if completed.stdout.strip() != f"OK {scenario}":
        fail(
            f"unexpected TestMain output for {scenario}: "
            f"{completed.stdout!r}"
        )


def assert_wire(
    requests: list[dict[str, object]], expected_queries: list[str]
) -> None:
    if len(requests) != len(expected_queries):
        fail(
            f"got {len(requests)} requests, want {len(expected_queries)}: "
            f"{requests!r}"
        )
    expected_auth = (
        "Basic "
        + base64.b64encode(b"admin:secret").decode("ascii")
    )
    for index, (request, query) in enumerate(
        zip(requests, expected_queries, strict=True)
    ):
        if request.get("operationId") != OPERATION_ID:
            fail(
                f"request {index} operationId: "
                f"{request.get('operationId')!r}"
            )
        if request.get("method") != "GET":
            fail(f"request {index} method: {request.get('method')!r}")
        if request.get("path") != LIST_PATH:
            fail(f"request {index} path: {request.get('path')!r}")
        if request.get("raw_query") != query:
            fail(
                f"request {index} query\n"
                f" got: {request.get('raw_query')!r}\n"
                f"want: {query!r}"
            )
        expected_target = LIST_PATH + ("?" + query if query else "")
        if request.get("target") != expected_target:
            fail(
                f"request {index} target: {request.get('target')!r}, "
                f"want {expected_target!r}"
            )
        headers = request.get("headers")
        if not isinstance(headers, dict):
            fail(f"request {index} headers are not an object")
        if headers.get("accept") != ["application/json"]:
            fail(f"request {index} Accept: {headers.get('accept')!r}")
        if headers.get("authorization") != [expected_auth]:
            fail(
                f"request {index} Authorization: "
                f"{headers.get('authorization')!r}"
            )
        if "content-type" in headers:
            fail(
                f"request {index} unexpectedly sent Content-Type: "
                f"{headers.get('content-type')!r}"
            )
        if request.get("body") != "":
            fail(f"request {index} body is not empty: {request.get('body')!r}")


def verify_route_allow_list(temporary_root: Path) -> None:
    with running_mock("route-guard", temporary_root) as (base_url, log_path):
        split = urlsplit(base_url)
        connection = http.client.HTTPConnection(
            split.hostname, split.port, timeout=3
        )
        connection.request("GET", "/policy/api/v1/infra/tier-1s")
        response = connection.getresponse()
        response.read()
        connection.close()
        if response.status != 404:
            fail(
                "contract mock served an operation absent from the contract: "
                f"HTTP {response.status}"
            )
        connection = http.client.HTTPConnection(
            split.hostname, split.port, timeout=3
        )
        connection.request("POST", LIST_PATH)
        response = connection.getresponse()
        response.read()
        connection.close()
        if response.status != 405:
            fail(
                "contract mock served an unlisted method on a known path: "
                f"HTTP {response.status}"
            )
    requests = read_requests(log_path)
    if (
        len(requests) != 2
        or any(request.get("operationId") is not None for request in requests)
        or requests[0].get("path") != "/policy/api/v1/infra/tier-1s"
        or requests[1].get("method") != "POST"
        or requests[1].get("path") != LIST_PATH
    ):
        fail(f"unexpected route-guard request log: {requests!r}")


def main() -> None:
    verify_contract()
    with tempfile.TemporaryDirectory(prefix="vcf91-0081-") as temporary:
        temporary_root = Path(temporary)
        build_dir = temporary_root / "classes"
        build_dir.mkdir()
        compile_client(build_dir)
        verify_route_allow_list(temporary_root)

        unset_queries = [
            "sort_ascending=true&sort_by=display_name",
            "cursor=next+%2B%2F%3D&sort_ascending=true&sort_by=display_name",
            "cursor=empty-page&sort_ascending=true&sort_by=display_name",
        ]
        set_base = (
            "include_mark_for_delete_objects=false"
            "&included_fields=id%2Cdisplay_name%2Cpath"
            "&page_size=2"
            "&segment_type=DVPortgroup"
            "&sort_ascending=true"
            "&sort_by=display_name"
        )
        expected = {
            "unset": unset_queries,
            "set": [set_base, "cursor=page-2&" + set_base],
            "repeated": [
                "sort_ascending=true&sort_by=display_name",
                "cursor=repeat-me&sort_ascending=true&sort_by=display_name",
            ],
            "invalid": [],
            "malformed": [
                "sort_ascending=true&sort_by=display_name"
            ],
            "http-error": [
                "sort_ascending=true&sort_by=display_name"
            ],
        }
        for scenario, expected_queries in expected.items():
            with running_mock(
                scenario, temporary_root
            ) as (base_url, log_path):
                run_harness(build_dir, base_url, scenario)
            assert_wire(read_requests(log_path), expected_queries)

    print("PASS: VCF 9.1 NSX Policy Java contract verified")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
