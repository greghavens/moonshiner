#!/usr/bin/env python3
"""Deterministic protected verifier for VcfHostRefreshClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "docs/contract.json": "708b9f2ecca3243b5f6fa2c861cf26e57a4c48f21641b68fe1699ef8b7502240",
    "docs/official_sources.json": "34c4db6b43c9246c973fac1b8732f79279c3820a68ccb364a255953105dee2d3",
    "tests/TestMain.java": "8f6d7fdba7e2801395289b9056a121e493c540822d86afa5b089c4bec7713b2c",
    "tests/mock_server.py": "90907eeb8b5367bfef8367dcd0ff856c8d51656a9b3c4f294382f54e8e593a36",
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
    source = json.loads(
        (PROJECT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
    spec_path = "specifications/sddc-manager/sddc-manager-openapi.json"
    expected_operations = {
        ("updateHosts", "PATCH", "/v1/hosts"),
        ("getTask", "GET", "/v1/tasks/{id}"),
        ("getHosts", "GET", "/v1/hosts"),
    }

    if contract["contract_format"] != "focused-openapi-projection-v1":
        fail("contract projection format changed")
    if (
        contract["derived_from"]["repository_commit_sha"] != sha
        or source["repository"]["commit_sha"] != sha
    ):
        fail("official source commit is not pinned")
    if (
        contract["derived_from"]["spec_path"] != spec_path
        or source["specification"]["path"] != spec_path
    ):
        fail("official specification path changed")
    if (
        contract["derived_from"]["info_version"] != "9.1.0.0"
        or source["specification"]["info_version"] != "9.1.0.0"
    ):
        fail("contract is not the VCF 9.1 SDDC Manager specification")

    projected = {
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    }
    recorded = {
        (item["operationId"], item["method"], item["path"])
        for item in source["operations"]
    }
    if projected != expected_operations or recorded != expected_operations:
        fail("operationId projection or source record changed")
    for operation in source["operations"]:
        if (
            operation["repository_commit_sha"] != sha
            or operation["spec_path"] != spec_path
        ):
            fail("operation provenance is not recorded at commit granularity")

    update, task, hosts = contract["operations"]
    if update["operationId"] != "updateHosts":
        fail("updateHosts must be the first focused operation")
    if update["request_body"] != {
        "required": True,
        "media_type": "application/json",
        "schema_ref": "#/components/schemas/HostsUpdateSpec",
    }:
        fail("updateHosts request projection changed")
    if update["responses"]["202"]["schema_ref"] != "#/components/schemas/Task":
        fail("updateHosts accepted response projection changed")
    if task["operationId"] != "getTask" or task["parameters"] != [
        {
            "name": "id",
            "in": "path",
            "description": "Task id to retrieve",
            "required": True,
            "schema": {"type": "string"},
        }
    ]:
        fail("getTask path parameter projection changed")
    if task["responses"]["200"]["schema_ref"] != "#/components/schemas/Task":
        fail("getTask success response projection changed")

    expected_query_names = [
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
    ]
    if hosts["operationId"] != "getHosts":
        fail("getHosts must be the final focused operation")
    if [parameter["name"] for parameter in hosts["parameters"]] != expected_query_names:
        fail("getHosts query projection changed")
    if any(
        parameter["in"] != "query" or parameter["required"] is not False
        for parameter in hosts["parameters"]
    ):
        fail("getHosts optional query semantics changed")
    if hosts["responses"]["200"]["schema_ref"] != "#/components/schemas/PageOfHost":
        fail("getHosts response projection changed")

    schemas = contract["schemas"]
    host_ids = schemas["HostsUpdateSpec"]["properties"]["hostIds"]
    if (
        schemas["HostsUpdateSpec"]["required"] != ["hostIds"]
        or host_ids["minItems"] != 1
        or host_ids["maxItems"] != 100
        or schemas["HostsRefreshSpec"]["required"] != ["forceRefresh"]
    ):
        fail("host update schema bounds changed")
    if schemas["Task"]["required"] != [
        "creationTimestamp",
        "id",
        "name",
        "status",
    ]:
        fail("Task required fields changed")
    elements = schemas["PageOfHost"]["properties"]["elements"]
    if (
        elements["type"] != "array"
        or elements["items_ref"] != "#/components/schemas/Host"
    ):
        fail("PageOfHost elements projection changed")


def wait_for_server(port_file: Path, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited during startup\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
            return json.loads(port_file.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("mock did not publish its loopback port")


def read_entries(log_path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def assert_common_headers(entry: dict, access_token: str) -> None:
    headers = entry["headers"]
    if headers.get("accept") != "application/json":
        fail(f"{entry['operationId']} did not send Accept: application/json")
    if headers.get("authorization") != "Bearer " + access_token:
        fail(f"{entry['operationId']} used the wrong bearer")


def assert_update(
    entry: dict,
    server_info: dict,
    *,
    force_refresh,
) -> None:
    if (
        entry["operationId"],
        entry["method"],
        entry["target"],
        entry["path"],
        entry["query"],
        entry["responseStatus"],
    ) != ("updateHosts", "PATCH", "/v1/hosts", "/v1/hosts", "", 202):
        fail(f"updateHosts wire shape changed: {entry}")
    assert_common_headers(entry, server_info["access_token"])
    headers = entry["headers"]
    if headers.get("content-type") != "application/json":
        fail("updateHosts must send Content-Type: application/json")
    if "transfer-encoding" in headers:
        fail("updateHosts must not use transfer encoding")

    expected = {
        "hostIds": [
            server_info["selected_host_ids"][0],
            server_info["selected_host_ids"][1],
            server_info["selected_host_ids"][0],
        ]
    }
    if force_refresh is not None:
        expected["hostsRefreshSpec"] = {"forceRefresh": force_refresh}
    expected_body = json.dumps(
        expected,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if entry["body"] != expected_body:
        fail("updateHosts compact JSON bytes or optional-member semantics changed")
    if json.loads(entry["body"]) != expected:
        fail("updateHosts JSON values changed")
    if headers.get("content-length") != str(len(expected_body.encode("utf-8"))):
        fail("updateHosts Content-Length does not match UTF-8 body bytes")


def assert_get(
    entry: dict,
    server_info: dict,
    operation_id: str,
    target: str,
) -> None:
    if (
        entry["operationId"],
        entry["method"],
        entry["target"],
        entry["query"],
        entry["responseStatus"],
    ) != (operation_id, "GET", target, "", 200):
        fail(f"{operation_id} wire shape changed: {entry}")
    assert_common_headers(entry, server_info["access_token"])
    if entry["body"] != "":
        fail(f"{operation_id} must not send a body")
    for forbidden in ("content-type", "transfer-encoding"):
        if forbidden in entry["headers"]:
            fail(f"{operation_id} must omit {forbidden}")


def check_wire_log(log_path: Path, server_info: dict) -> None:
    entries = read_entries(log_path)
    expected_ids = [
        "updateHosts",
        "getTask",
        "getTask",
        "getTask",
        "getHosts",
    ] * 2
    if len(entries) != 10:
        fail(f"expected ten requests across two workflows, got {len(entries)}")
    if [entry["sequence"] for entry in entries] != list(range(1, 11)):
        fail("request sequence is not deterministic")
    if [entry["operationId"] for entry in entries] != expected_ids:
        fail("client did not submit, poll to terminal, then collect in order")
    if any(entry["operationId"] not in {"updateHosts", "getTask", "getHosts"} for entry in entries):
        fail("client contacted a route outside the pinned contract")

    for workflow in range(2):
        offset = workflow * 5
        assert_update(
            entries[offset],
            server_info,
            force_refresh=None if workflow == 0 else False,
        )
        task_id = server_info["task_ids"][workflow]
        task_target = "/v1/tasks/" + quote(task_id, safe="-._~")
        for poll in range(3):
            entry = entries[offset + 1 + poll]
            assert_get(entry, server_info, "getTask", task_target)
            if entry["path"] != task_target:
                fail("getTask task id was not encoded as one path segment")
            if (
                entry["taskReadNumber"] != poll + 1
                or entry["taskStatus"]
                != ["PENDING", "IN_PROGRESS", "SUCCESSFUL"][poll]
            ):
                fail("getTask polling boundary changed")
        assert_get(entries[offset + 4], server_info, "getHosts", "/v1/hosts")

    host_entries = [entries[4], entries[9]]
    orders = [entry["hostResponseOrder"] for entry in host_entries]
    if [entry["hostReadNumber"] for entry in host_entries] != [1, 2]:
        fail("getHosts response counter changed")
    if orders[0] != list(reversed(orders[1])):
        fail("mock did not flip host element order on every response")
    expected_sorted_order = [
        {"fqdn": host["fqdn"], "id": host["id"]}
        for host in server_info["hosts"]
    ]
    if orders[1] != expected_sorted_order or orders[0] == expected_sorted_order:
        fail("fixture no longer guarantees that unsorted client output fails")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0045-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(PROJECT / "VcfHostRefreshClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("Java sources did not compile")

        request_log = temp / "requests.jsonl"
        port_file = temp / "port.json"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT / "tests/mock_server.py"),
                "--contract",
                str(PROJECT / "docs/contract.json"),
                "--log",
                str(request_log),
                "--port-file",
                str(port_file),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            server_info = wait_for_server(port_file, mock)
            arguments = [
                "java",
                "-cp",
                str(classes),
                "TestMain",
                f"http://127.0.0.1:{server_info['port']}/",
                server_info["access_token"],
                *server_info["selected_host_ids"],
                *server_info["task_ids"],
            ]
            for host in server_info["hosts"]:
                arguments.extend([host["id"], host["fqdn"], host["status"]])
            run_result = subprocess.run(
                arguments,
                text=True,
                capture_output=True,
                timeout=12,
            )
            if run_result.returncode != 0:
                sys.stderr.write(run_result.stdout + run_result.stderr)
                fail("TestMain failed")
            if run_result.stdout.strip() != "SUCCESSFUL":
                fail(f"unexpected TestMain output: {run_result.stdout!r}")
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

        check_wire_log(request_log, server_info)

    print("PASS: terminal host refresh with stable sorted collection")


if __name__ == "__main__":
    main()
