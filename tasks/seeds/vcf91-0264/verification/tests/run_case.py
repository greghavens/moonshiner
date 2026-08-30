#!/usr/bin/env python3
"""Protected driver that exercises the solution package against the loopback mock.

usage: run_case.py SCENARIO_FILE BASE_URL OUTPUT_FILE
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def describe_exception(error: BaseException) -> dict[str, Any]:
    return {
        "type": type(error).__name__,
        "module": type(error).__module__,
        "text": str(error),
        "stage": getattr(error, "stage", None),
        "reason": getattr(error, "reason", None),
        "statusCode": getattr(error, "status_code", None),
        "operationId": getattr(error, "operation_id", None),
        "message": getattr(error, "message", None),
        "conflictingInstanceIds": list(
            getattr(error, "conflicting_instance_ids", []) or []
        ),
        "conflictingIsTuple": isinstance(
            getattr(error, "conflicting_instance_ids", None), tuple
        ),
    }


def validation_cases(
    package: Any, base_url: str, exemplar: dict[str, Any]
) -> list[dict[str, Any]]:
    base_kwargs: dict[str, Any] = {
        "username": exemplar["username"],
        "password": exemplar["password"],
        "name": exemplar["instanceName"],
        "adapter_kind_key": exemplar["adapterKindKey"],
        "credential_name": exemplar["credentialName"],
        "credential_kind_key": exemplar["credentialKindKey"],
        "credential_fields": [("field", "value")],
        "resource_identifiers": [],
    }
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("blank-base-url", " \t", {}),
        ("blank-username", base_url, {"username": " \t"}),
        ("blank-password", base_url, {"password": " \t"}),
        ("blank-name", base_url, {"name": " \t"}),
        ("blank-adapter-kind", base_url, {"adapter_kind_key": " \t"}),
        ("blank-credential-name", base_url, {"credential_name": " \t"}),
        ("blank-credential-kind", base_url, {"credential_kind_key": " \t"}),
        ("blank-auth-source", base_url, {"auth_source": " \t"}),
        ("blank-description", base_url, {"description": " \t"}),
        ("fields-not-sequence", base_url, {"credential_fields": "field=value"}),
        ("fields-empty", base_url, {"credential_fields": []}),
        ("fields-malformed-pair", base_url, {"credential_fields": [("field",)]}),
        ("fields-nonstring", base_url, {"credential_fields": [("field", 7)]}),
        (
            "identifiers-not-sequence",
            base_url,
            {"resource_identifiers": "name=value"},
        ),
        (
            "identifiers-malformed-pair",
            base_url,
            {"resource_identifiers": [("name", "value", "extra")]},
        ),
        (
            "identifiers-nonstring",
            base_url,
            {"resource_identifiers": [(7, "value")]},
        ),
        ("timeout-zero", base_url, {"timeout": 0}),
        ("timeout-negative", base_url, {"timeout": -0.25}),
        ("timeout-boolean", base_url, {"timeout": True}),
        ("timeout-not-number", base_url, {"timeout": "30"}),
        ("timeout-nan", base_url, {"timeout": float("nan")}),
    ]
    records: list[dict[str, Any]] = []
    for key, case_base_url, changes in cases:
        kwargs = {**base_kwargs, **changes}
        try:
            package.onboard_adapter_instance(case_base_url, **kwargs)
        except BaseException as error:  # noqa: BLE001 - reported to the verifier
            records.append(
                {
                    "key": key,
                    "type": type(error).__name__,
                    "isValueError": isinstance(error, ValueError),
                    "text": str(error),
                }
            )
        else:
            records.append(
                {"key": key, "type": "no-error", "isValueError": False, "text": ""}
            )
    return records


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        raise SystemExit("usage: run_case.py SCENARIO_FILE BASE_URL OUTPUT_FILE")
    scenario_file, base_url, output_file = Path(argv[1]), argv[2], Path(argv[3])
    scenario = json.loads(scenario_file.read_text(encoding="utf-8"))

    sys.path.insert(0, str(ROOT))
    import vcfops_onboarding as package

    exports = list(getattr(package, "__all__", []))
    result_class = getattr(package, "OnboardingResult")
    precheck_error = getattr(package, "PrecheckFailedError")
    api_error = getattr(package, "OperationsApiError")

    report: dict[str, Any] = {
        "exports": exports,
        "packageFile": getattr(package, "__file__", None),
        "resultIsDataclass": bool(dataclasses.is_dataclass(result_class)),
        "resultIsFrozen": bool(
            dataclasses.is_dataclass(result_class)
            and result_class.__dataclass_params__.frozen
        ),
        "resultFieldOrder": (
            [field.name for field in dataclasses.fields(result_class)]
            if dataclasses.is_dataclass(result_class)
            else []
        ),
        "precheckIsRuntimeError": issubclass(precheck_error, RuntimeError),
        "apiIsRuntimeError": issubclass(api_error, RuntimeError),
        "precheckIsApiError": issubclass(precheck_error, api_error),
        "validation": [],
        "cases": [],
    }

    if scenario.get("runValidation") is True:
        report["validation"] = validation_cases(
            package, base_url, scenario["cases"][0]
        )

    for case in scenario["cases"]:
        record: dict[str, Any] = {"key": case["key"]}
        kwargs: dict[str, Any] = {
            "username": case["username"],
            "password": case["password"],
            "name": case["instanceName"],
            "adapter_kind_key": case["adapterKindKey"],
            "credential_name": case["credentialName"],
            "credential_kind_key": case["credentialKindKey"],
            "credential_fields": [tuple(item) for item in case["credentialFields"]],
        }
        if not case.get("useDefaultResourceIdentifiers"):
            kwargs["resource_identifiers"] = [
                tuple(item) for item in case["resourceIdentifiers"]
            ]
        if case.get("authSource"):
            kwargs["auth_source"] = case["authSource"]
        if case.get("description"):
            kwargs["description"] = case["description"]
        try:
            value = package.onboard_adapter_instance(base_url, **kwargs)
        except precheck_error as error:  # noqa: PERF203 - one branch per case
            record["kind"] = "precheck"
            record["error"] = describe_exception(error)
        except api_error as error:  # noqa: PERF203 - one branch per case
            record["kind"] = "api-error"
            record["error"] = describe_exception(error)
        except BaseException as error:  # noqa: BLE001 - reported to the verifier
            record["kind"] = "unexpected"
            record["error"] = describe_exception(error)
        else:
            record["kind"] = "result"
            record["resultType"] = type(value).__name__
            record["resultIsOnboardingResult"] = isinstance(value, result_class)
            record["result"] = (
                dataclasses.asdict(value)
                if dataclasses.is_dataclass(value)
                else {"unstructured": repr(value)}
            )
        report["cases"].append(record)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, separators=(",", ":"), sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
