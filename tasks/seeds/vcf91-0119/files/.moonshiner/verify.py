#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0119."""

from __future__ import annotations

import base64
import hashlib
import json
import select
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
CLIENT = ROOT / "VcenterCloneClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
MOCK = ROOT / "tests" / "mock_vcenter.py"

EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATIONS = [
    "Vcenter.VM_clone$Task",
    "Cis.Tasks_get",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_contract() -> dict:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    derived = contract["derived_from"]
    if derived["commit_sha"] != EXPECTED_COMMIT:
        fail("contract commit is not pinned")
    if derived["spec_blob_sha"] != EXPECTED_BLOB:
        fail("contract blob is not pinned")
    if derived["spec_path"] != EXPECTED_SPEC:
        fail("contract spec path changed")
    operation_ids = [
        operation["operationId"]
        for operation in contract["operations"]
    ]
    if operation_ids != EXPECTED_OPERATIONS:
        fail("contract operation order/set changed")
    source_ids = [
        operation["operationId"]
        for operation in sources["operationIds"]
    ]
    if source_ids != EXPECTED_OPERATIONS:
        fail("official source operationIds changed")
    if sources["repository_commit_sha"] != EXPECTED_COMMIT:
        fail("official source commit changed")
    if sources["spec_blob_sha"] != EXPECTED_BLOB:
        fail("official source blob changed")
    if sources["spec_path"] != EXPECTED_SPEC:
        fail("official source spec path changed")
    if contract["security"]["name"] != "vmware-api-session-id":
        fail("contract security header changed")
    return contract


def read_fixture_banner(process: subprocess.Popen[str]) -> dict:
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 5.0)
    if not ready:
        stderr = process.stderr.read() if process.stderr else ""
        fail(f"loopback fixture did not start: {stderr}")
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr else ""
        fail(f"loopback fixture exited during startup: {stderr}")
    return json.loads(line)


def verify_log(
    log_path: Path,
    contract: dict,
    session_id: str,
    source: str,
    name: str,
    task_id: str,
) -> None:
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(events) != 5:
        fail(f"expected 5 requests, observed {len(events)}")

    clone_operation, task_operation = contract["operations"]
    expected_clone_target = (
        contract["server"]["api_root_suffix"] + clone_operation["path"]
    )
    encoded_task = quote(task_id, safe="-._~", encoding="utf-8")
    expected_task_target = (
        contract["server"]["api_root_suffix"]
        + task_operation["path"].replace("{task}", encoded_task)
    )
    expected_body = json.dumps(
        {"source": source, "name": name},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    optional_clone_fields = {
        "placement",
        "disks_to_remove",
        "disks_to_update",
        "power_on",
        "guest_customization_spec",
    }

    for index, event in enumerate(events):
        if event["seq"] != index + 1:
            fail("request sequence is not contiguous")
        if event["session"] != session_id:
            fail("missing or incorrect vmware-api-session-id")
        if event["accept"] != "application/json":
            fail("missing or incorrect Accept header")

        body = base64.b64decode(event["body_b64"], validate=True)
        if index == 0:
            if event["operation_id"] != clone_operation["operationId"]:
                fail("first request was not the clone task operation")
            if event["method"] != clone_operation["method"]:
                fail("clone method mismatch")
            if event["raw_target"] != expected_clone_target:
                fail("clone raw target mismatch")
            if event["content_type"] != "application/json":
                fail("clone content type mismatch")
            if body != expected_body:
                fail("clone body bytes/order/escaping mismatch")
            parsed = json.loads(body)
            if list(parsed) != ["source", "name"]:
                fail("clone body property order or minimal shape mismatch")
            if set(parsed) & optional_clone_fields:
                fail("an unset optional clone field was serialized")
        else:
            if event["operation_id"] != task_operation["operationId"]:
                fail("non-contract task operation observed")
            if event["method"] != task_operation["method"]:
                fail("task method mismatch")
            if event["raw_target"] != expected_task_target:
                fail("task path encoding or optional-query omission mismatch")
            if "?" in event["raw_target"]:
                fail("unset optional task spec was serialized")
            if event["content_type"] is not None:
                fail("bodyless task GET sent Content-Type")
            if body or event["body_length"] != 0:
                fail("task GET sent a request body")
            if event["poll_ordinal"] != index:
                fail("task polling sequence mismatch")


def main() -> int:
    contract = check_protected_contract()
    if not CLIENT.is_file():
        fail("VcenterCloneClient.java is missing")

    source_digest = hashlib.sha256(
        CLIENT.read_bytes()
    ).hexdigest()[:14]
    nonce = source_digest + "7a"
    session_id = "session-" + nonce
    source = "vm-source/" + nonce + r"\gold"
    name = f'Clone "{nonce}"\nblue \N{SNOWMAN}'
    task_id = "task:clone/" + nonce + " +blue"
    virtual_machine_id = "vm-result/" + nonce

    with tempfile.TemporaryDirectory(prefix="vcf91-0119-") as raw_tmp:
        tmp = Path(raw_tmp)
        classes = tmp / "classes"
        classes.mkdir()
        config_path = tmp / "fixture.json"
        request_log = tmp / "requests.jsonl"
        config_path.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "virtual_machine_id": virtual_machine_id,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                str(CLIENT),
                str(TEST_MAIN),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(
                "javac failed:\n"
                + compile_result.stdout
                + compile_result.stderr
            )

        fixture = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--config",
                str(config_path),
                "--log",
                str(request_log),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            banner = read_fixture_banner(fixture)
            api_root = (
                f"http://{banner['host']}:{banner['port']}"
                + contract["server"]["api_root_suffix"]
            )
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    api_root,
                    session_id,
                    source,
                    name,
                    task_id,
                    virtual_machine_id,
                    str(request_log),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            if run_result.returncode != 0:
                fail(
                    "TestMain failed:\n"
                    + run_result.stdout
                    + run_result.stderr
                )
            if run_result.stdout.strip() != "TEST_MAIN_OK":
                fail("unexpected TestMain output")
            verify_log(
                request_log,
                contract,
                session_id,
                source,
                name,
                task_id,
            )
        finally:
            if fixture.poll() is None:
                fixture.terminate()
            try:
                fixture.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.communicate(timeout=3)

    print("verification passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
