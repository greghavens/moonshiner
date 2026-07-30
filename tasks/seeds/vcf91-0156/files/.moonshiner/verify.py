#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tools" / "contract_mock.py"
CLIENT = ROOT / "VksSupervisorBackupClient.java"
TEST_MAIN = ROOT / "TestMain.java"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATION_IDS = [
    "Vcenter.Namespaces.Instances_getV2",
    "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create",
    "Cis.Tasks_get",
]
EXPECTED_CONTRACT_OPERATIONS = [
    "getSupervisorNamespace",
    "getVksDeployment",
    "createSupervisorBackup",
    "getTask",
]

# Filled with hashes of protected fixture files. The verifier itself is protected
# by the harness and intentionally is not self-hashed.
PROTECTED_SHA256 = {
    "TestMain.java": "078d6d5edb729a3bda6570f6f054097fe18ce472387201ca4a7abeda8cc77a4e",
    "docs/contract.json": "27193c6f32817aba25621272049d4bfb280a0e176ea26b30df4fdedece8c5dd3",
    "docs/official_sources.json": "1818ff37ff3f1b19fd1b5a53475f81d7ee11fb7d5a3629def06138b02561c5fd",
    "tools/contract_mock.py": "a2de42ac5538e226706cfeb86bb842fbfb0faf992c8a7c251ca4047f12c37166",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        require(path.is_file(), f"missing protected file: {relative}")
        require(sha256(path) == expected, f"protected file changed: {relative}")


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    source = contract.get("source", {})

    for item in (source, sources):
        require(item.get("repositoryCommitSha") == PINNED_COMMIT, "wrong source commit")
        require(item.get("specBlobSha") == PINNED_BLOB, "wrong spec blob")
        require(item.get("specPath") == PINNED_SPEC, "wrong spec path")
        require(item.get("license") == "Apache-2.0", "wrong source license")

    require(
        sources.get("operationIds") == EXPECTED_OPERATION_IDS,
        "official operationId list changed",
    )
    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations missing")
    require(
        [operation.get("contractName") for operation in operations]
        == EXPECTED_CONTRACT_OPERATIONS,
        "contract operation order changed",
    )
    projected = [
        operation.get("operationId")
        for operation in operations
        if operation.get("sourceKind") == "vcenter-openapi-operation"
    ]
    require(projected == EXPECTED_OPERATION_IDS, "projected operationIds changed")

    native = operations[1]
    require(native.get("sourceKind") == "native-kubernetes-api", "Kubernetes label")
    require("operationId" not in native, "fictional Kubernetes operationId")
    require(
        native.get("operationKey") == "apps/v1:namespaced-deployments:read",
        "Kubernetes operation key changed",
    )


def encode_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="-._~", encoding="utf-8", errors="strict")


def header_values(entry: dict[str, object], name: str) -> list[str]:
    wanted = name.casefold()
    values: list[str] = []
    for pair in entry["headers"]:
        if pair[0].casefold() == wanted:
            values.append(pair[1])
    return values


def body_bytes(entry: dict[str, object]) -> bytes:
    return base64.b64decode(entry["bodyBase64"], validate=True)


def assert_get_wire(
    entry: dict[str, object],
    operation: str,
    target: str,
    auth_header: str,
    auth_value: str,
    forbidden_auth: str,
) -> None:
    require(entry["operation"] == operation, f"wrong operation for {operation}")
    require(entry["method"] == "GET", f"wrong method for {operation}")
    require(entry["rawTarget"] == target, f"wrong raw target for {operation}")
    require("?" not in entry["rawTarget"], f"unexpected query for {operation}")
    require(body_bytes(entry) == b"", f"GET body sent for {operation}")
    require(header_values(entry, "Accept") == ["application/json"], "Accept wire shape")
    require(header_values(entry, auth_header) == [auth_value], f"{operation} auth")
    require(header_values(entry, forbidden_auth) == [], f"{operation} leaked auth")
    require(header_values(entry, "Content-Type") == [], f"{operation} Content-Type")
    require(header_values(entry, "Transfer-Encoding") == [], f"{operation} transfer")
    require(header_values(entry, "Content-Length") == [], f"{operation} length")


def assert_request_log(
    scenario: str,
    config: dict[str, object],
    entries: list[dict[str, object]],
    max_polls: int,
) -> None:
    if scenario == "happy":
        expected_operations = [
            "getSupervisorNamespace",
            "getVksDeployment",
            "createSupervisorBackup",
            "getTask",
            "getTask",
            "getTask",
        ]
    elif scenario == "explicit_false":
        expected_operations = [
            "getSupervisorNamespace",
            "getVksDeployment",
            "createSupervisorBackup",
            "getTask",
        ]
    elif scenario == "task_failed":
        expected_operations = [
            "getSupervisorNamespace",
            "getVksDeployment",
            "createSupervisorBackup",
            "getTask",
            "getTask",
        ]
    elif scenario == "unstable":
        expected_operations = ["getSupervisorNamespace", "getVksDeployment"]
    elif scenario == "namespace_not_ready":
        expected_operations = ["getSupervisorNamespace"]
    elif scenario == "poll_limit":
        expected_operations = [
            "getSupervisorNamespace",
            "getVksDeployment",
            "createSupervisorBackup",
        ] + ["getTask"] * max_polls
    else:
        raise VerificationError("unknown verifier scenario")

    require(
        [entry["operation"] for entry in entries] == expected_operations,
        f"wrong operation sequence for {scenario}",
    )
    require(all(entry["operation"] is not None for entry in entries), "off-contract route")

    session = str(config["session"])
    token = str(config["token"])
    supervisor_namespace = encode_segment(str(config["supervisorNamespace"]))
    supervisor = encode_segment(str(config["supervisor"]))
    workload_namespace = encode_segment(str(config["workloadNamespace"]))
    deployment = encode_segment(str(config["deployment"]))
    task_id = encode_segment(str(config["taskId"]))

    assert_get_wire(
        entries[0],
        "getSupervisorNamespace",
        f"/api/vcenter/namespaces/instances/v2/{supervisor_namespace}",
        "vmware-api-session-id",
        session,
        "Authorization",
    )

    if scenario == "namespace_not_ready":
        return

    assert_get_wire(
        entries[1],
        "getVksDeployment",
        (
            f"/apis/apps/v1/namespaces/{workload_namespace}"
            f"/deployments/{deployment}"
        ),
        "Authorization",
        f"Bearer {token}",
        "vmware-api-session-id",
    )

    if scenario == "unstable":
        return

    create = entries[2]
    require(create["operation"] == "createSupervisorBackup", "backup operation")
    require(create["method"] == "POST", "backup method")
    require(
        create["rawTarget"]
        == (
            "/api/vcenter/namespace-management/supervisors/"
            f"{supervisor}/recovery/backup/jobs"
        ),
        "backup raw target",
    )
    require("?" not in create["rawTarget"], "backup query")
    require(header_values(create, "Accept") == ["application/json"], "backup Accept")
    require(
        header_values(create, "Content-Type") == ["application/json"],
        "backup Content-Type",
    )
    require(
        header_values(create, "vmware-api-session-id") == [session],
        "backup session",
    )
    require(header_values(create, "Authorization") == [], "backup bearer leak")

    if scenario == "explicit_false":
        expected_body = b'{"ignore_health_check_failure":false}'
    else:
        expected_body = json.dumps(
            {"comment": config["comment"]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    actual_body = body_bytes(create)
    require(actual_body == expected_body, f"backup body wire shape for {scenario}")
    require(b"null" not in actual_body, "null optional field sent")
    if scenario != "explicit_false":
        require(b"ignore_health_check_failure" not in actual_body, "unset bool sent")
    else:
        require(b'"comment"' not in actual_body, "unset comment sent")

    expected_task_target = f"/api/cis/tasks/{task_id}"
    for entry in entries[3:]:
        assert_get_wire(
            entry,
            "getTask",
            expected_task_target,
            "vmware-api-session-id",
            session,
            "Authorization",
        )
        require("spec" not in entry["rawTarget"], "unset task spec sent")


def read_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def run_scenario(
    build_dir: Path,
    scenario: str,
    expected_polls: int,
    max_polls: int,
    use_loopback: bool,
) -> None:
    marker = secrets.token_hex(6)
    config: dict[str, object] = {
        "scenario": scenario,
        "session": f"session-{secrets.token_urlsafe(19)}",
        "token": f"vks.{secrets.token_urlsafe(23)}",
        "supervisorNamespace": f"tenant-{marker}",
        "supervisor": f"supervisor:{marker}/zone \u03a9",
        "workloadNamespace": f"apps-{marker}",
        "deployment": f"payments-{marker}",
        "comment": f'pre-upgrade "{marker}" \u03a9',
        "taskId": f"task:{marker}/phase one",
        "generation": 10 + secrets.randbelow(500),
        "replicas": 2 + secrets.randbelow(4),
        "cpuUsed": 100 + secrets.randbelow(500),
        "memoryUsed": 512 + secrets.randbelow(1024),
        "storageUsed": 2048 + secrets.randbelow(4096),
    }

    with tempfile.TemporaryDirectory(prefix=f"vcf91-{scenario}-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        log_path.touch()

        mock: subprocess.Popen[str] | None = None
        if use_loopback:
            mock = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    str(MOCK),
                    "--contract",
                    str(CONTRACT),
                    "--config",
                    str(config_path),
                    "--log",
                    str(log_path),
                    "--ready",
                    str(ready_path),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 5
            while not ready_path.exists():
                if mock.poll() is not None:
                    stdout, stderr = mock.communicate()
                    raise VerificationError(
                        f"mock exited early: {stdout[-300:]} {stderr[-500:]}"
                    )
                if time.monotonic() >= deadline:
                    raise VerificationError("mock did not become ready")
                time.sleep(0.01)
            endpoint = json.loads(ready_path.read_text(encoding="utf-8"))["endpoint"]
        else:
            endpoint = "http://127.0.0.1:9"

        command = [
            "java",
            "-cp",
            str(build_dir),
            "TestMain",
            scenario,
            endpoint,
            str(config["session"]),
            str(config["token"]),
            str(config["supervisorNamespace"]),
            str(config["supervisor"]),
            str(config["workloadNamespace"]),
            str(config["deployment"]),
            str(config["comment"]),
            str(expected_polls),
            str(max_polls),
        ]
        if not use_loopback:
            command.extend(
                [
                    "in-memory",
                    str(config["taskId"]),
                    str(log_path),
                    str(CONTRACT),
                ]
            )

        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=15,
                env={**os.environ, "LC_ALL": "C.UTF-8"},
            )
            require(
                completed.returncode == 0,
                (
                    f"TestMain failed for {scenario}\n"
                    f"stdout:\n{completed.stdout[-1200:]}\n"
                    f"stderr:\n{completed.stderr[-2400:]}"
                ),
            )
            require(
                f"TEST_MAIN_OK {scenario}" in completed.stdout,
                f"missing TestMain success marker for {scenario}",
            )
        finally:
            if mock is not None:
                mock.terminate()
                try:
                    mock.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.communicate(timeout=3)

        assert_request_log(scenario, config, read_log(log_path), max_polls)


def compile_sources(build_dir: Path) -> None:
    java_files = sorted(path.name for path in ROOT.glob("*.java"))
    require(
        java_files == ["TestMain.java", "VksSupervisorBackupClient.java"],
        "add no other Java source file",
    )
    completed = subprocess.run(
        [
            "javac",
            "--release",
            "17",
            "-encoding",
            "UTF-8",
            "-d",
            str(build_dir),
            str(CLIENT),
            str(TEST_MAIN),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    require(
        completed.returncode == 0,
        f"javac failed:\n{completed.stdout}\n{completed.stderr}",
    )


def main() -> None:
    verify_protected_files()
    verify_contract()
    require(CLIENT.is_file(), "missing VksSupervisorBackupClient.java")

    with tempfile.TemporaryDirectory(prefix="vcf91-java-build-") as build_name:
        build_dir = Path(build_name)
        compile_sources(build_dir)
        use_loopback = loopback_available()
        run_scenario(build_dir, "happy", 3, 6, use_loopback)
        run_scenario(build_dir, "explicit_false", 1, 4, use_loopback)
        run_scenario(build_dir, "task_failed", 0, 5, use_loopback)
        run_scenario(build_dir, "unstable", 0, 5, use_loopback)
        run_scenario(build_dir, "namespace_not_ready", 0, 5, use_loopback)
        run_scenario(build_dir, "poll_limit", 0, 2, use_loopback)

    print("PASS: VCF 9.1 Supervisor backup client contract verified")


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
