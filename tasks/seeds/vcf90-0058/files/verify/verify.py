#!/usr/bin/env python3
"""Protected verifier for vcf-rightsizer.

Builds the project, drives the client against the loopback mock and checks
three things:

  1. docs/contract.json and docs/official_sources.json match the vSphere
     Automation API OpenAPI document for vCenter as published at tag 9.0.0.0
     of vmware/vcf-api-specs.
  2. The requests the client actually put on the wire have the exact shape the
     contract implies -- including that optional properties the caller did not
     ask for are absent from the request body rather than present and empty.
  3. The report the client returned describes the partly-applied change
     accurately.

Nothing here contacts a VMware endpoint or any other network host. The only
HTTP traffic is to the mock on 127.0.0.1.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"

# --------------------------------------------------------------------------
# Facts taken from the OpenAPI document. The verifier is offline; these are the
# values a correct derivation must land on.
# --------------------------------------------------------------------------

REPO = "vmware/vcf-api-specs"
SPEC_TAG = "9.0.0.0"
SPEC_VERSION = "9.0.0.0"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_URL = (
    "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
    + SPEC_COMMIT
    + "/"
    + SPEC_PATH
)
SPEC_LICENSE = "Apache-2.0"
BASE_PATH = "/api"
SESSION_HEADER = "vmware-api-session-id"
SESSION_CREATE_OP = "Cis.Session_create"

EXPECTED_OPERATIONS = [
    {
        "operation_id": "Cis.Session_create",
        "method": "POST",
        "path": "/session",
        "query": {},
        "security": ["basic_auth"],
        "success_status": 201,
        "request_body": None,
    },
    {
        "operation_id": "Vcenter.Vm.Power_get",
        "method": "GET",
        "path": "/vcenter/vm/{vm}/power",
        "query": {},
        "security": ["api_key_auth"],
        "success_status": 200,
        "request_body": None,
    },
    {
        "operation_id": "Vcenter.Vm.Power_stop",
        "method": "POST",
        "path": "/vcenter/vm/{vm}/power",
        "query": {"action": "stop"},
        "security": ["api_key_auth"],
        "success_status": 204,
        "request_body": None,
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Cpu_update",
        "method": "PATCH",
        "path": "/vcenter/vm/{vm}/hardware/cpu",
        "query": {},
        "security": ["api_key_auth"],
        "success_status": 204,
        "request_body": {
            "schema": "Vcenter.Vm.Hardware.Cpu.UpdateSpec",
            "required": True,
            "required_properties": [],
            "optional_properties": [
                "cores_per_socket",
                "count",
                "hot_add_enabled",
                "hot_remove_enabled",
            ],
        },
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Memory_update",
        "method": "PATCH",
        "path": "/vcenter/vm/{vm}/hardware/memory",
        "query": {},
        "security": ["api_key_auth"],
        "success_status": 204,
        "request_body": {
            "schema": "Vcenter.Vm.Hardware.Memory.UpdateSpec",
            "required": True,
            "required_properties": [],
            "optional_properties": ["hot_add_enabled", "size_mib"],
        },
    },
    {
        "operation_id": "Vcenter.Vm.Hardware.Disk_create",
        "method": "POST",
        "path": "/vcenter/vm/{vm}/hardware/disk",
        "query": {},
        "security": ["api_key_auth"],
        "success_status": 201,
        "request_body": {
            "schema": "Vcenter.Vm.Hardware.Disk.CreateSpec",
            "required": True,
            "required_properties": [],
            "optional_properties": [
                "backing",
                "ide",
                "new_vmdk",
                "nvme",
                "sata",
                "scsi",
                "type",
            ],
        },
    },
]

EXPECTED_OPERATION_IDS = [op["operation_id"] for op in EXPECTED_OPERATIONS]

# The failure the mock scripts for the last step, verbatim.
DISK_ERROR_TYPE = "RESOURCE_IN_USE"
DISK_ERROR_MESSAGE = (
    "The storage address SCSI(0:0) on SCSI controller 0 is already in use by "
    "an existing virtual disk of virtual machine vm-4021."
)

# Files the task must not change. Digests are checked before anything is run.
PROTECTED = {
    "lib/MiniJson.java": "3dc7f6de59cb9abedab8d96286dc8b735e9cd1f102a80a259211fc9f7356fe7c",
    "mock/VcenterMock.java": "acbf2e025415f7466bb34130d82c55d7acf579d8c7f2575595ee992d2f5a2406",
    "mock/fixtures/inventory.json": "7dcfc2500a2dd223341514f81bc0edf4830c827387b7e553985ac47c0d5b43ea",
    "harness/TestMain.java": "c2936d7625c8708faa5a6817fd288d0a512d56f63aeb8cb545cf709efee0d009",
    "config/lab-vcenter.json": "42926a01f33833d5595169cbc15805c34786b23156c723e30d452e3c7b3222f2",
    "docs/CONTRACT_FORMAT.md": "4ce53c9723990bd3409ce5aa101e6710319e95e31dc6a80d56b46ff463c91677",
    "README.md": "05d9915c1308199b8a317557800b2d4e8f163cae7daef0c89beb4b1b2567fc25",
    "run.sh": "a992fcb6d2ef4fa6e270c4d204b0ccd091ce9138684ffb8476ba6ad6c66ecd7e",
}

# --------------------------------------------------------------------------

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail if not ok else ""))
    return bool(ok)


def equal(actual, expected, name: str) -> bool:
    return check(
        actual == expected,
        name,
        f"expected {expected!r}, got {actual!r}",
    )


def exact_keys(actual: dict, expected: set[str], name: str) -> bool:
    return equal(sorted(actual), sorted(expected), name)


class Abort(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, name: str):
    if not path.is_file():
        check(False, f"{name} exists", f"{path.relative_to(ROOT)} is missing")
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        check(False, f"{name} parses as JSON", f"{path.relative_to(ROOT)}: {exc}")
        return None


# --------------------------------------------------------------------------
# 1. integrity
# --------------------------------------------------------------------------


def check_integrity() -> None:
    for rel, digest in PROTECTED.items():
        path = ROOT / rel
        if not path.is_file():
            check(False, f"protected file {rel} is present", "file is missing")
            continue
        check(
            sha256(path) == digest,
            f"protected file {rel} is unmodified",
            "contents differ from the shipped version",
        )


# --------------------------------------------------------------------------
# 2. contract.json
# --------------------------------------------------------------------------


def check_contract() -> None:
    contract = load_json(ROOT / "docs" / "contract.json", "docs/contract.json")
    if contract is None:
        return
    if not isinstance(contract, dict):
        check(False, "contract is a JSON object", f"got {type(contract).__name__}")
        return
    exact_keys(
        contract,
        {"source", "base_path", "auth", "operations"},
        "contract contains exactly the documented top-level fields",
    )

    source = contract.get("source")
    if check(isinstance(source, dict), "contract has a source block"):
        exact_keys(
            source,
            {"repository", "tag", "spec_path", "spec_version"},
            "contract source contains exactly the documented fields",
        )
        equal(source.get("repository"), REPO, "contract source names the specification repository")
        equal(source.get("tag"), SPEC_TAG, "contract source names tag 9.0.0.0")
        equal(source.get("spec_path"), SPEC_PATH, "contract source names the vcenter OpenAPI document")
        equal(
            source.get("spec_version"),
            SPEC_VERSION,
            "contract records the document's own info.version (9.0.0.0, not the 9.1 revision)",
        )

    equal(contract.get("base_path"), BASE_PATH, "contract base_path matches the document's servers block")

    auth = contract.get("auth")
    if check(isinstance(auth, dict), "contract has an auth block"):
        exact_keys(
            auth,
            {"session_create_operation_id", "session_header"},
            "contract auth contains exactly the documented fields",
        )
        equal(
            auth.get("session_create_operation_id"),
            SESSION_CREATE_OP,
            "contract names the session-create operation",
        )
        equal(
            auth.get("session_header"),
            SESSION_HEADER,
            "contract names the API-key security scheme's header",
        )

    operations = contract.get("operations")
    if not check(isinstance(operations, list), "contract has an operations array"):
        return

    actual_ids = [op.get("operation_id") for op in operations if isinstance(op, dict)]
    if not equal(actual_ids, EXPECTED_OPERATION_IDS, "contract names exactly the operationIds used, in call order"):
        return

    for expected, actual in zip(EXPECTED_OPERATIONS, operations):
        oid = expected["operation_id"]
        if not check(isinstance(actual, dict), f"{oid}: operation entry is an object", f"got {actual!r}"):
            continue
        exact_keys(
            actual,
            {"operation_id", "method", "path", "query", "security", "success_status", "request_body"},
            f"{oid}: contains exactly the documented operation fields",
        )
        equal(actual.get("method"), expected["method"], f"{oid}: method")
        equal(actual.get("path"), expected["path"], f"{oid}: path relative to base_path")
        equal(actual.get("query") or {}, expected["query"], f"{oid}: query parameters")
        equal(actual.get("security"), expected["security"], f"{oid}: security schemes")
        equal(actual.get("success_status"), expected["success_status"], f"{oid}: success status")

        body = actual.get("request_body")
        want = expected["request_body"]
        if want is None:
            equal(body, None, f"{oid}: declares no request body")
            continue
        if not check(isinstance(body, dict), f"{oid}: has a request_body block", f"got {body!r}"):
            continue
        exact_keys(
            body,
            {"schema", "required", "required_properties", "optional_properties"},
            f"{oid}: request_body contains exactly the documented fields",
        )
        equal(body.get("schema"), want["schema"], f"{oid}: request body schema name")
        equal(body.get("required"), want["required"], f"{oid}: request body required flag")
        equal(
            body.get("required_properties"),
            want["required_properties"],
            f"{oid}: request body required properties",
        )
        equal(
            body.get("optional_properties"),
            want["optional_properties"],
            f"{oid}: request body optional properties",
        )


# --------------------------------------------------------------------------
# 3. official_sources.json
# --------------------------------------------------------------------------


def check_sources() -> None:
    doc = load_json(ROOT / "docs" / "official_sources.json", "docs/official_sources.json")
    if doc is None:
        return
    if not check(isinstance(doc, dict), "official_sources.json is a JSON object", f"got {type(doc).__name__}"):
        return
    exact_keys(doc, {"sources"}, "official_sources.json contains exactly the documented top-level field")
    sources = doc.get("sources") if isinstance(doc, dict) else None
    if not check(isinstance(sources, list) and sources, "official_sources.json has a non-empty sources array"):
        return

    entry = None
    for candidate in sources:
        if isinstance(candidate, dict) and candidate.get("spec_path") == SPEC_PATH:
            entry = candidate
            break
    if not check(entry is not None, f"a source entry records spec_path {SPEC_PATH}"):
        return

    exact_keys(
        entry,
        {
            "title",
            "repository",
            "tag",
            "commit_sha",
            "spec_path",
            "url",
            "license",
            "retrieved",
            "operation_ids",
        },
        "the vCenter source entry contains exactly the documented fields",
    )
    check(
        isinstance(entry.get("title"), str) and bool(entry["title"].strip()),
        "source entry has a nonblank human-readable title",
        f"got {entry.get('title')!r}",
    )

    equal(entry.get("repository"), REPO, "source entry names the specification repository")
    equal(entry.get("tag"), SPEC_TAG, "source entry names tag 9.0.0.0")
    equal(entry.get("license"), SPEC_LICENSE, "source entry records the repository licence")

    sha = entry.get("commit_sha")
    equal(
        sha.lower() if isinstance(sha, str) else sha,
        SPEC_COMMIT,
        "source entry records the commit sha that tag 9.0.0.0 resolves to",
    )

    equal(entry.get("url"), SPEC_URL, "source entry uses the reachable raw URL pinned to that commit sha")

    retrieved = entry.get("retrieved")
    try:
        parsed_retrieved = date.fromisoformat(retrieved) if isinstance(retrieved, str) else None
    except ValueError:
        parsed_retrieved = None
    check(parsed_retrieved is not None, "source entry records an ISO-8601 retrieval date", f"got {retrieved!r}")

    equal(
        entry.get("operation_ids"),
        sorted(EXPECTED_OPERATION_IDS),
        "source entry lists every operationId the contract names, sorted",
    )


# --------------------------------------------------------------------------
# 4. build and run
# --------------------------------------------------------------------------


def check_client_is_one_file() -> list[Path]:
    src = ROOT / "src"
    files = sorted(src.rglob("*.java")) if src.is_dir() else []
    check(
        [p.name for p in files] == ["VcenterRightSizer.java"],
        "the client is a single file at src/VcenterRightSizer.java",
        f"found {[str(p.relative_to(ROOT)) for p in files]}",
    )
    return files


def build(sources: list[Path]) -> None:
    shutil.rmtree(BUILD, ignore_errors=True)
    classes = BUILD / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    cmd = [
        "javac",
        "-nowarn",
        "-d",
        str(classes),
        str(ROOT / "lib" / "MiniJson.java"),
        str(ROOT / "mock" / "VcenterMock.java"),
        str(ROOT / "harness" / "TestMain.java"),
        *[str(p) for p in sources],
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if not check(proc.returncode == 0, "the project compiles", (proc.stderr or proc.stdout).strip()[:4000]):
        raise Abort("compilation failed")


def start_mock(
    *,
    config: str = "config/lab-vcenter.json",
    fixtures: str = "mock/fixtures/inventory.json",
    request_log: str = "build/requests.jsonl",
    port_file_name: str = "build/mock.port",
    mock_log_name: str = "build/mock.log",
) -> tuple[subprocess.Popen, int]:
    port_file = ROOT / port_file_name
    port_file.unlink(missing_ok=True)
    mock_log_path = ROOT / mock_log_name
    mock_log_path.parent.mkdir(parents=True, exist_ok=True)
    mock_log = mock_log_path.open("w")
    proc = subprocess.Popen(
        [
            "java",
            "-cp",
            str(BUILD / "classes"),
            "VcenterMock",
            "--contract",
            "docs/contract.json",
            "--config",
            config,
            "--fixtures",
            fixtures,
            "--log",
            request_log,
            "--port-file",
            port_file_name,
        ],
        cwd=ROOT,
        stdout=mock_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if port_file.is_file() and port_file.read_text().strip():
            return proc, int(port_file.read_text().strip())
        if proc.poll() is not None:
            detail = mock_log_path.read_text().strip()[:4000]
            check(False, "the mock accepts docs/contract.json and starts", detail)
            raise Abort("mock did not start")
        time.sleep(0.05)
    proc.kill()
    check(False, "the mock accepts docs/contract.json and starts", "no port reported within 40s")
    raise Abort("mock did not start")


def run_client(
    port: int,
    *,
    config: str = "config/lab-vcenter.json",
    out: str = "build/report.json",
    testmain_log: str = "build/testmain.log",
) -> None:
    proc = subprocess.run(
        [
            "java",
            "-cp",
            str(BUILD / "classes"),
            "TestMain",
            "--base-url",
            f"http://127.0.0.1:{port}",
            "--config",
            config,
            "--out",
            out,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log_path = ROOT / testmain_log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(proc.stdout + proc.stderr)
    check(
        proc.returncode == 0,
        "the client returns a report instead of throwing",
        (proc.stderr or proc.stdout).strip()[:4000],
    )


def stop_mock(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def probe_uncontracted(port: int) -> None:
    """An operation the contract does not name must not be served."""
    url = f"http://127.0.0.1:{port}{BASE_PATH}/vcenter/vm/vm-4021/hardware/cpu"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            check(
                False,
                "an operation outside the contract is not served",
                f"GET {url} returned {response.status}",
            )
            return
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read().decode("utf-8", "replace")
    except OSError as exc:
        check(False, "an operation outside the contract is not served", f"probe failed: {exc}")
        return

    if not equal(status, 404, "an operation outside the contract answers 404"):
        return
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        check(False, "the 404 carries a vAPI error body", f"body was {payload!r}")
        return
    equal(body.get("error_type"), "OPERATION_NOT_FOUND", "the 404 is reported as OPERATION_NOT_FOUND")


# --------------------------------------------------------------------------
# 5. the wire
# --------------------------------------------------------------------------


def find_nulls(value, path: str = "$") -> list[str]:
    if value is None:
        return [path]
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            found.extend(find_nulls(item, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, item in enumerate(value):
            found.extend(find_nulls(item, f"{path}[{index}]"))
        return found
    return []


def check_wire(username: str) -> None:
    log_path = BUILD / "requests.jsonl"
    if not check(log_path.is_file(), "the mock recorded a request log"):
        return
    lines = [line for line in log_path.read_text().splitlines() if line.strip()]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            check(False, "the request log is well-formed", str(exc))
            return

    # Six requests from the client, then the verifier's own out-of-contract probe.
    if not check(
        len(entries) == 7,
        "the client issued exactly the six contracted requests, once each",
        "the log holds "
        + str(len(entries) - 1)
        + " client request(s): "
        + ", ".join(f"{e.get('method')} {e.get('path')}" for e in entries[:-1]),
    ):
        return

    vm = "vm-4021"
    expected = [
        {
            "method": "POST",
            "path": f"{BASE_PATH}/session",
            "query": "",
            "operation_id": "Cis.Session_create",
            "status": 201,
            "body": None,
        },
        {
            "method": "GET",
            "path": f"{BASE_PATH}/vcenter/vm/{vm}/power",
            "query": "",
            "operation_id": "Vcenter.Vm.Power_get",
            "status": 200,
            "body": None,
        },
        {
            "method": "POST",
            "path": f"{BASE_PATH}/vcenter/vm/{vm}/power",
            "query": "action=stop",
            "operation_id": "Vcenter.Vm.Power_stop",
            "status": 204,
            "body": None,
        },
        {
            "method": "PATCH",
            "path": f"{BASE_PATH}/vcenter/vm/{vm}/hardware/cpu",
            "query": "",
            "operation_id": "Vcenter.Vm.Hardware.Cpu_update",
            "status": 204,
            "body": {"count": 8},
        },
        {
            "method": "PATCH",
            "path": f"{BASE_PATH}/vcenter/vm/{vm}/hardware/memory",
            "query": "",
            "operation_id": "Vcenter.Vm.Hardware.Memory_update",
            "status": 204,
            "body": {"size_mib": 16384},
        },
        {
            "method": "POST",
            "path": f"{BASE_PATH}/vcenter/vm/{vm}/hardware/disk",
            "query": "",
            "operation_id": "Vcenter.Vm.Hardware.Disk_create",
            "status": 400,
            "body": {"scsi": {"bus": 0}, "new_vmdk": {"capacity": 214748364800}},
        },
    ]

    for index, want in enumerate(expected):
        got = entries[index]
        label = f"request {index + 1} ({want['operation_id']})"
        equal(got.get("method"), want["method"], f"{label}: method")
        equal(got.get("path"), want["path"], f"{label}: path")
        equal(got.get("query") or "", want["query"], f"{label}: query string")
        equal(got.get("operation_id"), want["operation_id"], f"{label}: routed to the contracted operation")
        equal(got.get("status"), want["status"], f"{label}: response status")

        if want["body"] is None:
            equal(got.get("body_length"), 0, f"{label}: carries no request body")
            continue

        raw = got.get("body") or ""
        content_type = got.get("content_type") or ""
        check(
            content_type.split(";")[0].strip().lower() == "application/json",
            f"{label}: Content-Type is application/json",
            f"got {content_type!r}",
        )
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            check(False, f"{label}: request body is valid JSON", f"{exc}: {raw!r}")
            continue

        nulls = find_nulls(body)
        check(
            not nulls,
            f"{label}: unset optional properties are omitted, not sent as null",
            "these properties were sent with a null value: " + ", ".join(nulls),
        )
        equal(body, want["body"], f"{label}: request body is exactly the properties that were asked for")

    # Authentication shape.
    session = entries[0]
    check(
        str(session.get("auth_scheme") or "").lower() == "basic",
        "the session is created with HTTP Basic credentials",
        f"Authorization scheme was {session.get('auth_scheme')!r}",
    )
    equal(session.get("auth_user"), username, "the session is created as the configured service account")
    check(
        not session.get("session_header_present"),
        "the session-create request does not carry a session token",
    )
    for index in range(1, 6):
        got = entries[index]
        check(
            got.get("session_header_matches_issued_token") is True,
            f"request {index + 1} ({got.get('operation_id')}): reuses the issued session token",
            f"the {SESSION_HEADER} header did not match the token issued by the session-create call",
        )


# --------------------------------------------------------------------------
# 6. the report
# --------------------------------------------------------------------------


def check_report() -> None:
    report = load_json(BUILD / "report.json", "the returned report")
    if report is None:
        return
    if not check(isinstance(report, dict), "the report is a JSON object", f"got {type(report).__name__}"):
        return
    exact_keys(
        report,
        {"vm", "outcome", "steps", "vm_left_powered_off"},
        "the report contains exactly the documented top-level fields",
    )

    equal(report.get("vm"), "vm-4021", "the report names the VM that was operated on")
    equal(
        report.get("outcome"),
        "PARTIAL_FAILURE",
        "the report classifies the run as a partial failure rather than a clean success or a clean failure",
    )
    equal(
        report.get("vm_left_powered_off"),
        True,
        "the report states that the VM was left powered off",
    )

    steps = report.get("steps")
    if not check(isinstance(steps, list), "the report has a steps array"):
        return
    if not check(
        len(steps) == len(EXPECTED_OPERATION_IDS) and all(isinstance(step, dict) for step in steps),
        "the report has exactly one object per attempted request",
        f"got {len(steps)} entries with types {[type(step).__name__ for step in steps]}",
    ):
        return
    if not equal(
        [s.get("operation_id") for s in steps],
        EXPECTED_OPERATION_IDS,
        "the report accounts for every step that was attempted, in order",
    ):
        return

    succeeded_status = [201, 200, 204, 204, 204]
    for index, want_status in enumerate(succeeded_status):
        step = steps[index]
        oid = EXPECTED_OPERATION_IDS[index]
        exact_keys(
            step,
            {"operation_id", "status", "http_status"},
            f"report step {index + 1} ({oid}) contains exactly the successful-step fields",
        )
        equal(step.get("status"), "SUCCEEDED", f"report step {index + 1} ({oid}) is reported as succeeded")
        equal(step.get("http_status"), want_status, f"report step {index + 1} ({oid}): http status")
        check(
            "error_type" not in step and "error_message" not in step,
            f"report step {index + 1} ({oid}) carries no error detail",
            f"unexpected keys on a succeeded step: {sorted(set(step) & {'error_type', 'error_message'})}",
        )

    failed = steps[5]
    exact_keys(
        failed,
        {"operation_id", "status", "http_status", "error_type", "error_message"},
        "the failed disk step contains exactly the documented failed-step fields",
    )
    equal(failed.get("status"), "FAILED", "the disk step is reported as failed")
    equal(failed.get("http_status"), 400, "the disk step records the status vCenter returned")
    equal(failed.get("error_type"), DISK_ERROR_TYPE, "the disk step records the vAPI error type")
    equal(
        failed.get("error_message"),
        DISK_ERROR_MESSAGE,
        "the disk step quotes vCenter's message verbatim",
    )


# --------------------------------------------------------------------------
# 7. conditional and early-failure scenarios
# --------------------------------------------------------------------------


def write_scenario(
    name: str,
    *,
    vm_id: str,
    username: str,
    password: str,
    plan: dict[str, int],
    power_state: str = "POWERED_ON",
    disk_free_units: int = 1,
    include_vm: bool = True,
) -> tuple[str, str]:
    scenario_dir = BUILD / "scenarios" / name
    scenario_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "vm": vm_id,
        "username": username,
        "password": password,
        "plan": plan,
    }
    fixtures = json.loads((ROOT / "mock" / "fixtures" / "inventory.json").read_text())
    fixtures["session_token"] = f"deterministic-token-{name}"
    fixtures["session_user"] = username
    if include_vm:
        original = fixtures["vms"]["vm-4021"]
        vm = json.loads(json.dumps(original))
        vm["power_state"] = power_state
        vm["scsi_adapters"][0]["bus"] = plan["disk_scsi_bus"]
        vm["scsi_adapters"][0]["free_units"] = disk_free_units
        fixtures["vms"] = {vm_id: vm}

    config_path = scenario_dir / "config.json"
    fixtures_path = scenario_dir / "inventory.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    fixtures_path.write_text(json.dumps(fixtures, indent=2) + "\n")
    return str(config_path.relative_to(ROOT)), str(fixtures_path.relative_to(ROOT))


def load_request_log(relative_path: str, label: str) -> list[dict]:
    path = ROOT / relative_path
    if not check(path.is_file(), f"{label}: the mock recorded a request log"):
        return []
    entries = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            check(False, f"{label}: the request log is well-formed", str(exc))
            return []
        if not isinstance(entry, dict):
            check(False, f"{label}: each request-log entry is an object", f"got {entry!r}")
            return []
        entries.append(entry)
    return entries


def check_scenario_wire(
    label: str,
    relative_path: str,
    username: str,
    expected: list[dict],
) -> None:
    entries = load_request_log(relative_path, label)
    if not equal(len(entries), len(expected), f"{label}: only the required requests were issued"):
        return

    for index, (got, want) in enumerate(zip(entries, expected), start=1):
        request_label = f"{label}: request {index} ({want['operation_id']})"
        for field in ("method", "path", "query", "operation_id", "status"):
            equal(got.get(field) or ("" if field == "query" else None), want[field], f"{request_label}: {field}")

        if want["body"] is None:
            equal(got.get("body_length"), 0, f"{request_label}: carries no request body")
        else:
            try:
                body = json.loads(got.get("body") or "")
            except json.JSONDecodeError as exc:
                check(False, f"{request_label}: request body is valid JSON", str(exc))
            else:
                check(
                    not find_nulls(body),
                    f"{request_label}: unset optional properties are omitted",
                    f"got {body!r}",
                )
                equal(body, want["body"], f"{request_label}: request body uses the runtime plan exactly")

    equal(str(entries[0].get("auth_scheme") or "").lower(), "basic", f"{label}: session uses Basic auth")
    equal(entries[0].get("auth_user"), username, f"{label}: session uses the runtime service account")
    check(not entries[0].get("session_header_present"), f"{label}: session request has no session token")
    for index, entry in enumerate(entries[1:], start=2):
        check(
            entry.get("session_header_matches_issued_token") is True,
            f"{label}: request {index} reuses the issued session token",
        )


def check_scenario_report(
    label: str,
    relative_path: str,
    *,
    vm_id: str,
    outcome: str,
    powered_off: bool,
    expected_steps: list[dict],
) -> None:
    report = load_json(ROOT / relative_path, f"{label} report")
    if not check(isinstance(report, dict), f"{label}: report is a JSON object", f"got {report!r}"):
        return
    exact_keys(
        report,
        {"vm", "outcome", "steps", "vm_left_powered_off"},
        f"{label}: report contains exactly the documented top-level fields",
    )
    equal(report.get("vm"), vm_id, f"{label}: report names the runtime VM")
    equal(report.get("outcome"), outcome, f"{label}: report outcome")
    equal(report.get("vm_left_powered_off"), powered_off, f"{label}: final power-state report")

    steps = report.get("steps")
    if not check(
        isinstance(steps, list)
        and len(steps) == len(expected_steps)
        and all(isinstance(step, dict) for step in steps),
        f"{label}: report has exactly one object per attempted request",
        f"got {steps!r}",
    ):
        return
    for index, (got, want) in enumerate(zip(steps, expected_steps), start=1):
        failed = want["status"] == "FAILED"
        keys = {"operation_id", "status", "http_status"}
        if failed:
            keys.update({"error_type", "error_message"})
        exact_keys(got, keys, f"{label}: report step {index} has exactly the documented fields")
        for field, value in want.items():
            equal(got.get(field), value, f"{label}: report step {index} {field}")


def check_already_powered_off_scenario() -> None:
    label = "already-powered-off scenario"
    name = "already-off"
    vm_id = "vm-off-771"
    username = "svc-off-branch@vsphere.local"
    plan = {
        "cpu_count": 6,
        "memory_size_mib": 12288,
        "disk_capacity_bytes": 53687091200,
        "disk_scsi_bus": 1,
    }
    config, fixtures = write_scenario(
        name,
        vm_id=vm_id,
        username=username,
        password="Deterministic-off-branch!",
        plan=plan,
        power_state="POWERED_OFF",
    )
    request_log = f"build/scenarios/{name}/requests.jsonl"
    report_path = f"build/scenarios/{name}/report.json"
    proc = None
    try:
        proc, port = start_mock(
            config=config,
            fixtures=fixtures,
            request_log=request_log,
            port_file_name=f"build/scenarios/{name}/mock.port",
            mock_log_name=f"build/scenarios/{name}/mock.log",
        )
        check(True, f"{label}: mock starts")
        run_client(
            port,
            config=config,
            out=report_path,
            testmain_log=f"build/scenarios/{name}/testmain.log",
        )
        vm_path = f"{BASE_PATH}/vcenter/vm/{vm_id}"
        expected_wire = [
            {"method": "POST", "path": f"{BASE_PATH}/session", "query": "", "operation_id": SESSION_CREATE_OP, "status": 201, "body": None},
            {"method": "GET", "path": f"{vm_path}/power", "query": "", "operation_id": "Vcenter.Vm.Power_get", "status": 200, "body": None},
            {"method": "PATCH", "path": f"{vm_path}/hardware/cpu", "query": "", "operation_id": "Vcenter.Vm.Hardware.Cpu_update", "status": 204, "body": {"count": 6}},
            {"method": "PATCH", "path": f"{vm_path}/hardware/memory", "query": "", "operation_id": "Vcenter.Vm.Hardware.Memory_update", "status": 204, "body": {"size_mib": 12288}},
            {"method": "POST", "path": f"{vm_path}/hardware/disk", "query": "", "operation_id": "Vcenter.Vm.Hardware.Disk_create", "status": 201, "body": {"scsi": {"bus": 1}, "new_vmdk": {"capacity": 53687091200}}},
        ]
        check_scenario_wire(label, request_log, username, expected_wire)
        check_scenario_report(
            label,
            report_path,
            vm_id=vm_id,
            outcome="SUCCEEDED",
            powered_off=True,
            expected_steps=[
                {"operation_id": item["operation_id"], "status": "SUCCEEDED", "http_status": item["status"]}
                for item in expected_wire
            ],
        )
    finally:
        stop_mock(proc)


def check_early_failure_scenario() -> None:
    label = "early-failure scenario"
    name = "missing-vm"
    vm_id = "vm-missing-772"
    username = "svc-missing-vm@vsphere.local"
    plan = {
        "cpu_count": 10,
        "memory_size_mib": 24576,
        "disk_capacity_bytes": 85899345920,
        "disk_scsi_bus": 2,
    }
    config, fixtures = write_scenario(
        name,
        vm_id=vm_id,
        username=username,
        password="Deterministic-missing-vm!",
        plan=plan,
        include_vm=False,
    )
    request_log = f"build/scenarios/{name}/requests.jsonl"
    report_path = f"build/scenarios/{name}/report.json"
    proc = None
    try:
        proc, port = start_mock(
            config=config,
            fixtures=fixtures,
            request_log=request_log,
            port_file_name=f"build/scenarios/{name}/mock.port",
            mock_log_name=f"build/scenarios/{name}/mock.log",
        )
        check(True, f"{label}: mock starts")
        run_client(
            port,
            config=config,
            out=report_path,
            testmain_log=f"build/scenarios/{name}/testmain.log",
        )
        vm_path = f"{BASE_PATH}/vcenter/vm/{vm_id}"
        expected_wire = [
            {"method": "POST", "path": f"{BASE_PATH}/session", "query": "", "operation_id": SESSION_CREATE_OP, "status": 201, "body": None},
            {"method": "GET", "path": f"{vm_path}/power", "query": "", "operation_id": "Vcenter.Vm.Power_get", "status": 404, "body": None},
        ]
        check_scenario_wire(label, request_log, username, expected_wire)
        check_scenario_report(
            label,
            report_path,
            vm_id=vm_id,
            outcome="PARTIAL_FAILURE",
            powered_off=False,
            expected_steps=[
                {"operation_id": SESSION_CREATE_OP, "status": "SUCCEEDED", "http_status": 201},
                {
                    "operation_id": "Vcenter.Vm.Power_get",
                    "status": "FAILED",
                    "http_status": 404,
                    "error_type": "NOT_FOUND",
                    "error_message": f"Virtual machine {vm_id} was not found.",
                },
            ],
        )
    finally:
        stop_mock(proc)


# --------------------------------------------------------------------------


def main() -> int:
    mock = None
    try:
        check_integrity()
        check_contract()
        check_sources()
        sources = check_client_is_one_file()
        if not sources:
            raise Abort("no client to build")
        build(sources)
        mock, port = start_mock()
        check(True, "the mock accepts docs/contract.json and starts")
        run_client(port)
        probe_uncontracted(port)
        config = json.loads((ROOT / "config" / "lab-vcenter.json").read_text())
        check_wire(config["username"])
        check_report()
        stop_mock(mock)
        mock = None
        check_already_powered_off_scenario()
        check_early_failure_scenario()
    except Abort as exc:
        RESULTS.append((False, "verification ran to completion", str(exc)))
    finally:
        stop_mock(mock)

    failures = [(name, detail) for ok, name, detail in RESULTS if not ok]
    for ok, name, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok and detail:
            for line in detail.splitlines():
                print(f"        {line}")

    print()
    print(f"{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    if failures:
        print("FAILED")
        return 1
    print("PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
