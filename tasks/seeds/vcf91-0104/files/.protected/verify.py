"""Protected verification for the contract-focused vCenter CPU safety gate."""

from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.mock_vcenter import (  # noqa: E402
    POWER_OPERATION,
    UPDATE_OPERATION,
    ContractMock,
)
from vcf_vcenter_cpu_guard import (  # noqa: E402
    CpuUpdateResult,
    PrecheckFailed,
    ProtocolError,
    VCenterClient,
    VcenterError,
)


CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
EXPECTED_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATIONS = [POWER_OPERATION, UPDATE_OPERATION]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_raises(
    exception_type: type[BaseException],
    action: Callable[[], Any],
    message: str,
) -> BaseException:
    try:
        action()
    except exception_type as exc:
        return exc
    except Exception as exc:  # pragma: no cover - diagnostic path
        raise AssertionError(
            f"{message}: expected {exception_type.__name__}, got "
            f"{type(exc).__name__}"
        ) from exc
    raise AssertionError(f"{message}: no exception was raised")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def header_map(record: dict[str, Any]) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    for name, value in record["headers"]:
        headers.setdefault(name.lower(), []).append(value)
    return headers


def assert_common_headers(record: dict[str, Any], token: str) -> None:
    headers = header_map(record)
    require(headers.get("accept") == ["application/json"], "wrong Accept header")
    require(
        headers.get("vmware-api-session-id") == [token],
        "wrong vCenter session header",
    )
    require("authorization" not in headers, "Authorization must not be sent")


def assert_provenance() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    operations = contract["operations"]
    source_operations = sources["operations"]

    require(
        [item["operationId"] for item in operations] == EXPECTED_OPERATIONS,
        "contract operationIds changed",
    )
    require(
        [item["operationId"] for item in source_operations]
        == EXPECTED_OPERATIONS,
        "source operationIds changed",
    )
    require(
        [(item["method"], item["path"]) for item in operations]
        == [
            ("GET", "/api/vcenter/vm/{vm}/power"),
            ("PATCH", "/api/vcenter/vm/{vm}/hardware/cpu"),
        ],
        "focused operation wire paths changed",
    )
    require(contract["source"]["commitSha"] == EXPECTED_SHA, "wrong contract SHA")
    require(sources["repositoryCommitSha"] == EXPECTED_SHA, "wrong sources SHA")
    require(
        contract["source"]["specBlobSha"] == EXPECTED_BLOB,
        "wrong contract blob SHA",
    )
    require(sources["specBlobSha"] == EXPECTED_BLOB, "wrong sources blob SHA")
    require(
        contract["source"]["specPath"] == EXPECTED_SPEC,
        "wrong contract spec path",
    )
    require(sources["specPath"] == EXPECTED_SPEC, "wrong sources spec path")
    for operation in source_operations:
        require(
            operation["repositoryCommitSha"] == EXPECTED_SHA,
            "operation source is not commit-pinned",
        )
        require(
            operation["specPath"] == EXPECTED_SPEC,
            "operation source does not name the YAML specification",
        )

    security = contract["securitySchemes"]["api_key_auth"]
    require(
        security
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter session security contract changed",
    )
    power_info = contract["schemas"]["Vcenter.Vm.Power.Info"]
    require(power_info["required"] == ["state"], "power state is not required")
    require(
        power_info["properties"]["state"]["enum"]
        == ["POWERED_OFF", "POWERED_ON", "SUSPENDED"],
        "power-state enum changed",
    )
    update = contract["schemas"]["Vcenter.Vm.Hardware.Cpu.UpdateSpec"]
    require(
        list(update["properties"])
        == [
            "count",
            "cores_per_socket",
            "hot_add_enabled",
            "hot_remove_enabled",
        ],
        "CPU update property projection changed",
    )
    require(
        all(
            item["required"] is False
            and item["unsetBehavior"] == "unchanged"
            for item in update["properties"].values()
        ),
        "CPU update optionality projection changed",
    )


def assert_constructor_validation() -> None:
    token = "validation-" + secrets.token_urlsafe(12)
    invalid_origins: list[Any] = [
        None,
        "",
        "/relative",
        "ftp://127.0.0.1",
        "http://user:pass@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1/?x=1",
        "http://127.0.0.1/#fragment",
        "http://127.0.0.1:70000",
    ]
    for origin in invalid_origins:
        require_raises(
            ValueError,
            lambda value=origin: VCenterClient(value, token),
            f"invalid origin accepted: {origin!r}",
        )
    for bad_token in [None, "", "   ", "bad\nheader", "bad\rheader"]:
        require_raises(
            ValueError,
            lambda value=bad_token: VCenterClient(
                "http://127.0.0.1:9",
                value,
            ),
            "invalid session token accepted",
        )
    for bad_timeout in [True, 0, -1, math.inf, math.nan, "10"]:
        require_raises(
            ValueError,
            lambda value=bad_timeout: VCenterClient(
                "http://127.0.0.1:9",
                token,
                timeout=value,
            ),
            "invalid timeout accepted",
        )
    VCenterClient("http://127.0.0.1:9/", token)


def assert_argument_validation(directory: Path) -> None:
    token = "argument-" + secrets.token_urlsafe(12)
    log_path = directory / "arguments.jsonl"
    with ContractMock(
        CONTRACT_PATH,
        log_path,
        power_payload={"state": "POWERED_OFF"},
    ) as mock:
        client = VCenterClient(mock.base_url, token)
        for bad_vm in [None, "", "   ", 42]:
            require_raises(
                ValueError,
                lambda value=bad_vm: client.set_cpu_count_if_powered_off(
                    value,
                    4,
                ),
                "invalid VM identifier accepted",
            )
        for bad_count in [True, 0, -1, 1.5, "4", 2**63]:
            require_raises(
                ValueError,
                lambda value=bad_count: client.set_cpu_count_if_powered_off(
                    "vm-argument-check",
                    value,
                ),
                "invalid CPU count accepted",
            )
    require(read_log(log_path) == [], "invalid arguments triggered a request")


def assert_success(directory: Path) -> None:
    token = "session-" + secrets.token_urlsafe(24)
    vm = "vm " + secrets.token_urlsafe(8) + "/blue β"
    cpu_count = 2 + secrets.randbelow(15)
    log_path = directory / "success.jsonl"

    with ContractMock(
        CONTRACT_PATH,
        log_path,
        power_payload={"state": "POWERED_OFF", "clean_power_off": False},
    ) as mock:
        client = VCenterClient(mock.base_url + "/", token, timeout=3.0)
        result = client.set_cpu_count_if_powered_off(vm, cpu_count)
        mutation_count = mock.mutation_count

    require(isinstance(result, CpuUpdateResult), "wrong result type")
    require(result.vm == vm, "result did not preserve VM")
    require(
        result.previous_power_state == "POWERED_OFF",
        "result did not preserve precheck state",
    )
    require(result.cpu_count == cpu_count, "result did not preserve CPU count")
    require(
        result.completed_operation_ids == tuple(EXPECTED_OPERATIONS),
        "result operationIds are wrong or mutable",
    )
    require(mutation_count == 1, "successful workflow must mutate exactly once")
    require_raises(
        FrozenInstanceError,
        lambda: setattr(result, "cpu_count", cpu_count + 1),
        "CpuUpdateResult must be frozen",
    )

    records = read_log(log_path)
    require(len(records) == 2, "success must issue one GET and one PATCH")
    require(
        [record["sequence"] for record in records] == [1, 2],
        "request log sequence is inconsistent",
    )
    require(
        [record["operation_id"] for record in records]
        == EXPECTED_OPERATIONS,
        "operations were not invoked in contract order",
    )

    encoded_vm = quote(vm, safe="")
    precheck, update = records
    require(precheck["method"] == "GET", "precheck must use GET")
    require(
        precheck["raw_target"] == f"/api/vcenter/vm/{encoded_vm}/power",
        "precheck target or VM path encoding is wrong",
    )
    require("?" not in precheck["raw_target"], "precheck must be queryless")
    assert_common_headers(precheck, token)
    precheck_headers = header_map(precheck)
    require("content-type" not in precheck_headers, "GET sent Content-Type")
    require("content-length" not in precheck_headers, "GET sent Content-Length")
    require(precheck["body_length"] == 0, "GET sent a request body")
    require(
        base64.b64decode(precheck["body_base64"], validate=True) == b"",
        "GET request-log body is inconsistent",
    )

    require(update["method"] == "PATCH", "CPU update must use PATCH")
    require(
        update["raw_target"]
        == f"/api/vcenter/vm/{encoded_vm}/hardware/cpu",
        "CPU update target or VM path encoding is wrong",
    )
    require("?" not in update["raw_target"], "CPU update must be queryless")
    assert_common_headers(update, token)
    update_headers = header_map(update)
    require(
        update_headers.get("content-type") == ["application/json"],
        "wrong PATCH Content-Type",
    )
    expected_body = f'{{"count":{cpu_count}}}'.encode("ascii")
    actual_body = base64.b64decode(update["body_base64"], validate=True)
    require(actual_body == expected_body, "PATCH JSON bytes are not exact")
    require(
        update["body_length"] == len(expected_body),
        "PATCH request-log body length is inconsistent",
    )
    require(
        update_headers.get("content-length") == [str(len(expected_body))],
        "PATCH Content-Length does not match exact body bytes",
    )
    parsed = json.loads(actual_body)
    require(list(parsed) == ["count"], "PATCH body property shape changed")
    for optional in [
        "cores_per_socket",
        "hot_add_enabled",
        "hot_remove_enabled",
    ]:
        require(optional not in parsed, f"unset {optional} must be omitted")


def assert_gate_failures(directory: Path) -> None:
    for index, state in enumerate(["POWERED_ON", "SUSPENDED"], start=1):
        token = "gate-" + secrets.token_urlsafe(18)
        vm = f"vm-gate-{index}/" + secrets.token_urlsafe(8)
        log_path = directory / f"gate-{index}.jsonl"
        with ContractMock(
            CONTRACT_PATH,
            log_path,
            power_payload={"state": state},
        ) as mock:
            client = VCenterClient(mock.base_url, token)
            error = require_raises(
                PrecheckFailed,
                lambda: client.set_cpu_count_if_powered_off(vm, 6),
                f"{state} did not fail the precheck",
            )
            mutation_count = mock.mutation_count

        require(error.vm == vm, "PrecheckFailed did not preserve VM")
        require(
            error.observed_state == state,
            "PrecheckFailed did not preserve observed state",
        )
        records = read_log(log_path)
        require(len(records) == 1, f"{state} issued a mutation request")
        require(mutation_count == 0, f"{state} changed mock state")
        require(
            records[0]["operation_id"] == POWER_OPERATION,
            f"{state} contacted a non-precheck route",
        )
        assert_common_headers(records[0], token)


def assert_malformed_prechecks(directory: Path) -> None:
    malformed_payloads = [
        {},
        {"state": "POWERED_OFF", "clean_power_off": "false"},
        {"state": "UNKNOWN"},
        ["POWERED_OFF"],
    ]
    for index, payload in enumerate(malformed_payloads, start=1):
        token = "protocol-" + secrets.token_urlsafe(16)
        log_path = directory / f"protocol-{index}.jsonl"
        with ContractMock(
            CONTRACT_PATH,
            log_path,
            power_payload=payload,
        ) as mock:
            client = VCenterClient(mock.base_url, token)
            error = require_raises(
                ProtocolError,
                lambda: client.set_cpu_count_if_powered_off(
                    "vm-protocol-" + secrets.token_urlsafe(5),
                    8,
                ),
                "malformed power success was accepted",
            )
            mutation_count = mock.mutation_count

        require(
            error.operation_id == POWER_OPERATION,
            "ProtocolError operationId is wrong",
        )
        require(len(read_log(log_path)) == 1, "malformed precheck issued PATCH")
        require(mutation_count == 0, "malformed precheck changed mock state")


def assert_http_failures(directory: Path) -> None:
    token = "http-" + secrets.token_urlsafe(22)
    sentinel = "payload-" + secrets.token_urlsafe(18)
    precheck_log = directory / "precheck-http.jsonl"
    payload = {"error_type": "SERVICE_UNAVAILABLE", "sentinel": sentinel}
    with ContractMock(
        CONTRACT_PATH,
        precheck_log,
        power_payload=payload,
        power_status=503,
    ) as mock:
        client = VCenterClient(mock.base_url, token)
        error = require_raises(
            VcenterError,
            lambda: client.set_cpu_count_if_powered_off("vm-http", 4),
            "precheck HTTP error was accepted",
        )
        mutation_count = mock.mutation_count

    require(error.operation_id == POWER_OPERATION, "wrong precheck error operation")
    require(error.status_code == 503, "precheck HTTP status was not preserved")
    require(error.payload == payload, "precheck JSON error was not preserved")
    require(len(read_log(precheck_log)) == 1, "HTTP precheck failure issued PATCH")
    require(mutation_count == 0, "HTTP precheck failure changed mock state")
    for rendered in [str(error), repr(error)]:
        require(token not in rendered, "error exposed the session token")
        require(sentinel not in rendered, "error exposed response payload data")

    update_token = "update-" + secrets.token_urlsafe(22)
    update_sentinel = "update-payload-" + secrets.token_urlsafe(15)
    update_log = directory / "update-http.jsonl"
    update_payload = {"error_type": "RESOURCE_BUSY", "sentinel": update_sentinel}
    with ContractMock(
        CONTRACT_PATH,
        update_log,
        power_payload={"state": "POWERED_OFF"},
        update_status=500,
        update_payload=update_payload,
    ) as mock:
        client = VCenterClient(mock.base_url, update_token)
        error = require_raises(
            VcenterError,
            lambda: client.set_cpu_count_if_powered_off("vm-update", 10),
            "mutation HTTP error was accepted",
        )
        mutation_count = mock.mutation_count

    require(error.operation_id == UPDATE_OPERATION, "wrong update error operation")
    require(error.status_code == 500, "update HTTP status was not preserved")
    require(error.payload == update_payload, "update JSON error was not preserved")
    records = read_log(update_log)
    require(
        [record["operation_id"] for record in records] == EXPECTED_OPERATIONS,
        "mutation failure retried, reordered, or contacted another route",
    )
    require(mutation_count == 1, "failed mutation was not issued exactly once")
    for rendered in [str(error), repr(error)]:
        require(update_token not in rendered, "error exposed refreshed token")
        require(update_sentinel not in rendered, "error exposed update payload")


def main() -> int:
    assert_provenance()
    assert_constructor_validation()
    with tempfile.TemporaryDirectory(prefix="vcf-cpu-guard-") as raw_directory:
        directory = Path(raw_directory)
        assert_argument_validation(directory)
        assert_success(directory)
        assert_gate_failures(directory)
        assert_malformed_prechecks(directory)
        assert_http_failures(directory)
    print("verified: vCenter power-state precheck gates the exact CPU update wire call")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
