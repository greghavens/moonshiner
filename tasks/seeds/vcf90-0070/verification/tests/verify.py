"""Protected verifier for the VCF Operations 9.0 integration seed."""

from __future__ import annotations

import ast
from dataclasses import fields
import importlib
import inspect
import json
from pathlib import Path
import secrets
import sys
from typing import get_type_hints, Sequence
from urllib.parse import urlencode, urlsplit

from mock_vcf import ContractMock, RequestRecord


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "vcf_ops_maintenance"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATIONS = (
    "createMaintenanceSchedules",
    "updateMaintenanceSchedules",
    "deleteMaintenanceSchedules",
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_provenance() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    source = contract["x-official-source"]
    check(contract["openapi"] == "3.0.1", "contract must retain OpenAPI 3.0.1")
    check(contract["servers"] == [{"url": "/suite-api", "description": "Base path"}], "wrong server path")
    check(source["tag"] == "9.0.0.0", "contract is not pinned to tag 9.0.0.0")
    check(source["commitSha"] == EXPECTED_SHA, "contract commit pin changed")
    check(source["specPath"] == EXPECTED_SPEC, "contract spec path changed")
    check(sources["tag"] == "9.0.0.0", "official source tag changed")
    check(sources["tagCommitSha"] == EXPECTED_SHA, "official source commit changed")
    check(sources["specPath"] == EXPECTED_SPEC, "official source path changed")

    found: list[tuple[str, str, str]] = []
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                found.append((operation["operationId"], method.upper(), path))
    check(
        tuple(item[0] for item in found) == (
            "updateMaintenanceSchedules",
            "createMaintenanceSchedules",
            "deleteMaintenanceSchedules",
        ),
        "focused contract operation set changed",
    )
    source_ops = tuple(item["operationId"] for item in sources["operationIds"])
    check(source_ops == EXPECTED_OPERATIONS, "official source operationIds changed")


def verify_stdlib_only() -> None:
    allowed_local = {"vcf_ops_maintenance"}
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".", 1)[0]
                check(
                    root in sys.stdlib_module_names or root in allowed_local,
                    f"non-stdlib import in {path.name}: {name}",
                )


def schedule_json(spec: object) -> dict[str, object]:
    result: dict[str, object] = {
        "hour": spec.hour,
        "minuteOfTheHour": spec.minute_of_the_hour,
        "duration": spec.duration,
        "scheduleType": spec.schedule_type,
    }
    optionals = (
        ("recurrence", "recurrence"),
        ("dayOfTheMonth", "day_of_the_month"),
        ("daysOfTheMonth", "days_of_the_month"),
        ("weeksOfTheMonth", "weeks_of_the_month"),
        ("daysOfTheWeek", "days_of_the_week"),
        ("month", "month"),
        ("months", "months"),
        ("startDate", "start_date"),
        ("expirationDate", "expiration_date"),
        ("timeZone", "time_zone"),
        ("expireRuns", "expire_runs"),
    )
    for wire_name, attr_name in optionals:
        value = getattr(spec, attr_name)
        if value is not None:
            result[wire_name] = list(value) if isinstance(value, tuple) else value
    return result


def maintenance_json(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    if value.id is not None:
        result["id"] = value.id
    result["key"] = value.key
    result["schedule"] = schedule_json(value.schedule)
    return result


def compact(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def header_pairs(record: RequestRecord) -> list[tuple[str, str]]:
    return [(name.lower(), value) for name, value in record.headers]


def verify_headers(record: RequestRecord, token: str, body: bytes | None) -> None:
    expected_names = ["host", "accept", "authorization"]
    if body is not None:
        expected_names.extend(("content-type", "content-length"))
    pairs = header_pairs(record)
    grouped: dict[str, list[str]] = {}
    for name, value in pairs:
        grouped.setdefault(name, []).append(value)
    check(set(grouped) == set(expected_names), f"unexpected headers: {pairs!r}")
    check(all(len(grouped[name]) == 1 for name in expected_names), "duplicate request header")
    parsed = urlsplit("http://" + grouped["host"][0])
    check(parsed.hostname == "127.0.0.1", "request Host is not loopback")
    check(grouped["accept"] == ["application/json"], "Accept header is not exact")
    check(grouped["authorization"] == [token], "Authorization header is not exact")
    if body is not None:
        check(grouped["content-type"] == ["application/json"], "Content-Type header is not exact")
        check(grouped["content-length"] == [str(len(body))], "Content-Length is not exact")


def verify_log(
    records: tuple[RequestRecord, ...],
    *,
    token: str,
    create: object,
    update: object,
    retire_ids: tuple[str, ...],
    expected_count: int = 3,
) -> None:
    check(
        len(records) == expected_count,
        f"expected exactly {expected_count} calls, got {len(records)}",
    )
    create_body = compact(maintenance_json(create))
    update_body = compact(maintenance_json(update))
    expected_query = urlencode([("id", value) for value in retire_ids])
    expected = (
        ("POST", "/suite-api/api/maintenanceschedules", create_body),
        ("PUT", "/suite-api/api/maintenanceschedules", update_body),
        ("DELETE", f"/suite-api/api/maintenanceschedules?{expected_query}", b""),
    )
    for record, (method, target, body) in zip(
        records,
        expected[:expected_count],
        strict=True,
    ):
        check(record.method == method, f"wrong method for {target}")
        check(record.raw_target == target, f"wrong raw target: {record.raw_target!r}")
        check(record.body == body, f"wrong body for {method}: {record.body!r}")
        verify_headers(record, token, None if method == "DELETE" else body)


def make_inputs(module: object, mock: ContractMock) -> tuple[object, object]:
    suffix = secrets.token_hex(4)
    create = module.MaintenanceSchedule(
        key=f"db-π-{suffix}",
        schedule=module.ScheduleSpec(
            hour=2,
            minute_of_the_hour=15,
            duration=90,
            schedule_type="DAILY",
            recurrence=1,
            expiration_date="09/30/2026",
        ),
    )
    update = module.MaintenanceSchedule(
        id=mock.update_id,
        key=f"app-{suffix}",
        schedule=module.ScheduleSpec(
            hour=3,
            minute_of_the_hour=0,
            duration=45,
            schedule_type="WEEKLY",
            recurrence=2,
            days_of_the_week=("SATURDAY",),
            expire_runs=4,
        ),
    )
    return create, update


def make_full_inputs(module: object, mock: ContractMock) -> tuple[object, object]:
    create = module.MaintenanceSchedule(
        key="all-optionals-π",
        schedule=module.ScheduleSpec(
            hour=0,
            minute_of_the_hour=0,
            duration=1,
            schedule_type="ONCE",
            recurrence=1,
            day_of_the_month=0,
            days_of_the_month=(),
            weeks_of_the_month=("FIRST", "LAST"),
            days_of_the_week=(),
            month=0,
            months=(),
            start_date="",
            expiration_date="",
            time_zone="",
            expire_runs=0,
        ),
    )
    update = module.MaintenanceSchedule(
        id=mock.update_id,
        key="minimal-update",
        schedule=module.ScheduleSpec(
            hour=23,
            minute_of_the_hour=59,
            duration=30,
            schedule_type="DAILY",
        ),
    )
    return create, update


def verify_public_api(module: object) -> None:
    expected_fields = {
        "ScheduleSpec": (
            "hour",
            "minute_of_the_hour",
            "duration",
            "schedule_type",
            "recurrence",
            "day_of_the_month",
            "days_of_the_month",
            "weeks_of_the_month",
            "days_of_the_week",
            "month",
            "months",
            "start_date",
            "expiration_date",
            "time_zone",
            "expire_runs",
        ),
        "MaintenanceSchedule": ("key", "schedule", "id"),
        "StepResult": ("operation_id", "status_code", "schedule_id"),
        "ChangeReport": ("completed",),
    }
    for class_name, names in expected_fields.items():
        cls = getattr(module, class_name)
        check(tuple(field.name for field in fields(cls)) == names, f"{class_name} declaration changed")
        check(cls.__dataclass_params__.frozen, f"{class_name} must remain frozen")
        check(tuple(cls.__slots__) == names, f"{class_name} slots changed")

    expected_parameters = {
        "__init__": (
            ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("base_url", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("token", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("timeout", inspect.Parameter.KEYWORD_ONLY, 5.0),
        ),
        "apply_change": (
            ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("create", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("update", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("retire_ids", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ),
    }
    expected_hints = {
        "__init__": {
            "base_url": str,
            "token": str,
            "timeout": float,
            "return": type(None),
        },
        "apply_change": {
            "create": module.MaintenanceSchedule,
            "update": module.MaintenanceSchedule,
            "retire_ids": Sequence[str],
            "return": module.ChangeReport,
        },
    }
    for method_name, expected in expected_parameters.items():
        method = getattr(module.VcfOperationsClient, method_name)
        signature = inspect.signature(method)
        actual = tuple(
            (parameter.name, parameter.kind, parameter.default)
            for parameter in signature.parameters.values()
        )
        check(actual == expected, f"VcfOperationsClient.{method_name} parameters changed")
        check(
            get_type_hints(method) == expected_hints[method_name],
            f"VcfOperationsClient.{method_name} annotations changed",
        )
    check(issubclass(module.ResponseContractError, module.ApiError), "error hierarchy changed")
    check(issubclass(module.ChangeFailed, module.ApiError), "error hierarchy changed")


def verify_failure_scenario(module: object) -> None:
    with ContractMock(CONTRACT_PATH, fail_delete=True) as mock:
        create, update = make_inputs(module, mock)
        client = module.VcfOperationsClient(mock.base_url, mock.token, timeout=3.0)
        try:
            client.apply_change(create, update, mock.retire_ids)
        except module.ChangeFailed as error:
            check(error.failed_operation_id == "deleteMaintenanceSchedules", "wrong failed operation")
            check(error.status_code == 503, "wrong failed HTTP status")
            expected_completed = (
                module.StepResult("createMaintenanceSchedules", 201, mock.created_id),
                module.StepResult("updateMaintenanceSchedules", 200, mock.update_id),
            )
            check(error.report.completed == expected_completed, "earlier outcomes were reported inaccurately")
            rendered = str(error)
            check(mock.token not in rendered, "token leaked through error text")
            check(mock.failure_marker not in rendered, "raw response body leaked through error text")
        else:
            raise AssertionError("final HTTP 503 did not raise ChangeFailed")

        verify_log(
            mock.request_log,
            token=mock.token,
            create=create,
            update=update,
            retire_ids=mock.retire_ids,
        )


def verify_success_report(module: object) -> None:
    with ContractMock(CONTRACT_PATH, fail_delete=False) as mock:
        create, update = make_full_inputs(module, mock)
        client = module.VcfOperationsClient(mock.base_url, mock.token, timeout=3)
        report = client.apply_change(create, update, list(mock.retire_ids))
        expected = (
            module.StepResult("createMaintenanceSchedules", 201, mock.created_id),
            module.StepResult("updateMaintenanceSchedules", 200, mock.update_id),
            module.StepResult("deleteMaintenanceSchedules", 204, None),
        )
        check(report == module.ChangeReport(expected), "successful ChangeReport is inaccurate")
        verify_log(
            mock.request_log,
            token=mock.token,
            create=create,
            update=update,
            retire_ids=mock.retire_ids,
        )


def verify_early_http_failures(module: object) -> None:
    scenarios = (
        ("createMaintenanceSchedules", 422, (), 1),
        ("updateMaintenanceSchedules", 404, None, 2),
    )
    for operation_id, status_code, fixed_completed, expected_count in scenarios:
        with ContractMock(
            CONTRACT_PATH,
            fail_delete=False,
            failure_operation_id=operation_id,
        ) as mock:
            create, update = make_inputs(module, mock)
            completed = fixed_completed
            if completed is None:
                completed = (
                    module.StepResult("createMaintenanceSchedules", 201, mock.created_id),
                )
            client = module.VcfOperationsClient(mock.base_url, mock.token, timeout=3.0)
            try:
                client.apply_change(create, update, mock.retire_ids)
            except module.ChangeFailed as error:
                check(error.failed_operation_id == operation_id, "wrong early failed operation")
                check(error.status_code == status_code, "wrong early failed status")
                check(error.report.completed == completed, "wrong early partial report")
                check(mock.token not in str(error), "token leaked through early failure")
                check(mock.failure_marker not in str(error), "raw early failure body leaked")
            else:
                raise AssertionError(f"HTTP {status_code} did not raise ChangeFailed")
            verify_log(
                mock.request_log,
                token=mock.token,
                create=create,
                update=update,
                retire_ids=mock.retire_ids,
                expected_count=expected_count,
            )


def verify_malformed_create(module: object) -> None:
    for malformed in ("invalid-json", "invalid-id"):
        with ContractMock(
            CONTRACT_PATH,
            fail_delete=False,
            malformed_create=malformed,
        ) as mock:
            create, update = make_inputs(module, mock)
            client = module.VcfOperationsClient(mock.base_url, mock.token, timeout=3.0)
            try:
                client.apply_change(create, update, mock.retire_ids)
            except module.ResponseContractError as error:
                check(mock.token not in str(error), "token leaked through malformed response error")
                check(mock.failure_marker not in str(error), "raw malformed response body leaked")
            else:
                raise AssertionError("malformed create success did not raise ResponseContractError")
            verify_log(
                mock.request_log,
                token=mock.token,
                create=create,
                update=update,
                retire_ids=mock.retire_ids,
                expected_count=1,
            )


def verify_transport_failure(module: object) -> None:
    with ContractMock(
        CONTRACT_PATH,
        fail_delete=False,
        disconnect_operation_id="createMaintenanceSchedules",
    ) as mock:
        create, update = make_inputs(module, mock)
        client = module.VcfOperationsClient(mock.base_url, mock.token, timeout=3.0)
        try:
            client.apply_change(create, update, mock.retire_ids)
        except module.ChangeFailed as error:
            check(error.failed_operation_id == "createMaintenanceSchedules", "wrong transport operation")
            check(error.status_code is None, "transport failure unexpectedly has an HTTP status")
            check(error.report.completed == (), "transport failure has a false completed step")
            check(mock.token not in str(error), "token leaked through transport failure")
        else:
            raise AssertionError("transport disconnect did not raise ChangeFailed")
        verify_log(
            mock.request_log,
            token=mock.token,
            create=create,
            update=update,
            retire_ids=mock.retire_ids,
            expected_count=1,
        )


def main() -> None:
    verify_provenance()
    verify_stdlib_only()
    sys.path.insert(0, str(ROOT))
    try:
        module = importlib.import_module("vcf_ops_maintenance")
    finally:
        sys.path.pop(0)
    verify_public_api(module)
    verify_failure_scenario(module)
    verify_success_report(module)
    verify_early_http_failures(module)
    verify_malformed_create(module)
    verify_transport_failure(module)
    print("verified: VCF Operations 9.0 maintenance change contract and failure report")


if __name__ == "__main__":
    main()
