#!/usr/bin/env python3
"""Protected deterministic verifier for the vCenter session-resume client."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote


PROJECT = Path(__file__).resolve().parents[1]
COMMIT_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_BLOB_SHA = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_OPERATIONS = {
    ("Cis.Session_create", "POST", "/session", "/api/session"),
    ("Vcenter.VM_list", "GET", "/vcenter/vm", "/api/vcenter/vm"),
}
EXPECTED_FILTERS = [
    "vms",
    "names",
    "folders",
    "datacenters",
    "hosts",
    "clusters",
    "resource_pools",
    "power_states",
]

# Filled with immutable fixture digests by the seed author.
PROTECTED_HASHES = {
    "docs/contract.json": "3825c7692dfad639aea8ab7fa7ab2ece335d871abcc8a46eea538a203c4757b5",
    "docs/official_sources.json": "e800e8c769789d7bd507577ef82d5edf79a1ddc7845dc917abf3d17fa74c70f8",
    "tests/TestMain.java": "448512d2a3aa0e0c29eaa7f8b4cb9e463ec8b28ce65188dd8828701d953d04cd",
    "tests/mock_vcenter.py": "2f8b38d40685e45b9496168bbeb7cefa49f6925e833391834e2a7b6d180f64bf",
}


def fail(message: str) -> None:
    raise SystemExit("VERIFY FAILED: " + message)


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
    specification = sources["specification"]
    if (
        derived["repository_commit_sha"] != COMMIT_SHA
        or sources["repository"]["commit_sha"] != COMMIT_SHA
    ):
        fail("official source commit is not pinned")
    if derived["spec_path"] != SPEC_PATH or specification["path"] != SPEC_PATH:
        fail("official specification path changed")
    if (
        derived["spec_blob_sha"] != SPEC_BLOB_SHA
        or specification["blob_sha"] != SPEC_BLOB_SHA
    ):
        fail("official specification blob is not pinned")
    if (
        derived["openapi"] != "3.0.3"
        or derived["info_version"] != "9.1.0.0"
        or derived["server_url_template"] != "https://{host}/api"
    ):
        fail("OpenAPI identity changed")

    projected = {
        (
            item["operationId"],
            item["method"],
            item["path"],
            item["wire_path"],
        )
        for item in contract["operations"]
    }
    recorded = {
        (
            item["operationId"],
            item["method"],
            item["path"],
            item["wire_path"],
        )
        for item in sources["operations"]
    }
    if projected != EXPECTED_OPERATIONS or recorded != EXPECTED_OPERATIONS:
        fail("operationId source record changed")
    for operation in sources["operations"]:
        if (
            operation["repository_commit_sha"] != COMMIT_SHA
            or operation["spec_path"] != SPEC_PATH
        ):
            fail("operation provenance is not recorded at commit granularity")

    by_id = {item["operationId"]: item for item in contract["operations"]}
    session = by_id["Cis.Session_create"]
    if (
        session["requestBody"] is not None
        or session["security"] != ["basic_auth"]
        or session["responses"]["201"]["schema"]
        != {"type": "string", "format": "password"}
    ):
        fail("session-create projection changed")
    listing = by_id["Vcenter.VM_list"]
    parameters = listing["parameters"]
    if [item["name"] for item in parameters] != EXPECTED_FILTERS:
        fail("VM filter order changed")
    for item in parameters:
        if (
            item["in"] != "query"
            or item["required"] is not False
            or item["style"] != "form"
            or item["explode"] is not True
            or item["schema"]["type"] != "array"
        ):
            fail(f"VM filter projection changed: {item['name']}")
    if listing["security"] != ["api_key_auth"]:
        fail("VM-list authentication projection changed")

    summary = contract["schemas"]["Vcenter.VM.Summary"]
    if summary["required"] != ["name", "power_state", "vm"]:
        fail("VM summary required fields changed")
    if list(summary["properties"]) != [
        "vm",
        "name",
        "power_state",
        "cpu_count",
        "memory_size_mib",
    ]:
        fail("VM summary property projection changed")
    schemes = contract["securitySchemes"]
    if schemes["api_key_auth"] != {
        "type": "apiKey",
        "name": "vmware-api-session-id",
        "in": "header",
    }:
        fail("vCenter session-header contract changed")


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


def one_header(entry: dict, name: str) -> str | None:
    values = entry["headers"].get(name)
    if values is None:
        return None
    if len(values) != 1:
        fail(f"{entry['operationId']} sent {len(values)} {name} headers")
    return values[0]


def check_wire_log(log_path: Path, server_info: dict) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    if len(entries) != 6:
        fail(f"expected six requests for refresh/resume, got {len(entries)}")
    if [entry["sequence"] for entry in entries] != list(range(1, 7)):
        fail("request sequence is not deterministic")

    expected_ids = [
        "Cis.Session_create",
        "Vcenter.VM_list",
        "Vcenter.VM_list",
        "Cis.Session_create",
        "Vcenter.VM_list",
        "Vcenter.VM_list",
    ]
    if [entry["operationId"] for entry in entries] != expected_ids:
        fail("operation sequence restarted, skipped work, or used an unnamed route")

    first_session, first_list, expired, replacement_session, retry, final = entries
    expected_basic = "Basic " + base64.b64encode(
        (
            server_info["username"] + ":" + server_info["password"]
        ).encode("utf-8")
    ).decode("ascii")

    for entry in (first_session, replacement_session):
        if (
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
            entry["body"],
        ) != ("POST", "/api/session", "/api/session", "", ""):
            fail(f"wrong Cis.Session_create wire shape: {entry}")
        if one_header(entry, "accept") != "application/json":
            fail("session creation must send Accept: application/json")
        if one_header(entry, "authorization") != expected_basic:
            fail("session creation Basic credentials differ from UTF-8 contract")
        if "vmware-api-session-id" in entry["headers"]:
            fail("session creation must omit vmware-api-session-id")
        if "content-type" in entry["headers"]:
            fail("bodyless session creation must omit Content-Type")
        if "transfer-encoding" in entry["headers"]:
            fail("bodyless session creation must omit Transfer-Encoding")

    list_entries = [first_list, expired, retry, final]
    datacenter_indexes = [0, 1, 1, 2]
    expected_tokens = [
        server_info["initial_token"],
        server_info["initial_token"],
        server_info["replacement_token"],
        server_info["replacement_token"],
    ]
    for position, (entry, dc_index, token) in enumerate(
        zip(list_entries, datacenter_indexes, expected_tokens, strict=True),
        start=1,
    ):
        datacenter = server_info["datacenter_ids"][dc_index]
        encoded = quote(datacenter, safe="-._~", encoding="utf-8", errors="strict")
        target = "/api/vcenter/vm?datacenters=" + encoded
        if (
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
            entry["body"],
        ) != (
            "GET",
            target,
            "/api/vcenter/vm",
            "datacenters=" + encoded,
            "",
        ):
            fail(f"wrong Vcenter.VM_list request {position}: {entry}")
        if parse_qsl(entry["query"], keep_blank_values=True) != [
            ("datacenters", datacenter)
        ]:
            fail("VM list sent an unset optional filter or malformed explode query")
        for optional in set(EXPECTED_FILTERS) - {"datacenters"}:
            if optional + "=" in entry["query"]:
                fail(f"unset optional filter was serialized: {optional}")
        if one_header(entry, "accept") != "application/json":
            fail("VM list must send Accept: application/json")
        if one_header(entry, "vmware-api-session-id") != token:
            fail("VM list used the wrong session generation")
        if "authorization" in entry["headers"]:
            fail("VM list must omit Authorization")
        if "content-type" in entry["headers"]:
            fail("bodyless VM list must omit Content-Type")
        if "transfer-encoding" in entry["headers"]:
            fail("bodyless VM list must omit Transfer-Encoding")

    if expired["target"] != retry["target"]:
        fail("the interrupted VM-list target was not retried byte-for-byte")
    if first_list["target"] == retry["target"]:
        fail("the completed first datacenter was restarted after refresh")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0120-") as temporary:
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
                str(PROJECT / "VcenterVmClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=12,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("Java sources did not compile")

        request_log = temp / "requests.jsonl"
        port_file = temp / "port.json"
        mock = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(PROJECT / "tests/mock_vcenter.py"),
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
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{server_info['port']}/",
                    server_info["username"],
                    server_info["password"],
                    *server_info["datacenter_ids"],
                    *server_info["vm_ids"],
                    *server_info["vm_names"],
                ],
                text=True,
                capture_output=True,
                timeout=15,
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

    print("PASS: spec-derived vCenter session refresh client")


if __name__ == "__main__":
    main()
