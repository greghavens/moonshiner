#!/usr/bin/env python3
"""Protected, offline verifier for the VCF 9.1 vCenter EVC client."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "VcenterEvcClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
MOCK = ROOT / "tests" / "mock_vcenter.py"
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"

PINNED = {
    CONTRACT: "42c9a3e31fe72db2aff943c0040524d845e78e5e19ce61666d335d5d7c799a47",
    SOURCES: "9207b41ac608941dd4b18aa661930de3f583e36f6a5270d3ccb833d7b0d0149d",
    TEST_MAIN: "2f0e00a1ddfc4bf5148ef238a333a8cc335836eb4ac1155ca7ec3560cd3d9644",
    MOCK: "34d97a95affeff106ff584a0af6a4562f429812e259645ad9b85f96c1a8efea3",
}
EXPECTED_OPERATIONS = [
    (
        "Vcenter.Cluster.EvcMode_checkSet$Task",
        "POST",
        "/vcenter/cluster/{cluster}/evc-mode?action=check-set&vmw-task=true",
    ),
    ("Cis.Tasks_get", "GET", "/cis/tasks/{task}"),
    (
        "Vcenter.Cluster.EvcMode_set$Task",
        "PUT",
        "/vcenter/cluster/{cluster}/evc-mode?vmw-task=true",
    ),
]
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_protected_inputs() -> None:
    for path, expected in PINNED.items():
        if not path.is_file():
            fail(f"protected file is missing: {path.relative_to(ROOT)}")
        if sha256(path) != expected:
            fail(f"protected file was modified: {path.relative_to(ROOT)}")

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    contract_operations = [
        (
            item["operationId"],
            item["method"],
            item["path_template"],
        )
        for item in contract["operations"]
    ]
    source_operations = [
        (item["operationId"], item["method"], item["path"])
        for item in sources["operations"]
    ]
    if contract_operations != EXPECTED_OPERATIONS:
        fail("contract does not contain the exact pinned operation set")
    if source_operations != EXPECTED_OPERATIONS:
        fail("official_sources does not record every contract operation")
    if (
        contract["source"]["repository_commit_sha"] != EXPECTED_COMMIT
        or sources["repository_commit_sha"] != EXPECTED_COMMIT
    ):
        fail("contract provenance is not pinned to the VCF 9.1 commit")
    if (
        contract["source"]["spec_path"] != EXPECTED_SPEC
        or sources["spec_path"] != EXPECTED_SPEC
    ):
        fail("contract provenance names the wrong specification")


def check_client_source() -> None:
    if not CLIENT.is_file():
        fail("VcenterEvcClient.java is missing")
    source = CLIENT.read_text(encoding="utf-8")
    forbidden = {
        "a package declaration": "package ",
        "a subprocess": "ProcessBuilder",
        "Runtime process execution": "Runtime.getRuntime",
        "a raw socket": "java.net.Socket",
        "a raw server socket": "java.net.ServerSocket",
    }
    for label, marker in forbidden.items():
        if marker in source:
            fail(f"the single-file client contains forbidden {label}")
    for required in (
        "VcenterEvcClient",
        "applySafely",
        "Vcenter.Cluster.EvcMode_checkSet$Task",
        "Cis.Tasks_get",
        "Vcenter.Cluster.EvcMode_set$Task",
        "vmware-api-session-id",
    ):
        if required not in source:
            fail(f"client does not reference required contract surface: {required}")


def wait_for_port(
    process: subprocess.Popen[str],
    port_file: Path,
) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            fail(f"loopback mock exited during startup: {stderr.strip()}")
        if port_file.is_file():
            text = port_file.read_text(encoding="ascii").strip()
            if text:
                port = int(text)
                if 1 <= port <= 65535:
                    return port
        time.sleep(0.01)
    fail("loopback mock did not publish its port")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> None:
    check_protected_inputs()
    check_client_source()
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        fail("Java 17 javac and java are required")

    nonce = secrets.token_hex(9)
    session = f"session-{nonce}.fixture"
    task_prefix = f"task-{nonce}"
    set_cluster = f"domain-set-{nonce}/blue space+#\u03a9"
    clear_cluster = f"domain-clear-{nonce}?edge/green #"
    reject_cluster = f"domain-reject-{nonce}#red /?"
    mode_key = f'intel-"ice\\nlake-\u03a9-{nonce}'
    mask_key = f'cpuid.7/"{nonce}'
    mask_name = f"mask\\\\name\\n\u03a9-{nonce}"
    mask_value = f'1010"\\\\{nonce}'

    with tempfile.TemporaryDirectory(prefix="vcf91-0124-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compiled = subprocess.run(
            [
                javac,
                "--release",
                "17",
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
        if compiled.returncode != 0:
            detail = (compiled.stderr or compiled.stdout).strip()
            fail(f"Java compilation failed: {detail}")

        log_path = temp / "requests.jsonl"
        port_file = temp / "port.txt"
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        mock = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--log",
                str(log_path),
                "--port-file",
                str(port_file),
                "--session",
                session,
                "--task-prefix",
                task_prefix,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        try:
            port = wait_for_port(mock, port_file)
            completed = subprocess.run(
                [
                    java,
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{port}/api",
                    session,
                    str(log_path),
                    set_cluster,
                    clear_cluster,
                    reject_cluster,
                    task_prefix,
                    mode_key,
                    mask_key,
                    mask_name,
                    mask_value,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                check=False,
            )
        finally:
            stop_process(mock)

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            fail(f"TestMain failed: {detail}")
        if "PASS: EVC mutation was gated" not in completed.stdout:
            fail("TestMain did not report the expected success marker")

        log = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if len(log) != 10:
            fail(f"request log has unexpected length after TestMain: {len(log)}")
        allowed_ids = {item[0] for item in EXPECTED_OPERATIONS}
        if any(entry["operationId"] not in allowed_ids for entry in log):
            fail("client contacted a route outside docs/contract.json")
        mutations = [
            entry
            for entry in log
            if entry["operationId"]
            == "Vcenter.Cluster.EvcMode_set$Task"
        ]
        if len(mutations) != 2:
            fail("failed precheck did not leave the mutation count unchanged")

    print(
        "PASS: protected contract, precheck gate, and exact request wire shape verified."
    )


if __name__ == "__main__":
    main()
