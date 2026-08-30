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
CLIENT = ROOT / "src" / "VksNamespaceBackupClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATION_IDS = [
    "Vcenter.Namespaces.Instances_getV2",
    "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create",
    "Cis.Tasks_get",
]
EXPECTED_CONTRACT_NAMES = [
    "getSupervisorNamespace",
    "listVksClusters",
    "createSupervisorBackup",
    "getTask",
]
KUBERNETES_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)

# The harness protects verify.py. These hashes additionally make every other
# protected fixture deterministic.
PROTECTED_SHA256 = {
    "docs/contract.json": "91399945eb2e67645d2d08ce7ced1f893f5f1034dbabb9368a000a9b33594e4c",
    "docs/official_sources.json": "d0b242146c9717605eb3b36a8c5d849be6c2db30ad9df9f2f8ef8da988a1636a",
    "tests/TestMain.java": "4f644cf66e3c7fb57c617c82c28de94f6f392b240feadd88d4dfb7450b53df61",
    "tools/contract_mock.py": "07066cfdcdab3143556368bb15f58d4a3792672925a8439b3208a2d3e6c90a82",
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
        require(
            sha256(path) == expected,
            f"protected file changed: {relative}",
        )


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    source = contract.get("source", {})

    for item in (source, sources):
        require(
            item.get("repositoryCommitSha") == PINNED_COMMIT,
            "wrong source commit",
        )
        require(item.get("specBlobSha") == PINNED_BLOB, "wrong spec blob")
        require(item.get("specPath") == PINNED_SPEC, "wrong spec path")
        require(item.get("license") == "Apache-2.0", "wrong source license")

    require(
        sources.get("operationIds") == EXPECTED_OPERATION_IDS,
        "official operationId list changed",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and [item.get("operationId") for item in source_operations]
        == EXPECTED_OPERATION_IDS,
        "official operation records changed",
    )
    for item in source_operations:
        require(
            item.get("repositoryCommitSha") == PINNED_COMMIT,
            "operation record missing commit",
        )
        require(
            item.get("specPath") == PINNED_SPEC,
            "operation record missing spec path",
        )

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations missing")
    require(
        [operation.get("contractName") for operation in operations]
        == EXPECTED_CONTRACT_NAMES,
        "contract operation order changed",
    )
    projected = [
        operation.get("operationId")
        for operation in operations
        if operation.get("sourceKind") == "vcenter-openapi-operation"
    ]
    require(projected == EXPECTED_OPERATION_IDS, "projected operationIds changed")
    native = operations[1]
    require(
        native.get("sourceKind") == "native-kubernetes-api",
        "Kubernetes provenance changed",
    )
    require("operationId" not in native, "fictional Kubernetes operationId")
    require(
        native.get("operationKey") == KUBERNETES_OPERATION,
        "Kubernetes operation key changed",
    )


def encode_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="-._~", encoding="utf-8", errors="strict")


def header_values(entry: dict[str, object], name: str) -> list[str]:
    wanted = name.casefold()
    return [
        pair[1]
        for pair in entry["headers"]
        if pair[0].casefold() == wanted
    ]


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
    require(
        header_values(entry, "Accept") == ["application/json"],
        f"wrong Accept for {operation}",
    )
    require(
        header_values(entry, auth_header) == [auth_value],
        f"wrong authentication for {operation}",
    )
    require(
        header_values(entry, forbidden_auth) == [],
        f"credential crossed control planes for {operation}",
    )
    require(
        header_values(entry, "Content-Type") == [],
        f"GET Content-Type sent for {operation}",
    )
    require(
        header_values(entry, "Content-Length") == [],
        f"GET Content-Length sent for {operation}",
    )
    require(
        header_values(entry, "Transfer-Encoding") == [],
        f"GET transfer encoding sent for {operation}",
    )


def expected_operations(scenario: str, max_polls: int) -> list[str]:
    prefix = [
        "getSupervisorNamespace",
        "listVksClusters",
        "createSupervisorBackup",
    ]
    if scenario == "validation":
        return []
    if scenario == "namespace_not_ready":
        return ["getSupervisorNamespace"]
    if scenario == "malformed_cluster":
        return ["getSupervisorNamespace", "listVksClusters"]
    if scenario == "api_error":
        return prefix
    if scenario == "task_failed":
        return prefix + ["getTask", "getTask"]
    if scenario == "poll_timeout":
        return prefix + ["getTask"] * max_polls
    if scenario == "malformed_task":
        return prefix + ["getTask"]
    if scenario in {"empty_comment", "inventory_changed", "result_value"}:
        return prefix + ["getTask", "listVksClusters"]
    if scenario == "happy":
        return prefix + ["getTask"] * 4 + ["listVksClusters"]
    raise VerificationError(f"unknown scenario: {scenario}")


def assert_request_log(
    scenario: str,
    config: dict[str, object],
    entries: list[dict[str, object]],
    max_polls: int,
    use_loopback: bool,
) -> None:
    require(
        [entry["operation"] for entry in entries]
        == expected_operations(scenario, max_polls),
        f"wrong operation sequence for {scenario}",
    )
    require(
        all(entry["operation"] is not None for entry in entries),
        "off-contract route was invoked",
    )
    if not entries:
        return

    namespace = encode_segment(str(config["namespace"]))
    supervisor = encode_segment(str(config["supervisor"]))
    task_id = encode_segment(str(config["taskId"]))
    session = str(config["session"])
    token = str(config["token"])

    assert_get_wire(
        entries[0],
        "getSupervisorNamespace",
        f"/api/vcenter/namespaces/instances/v2/{namespace}",
        "vmware-api-session-id",
        session,
        "Authorization",
    )
    if scenario == "namespace_not_ready":
        return

    assert_get_wire(
        entries[1],
        "listVksClusters",
        (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            f"{namespace}/clusters"
        ),
        "Authorization",
        f"Bearer {token}",
        "vmware-api-session-id",
    )
    if scenario == "malformed_cluster":
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
    require(
        header_values(create, "Accept") == ["application/json"],
        "backup Accept",
    )
    require(
        header_values(create, "Content-Type") == ["application/json"],
        "backup Content-Type",
    )
    require(
        header_values(create, "vmware-api-session-id") == [session],
        "backup session",
    )
    require(header_values(create, "Authorization") == [], "backup bearer leak")
    require(
        header_values(create, "Transfer-Encoding") == [],
        "backup transfer encoding",
    )
    expected_body = (
        b"{}"
        if config["comment"] is None
        else json.dumps(
            {"comment": config["comment"]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    require(body_bytes(create) == expected_body, "backup body wire shape")
    if use_loopback:
        require(
            header_values(create, "Content-Length") == [str(len(expected_body))],
            "backup Content-Length",
        )
    else:
        require(
            header_values(create, "Content-Length") == [],
            "fallback request declared Content-Length",
        )
    if scenario == "api_error":
        return

    for entry in entries[3:]:
        if entry["operation"] == "getTask":
            assert_get_wire(
                entry,
                "getTask",
                f"/api/cis/tasks/{task_id}",
                "vmware-api-session-id",
                session,
                "Authorization",
            )
            for unset in ("spec", "return_all", "exclude_result"):
                require(
                    unset not in entry["rawTarget"],
                    f"unset task field sent: {unset}",
                )
        elif entry["operation"] == "listVksClusters":
            assert_get_wire(
                entry,
                "listVksClusters",
                (
                    "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                    f"{namespace}/clusters"
                ),
                "Authorization",
                f"Bearer {token}",
                "vmware-api-session-id",
            )
            require("pretty" not in entry["rawTarget"], "unset pretty sent")
        else:
            raise VerificationError("unexpected operation after backup")


def read_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def compile_sources(build_dir: Path) -> None:
    java_files = sorted(
        str(path.relative_to(ROOT)) for path in ROOT.rglob("*.java")
    )
    require(
        java_files
        == ["src/VksNamespaceBackupClient.java", "tests/TestMain.java"],
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
        timeout=25,
    )
    require(
        completed.returncode == 0,
        f"javac failed:\n{completed.stdout}\n{completed.stderr}",
    )


def scenario_parameters(scenario: str) -> tuple[int, int, object, bool]:
    if scenario == "happy":
        return 4, 7, 'nightly "guard" Ω', False
    if scenario == "empty_comment":
        return 1, 3, "", True
    if scenario == "task_failed":
        return 2, 5, None, False
    if scenario == "poll_timeout":
        return 0, 3, None, True
    if scenario in {
        "namespace_not_ready",
        "inventory_changed",
        "malformed_cluster",
        "malformed_task",
        "api_error",
        "result_value",
        "validation",
    }:
        expected = 1 if scenario in {
            "inventory_changed",
            "malformed_task",
            "result_value",
        } else 0
        return expected, 4, None, scenario in {
            "inventory_changed",
            "result_value",
        }
    raise VerificationError(f"unknown scenario parameters: {scenario}")


def loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def run_scenario(
    build_dir: Path, scenario: str, use_loopback: bool
) -> None:
    expected_polls, max_polls, comment, initial_reverse = scenario_parameters(
        scenario
    )
    marker = secrets.token_hex(7)
    clusters = [
        {"name": f"zeta-{marker}", "version": f"v1.31.{secrets.randbelow(8)}"},
        {"name": f"Alpha-{marker}", "version": f"v1.30.{secrets.randbelow(8)}"},
        {"name": f"beta-{marker}", "version": f"v1.29.{secrets.randbelow(8)}"},
    ]
    config: dict[str, object] = {
        "scenario": scenario,
        "session": f"vc-session-{secrets.token_urlsafe(19)}",
        "token": f"vks.{secrets.token_urlsafe(23)}",
        "namespace": f"tenant:{marker}/zone Ω",
        "supervisor": f"supervisor:{marker}/domain Ω",
        "taskId": f"task:{marker}/phase one",
        "comment": comment,
        "resultMarker": f"result-{secrets.token_urlsafe(9)}",
        "cpuUsed": 100 + secrets.randbelow(500),
        "memoryUsed": 512 + secrets.randbelow(1024),
        "storageUsed": 2048 + secrets.randbelow(4096),
        "clusters": clusters,
        "initialReverse": initial_reverse,
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
        try:
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
                            f"mock exited early: {stdout[-300:]} {stderr[-600:]}"
                        )
                    if time.monotonic() >= deadline:
                        raise VerificationError("mock did not become ready")
                    time.sleep(0.01)
                endpoint = json.loads(
                    ready_path.read_text(encoding="utf-8")
                )["endpoint"]
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
                str(config["namespace"]),
                str(config["supervisor"]),
                str(config["taskId"]),
                "<null>" if comment is None else str(comment),
                str(config["resultMarker"]),
                str(expected_polls),
                str(max_polls),
            ]
            for cluster in clusters:
                command.extend([cluster["name"], cluster["version"]])
            if not use_loopback:
                command.extend([
                    "in-memory",
                    str(log_path),
                    str(CONTRACT),
                ])
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=18,
                env={**os.environ, "LC_ALL": "C.UTF-8"},
            )
            require(
                completed.returncode == 0,
                (
                    f"TestMain failed for {scenario}\n"
                    f"stdout:\n{completed.stdout[-1500:]}\n"
                    f"stderr:\n{completed.stderr[-3000:]}"
                ),
            )
            require(
                f"TEST_MAIN_OK {scenario}" in completed.stdout,
                f"missing TestMain marker for {scenario}",
            )
        finally:
            if mock is not None:
                mock.terminate()
                try:
                    mock.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.communicate(timeout=3)

        assert_request_log(
            scenario,
            config,
            read_log(log_path),
            max_polls,
            use_loopback,
        )


def main() -> None:
    verify_protected_files()
    verify_contract()
    require(CLIENT.is_file(), "missing production client")

    with tempfile.TemporaryDirectory(prefix="vcf91-java-build-") as build_name:
        build_dir = Path(build_name)
        compile_sources(build_dir)
        use_loopback = loopback_available()
        for scenario in (
            "happy",
            "empty_comment",
            "result_value",
            "task_failed",
            "poll_timeout",
            "namespace_not_ready",
            "inventory_changed",
            "malformed_cluster",
            "malformed_task",
            "api_error",
            "validation",
        ):
            run_scenario(build_dir, scenario, use_loopback)

    print("PASS: VCF 9.1 Supervisor backup and sorted VKS inventory verified")


if __name__ == "__main__":
    try:
        main()
    except (
        VerificationError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
