#!/usr/bin/env python3
"""Protected, deterministic acceptance verifier for vcf90-0060.

Compiles the single-file client together with the protected harness, drives it against a
contract-pinned loopback mock, and asserts the exact wire shape of every request from the
mock's request log. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src/main/java/com/vmware/vcf/lab/VcenterSessionClient.java"

SOURCE_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SOURCE_TAG = "9.0.0.0"
SOURCE_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
WRONG_RELEASE_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"  # tag 9.1.0.0 of the same file

EXPECTED_OPERATIONS = {
    "Cis.Session_create": ("POST", "/session"),
    "Cis.Session_get": ("GET", "/session"),
    "Cis.Session_delete": ("DELETE", "/session"),
    "Vcenter.Vm.Hardware.Cpu_update": ("PATCH", "/vcenter/vm/{vm}/hardware/cpu"),
}

PRINCIPAL = "administrator@vsphere.local"
OLD_PASSWORD = "OldSecret!23"
NEW_PASSWORD = "NewSecret!45"
UNVERIFIABLE_PASSWORD = "Quarantined!99"

TOKEN_1 = "cis-session-token-1"  # session created from the credential being rotated away from
TOKEN_2 = "cis-session-token-2"  # session created from the credential that cannot be validated
TOKEN_3 = "cis-session-token-3"  # session created from the replacement credential

SESSION_PATH = "/api/session"


def fail(message: str) -> None:
    raise AssertionError(message)


def basic(password: str) -> str:
    raw = f"{PRINCIPAL}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


# --------------------------------------------------------------------------- contract


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))

    if contract["apiVersion"] != SOURCE_TAG:
        fail("contract API version is not vSphere Automation API 9.0.0.0")
    if contract["openapiVersion"] != "3.0.3":
        fail("contract OpenAPI version does not match the pinned specification")
    derived = contract["derivedFrom"]
    if derived["commitSha"] != SOURCE_SHA:
        fail("contract source commit changed")
    if derived["releaseTag"] != SOURCE_TAG:
        fail("contract source release tag changed")
    if derived["specPath"] != SOURCE_PATH:
        fail("contract source path changed")
    if contract["serverBasePath"] != "/api":
        fail("contract server base path is not /api")

    if contract["operationIds"] != list(EXPECTED_OPERATIONS):
        fail("contract operationId order or contents changed")
    actual = {
        operation_id: (definition["method"], definition["path"])
        for operation_id, definition in contract["operations"].items()
    }
    if actual != EXPECTED_OPERATIONS:
        fail(f"contract operation map changed: {actual!r}")

    schemes = contract["securitySchemes"]
    if schemes["basic_auth"] != {"type": "http", "scheme": "basic"}:
        fail("basic_auth security scheme no longer matches the specification")
    if schemes["api_key_auth"] != {
        "type": "apiKey",
        "in": "header",
        "name": "vmware-api-session-id",
    }:
        fail("api_key_auth security scheme no longer matches the specification")
    if contract["operations"]["Cis.Session_create"]["security"] != ["basic_auth"]:
        fail("Cis.Session_create must be the only basic_auth operation")
    for operation_id in ("Cis.Session_get", "Cis.Session_delete", "Vcenter.Vm.Hardware.Cpu_update"):
        if contract["operations"][operation_id]["security"] != ["api_key_auth"]:
            fail(f"{operation_id} must authenticate with api_key_auth")
    for operation_id in ("Cis.Session_create", "Cis.Session_get", "Cis.Session_delete"):
        if contract["operations"][operation_id]["requestBody"] is not None:
            fail(f"{operation_id} declares no request body in the specification")

    update_spec = contract["schemas"]["Vcenter.Vm.Hardware.Cpu.UpdateSpec"]
    if set(update_spec["properties"]) != {
        "count",
        "cores_per_socket",
        "hot_add_enabled",
        "hot_remove_enabled",
    }:
        fail("Vcenter.Vm.Hardware.Cpu.UpdateSpec properties no longer match the pinned schema")
    if update_spec["required"] != []:
        fail("every Vcenter.Vm.Hardware.Cpu.UpdateSpec property is optional in the specification")
    session_info = contract["schemas"]["Cis.Session.Info"]
    if set(session_info["required"]) != {"created_time", "last_accessed_time", "user"}:
        fail("Cis.Session.Info required properties no longer match the pinned schema")

    if sources["commitSha"] != SOURCE_SHA or sources["specPath"] != SOURCE_PATH:
        fail("official source provenance changed")
    if sources["releaseTag"] != SOURCE_TAG:
        fail("official source release tag must be 9.0.0.0")
    if WRONG_RELEASE_SHA in json.dumps(sources) or WRONG_RELEASE_SHA in json.dumps(contract):
        fail("the contract points at the 9.1.0.0 revision of vcenter.yaml")
    if sources["repositoryLicense"] != "Apache-2.0":
        fail("official source license changed")
    if SOURCE_SHA not in sources["specUrl"] or SOURCE_PATH not in sources["specUrl"]:
        fail("official spec URL is not commit-pinned")
    recorded = {
        row["operationId"]: (row["method"], row["path"]) for row in sources["operations"]
    }
    if recorded != EXPECTED_OPERATIONS:
        fail("official_sources.json must record every exact operationId")


# --------------------------------------------------------------------- candidate shape


def strip_comments(source: str) -> str:
    """Drop block comments and whole-line comments so prose cannot trip the source scan."""
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(
        line for line in without_blocks.splitlines() if not line.lstrip().startswith("//")
    )


def verify_candidate_shape() -> None:
    java_sources = sorted(path for path in (ROOT / "src").rglob("*.java"))
    if java_sources != [CLIENT]:
        fail(f"the client must remain a single Java source file: {[str(p) for p in java_sources]}")

    source = strip_comments(CLIENT.read_text(encoding="utf-8"))
    forbidden = [
        "HttpURLConnection",
        "okhttp",
        "org.apache.http",
        "ProcessBuilder",
        "Runtime.getRuntime",
        "System.exit",
    ]
    used = [token for token in forbidden if token in source]
    if used:
        fail("client must use java.net.http only: " + ", ".join(used))

    leaked = [
        token
        for token in (
            "vm-slow",
            "vm-101",
            "vm-102",
            "vm-103",
            "vm-rejected",
            "vm-validation-window",
            "cis-session-token",
            PRINCIPAL,
            OLD_PASSWORD,
            NEW_PASSWORD,
            UNVERIFIABLE_PASSWORD,
        )
        if token in source
    ]
    if leaked:
        fail("client must not hard-code harness fixtures: " + ", ".join(leaked))
    if "contract.json" in source or "requests.jsonl" in source:
        fail("client must not read the contract document or the mock request log at runtime")

    required_signatures = [
        r"public\s+String\s+connect\s*\(\s*String\s+\w+\s*,\s*String\s+\w+\s*\)",
        r"public\s+String\s+currentSessionToken\s*\(\s*\)",
        r"public\s+void\s+updateVirtualMachineCpu\s*\(\s*String\s+\w+\s*,\s*CpuUpdateSpec\s+\w+\s*\)",
        r"public\s+void\s+rotateCredential\s*\(\s*String\s+\w+\s*,\s*String\s+\w+\s*\)",
        r"public\s+void\s+close\s*\(\s*\)",
    ]
    for pattern in required_signatures:
        if not re.search(pattern, source):
            fail(f"public client API changed, missing: {pattern}")


# ------------------------------------------------------------------------ request log


def read_log(log_path: Path) -> tuple[list[dict], list[dict]]:
    received: list[dict] = []
    completed: dict[int, dict] = {}
    rejected: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] == "received":
            received.append(record)
        elif record["event"] == "completed":
            completed[record["seq"]] = record
        else:
            rejected.append(record)
    if rejected:
        offenders = sorted({f"{row['method']} {row['path']}" for row in rejected})
        fail("requests were made outside docs/contract.json: " + ", ".join(offenders))
    received.sort(key=lambda record: record["seq"])
    for record in received:
        record["completedAt"] = completed.get(record["seq"], {}).get("at")
        record["status"] = completed.get(record["seq"], {}).get("status")
    return received, rejected


def header(record: dict, name: str):
    return record["headers"].get(name)


def assert_no_query_or_body(record: dict, label: str) -> None:
    if record["query"] != "":
        fail(f"{label} must not carry a query string")
    if record["body"] != "":
        fail(f"{label} declares no request body but sent {record['body']!r}")
    if header(record, "content-type") is not None:
        fail(f"{label} has no request body and must not send a Content-Type header")


def assert_accepts_json(record: dict, label: str) -> None:
    accept = str(header(record, "accept") or "")
    if "application/json" not in accept.lower():
        fail(f"{label} must send Accept: application/json, got {accept!r}")


def verify_session_create(record: dict, password: str, label: str) -> None:
    assert_no_query_or_body(record, label)
    assert_accepts_json(record, label)
    if header(record, "authorization") != basic(password):
        fail(f"{label} did not present the expected basic_auth credential")
    if header(record, "vmware-api-session-id") is not None:
        fail(f"{label} is a basic_auth operation and must not send a session token")
    if record["status"] != 201:
        fail(f"{label} was answered with HTTP {record['status']}")


def verify_token_operation(record: dict, token: str, label: str, expected_status: int) -> None:
    assert_no_query_or_body(record, label)
    assert_accepts_json(record, label)
    if header(record, "vmware-api-session-id") != token:
        fail(f"{label} used session token {header(record, 'vmware-api-session-id')!r}, expected {token!r}")
    if header(record, "authorization") is not None:
        fail(f"{label} must not resend basic_auth credentials on an api_key_auth operation")
    if record["status"] != expected_status:
        fail(f"{label} was answered with HTTP {record['status']}, expected {expected_status}")


def verify_cpu_update(
    record: dict,
    vm_id: str,
    token: str,
    expected_body: dict,
    label: str,
    expected_status: int = 204,
) -> None:
    if record["path"] != f"/api/vcenter/vm/{vm_id}/hardware/cpu":
        fail(f"{label} targeted {record['path']!r}")
    if record["query"] != "":
        fail(f"{label} must not carry a query string")
    if header(record, "vmware-api-session-id") != token:
        fail(f"{label} used session token {header(record, 'vmware-api-session-id')!r}, expected {token!r}")
    if header(record, "authorization") is not None:
        fail(f"{label} must not resend basic_auth credentials on an api_key_auth operation")
    content_type = str(header(record, "content-type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        fail(f"{label} Content-Type is not application/json: {content_type!r}")
    assert_accepts_json(record, label)
    try:
        body = json.loads(record["body"])
    except json.JSONDecodeError as error:
        fail(f"{label} body is not JSON: {error}")
    if not isinstance(body, dict):
        fail(f"{label} body must be a Vcenter.Vm.Hardware.Cpu.UpdateSpec object")
    absent = sorted(set(expected_body) ^ set(body))
    if body != expected_body:
        fail(
            f"{label} sent the wrong Vcenter.Vm.Hardware.Cpu.UpdateSpec: {body!r}, "
            f"expected {expected_body!r} (differing properties: {absent})"
        )
    for name, value in body.items():
        if value is None:
            fail(f"{label} sent unset optional property {name!r} as null instead of omitting it")
    if record["status"] != expected_status:
        fail(
            f"{label} was answered with HTTP {record['status']}, expected {expected_status}"
        )


def verify_wire(received: list[dict]) -> None:
    validation_window_path = "/api/vcenter/vm/vm-validation-window/hardware/cpu"
    expected_sequence = [
        ("Cis.Session_create", "POST", SESSION_PATH),
        ("Vcenter.Vm.Hardware.Cpu_update", "PATCH", "/api/vcenter/vm/vm-101/hardware/cpu"),
        ("Vcenter.Vm.Hardware.Cpu_update", "PATCH", "/api/vcenter/vm/vm-rejected/hardware/cpu"),
        ("Cis.Session_create", "POST", SESSION_PATH),
        ("Cis.Session_get", "GET", SESSION_PATH),
        ("Cis.Session_delete", "DELETE", SESSION_PATH),
        ("Vcenter.Vm.Hardware.Cpu_update", "PATCH", "/api/vcenter/vm/vm-103/hardware/cpu"),
        ("Vcenter.Vm.Hardware.Cpu_update", "PATCH", "/api/vcenter/vm/vm-slow/hardware/cpu"),
        ("Cis.Session_create", "POST", SESSION_PATH),
        ("Cis.Session_get", "GET", SESSION_PATH),
        ("Cis.Session_delete", "DELETE", SESSION_PATH),
        ("Vcenter.Vm.Hardware.Cpu_update", "PATCH", "/api/vcenter/vm/vm-102/hardware/cpu"),
        ("Cis.Session_delete", "DELETE", SESSION_PATH),
    ]
    validation_window_updates = [
        record for record in received if record["path"] == validation_window_path
    ]
    if len(validation_window_updates) != 1:
        fail(
            "expected exactly one reconfigure submitted during replacement validation, got "
            f"{len(validation_window_updates)}"
        )
    core_records = [record for record in received if record["path"] != validation_window_path]
    actual_sequence = [
        (record["operationId"], record["method"], record["path"]) for record in core_records
    ]
    if actual_sequence != expected_sequence:
        fail(
            "the request sequence is wrong.\n  expected: "
            + json.dumps(expected_sequence)
            + "\n  actual:   "
            + json.dumps(actual_sequence)
        )

    login, first_update, rejected_update, quarantined_login = core_records[0:4]
    quarantined_validate, quarantined_cleanup = core_records[4], core_records[5]
    second_update, in_flight_update = core_records[6], core_records[7]
    rotation_login, rotation_validate = core_records[8], core_records[9]
    retire_old, third_update, final_logout = core_records[10:13]
    validation_window_update = validation_window_updates[0]

    verify_session_create(login, OLD_PASSWORD, "Cis.Session_create (initial connect)")
    verify_cpu_update(first_update, "vm-101", TOKEN_1, {"count": 8}, "Cpu_update vm-101")
    verify_cpu_update(
        rejected_update,
        "vm-rejected",
        TOKEN_1,
        {"hot_remove_enabled": True},
        "Cpu_update vm-rejected",
        expected_status=400,
    )

    verify_session_create(
        quarantined_login, UNVERIFIABLE_PASSWORD, "Cis.Session_create (rotation that cannot validate)"
    )
    verify_token_operation(
        quarantined_validate, TOKEN_2, "Cis.Session_get (validating the unusable session)", 503
    )
    verify_token_operation(
        quarantined_cleanup, TOKEN_2, "Cis.Session_delete (discarding the unusable session)", 204
    )
    verify_cpu_update(
        second_update, "vm-103", TOKEN_1, {"cores_per_socket": 1}, "Cpu_update vm-103"
    )

    verify_cpu_update(
        in_flight_update,
        "vm-slow",
        TOKEN_1,
        {"count": 16, "cores_per_socket": 2},
        "Cpu_update vm-slow (in flight during rotation)",
    )
    verify_session_create(rotation_login, NEW_PASSWORD, "Cis.Session_create (rotation)")
    verify_token_operation(
        rotation_validate, TOKEN_3, "Cis.Session_get (validating the replacement session)", 200
    )
    validation_window_token = header(validation_window_update, "vmware-api-session-id")
    if validation_window_token not in {TOKEN_1, TOKEN_3}:
        fail(
            "the validation-window update used neither the current nor replacement session: "
            f"{validation_window_token!r}"
        )
    verify_cpu_update(
        validation_window_update,
        "vm-validation-window",
        validation_window_token,
        {"hot_remove_enabled": False},
        "Cpu_update submitted around replacement validation",
    )
    verify_token_operation(
        retire_old, TOKEN_1, "Cis.Session_delete (retiring the rotated-away session)", 204
    )
    verify_cpu_update(
        third_update, "vm-102", TOKEN_3, {"hot_add_enabled": False}, "Cpu_update vm-102"
    )
    verify_token_operation(
        final_logout, TOKEN_3, "Cis.Session_delete (close)", 204
    )

    # A non-serializing client submits this update while the replacement GET is held open, in
    # which case it must still use the old session. A client may instead serialize it behind the
    # rotation; that request legitimately uses the replacement after validation completes.
    if rotation_validate["completedAt"] is None:
        fail("replacement session validation never completed")
    if (
        validation_window_update["at"] < rotation_validate["completedAt"]
        and validation_window_token != TOKEN_1
    ):
        fail("the replacement session was adopted while Cis.Session_get was still in flight")
    if rotation_validate["completedAt"] >= third_update["at"]:
        fail("the replacement session was used for work before Cis.Session_get validated it")

    # The rotation must have overlapped the in-flight reconfigure, otherwise the drain is untested.
    if in_flight_update["completedAt"] is None:
        fail("the in-flight reconfigure never completed")
    if rotation_login["at"] >= in_flight_update["completedAt"]:
        fail("the rotation did not start while the reconfigure was still in flight")

    # And it must not have retired the old session out from under that reconfigure.
    if retire_old["inFlightForToken"] != 0:
        fail(
            "Cis.Session_delete retired the old session while "
            f"{retire_old['inFlightForToken']} request(s) were still in flight on it"
        )
    if retire_old["at"] < in_flight_update["completedAt"]:
        fail("the old session was terminated before the in-flight reconfigure finished")

    for record in received:
        if record["sessionToken"] == TOKEN_1 and record["at"] > retire_old["at"]:
            fail("a request used the retired session token after it was deleted")
        if record["sessionToken"] == TOKEN_2 and record not in (quarantined_validate, quarantined_cleanup):
            fail("the session that could not be validated was used for work")


# ------------------------------------------------------------------------ integration


def start_mock(temp: Path, environment: dict) -> tuple[subprocess.Popen, int, Path]:
    log_path = temp / "requests.jsonl"
    port_path = temp / "port"
    server = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(ROOT / "tests/mock_vcenter.py"),
            "--contract",
            str(ROOT / "docs/contract.json"),
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
    deadline = time.monotonic() + 10
    while not port_path.exists() and time.monotonic() < deadline:
        if server.poll() is not None:
            stdout, stderr = server.communicate(timeout=5)
            fail(f"mock exited during startup\nstdout: {stdout}\nstderr: {stderr}")
        time.sleep(0.02)
    if not port_path.exists():
        fail("mock did not publish its loopback port")
    return server, int(port_path.read_text(encoding="ascii")), log_path


def run_integration() -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        fail("a JDK providing javac and java is required by this task")

    with tempfile.TemporaryDirectory(prefix="vcf90-0060-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        compiled = subprocess.run(
            [javac, "-nowarn", "-d", str(classes), str(CLIENT), str(ROOT / "tests/TestMain.java")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if compiled.returncode != 0:
            fail(f"the client does not compile\nstdout:\n{compiled.stdout}\nstderr:\n{compiled.stderr}")

        environment = os.environ.copy()
        environment["NO_PROXY"] = "127.0.0.1,localhost"
        environment["no_proxy"] = "127.0.0.1,localhost"

        server, port, log_path = start_mock(temp, environment)
        output_path = temp / "result.json"
        try:
            completed = subprocess.run(
                [
                    java,
                    "-cp",
                    str(classes),
                    "com.vmware.vcf.lab.harness.TestMain",
                    "--base-uri",
                    f"http://127.0.0.1:{port}/api",
                    "--log",
                    str(log_path),
                    "--out",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if completed.returncode != 0:
                fail(
                    "the harness failed against the loopback mock\n"
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                )
            if not output_path.exists():
                fail("the harness did not write a result")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            expected_result = {
                "connected": True,
                "connectReturnsCurrentToken": True,
                "cpuFailureStatus": 400,
                "cpuFailureOperationId": "Vcenter.Vm.Hardware.Cpu_update",
                "failedRotationStatus": 503,
                "failedRotationOperationId": "Cis.Session_get",
                "tokenKeptAfterFailedRotation": True,
                "inFlightRequestSucceeded": True,
                "inFlightFailure": "<none>",
                "rotationSucceeded": True,
                "rotationFailure": "<none>",
                "tokenReplacedAfterRotation": True,
                "tokenClearedByClose": True,
            }
            if result != expected_result:
                differences = {
                    key: (result.get(key, "<missing>"), value)
                    for key, value in expected_result.items()
                    if result.get(key, "<missing>") != value
                }
                fail(f"rotation outcomes are wrong (actual, expected): {differences!r}")
            received, _ = read_log(log_path)
            verify_wire(received)
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def main() -> None:
    verify_contract()
    verify_candidate_shape()
    run_integration()
    print("PASS: the credential rotation held the pinned vCenter wire contract and stranded nothing")


if __name__ == "__main__":
    try:
        main()
    except (
        AssertionError,
        KeyError,
        IndexError,
        OSError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
