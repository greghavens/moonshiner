#!/usr/bin/env python3
"""Protected deterministic verification for the vCenter refresh workflow."""

from __future__ import annotations

import ast
import importlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_vcenter.py"
EXPECTED_OPERATION_IDS = [
    "Vcenter.Vm.Hardware.Cpu_update",
    "Vcenter.Vm.Hardware.Memory_update",
    "Vcenter.Vm.Power_start",
]
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = (
    "specifications/vsphere/openapi/automation/vcenter.yaml"
)


class VerificationFailure(AssertionError):
    """A protected acceptance assertion failed."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def verify_provenance(contract: dict, sources: dict) -> None:
    source = contract.get("source")
    require(isinstance(source, dict), "contract source metadata is missing")
    require(
        source.get("commitSha") == EXPECTED_COMMIT,
        "contract commit SHA changed",
    )
    require(
        source.get("specPath") == EXPECTED_SPEC_PATH,
        "contract spec path changed",
    )
    operations = contract.get("operations")
    require(
        isinstance(operations, list),
        "contract operations must be an array",
    )
    require(
        [item.get("operationId") for item in operations]
        == EXPECTED_OPERATION_IDS,
        "contract operationIds or workflow order changed",
    )
    require(
        sources.get("repositoryCommitSha") == EXPECTED_COMMIT,
        "official source commit SHA changed",
    )
    require(
        sources.get("specPath") == EXPECTED_SPEC_PATH,
        "official source spec path changed",
    )
    require(
        sources.get("operationIds") == EXPECTED_OPERATION_IDS,
        "official source operationIds changed",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and len(source_operations) == len(EXPECTED_OPERATION_IDS),
        "official source operation records are incomplete",
    )
    for index, operation_id in enumerate(EXPECTED_OPERATION_IDS):
        record = source_operations[index]
        require(
            record.get("operationId") == operation_id
            and record.get("repositoryCommitSha") == EXPECTED_COMMIT
            and record.get("specPath") == EXPECTED_SPEC_PATH,
            f"official source record for {operation_id} is not pinned",
        )

    schemas = contract.get("schemas")
    require(isinstance(schemas, dict), "contract schemas are missing")
    cpu_properties = schemas.get(
        "Vcenter.Vm.Hardware.Cpu.UpdateSpec", {}
    ).get("properties")
    memory_properties = schemas.get(
        "Vcenter.Vm.Hardware.Memory.UpdateSpec", {}
    ).get("properties")
    require(
        isinstance(cpu_properties, dict)
        and list(cpu_properties)
        == [
            "count",
            "cores_per_socket",
            "hot_add_enabled",
            "hot_remove_enabled",
        ],
        "CPU update contract properties changed",
    )
    require(
        isinstance(memory_properties, dict)
        and list(memory_properties) == ["size_mib", "hot_add_enabled"],
        "memory update contract properties changed",
    )


def verify_stdlib_only(package_root: Path) -> None:
    require(package_root.is_dir(), "create the vcf_vcenter package")
    python_files = sorted(package_root.rglob("*.py"))
    require(python_files, "vcf_vcenter contains no Python source files")
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            raise VerificationFailure(f"{path} has invalid Python: {error}")
        for node in ast.walk(tree):
            root_name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".", 1)[0]
                    require(
                        root_name in stdlib or root_name == "vcf_vcenter",
                        f"{path} imports non-stdlib module {root_name}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                root_name = (node.module or "").split(".", 1)[0]
                require(
                    root_name in stdlib or root_name == "vcf_vcenter",
                    f"{path} imports non-stdlib module {root_name}",
                )


def wait_for_port(process: subprocess.Popen[str], port_file: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            raise VerificationFailure(
                f"loopback mock exited before startup: {stderr.strip()}"
            )
        if port_file.exists():
            text = port_file.read_text(encoding="utf-8").strip()
            if text:
                port = int(text)
                require(0 < port < 65536, "mock returned an invalid port")
                return port
        time.sleep(0.01)
    raise VerificationFailure("timed out waiting for loopback mock startup")


def read_log(log_file: Path, count: int) -> list[dict]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if log_file.exists():
            lines = [
                line
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if len(lines) >= count:
                return [json.loads(line) for line in lines]
        time.sleep(0.01)
    raise VerificationFailure(
        f"request log did not reach {count} entries"
    )


class RuntimeTokenProvider:
    """One-use initial credential followed by one forced refresh."""

    def __init__(self, initial_token: str, refreshed_token: str) -> None:
        self.initial_token = initial_token
        self.refreshed_token = refreshed_token
        self.calls: list[bool] = []

    def __call__(self, force_refresh: bool) -> str:
        require(
            type(force_refresh) is bool,
            "token_provider must receive a bool force_refresh argument",
        )
        self.calls.append(force_refresh)
        if self.calls == [False]:
            return self.initial_token
        if self.calls == [False, True]:
            return self.refreshed_token
        raise VerificationFailure("token_provider was called unexpectedly")


def verify_result(result: object, scenario: dict) -> None:
    require(type(result) is dict, "success result must be a new dictionary")
    require(
        list(result) == [
            "vm",
            "cpu_count",
            "memory_mib",
            "completed_operation_ids",
        ],
        "success result keys or key order are incorrect",
    )
    require(result["vm"] == scenario["vm"], "result VM is incorrect")
    require(
        result["cpu_count"] == scenario["cpu_count"],
        "result CPU count is incorrect",
    )
    require(
        result["memory_mib"] == scenario["memory_mib"],
        "result memory size is incorrect",
    )
    completed = result["completed_operation_ids"]
    require(
        type(completed) is list and completed == EXPECTED_OPERATION_IDS,
        "completed operationIds are incorrect",
    )


def verify_requests(entries: list[dict], scenario: dict) -> None:
    encoded_vm = quote(scenario["vm"], safe="")
    cpu_body = json.dumps(
        {"count": scenario["cpu_count"]},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    memory_body = json.dumps(
        {"size_mib": scenario["memory_mib"]},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    require(len(entries) == 4, "workflow must emit exactly four requests")
    require(
        [entry["operationId"] for entry in entries]
        == [
            "Vcenter.Vm.Hardware.Cpu_update",
            "Vcenter.Vm.Hardware.Memory_update",
            "Vcenter.Vm.Hardware.Memory_update",
            "Vcenter.Vm.Power_start",
        ],
        "operation order must retry only the interrupted memory update",
    )
    require(
        [entry["method"] for entry in entries]
        == ["PATCH", "PATCH", "PATCH", "POST"],
        "request methods are incorrect",
    )
    require(
        [entry["rawTarget"] for entry in entries]
        == [
            f"/api/vcenter/vm/{encoded_vm}/hardware/cpu",
            f"/api/vcenter/vm/{encoded_vm}/hardware/memory",
            f"/api/vcenter/vm/{encoded_vm}/hardware/memory",
            f"/api/vcenter/vm/{encoded_vm}/power?action=start",
        ],
        "request targets or VM path encoding are incorrect",
    )
    require(
        [entry["rawQuery"] for entry in entries]
        == ["", "", "", "action=start"],
        "request query strings are incorrect",
    )
    require(
        [entry["status"] for entry in entries] == [204, 401, 204, 204],
        "mock did not observe the required expiry and recovery sequence",
    )
    require(
        [entry["sequence"] for entry in entries] == [0, 1, 2, 3],
        "request sequence replayed or skipped work",
    )
    require(
        [entry["vmwareApiSessionId"] for entry in entries]
        == [
            scenario["initial_token"],
            scenario["initial_token"],
            scenario["refreshed_token"],
            scenario["refreshed_token"],
        ],
        "session credential continuity is incorrect",
    )

    expected_bodies = [cpu_body, memory_body, memory_body, b""]
    for index, (entry, body) in enumerate(zip(entries, expected_bodies)):
        require(
            entry["authorization"] is None
            and "authorization" not in entry["headerNames"],
            f"request {index + 1} must not use Authorization",
        )
        require(
            entry["accept"] == "application/json",
            f"request {index + 1} has the wrong Accept header",
        )
        require(
            entry["contentLength"] == len(body)
            and entry["bodyHex"] == body.hex(),
            f"request {index + 1} body bytes are incorrect",
        )

    for index in range(3):
        require(
            entries[index]["contentType"] == "application/json",
            f"PATCH request {index + 1} has the wrong Content-Type",
        )
    require(
        entries[3]["contentType"] is None
        and "content-type" not in entries[3]["headerNames"],
        "power-start POST must not send Content-Type",
    )

    require(
        entries[0]["bodyJson"] == {"count": scenario["cpu_count"]}
        and list(entries[0]["bodyJson"]) == ["count"],
        "CPU body must omit every unset optional property",
    )
    for index in (1, 2):
        require(
            entries[index]["bodyJson"]
            == {"size_mib": scenario["memory_mib"]}
            and list(entries[index]["bodyJson"]) == ["size_mib"],
            "memory body must omit every unset optional property",
        )
    require(
        entries[3]["bodyJson"] is None,
        "power-start POST must be bodyless",
    )


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def main() -> int:
    contract = load_object(CONTRACT_PATH)
    sources = load_object(SOURCES_PATH)
    verify_provenance(contract, sources)
    verify_stdlib_only(ROOT / "vcf_vcenter")

    sys.path.insert(0, str(ROOT))
    package = importlib.import_module("vcf_vcenter")
    require(
        package.__all__
        == ["VCenterClient", "VCenterError", "AuthenticationError"],
        "vcf_vcenter exports are incorrect",
    )
    for name in package.__all__:
        require(hasattr(package, name), f"vcf_vcenter does not expose {name}")

    nonce = secrets.token_hex(8)
    scenario = {
        "vm": f"vm/{nonce} snow \u03b2",
        "cpu_count": 4 + 2 * secrets.randbelow(5),
        "memory_mib": 12288 + 1024 * secrets.randbelow(9),
        "initial_token": f"session-initial-{secrets.token_urlsafe(18)}",
        "refreshed_token": f"session-refreshed-{secrets.token_urlsafe(18)}",
        "expired_message": f"session expired {secrets.token_hex(6)}",
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0100-") as temp_text:
        temp = Path(temp_text)
        port_file = temp / "port"
        log_file = temp / "requests.ndjson"
        scenario_file = temp / "scenario.json"
        scenario_file.write_text(
            json.dumps(scenario, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(log_file),
                str(CONTRACT_PATH),
                str(scenario_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(process, port_file)
            provider = RuntimeTokenProvider(
                scenario["initial_token"],
                scenario["refreshed_token"],
            )
            client = package.VCenterClient(
                f"http://127.0.0.1:{port}/",
                provider,
                timeout=3.0,
            )
            require(
                provider.calls == [],
                "client construction must not obtain a token",
            )
            result = client.resize_and_start(
                scenario["vm"],
                scenario["cpu_count"],
                scenario["memory_mib"],
            )
            require(
                provider.calls == [False, True],
                "workflow must obtain once and force exactly one refresh",
            )
            verify_result(result, scenario)
            entries = read_log(log_file, 4)
            verify_requests(entries, scenario)
        finally:
            stop_process(process)

    print(
        "protected verification passed: refreshed one expired vCenter "
        "session without replaying completed work"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
