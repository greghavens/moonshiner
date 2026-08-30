#!/usr/bin/env python3
"""Protected acceptance verifier for the focused VCF 9.1 Python task."""

from __future__ import annotations

import ast
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "mock_vcenter.py"
PACKAGE_ROOT = ROOT / "vcf_resize_report"
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_SPEC_PATH = (
    "specifications/vsphere/openapi/automation/vcenter.yaml"
)
EXPECTED_OPERATION_IDS = [
    "Vcenter.Vm.Hardware.Cpu_update",
    "Vcenter.Vm.Hardware.Memory_update",
    "Vcenter.Vm.Power_start",
]
EXPECTED_METHODS = ["PATCH", "PATCH", "POST"]
EXPECTED_PATHS = [
    "/api/vcenter/vm/{vm}/hardware/cpu",
    "/api/vcenter/vm/{vm}/hardware/memory",
    "/api/vcenter/vm/{vm}/power?action=start",
]
INT64_MAX = (1 << 63) - 1


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
        source.get("kind") == "pinned-openapi-specification"
        and source.get("repository") == "vmware/vcf-api-specs"
        and source.get("commitSha") == EXPECTED_COMMIT
        and source.get("specBlobSha") == EXPECTED_BLOB
        and source.get("specPath") == EXPECTED_SPEC_PATH
        and source.get("openapi") == "3.0.3"
        and source.get("apiVersion") == "9.1.0.0"
        and source.get("serverTemplate") == "https://{host}/api"
        and source.get("basePath") == "/api"
        and source.get("license") == "Apache-2.0",
        "contract source pin changed",
    )
    require(
        contract.get("securitySchemes", {}).get("api_key_auth")
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "contract security scheme changed",
    )

    operations = contract.get("operations")
    require(
        isinstance(operations, list) and len(operations) == 3,
        "contract must contain exactly three operations",
    )
    require(
        [item.get("operationId") for item in operations]
        == EXPECTED_OPERATION_IDS,
        "contract operationIds or workflow order changed",
    )
    require(
        [item.get("method") for item in operations] == EXPECTED_METHODS,
        "contract methods changed",
    )
    require(
        [item.get("path") for item in operations] == EXPECTED_PATHS,
        "contract API paths changed",
    )
    for index, operation in enumerate(operations):
        require(
            operation.get("parameters")
            == [
                {
                    "name": "vm",
                    "in": "path",
                    "required": True,
                    "type": "string",
                    "resourceType": "VirtualMachine",
                }
            ],
            f"path contract changed for {EXPECTED_OPERATION_IDS[index]}",
        )
        require(
            operation.get("security") == ["api_key_auth"],
            f"security contract changed for {EXPECTED_OPERATION_IDS[index]}",
        )
        responses = operation.get("responses")
        require(
            isinstance(responses, dict)
            and responses.get("204") == {"content": False}
            and responses.get("503")
            == {
                "contentType": "application/json",
                "schema": "Vapi.Std.Errors.ServiceUnavailable",
            },
            f"response contract changed for {EXPECTED_OPERATION_IDS[index]}",
        )
    require(
        operations[0].get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "Vcenter.Vm.Hardware.Cpu.UpdateSpec",
        },
        "CPU request contract changed",
    )
    require(
        operations[1].get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "Vcenter.Vm.Hardware.Memory.UpdateSpec",
        },
        "memory request contract changed",
    )
    require(
        operations[2].get("requestBody") is False,
        "power start must remain bodyless",
    )

    schemas = contract.get("schemas")
    require(isinstance(schemas, dict), "contract schemas are missing")
    cpu = schemas.get("Vcenter.Vm.Hardware.Cpu.UpdateSpec")
    memory = schemas.get("Vcenter.Vm.Hardware.Memory.UpdateSpec")
    require(
        isinstance(cpu, dict)
        and cpu.get("required") == []
        and list(cpu.get("properties", {}))
        == [
            "count",
            "cores_per_socket",
            "hot_add_enabled",
            "hot_remove_enabled",
        ],
        "CPU update schema projection changed",
    )
    require(
        isinstance(memory, dict)
        and memory.get("required") == []
        and list(memory.get("properties", {}))
        == ["size_mib", "hot_add_enabled"],
        "memory update schema projection changed",
    )
    for schema in (cpu, memory):
        for item in schema["properties"].values():
            require(
                item.get("required") is False
                and item.get("unsetBehavior") == "unchanged",
                "optional update-field semantics changed",
            )
    error_schema = schemas.get("Vapi.Std.Errors.Error")
    message_schema = schemas.get("Vapi.Std.LocalizableMessage")
    require(
        isinstance(error_schema, dict)
        and error_schema.get("required") == ["error_type", "messages"],
        "standard error schema projection changed",
    )
    require(
        isinstance(message_schema, dict)
        and message_schema.get("required")
        == ["args", "default_message", "id"],
        "localizable-message schema projection changed",
    )

    require(
        sources.get("repository") == "vmware/vcf-api-specs"
        and sources.get("repositoryCommitSha") == EXPECTED_COMMIT
        and sources.get("specBlobSha") == EXPECTED_BLOB
        and sources.get("specPath") == EXPECTED_SPEC_PATH
        and sources.get("license") == "Apache-2.0"
        and sources.get("operationIds") == EXPECTED_OPERATION_IDS,
        "official source metadata changed",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and len(source_operations) == len(EXPECTED_OPERATION_IDS),
        "official operation records are incomplete",
    )
    for index, operation_id in enumerate(EXPECTED_OPERATION_IDS):
        record = source_operations[index]
        require(
            record.get("operationId") == operation_id
            and record.get("method") == EXPECTED_METHODS[index]
            and record.get("path") == operations[index]["specPathItem"]
            and record.get("repositoryCommitSha") == EXPECTED_COMMIT
            and record.get("specPath") == EXPECTED_SPEC_PATH,
            f"official source record for {operation_id} is not pinned",
        )


def verify_stdlib_only() -> None:
    require(PACKAGE_ROOT.is_dir(), "vcf_resize_report package is missing")
    python_files = sorted(PACKAGE_ROOT.rglob("*.py"))
    require(python_files, "vcf_resize_report contains no Python files")
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    for path in python_files:
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
        except SyntaxError as error:
            raise VerificationFailure(f"{path} has invalid Python: {error}")
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                roots = [(node.module or "").split(".", 1)[0]]
            for root in roots:
                require(
                    root in stdlib or root == "vcf_resize_report",
                    f"{path} imports non-stdlib module {root}",
                )


def wait_for_port(process: subprocess.Popen[str], path: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            raise VerificationFailure(
                f"loopback mock exited before startup: {stderr.strip()}"
            )
        if path.exists():
            text = path.read_text(encoding="ascii").strip()
            if text:
                port = int(text)
                require(0 < port < 65536, "mock returned an invalid port")
                return port
        time.sleep(0.01)
    raise VerificationFailure("timed out waiting for loopback mock startup")


def read_log(path: Path, count: int) -> list[dict]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if path.exists():
            lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if len(lines) >= count:
                return [json.loads(line) for line in lines]
        time.sleep(0.01)
    raise VerificationFailure(f"request log did not reach {count} entries")


def expect_raises(
    error_type: type[BaseException],
    action,
    message: str,
) -> BaseException:
    try:
        action()
    except error_type as error:
        return error
    except Exception as error:
        raise VerificationFailure(
            f"{message}: raised {type(error).__name__}, expected "
            f"{error_type.__name__}"
        ) from error
    raise VerificationFailure(f"{message}: did not raise {error_type.__name__}")


def verify_public_shape(
    ResizeClient,
    ResizeReport,
    StepResult,
    ProtocolError,
) -> None:
    require(
        [item.name for item in fields(StepResult)]
        == [
            "name",
            "operation_id",
            "state",
            "http_status",
            "error_type",
            "message",
        ],
        "StepResult fields or order changed",
    )
    require(
        [item.name for item in fields(ResizeReport)]
        == [
            "vm",
            "overall_state",
            "completed_step_count",
            "failed_operation_id",
            "steps",
        ],
        "ResizeReport fields or order changed",
    )
    require(
        isinstance(ProtocolError, type)
        and issubclass(ProtocolError, RuntimeError),
        "ProtocolError must remain a RuntimeError",
    )
    require(callable(ResizeClient), "ResizeClient is not constructible")


def verify_constructor_validation(ResizeClient) -> None:
    invalid_origins = [
        "",
        "ftp://127.0.0.1",
        "http://user@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?x=1",
        "http://127.0.0.1#fragment",
        "http://127.0.0.1:99999",
    ]
    for origin in invalid_origins:
        expect_raises(
            ValueError,
            lambda origin=origin: ResizeClient(origin, "token"),
            f"invalid base_url {origin!r}",
        )
    for token in ["", "   ", "line\rbreak", "line\nbreak", "snowman-\u2603"]:
        expect_raises(
            ValueError,
            lambda token=token: ResizeClient("http://127.0.0.1", token),
            "blank or header-unsafe session token",
        )
    for timeout in [True, 0, -1, math.inf, -math.inf, math.nan]:
        expect_raises(
            ValueError,
            lambda timeout=timeout: ResizeClient(
                "http://127.0.0.1", "token", timeout=timeout
            ),
            f"invalid timeout {timeout!r}",
        )


def verify_invalid_calls(client, log_file: Path) -> None:
    invalid_calls = [
        lambda: client.resize_and_start("", 2, 4096),
        lambda: client.resize_and_start(None, 2, 4096),
        lambda: client.resize_and_start("vm", True, 4096),
        lambda: client.resize_and_start("vm", 0, 4096),
        lambda: client.resize_and_start("vm", INT64_MAX + 1, 4096),
        lambda: client.resize_and_start("vm", 2, False),
        lambda: client.resize_and_start("vm", 2, 0),
        lambda: client.resize_and_start("vm", 2, INT64_MAX + 1),
    ]
    for index, action in enumerate(invalid_calls, start=1):
        expect_raises(ValueError, action, f"invalid workflow input {index}")
    require(
        not log_file.exists()
        or not log_file.read_text(encoding="utf-8").strip(),
        "validation or client construction emitted an HTTP request",
    )


def verify_report(
    result: object,
    scenario: dict,
    ResizeReport,
    StepResult,
) -> None:
    require(type(result) is ResizeReport, "result must be a ResizeReport")
    require(result.vm == scenario["vm"], "report VM is incorrect")
    require(result.overall_state == "FAILED", "overall state is incorrect")
    require(
        result.completed_step_count == 2,
        "completed step count must preserve both successful updates",
    )
    require(
        result.failed_operation_id == EXPECTED_OPERATION_IDS[2],
        "failed operationId is incorrect",
    )
    require(type(result.steps) is tuple, "report steps must be a tuple")
    require(
        len(result.steps) == 3
        and all(type(item) is StepResult for item in result.steps),
        "report must contain exactly three attempted StepResult values",
    )
    require(
        [item.name for item in result.steps] == ["Cpu", "Memory", "PowerStart"],
        "step names or order are incorrect",
    )
    require(
        [item.operation_id for item in result.steps]
        == EXPECTED_OPERATION_IDS,
        "step operationIds or order are incorrect",
    )
    require(
        [item.state for item in result.steps]
        == ["SUCCEEDED", "SUCCEEDED", "FAILED"],
        "step states are incorrect",
    )
    require(
        [item.http_status for item in result.steps] == [204, 204, 503],
        "step HTTP statuses are incorrect",
    )
    require(
        [
            (item.error_type, item.message)
            for item in result.steps[:2]
        ]
        == [(None, None), (None, None)],
        "successful steps must not invent error details",
    )
    require(
        result.steps[2].error_type == "SERVICE_UNAVAILABLE"
        and result.steps[2].message == scenario["power_error_message"],
        "failed step did not preserve the standard vAPI error",
    )
    try:
        result.overall_state = "SUCCEEDED"
    except FrozenInstanceError:
        pass
    else:
        raise VerificationFailure("ResizeReport must remain immutable")
    try:
        result.steps[0].state = "FAILED"
    except FrozenInstanceError:
        pass
    else:
        raise VerificationFailure("StepResult must remain immutable")


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
    bodies = [cpu_body, memory_body, b""]

    require(len(entries) == 3, "workflow must emit exactly three requests")
    require(
        [item["operationId"] for item in entries]
        == EXPECTED_OPERATION_IDS,
        "operation order changed",
    )
    require(
        [item["method"] for item in entries] == EXPECTED_METHODS,
        "HTTP methods are incorrect",
    )
    require(
        [item["rawTarget"] for item in entries]
        == [
            f"/api/vcenter/vm/{encoded_vm}/hardware/cpu",
            f"/api/vcenter/vm/{encoded_vm}/hardware/memory",
            f"/api/vcenter/vm/{encoded_vm}/power?action=start",
        ],
        "raw targets or VM path encoding are incorrect",
    )
    require(
        [item["rawQuery"] for item in entries] == ["", "", "action=start"],
        "query strings are incorrect",
    )
    require(
        [item["status"] for item in entries] == [204, 204, 503],
        "mock did not observe the required partial-failure sequence",
    )
    require(
        all(
            item["sequenceIndex"] == index
            and item["sequenceValid"] is True
            and item["requestValid"] is True
            for index, item in enumerate(entries)
        ),
        "requests were repeated, skipped, out of order, or malformed",
    )

    for index, (entry, body) in enumerate(zip(entries, bodies), start=1):
        require(
            entry["sessionHeaderCount"] == 1
            and entry["vmwareApiSessionId"] == scenario["session_token"],
            f"request {index} has the wrong session header",
        )
        require(
            entry["authorizationHeaderCount"] == 0
            and entry["authorization"] is None
            and "authorization" not in entry["headerNames"],
            f"request {index} must not use Authorization",
        )
        require(
            entry["acceptHeaderCount"] == 1
            and entry["accept"] == "application/json",
            f"request {index} has the wrong Accept header",
        )
        require(
            entry["contentLength"] == len(body)
            and entry["bodyHex"] == body.hex(),
            f"request {index} has incorrect body bytes",
        )

    for index in (0, 1):
        entry = entries[index]
        require(
            entry["contentTypeHeaderCount"] == 1
            and entry["contentType"] == "application/json",
            f"PATCH request {index + 1} has the wrong Content-Type",
        )
        require(
            entry["declaredContentLength"] == str(len(bodies[index])),
            f"PATCH request {index + 1} has the wrong Content-Length",
        )
        decoded = json.loads(bytes.fromhex(entry["bodyHex"]).decode("utf-8"))
        expected = (
            {"count": scenario["cpu_count"]}
            if index == 0
            else {"size_mib": scenario["memory_mib"]}
        )
        require(
            decoded == expected and list(decoded) == list(expected),
            f"PATCH request {index + 1} sent an unset optional field",
        )

    power = entries[2]
    require(
        power["contentTypeHeaderCount"] == 0
        and power["contentType"] is None
        and "content-type" not in power["headerNames"]
        and power["contentLength"] == 0
        and power["bodyHex"] == "",
        "power start must be bodyless and omit Content-Type",
    )


def main() -> int:
    contract = load_object(CONTRACT_PATH)
    sources = load_object(SOURCES_PATH)
    verify_provenance(contract, sources)
    verify_stdlib_only()

    sys.path.insert(0, str(ROOT))
    try:
        import vcf_resize_report as package
    except Exception as error:
        raise VerificationFailure(
            f"could not import vcf_resize_report: {error}"
        ) from error
    expected_exports = {
        "ResizeClient",
        "ResizeReport",
        "StepResult",
        "ProtocolError",
    }
    require(
        set(package.__all__) == expected_exports
        and all(hasattr(package, name) for name in expected_exports),
        "public exports changed",
    )
    ResizeClient = package.ResizeClient
    ResizeReport = package.ResizeReport
    StepResult = package.StepResult
    ProtocolError = package.ProtocolError
    verify_public_shape(
        ResizeClient, ResizeReport, StepResult, ProtocolError
    )
    verify_constructor_validation(ResizeClient)

    nonce = secrets.token_hex(14)
    scenario = {
        "session_token": f"session-{nonce}",
        "vm": f"vm /edge?{nonce[:9]}\u2603",
        "cpu_count": 5 + secrets.randbelow(7),
        "memory_mib": 24576 + (1024 * secrets.randbelow(9)),
        "power_error_message": f"capacity unavailable {nonce}",
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0103-") as temp:
        temp_path = Path(temp)
        port_file = temp_path / "port"
        log_file = temp_path / "requests.jsonl"
        scenario_file = temp_path / "scenario.json"
        scenario_file.write_text(
            json.dumps(
                scenario, separators=(",", ":"), ensure_ascii=False
            ),
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_port(process, port_file)
            base_url = f"http://127.0.0.1:{port}"

            old_proxy = {
                name: os.environ.get(name)
                for name in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "NO_PROXY",
                    "no_proxy",
                )
            }
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
            os.environ["http_proxy"] = "http://127.0.0.1:9"
            os.environ["https_proxy"] = "http://127.0.0.1:9"
            os.environ["NO_PROXY"] = ""
            os.environ["no_proxy"] = ""
            try:
                client = ResizeClient(
                    base_url, scenario["session_token"], timeout=2.0
                )
                verify_invalid_calls(client, log_file)
                result = client.resize_and_start(
                    scenario["vm"],
                    scenario["cpu_count"],
                    scenario["memory_mib"],
                )
            finally:
                for name, value in old_proxy.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

            verify_report(result, scenario, ResizeReport, StepResult)
            entries = read_log(log_file, 3)
            time.sleep(0.15)
            final_entries = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line
            ]
            require(
                len(final_entries) == 3,
                "workflow retried, rolled back, or issued an extra request",
            )
            verify_requests(entries, scenario)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            if process.returncode not in {0, -15}:
                _stdout, stderr = process.communicate()
                raise VerificationFailure(
                    f"loopback mock failed: {stderr.strip()}"
                )

        transport_client = ResizeClient(
            base_url, scenario["session_token"], timeout=0.25
        )
        error = expect_raises(
            ProtocolError,
            lambda: transport_client.resize_and_start("vm-probe", 2, 4096),
            "transport failure",
        )
        require(
            getattr(error, "operation_id", None)
            == EXPECTED_OPERATION_IDS[0],
            "ProtocolError did not preserve the active operationId",
        )
        visible = f"{error!s}\n{error!r}"
        require(
            scenario["session_token"] not in visible
            and scenario["power_error_message"] not in visible
            and nonce not in visible,
            "ProtocolError exposes protected request or response data",
        )

    print("verification passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
