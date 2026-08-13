#!/usr/bin/env python3
"""Deterministic acceptance verifier; all HTTP traffic stays on loopback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OPERATIONS = [
    ("validateSddcSpec", "POST", "/v1/sddcs/validations"),
    ("getSddcSpecValidation", "GET", "/v1/sddcs/validations/{id}"),
    ("deploySddc", "POST", "/v1/sddcs"),
]
EXPECTED_MINIMAL_BODY = (
    '{"sddcId":"lab-01","vcenterSpec":{"vcenterHostname":"vc01.lab.example",'
    '"rootVcenterPassword":"VCF!Pass\\"\\\\2026"},"networkSpecs":'
    '[{"networkType":"MANAGEMENT","vlanId":120}],"dnsSpec":'
    '{"subdomain":"lab.example"}}'
)
EXPECTED_OPTIONAL_BODY = (
    '{"sddcId":"edge-02","workflowType":"VCF_COMPLETE","version":"9.1.0",'
    '"vcenterSpec":{"vcenterHostname":"vc02.edge.example",'
    '"rootVcenterPassword":"Opt!Pass\\n\\"\\\\2026","vmSize":"medium",'
    '"storageSize":"large"},"networkSpecs":['
    '{"networkType":"MANAGEMENT","vlanId":0},'
    '{"networkType":"VMOTION","vlanId":4094}],"dnsSpec":'
    '{"subdomain":"edge.example","nameservers":['
    '"192.0.2.10","ns\\"\\\\\\n.example"]}}'
)
SUCCESS_ID = "11111111-1111-4111-8111-111111111111"
FAILURE_ID = "22222222-2222-4222-8222-222222222222"
TIMEOUT_ID = "44444444-4444-4444-8444-444444444444"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    require(contract["openapiVersion"] == "3.0.1", "wrong OpenAPI version")
    require(contract["apiVersion"] == "9.1.0.0", "wrong VCF contract version")
    require(
        contract["source"]["commit"]
        == "c3f3b52c845dd967cabbc21680e893292077d5ba",
        "contract is not pinned to the VCF 9.1 commit",
    )
    require(
        contract["source"]["path"]
        == "specifications/vcf-installer/vcf-installer-openapi.json",
        "wrong source specification path",
    )
    actual = [
        (operation["operationId"], operation["method"], operation["path"])
        for operation in contract["operations"]
    ]
    require(actual == EXPECTED_OPERATIONS, "contract operation set changed")
    source_actual = [
        (operation["operationId"], operation["method"], operation["path"])
        for operation in sources["operations"]
    ]
    require(source_actual == EXPECTED_OPERATIONS, "official source operations changed")
    require(
        sources["repositoryCommitSha"] == contract["source"]["commit"],
        "source commit mismatch",
    )
    require(sources["specPath"] == contract["source"]["path"], "source path mismatch")

    operations = {operation["operationId"]: operation for operation in contract["operations"]}
    deploy_parameter = operations["deploySddc"]["parameters"][0]
    require(
        deploy_parameter
        == {
            "name": "skipValidations",
            "in": "query",
            "required": False,
            "type": "boolean",
            "default": False,
        },
        "deploy optional query contract changed",
    )
    require(
        contract["schemas"]["SddcSpec"]["required"]
        == ["dnsSpec", "networkSpecs", "sddcId", "vcenterSpec"],
        "SddcSpec required properties changed",
    )


def compile_client(classes: Path) -> None:
    command = [
        "javac",
        "-encoding",
        "UTF-8",
        "-d",
        str(classes),
        str(ROOT / "src/VcfInstallerClient.java"),
        str(ROOT / "tests/TestMain.java"),
    ]
    result = subprocess.run(command, text=True, capture_output=True, timeout=15)
    require(result.returncode == 0, "javac failed:\n" + result.stdout + result.stderr)


def read_log(log_path: Path) -> list[dict[str, object]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def start_server(temp: Path, scenario: str) -> tuple[subprocess.Popen[str], str, Path]:
    log_path = temp / f"{scenario}.jsonl"
    ready_path = temp / f"{scenario}.port"
    command = [
        sys.executable,
        str(ROOT / "tests/mock_server.py"),
        "--contract",
        str(ROOT / "docs/contract.json"),
        "--scenario",
        scenario,
        "--log",
        str(log_path),
        "--ready",
        str(ready_path),
    ]
    process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if ready_path.exists():
            port = int(ready_path.read_text(encoding="ascii"))
            return process, f"http://127.0.0.1:{port}", log_path
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError("mock failed to start:\n" + stdout + stderr)
        time.sleep(0.02)
    process.terminate()
    raise AssertionError("mock did not become ready")


def run_scenario(classes: Path, temp: Path, scenario: str) -> list[dict[str, object]]:
    process, base_url, log_path = start_server(temp, scenario)
    try:
        env = os.environ.copy()
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        result = subprocess.run(
            ["java", "-cp", str(classes), "TestMain", base_url, scenario],
            text=True,
            capture_output=True,
            timeout=12,
            env=env,
        )
        require(
            result.returncode == 0,
            f"{scenario} TestMain failed:\n{result.stdout}{result.stderr}",
        )
        expected_prefix = {
            "success": "SUCCESS ",
            "success-optionals-true": "SUCCESS ",
            "success-optionals-false": "SUCCESS ",
            "immediate-success": "SUCCESS ",
            "immediate-failure": "BLOCKED ",
            "failure": "BLOCKED ",
            "timeout": "TIMEOUT",
            "http-error": "HTTP_ERROR",
            "poll-error": "POLL_ERROR",
        }[scenario]
        require(result.stdout.startswith(expected_prefix), f"wrong {scenario} harness output")
        return read_log(log_path)
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def assert_headers_and_body(
    entry: dict[str, object], expected_body: str | None
) -> None:
    headers = entry["headers"]
    header_items = entry["headerItems"]
    require(isinstance(headers, dict), "headers were not logged")
    require(isinstance(header_items, list), "header multiplicity was not logged")
    header_names = [item[0] for item in header_items]
    require(header_names.count("authorization") == 1, "wrong Authorization multiplicity")
    require(header_names.count("accept") == 1, "wrong Accept multiplicity")
    require(headers.get("authorization") == "Bearer verifier-token", "wrong Authorization")
    require(headers.get("accept") == "application/json", "wrong Accept header")
    if expected_body is not None:
        require(header_names.count("content-type") == 1, "wrong Content-Type multiplicity")
        require(
            str(headers.get("content-type", "")).lower() == "application/json",
            "wrong Content-Type header",
        )
        require(
            entry["bodyUtf8"] == expected_body,
            "request body bytes differ from contract shape",
        )
        body = json.loads(str(entry["bodyUtf8"]))
        if expected_body == EXPECTED_MINIMAL_BODY:
            require("workflowType" not in body, "unset workflowType was serialized")
            require("version" not in body, "unset version was serialized")
            require("vmSize" not in body["vcenterSpec"], "unset vmSize was serialized")
            require(
                "storageSize" not in body["vcenterSpec"],
                "unset storageSize was serialized",
            )
            require(
                "nameservers" not in body["dnsSpec"],
                "unset nameservers was serialized",
            )
        require("null" not in str(entry["bodyUtf8"]), "unset optional field was sent as null")
    else:
        require(header_names.count("content-type") == 0, "GET request unexpectedly had Content-Type")
        require(entry["bodyUtf8"] == "", "GET request unexpectedly had a body")


def verify_success(
    log: list[dict[str, object]], expected_body: str, deploy_query: str
) -> None:
    expected = [
        ("POST", "/v1/sddcs/validations", "validateSddcSpec"),
        ("GET", f"/v1/sddcs/validations/{SUCCESS_ID}", "getSddcSpecValidation"),
        ("GET", f"/v1/sddcs/validations/{SUCCESS_ID}", "getSddcSpecValidation"),
        ("POST", "/v1/sddcs", "deploySddc"),
    ]
    actual = [(entry["method"], entry["rawPath"], entry["operationId"]) for entry in log]
    require(actual == expected, f"wrong successful request sequence: {actual!r}")
    expected_queries = ["", "", "", deploy_query]
    for index, entry in enumerate(log):
        assert_headers_and_body(
            entry, expected_body=expected_body if index in (0, 3) else None
        )
        require(entry["query"] == expected_queries[index], "wrong request query")
    require(log[0]["bodyHex"] == log[3]["bodyHex"], "validation/deploy bodies differ")


def verify_failure(log: list[dict[str, object]]) -> None:
    expected = [
        ("POST", "/v1/sddcs/validations", "validateSddcSpec"),
        ("GET", f"/v1/sddcs/validations/{FAILURE_ID}", "getSddcSpecValidation"),
    ]
    actual = [(entry["method"], entry["rawPath"], entry["operationId"]) for entry in log]
    require(actual == expected, f"failed precheck was not a hard mutation gate: {actual!r}")
    assert_headers_and_body(log[0], expected_body=EXPECTED_MINIMAL_BODY)
    assert_headers_and_body(log[1], expected_body=None)
    require(
        all(entry["path"] != "/v1/sddcs" for entry in log),
        "deploySddc was called after a failed precheck",
    )


def verify_timeout(log: list[dict[str, object]]) -> None:
    expected = [("POST", "/v1/sddcs/validations", "validateSddcSpec")]
    expected.extend(
        ("GET", f"/v1/sddcs/validations/{TIMEOUT_ID}", "getSddcSpecValidation")
        for _ in range(4)
    )
    actual = [(entry["method"], entry["rawPath"], entry["operationId"]) for entry in log]
    require(actual == expected, f"wrong poll-exhaustion sequence: {actual!r}")
    assert_headers_and_body(log[0], expected_body=EXPECTED_MINIMAL_BODY)
    for entry in log[1:]:
        assert_headers_and_body(entry, expected_body=None)
    require(
        all(entry["path"] != "/v1/sddcs" for entry in log),
        "deploySddc was called after poll exhaustion",
    )


def verify_http_error(log: list[dict[str, object]]) -> None:
    require(len(log) == 1, "client continued after validateSddcSpec returned HTTP 500")
    entry = log[0]
    require(
        (entry["method"], entry["rawPath"], entry["operationId"])
        == ("POST", "/v1/sddcs/validations", "validateSddcSpec"),
        "wrong request before HTTP failure",
    )
    assert_headers_and_body(entry, expected_body=EXPECTED_MINIMAL_BODY)


def verify_poll_error(log: list[dict[str, object]]) -> None:
    expected = [
        ("POST", "/v1/sddcs/validations", "validateSddcSpec"),
        ("GET", f"/v1/sddcs/validations/{TIMEOUT_ID}", "getSddcSpecValidation"),
    ]
    actual = [(entry["method"], entry["rawPath"], entry["operationId"]) for entry in log]
    require(actual == expected, f"client continued after polling returned HTTP 500: {actual!r}")
    assert_headers_and_body(log[0], expected_body=EXPECTED_MINIMAL_BODY)
    assert_headers_and_body(log[1], expected_body=None)
    require(
        all(entry["path"] != "/v1/sddcs" for entry in log),
        "deploySddc was called after polling failed",
    )


def verify_immediate_success(log: list[dict[str, object]]) -> None:
    expected = [
        ("POST", "/v1/sddcs/validations", "validateSddcSpec"),
        ("POST", "/v1/sddcs", "deploySddc"),
    ]
    actual = [(entry["method"], entry["rawPath"], entry["operationId"]) for entry in log]
    require(actual == expected, f"terminal successful validation was polled: {actual!r}")
    for entry in log:
        assert_headers_and_body(entry, expected_body=EXPECTED_MINIMAL_BODY)
        require(entry["query"] == "", "unset optional query parameter was not omitted")
    require(log[0]["bodyHex"] == log[1]["bodyHex"], "validation/deploy bodies differ")


def verify_immediate_failure(log: list[dict[str, object]]) -> None:
    require(len(log) == 1, "terminal failed validation was polled or deployed")
    entry = log[0]
    require(
        (entry["method"], entry["rawPath"], entry["operationId"])
        == ("POST", "/v1/sddcs/validations", "validateSddcSpec"),
        "wrong request for immediate terminal failure",
    )
    assert_headers_and_body(entry, expected_body=EXPECTED_MINIMAL_BODY)


def main() -> None:
    verify_contract()
    with tempfile.TemporaryDirectory(prefix="vcf91-0220-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_client(classes)
        verify_success(
            run_scenario(classes, temp, "success"), EXPECTED_MINIMAL_BODY, ""
        )
        verify_success(
            run_scenario(classes, temp, "success-optionals-true"),
            EXPECTED_OPTIONAL_BODY,
            "skipValidations=true",
        )
        verify_success(
            run_scenario(classes, temp, "success-optionals-false"),
            EXPECTED_OPTIONAL_BODY,
            "skipValidations=false",
        )
        verify_immediate_success(run_scenario(classes, temp, "immediate-success"))
        verify_immediate_failure(run_scenario(classes, temp, "immediate-failure"))
        verify_failure(run_scenario(classes, temp, "failure"))
        verify_timeout(run_scenario(classes, temp, "timeout"))
        verify_http_error(run_scenario(classes, temp, "http-error"))
        verify_poll_error(run_scenario(classes, temp, "poll-error"))
    print("PASS: VCF Installer precheck gate and exact wire contract verified")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
