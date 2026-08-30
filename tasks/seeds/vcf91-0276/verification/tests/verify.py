#!/usr/bin/env python3
"""Deterministic protected verifier for VcfOpsAlertHarvestClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_BLOB = "a56a00c504d4156aa765339c47d585d15db68768"
BASE_PATH = "/suite-api"
EXPECTED_OPERATIONS = [
    ("acquireToken", "POST", "/api/auth/token/acquire"),
    ("getResources", "GET", "/api/resources"),
    ("queryAlert", "POST", "/api/alerts/query"),
    ("releaseToken", "POST", "/api/auth/token/release"),
]
PROTECTED_HASHES = {
    "docs/contract.json": "43dc3ffefeee803ca8c75d31d6065c20a581a0def7c1f23ff18a191a9fc5021a",
    "docs/official_sources.json": "16406c6baf665bbbc54f64d6b617a7305d684736524828037e8f5f12917f636f",
    "tests/TestMain.java": "ad806f7e77b70c455e5dc9ba4aea81f033c336700c71bfab45c43c6a7ef390d5",
    "tests/mock_server.py": "69ddd31ab84bb27f860250e6e63a55965a324bd23657ca6883bf27e7501bab1d",
}
ACQUIRE = BASE_PATH + "/api/auth/token/acquire"
RESOURCES = BASE_PATH + "/api/resources"
ALERTS = BASE_PATH + "/api/alerts/query"
RELEASE = BASE_PATH + "/api/auth/token/release"
UNUSED_ALERT_FILTERS = {
    "alertControlState",
    "alertDefinitionId",
    "alertId",
    "alertImpact",
    "alertName",
    "alertStatus",
    "alertTypeSubtype",
    "cancelTimeRange",
    "compositeOperator",
    "extractOwnerName",
    "groupId",
    "groupingCondition",
    "includeChildrenResources",
    "resourceKind",
    "startTimeRange",
    "updateTimeRange",
    "userId",
    "userName",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_files() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_contract_metadata() -> None:
    contract = json.loads((PROJECT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (PROJECT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    derived = contract["derived_from"]
    if (
        derived["repository_commit_sha"] != COMMIT
        or sources["repository"]["commit_sha"] != COMMIT
        or derived["spec_path"] != SPEC_PATH
        or sources["specification"]["path"] != SPEC_PATH
        or derived["spec_blob_sha"] != SPEC_BLOB
        or sources["specification"]["spec_blob_sha"] != SPEC_BLOB
    ):
        fail("official specification provenance changed")
    if (
        derived["info_version"] != "9.1.0.0"
        or derived["info_title"] != "VMware Cloud Foundation Operations API"
        or sources["repository"]["license"] != "Apache-2.0"
    ):
        fail("product version or license provenance changed")
    if contract["base_path"] != BASE_PATH or sources["specification"]["base_path"] != BASE_PATH:
        fail("specification base path changed")

    projected = [
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    ]
    recorded = [
        (item["operationId"], item["method"], item["path"])
        for item in sources["operations"]
    ]
    if projected != EXPECTED_OPERATIONS or recorded != EXPECTED_OPERATIONS:
        fail("operationId projection changed")
    for operation in sources["operations"]:
        if (
            operation["repository_commit_sha"] != COMMIT
            or operation["spec_path"] != SPEC_PATH
        ):
            fail("operation provenance is not recorded at commit granularity")

    authentication = contract["authentication"]
    if (
        authentication["header"] != "Authorization"
        or authentication["in"] != "header"
        or authentication["value_format"] != "OpsToken {token}"
    ):
        fail("authentication projection changed")

    schemas = contract["schemas"]
    credentials = schemas["username-password"]
    if credentials["required"] != ["password", "username"]:
        fail("username-password required fields changed")
    if credentials["property_order"] != ["authSource", "password", "username"]:
        fail("username-password property order changed")
    if schemas["auth-token"]["required"] != ["token", "validity"]:
        fail("auth-token required fields changed")
    if schemas["alert-query"]["required"]:
        fail("every alert-query filter is optional in the pinned schema")
    order = schemas["alert-query"]["property_order"]
    if order[:3] != ["activeOnly", "alertControlState", "alertCriticality"]:
        fail("alert-query property order changed")
    if "resource-query" not in order or "resourceQuery" in order:
        fail("the nested resource query property is spelled resource-query")
    if not UNUSED_ALERT_FILTERS.issubset(set(order)):
        fail("alert-query optional projection changed")
    if "resourceId" not in schemas["resource-query"]["property_order"]:
        fail("resource-query projection changed")

    parameters = {
        parameter["name"]: parameter
        for operation in contract["operations"]
        if operation["operationId"] == "getResources"
        for parameter in operation["parameters"]
    }
    if parameters["page"]["schema"]["default"] != 0:
        fail("getResources page default changed")
    if parameters["pageSize"]["schema"]["default"] != 1000:
        fail("getResources pageSize default changed")
    if any(parameter["required"] for parameter in parameters.values()):
        fail("every getResources query parameter is optional in the pinned spec")
    if "resourceKind" not in parameters:
        fail("getResources resourceKind projection changed")


def wait_for_server(port_file: Path, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited during startup\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
            return json.loads(port_file.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("mock did not publish its loopback port")


def start_mock(temp: Path, name: str, mode: str) -> tuple[subprocess.Popen, dict, Path]:
    log = temp / f"{name}.jsonl"
    port_file = temp / f"{name}-port.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT / "tests/mock_server.py"),
            "--contract",
            str(PROJECT / "docs/contract.json"),
            "--log",
            str(log),
            "--port-file",
            str(port_file),
            "--mode",
            mode,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    info = wait_for_server(port_file, process)
    return process, info, log


def stop_mock(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def run_test_main(classes: Path, mode: str, info: dict) -> None:
    result = subprocess.run(
        [
            "java",
            "-cp",
            str(classes),
            "TestMain",
            mode,
            f"http://127.0.0.1:{info['port']}",
            info["username"],
            info["password"],
            info["resource_kind"],
            ",".join(info["monitored_ids"]),
            ",".join(info["monitored_names"]),
            ",".join(info["matching_alert_ids"]),
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        fail(f"TestMain failed in {mode} mode")
    if result.stdout.strip() != "SUCCESSFUL":
        fail(f"unexpected TestMain output in {mode} mode: {result.stdout!r}")


def read_log(log: Path) -> list[dict]:
    if not log.exists():
        fail("the mock recorded no requests")
    entries = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if [entry["sequence"] for entry in entries] != list(range(1, len(entries) + 1)):
        fail("request sequence is not deterministic")
    for entry in entries:
        if entry["operationId"] is None:
            fail(f"a request left the focused contract: {entry['method']} {entry['target']}")
    return entries


def query_of(entry: dict) -> dict:
    parameters: dict[str, list[str]] = {}
    for name, value in entry["queryParams"]:
        parameters.setdefault(name, []).append(value)
    for name, values in parameters.items():
        if len(values) != 1:
            fail(f"query parameter {name} was repeated")
        if values[0] == "":
            fail(f"query parameter {name} was sent with an empty value")
    return {name: values[0] for name, values in parameters.items()}


def check_common(entry: dict, index: int) -> None:
    headers = entry["headers"]
    if entry["headerValues"].get("accept") != ["application/json"]:
        fail(f"request {index} did not send exactly Accept: application/json")
    if "transfer-encoding" in headers:
        fail(f"request {index} used transfer encoding")
    if entry["body"]:
        if headers.get("content-type") != "application/json":
            fail(f"request {index} sent a body without Content-Type: application/json")
        if headers.get("content-length") != str(entry["bodyLength"]):
            fail(f"request {index} sent a wrong Content-Length")
    else:
        if "content-type" in headers:
            fail(f"request {index} sent Content-Type without a body")
        if entry["bodyLength"] != 0:
            fail(f"request {index} reported a body length without a body")
    if "?" in entry["target"] and entry["query"] == "":
        fail(f"request {index} sent a bare query delimiter")


def check_acquire_body(entry: dict, info: dict, index: int) -> None:
    expected = "{%s:%s,%s:%s}" % (
        json.dumps("password"),
        json.dumps(info["password"]),
        json.dumps("username"),
        json.dumps(info["username"]),
    )
    if entry["body"] != expected:
        fail(f"request {index} sent a wrong username-password body: {entry['body']!r}")
    document = json.loads(entry["body"])
    if "authSource" in document:
        fail("an unset authSource must be omitted, not sent")
    if set(document) != {"password", "username"}:
        fail(f"request {index} sent unexpected credential properties")


def check_harvest_log(entries: list[dict], info: dict) -> None:
    if len(entries) != 9:
        fail(
            "expected nine requests: acquire, two resource pages, three alert "
            f"pages with one expiry and one refreshed replay, and one release; got {len(entries)}"
        )
    page_size = "2"
    expected = [
        ("acquireToken", "POST", ACQUIRE, {}, 200),
        (
            "getResources",
            "GET",
            RESOURCES,
            {"resourceKind": info["resource_kind"], "page": "0", "pageSize": page_size},
            200,
        ),
        (
            "getResources",
            "GET",
            RESOURCES,
            {"resourceKind": info["resource_kind"], "page": "1", "pageSize": page_size},
            200,
        ),
        ("queryAlert", "POST", ALERTS, {"page": "0", "pageSize": page_size}, 200),
        ("queryAlert", "POST", ALERTS, {"page": "1", "pageSize": page_size}, 401),
        ("acquireToken", "POST", ACQUIRE, {}, 200),
        ("queryAlert", "POST", ALERTS, {"page": "1", "pageSize": page_size}, 200),
        ("queryAlert", "POST", ALERTS, {"page": "2", "pageSize": page_size}, 200),
        ("releaseToken", "POST", RELEASE, {}, 200),
    ]
    for index, (entry, wanted) in enumerate(zip(entries, expected, strict=True), start=1):
        operation, method, path, parameters, status = wanted
        actual = (entry["operationId"], entry["method"], entry["path"], entry["status"])
        if actual != (operation, method, path, status):
            fail(f"request {index} is {actual!r}, expected {wanted[:3] + (status,)!r}")
        if query_of(entry) != parameters:
            fail(f"request {index} sent the query {query_of(entry)!r}, expected {parameters!r}")
        check_common(entry, index)

    first = entries[0]["issuedToken"]
    second = entries[5]["issuedToken"]
    if not first or not second or first == second:
        fail("the client did not obtain a second, distinct session token")
    for index in (2, 3, 4, 5):
        if entries[index - 1]["headers"].get("authorization") != "OpsToken " + first:
            fail(f"request {index} did not carry the first token in the pinned scheme")
    for index in (7, 8, 9):
        if entries[index - 1]["headers"].get("authorization") != "OpsToken " + second:
            fail(f"request {index} did not carry the refreshed token")
    if "authorization" in entries[0]["headers"] or "authorization" in entries[5]["headers"]:
        fail("acquireToken must not present a session token")

    check_acquire_body(entries[0], info, 1)
    check_acquire_body(entries[5], info, 6)
    if entries[0]["body"] != entries[5]["body"]:
        fail("the refresh sent different credentials than the initial acquisition")

    expected_alert_body = (
        '{"activeOnly":true,"alertCriticality":["CRITICAL","IMMEDIATE"],'
        '"resource-query":{"resourceId":['
        + ",".join(json.dumps(identifier) for identifier in info["monitored_ids"])
        + "]}}"
    )
    for index in (4, 5, 7, 8):
        body = entries[index - 1]["body"]
        if body != expected_alert_body:
            fail(f"request {index} sent a wrong alert-query body: {body!r}")
        document = json.loads(body)
        if set(document) != {"activeOnly", "alertCriticality", "resource-query"}:
            fail(f"request {index} sent unexpected alert-query properties")
        if UNUSED_ALERT_FILTERS.intersection(document):
            fail("unset alert-query filters must be omitted rather than sent empty")
        if set(document["resource-query"]) != {"resourceId"}:
            fail("unset resource-query filters must be omitted rather than sent empty")
        if "null" in body or ':""' in body or ":[]" in body or ":{}" in body:
            fail("unset optional values appeared as JSON placeholders")

    if entries[4]["body"] != entries[6]["body"] or entries[4]["target"] != entries[6]["target"]:
        fail("the refreshed retry did not replay the expired request unchanged")
    if entries[4]["tokenIndex"] != 1 or entries[6]["tokenIndex"] != 2:
        fail("the expired page was not replayed with the refreshed token")

    for entry in entries[1:5] + entries[6:]:
        if entry["operationId"] in ("getResources", "queryAlert") and not entry["body"]:
            if entry["method"] == "POST":
                fail("queryAlert must send its alert-query body")
    if any(entry["body"] for entry in (entries[1], entries[2], entries[8])):
        fail("getResources and releaseToken must not send a request body")


def check_expired_log(entries: list[dict], info: dict) -> None:
    if len(entries) != 4:
        fail(
            "a permanently expired token must cost exactly one acquisition, one "
            f"failed page, one refresh, and one replay; got {len(entries)} requests"
        )
    expected = [
        ("acquireToken", "POST", ACQUIRE, 200),
        ("getResources", "GET", RESOURCES, 401),
        ("acquireToken", "POST", ACQUIRE, 200),
        ("getResources", "GET", RESOURCES, 401),
    ]
    for index, (entry, wanted) in enumerate(zip(entries, expected, strict=True), start=1):
        actual = (entry["operationId"], entry["method"], entry["path"], entry["status"])
        if actual != wanted:
            fail(f"expiry-run request {index} is {actual!r}, expected {wanted!r}")
        check_common(entry, index)
    for index in (2, 4):
        if query_of(entries[index - 1]) != {
            "resourceKind": info["resource_kind"],
            "page": "0",
            "pageSize": "2",
        }:
            fail("the expiry run did not replay the first resource page unchanged")
    check_acquire_body(entries[0], info, 1)
    check_acquire_body(entries[2], info, 3)


def check_empty_log(entries: list[dict], info: dict) -> None:
    if len(entries) != 3:
        fail(
            "an empty inventory must cost exactly one acquisition, one resource "
            f"page, and one release; got {len(entries)} requests"
        )
    expected = [
        ("acquireToken", "POST", ACQUIRE),
        ("getResources", "GET", RESOURCES),
        ("releaseToken", "POST", RELEASE),
    ]
    for index, (entry, wanted) in enumerate(zip(entries, expected, strict=True), start=1):
        actual = (entry["operationId"], entry["method"], entry["path"])
        if actual != wanted or entry["status"] != 200:
            fail(f"empty-inventory request {index} is {actual!r}, expected {wanted!r}")
        check_common(entry, index)
    if query_of(entries[0]) or query_of(entries[2]):
        fail("empty-inventory token operations sent query parameters")
    if query_of(entries[1]) != {
        "resourceKind": info["resource_kind"],
        "page": "0",
        "pageSize": "2",
    }:
        fail("the empty-inventory run sent a wrong resource query")
    check_acquire_body(entries[0], info, 1)
    token = entries[0]["issuedToken"]
    if not token:
        fail("the empty-inventory run did not acquire a token")
    for index in (2, 3):
        if entries[index - 1]["headers"].get("authorization") != "OpsToken " + token:
            fail(f"empty-inventory request {index} did not carry the acquired token")
    if any(entry["body"] for entry in entries[1:]):
        fail("empty-inventory getResources or releaseToken sent a request body")


def check_route_pinning(temp: Path) -> None:
    process, info, log = start_mock(temp, "pinning", "expire-once")
    try:
        base = f"http://127.0.0.1:{info['port']}"
        for target in (
            BASE_PATH + "/api/auth/currentuser",
            BASE_PATH + "/api/alerts",
            "/api/resources",
        ):
            request = urllib.request.Request(
                base + target, headers={"Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    fail(f"the mock served an unnamed operation: {target} -> {response.status}")
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    fail(f"the mock answered {target} with HTTP {error.code}")
    finally:
        stop_mock(process)

    entries = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(entries) != 3 or any(entry["operationId"] is not None for entry in entries):
        fail("the mock did not log off-contract requests as unnamed operations")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0276-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compiled = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(PROJECT / "VcfOpsAlertHarvestClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=120,
        )
        if compiled.returncode != 0:
            sys.stderr.write(compiled.stdout + compiled.stderr)
            fail("Java sources did not compile")

        process, info, log = start_mock(temp, "harvest", "expire-once")
        try:
            run_test_main(classes, "harvest", info)
        finally:
            stop_mock(process)
        check_harvest_log(read_log(log), info)

        process, expired_info, expired_log = start_mock(temp, "expired", "always-expired")
        try:
            run_test_main(classes, "expired", expired_info)
        finally:
            stop_mock(process)
        check_expired_log(read_log(expired_log), expired_info)

        process, empty_info, empty_log = start_mock(temp, "empty", "empty")
        try:
            run_test_main(classes, "empty", empty_info)
        finally:
            stop_mock(process)
        check_empty_log(read_log(empty_log), empty_info)

        check_route_pinning(temp)

    print("PASS: refreshed the expired VCF Operations token without losing work")


if __name__ == "__main__":
    main()
