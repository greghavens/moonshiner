#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0200."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_VERSION = "13.5.0.25380678"
SOURCE_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SOURCE_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getApplianceInfo": ("GET", "/v1/system/appliance-info"),
    "updateDepotSettings": ("PUT", "/v1/system/settings/depot"),
}
SDK_CONNECTION_ROUTE = ("GET", "/v1/sddc-manager")
PRIMARY_TOKEN = "0123456789abcdef0123456789abcdef"
SECONDARY_TOKEN = "fedcba9876543210fedcba9876543210"
REQUEST_TIMEOUT_TOKEN = "11111111111111111111111111111111"
THROTTLED_TOKEN = "22222222222222222222222222222222"
SERVER_ERROR_TOKEN = "33333333333333333333333333333333"
TRANSPORT_ERROR_TOKEN = "44444444444444444444444444444444"
EXHAUSTED_TOKEN = "55555555555555555555555555555555"


def fail(message: str) -> None:
    raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )

    if contract["openapiVersion"] != "3.0.1":
        fail("contract OpenAPI version is not 3.0.1")
    if contract["apiVersion"] != "9.1.0.0":
        fail("contract API version is not VCF Installer 9.1.0.0")
    if contract["derivedFrom"]["commitSha"] != SOURCE_SHA:
        fail("contract source commit changed")
    if contract["derivedFrom"]["specPath"] != SOURCE_PATH:
        fail("contract source path changed")

    actual_operations = {
        operation_id: (definition["method"], definition["path"])
        for operation_id, definition in contract["operations"].items()
    }
    if actual_operations != EXPECTED_OPERATIONS:
        fail(f"contract operation map changed: {actual_operations!r}")
    if contract["operationIds"] != list(EXPECTED_OPERATIONS):
        fail("contract operationId order or contents changed")

    update = contract["operations"]["updateDepotSettings"]
    if update["requestBody"] != {
        "required": True,
        "contentType": "application/json",
        "schema": "DepotSettings",
    }:
        fail("updateDepotSettings request contract changed")
    if update["responses"] != {
        "202": "DepotSettings",
        "400": "Error",
        "500": "Error",
    }:
        fail("updateDepotSettings response contract changed")

    account = contract["schemas"]["DepotAccount"]
    expected_account_properties = {
        "username",
        "password",
        "status",
        "message",
        "downloadToken",
        "downloadActivationCode",
    }
    if set(account["properties"]) != expected_account_properties:
        fail("DepotAccount fields no longer match the pinned OpenAPI schema")
    if account["required"] != []:
        fail("DepotAccount unexpectedly has required schema fields")
    if account["properties"]["downloadToken"].get("maxLength") != 32:
        fail("DepotAccount.downloadToken maxLength changed")

    settings = contract["schemas"]["DepotSettings"]
    if set(settings["properties"]) != {
        "vmwareAccount",
        "offlineAccount",
        "depotConfiguration",
    }:
        fail("DepotSettings fields no longer match the pinned OpenAPI schema")
    if settings["required"] != []:
        fail("DepotSettings unexpectedly has required schema fields")
    configuration = contract["schemas"]["DepotConfiguration"]
    if configuration["required"] != ["isOfflineDepot"]:
        fail("DepotConfiguration required fields changed")

    retry = contract["retryPolicy"]
    if (
        retry["operationId"] != "updateDepotSettings"
        or retry["method"] != "PUT"
        or retry["stableResourcePath"] != "/v1/system/settings/depot"
        or retry["reuseSameRepresentation"] is not True
    ):
        fail("idempotent retry policy changed")

    if sources["commitSha"] != SOURCE_SHA or sources["specPath"] != SOURCE_PATH:
        fail("official source provenance changed")
    if sources["repositoryLicense"] != "Apache-2.0":
        fail("official source license changed")
    if sources["apiVersion"] != "9.1.0.0":
        fail("official source API version changed")
    source_operations = {
        row["operationId"]: (row["method"], row["path"])
        for row in sources["operations"]
    }
    if source_operations != EXPECTED_OPERATIONS:
        fail("official_sources.json must record every exact operationId")
    if SOURCE_SHA not in sources["specUrl"] or SOURCE_PATH not in sources["specUrl"]:
        fail("official spec URL is not commit-pinned")


def verify_candidate_shape() -> None:
    module_path = ROOT / "src/VcfInstaller.Depot/VcfInstaller.Depot.psm1"
    module = module_path.read_text(encoding="utf-8")
    manifest = (
        ROOT / "src/VcfInstaller.Depot/VcfInstaller.Depot.psd1"
    ).read_text(encoding="utf-8")

    required_commands = {
        "Initialize-VcfInstallerDepotAccount",
        "Initialize-VcfInstallerDepotSettings",
        "Invoke-VcfInstallerUpdateDepotSettings",
    }
    missing = sorted(command for command in required_commands if command not in module)
    if missing:
        fail("implementation does not use required VMware SDK cmdlets: " + ", ".join(missing))

    forbidden = [
        "Invoke-RestMethod",
        "Invoke-WebRequest",
        "System.Net.WebClient",
        "System.Net.Http",
        "HttpClient",
        "curl",
    ]
    used_forbidden = [token for token in forbidden if token.lower() in module.lower()]
    if used_forbidden:
        fail("direct HTTP clients are not allowed: " + ", ".join(used_forbidden))
    if "Export-ModuleMember -Function Set-VcfInstallerDepotToken" not in module:
        fail("public function export changed")
    if "VMware.Sdk.Vcf.Installer" not in manifest or SDK_VERSION not in manifest:
        fail("module manifest does not pin the provided VMware SDK prerequisite")


def read_log(log_path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line:
            records.append(json.loads(line))
    return records


def verify_wire(records: list[dict[str, object]]) -> None:
    if not records:
        fail("loopback mock received no requests")

    known_routes = set(EXPECTED_OPERATIONS.values()) | {SDK_CONNECTION_ROUTE}
    for record in records:
        route = (str(record["method"]), str(record["path"]))
        if route not in known_routes:
            fail(
                "request used neither a projected operation nor the pinned "
                f"SDK connection route: {route!r}"
            )

    puts = [
        record
        for record in records
        if record["method"] == "PUT"
        and record["path"] == "/v1/system/settings/depot"
    ]
    if len(puts) != 15:
        fail(f"expected exactly fifteen depot PUT requests, received {len(puts)}")

    primary_body = {"vmwareAccount": {"downloadToken": PRIMARY_TOKEN}}
    client_error_body = {
        "vmwareAccount": {
            "downloadToken": SECONDARY_TOKEN,
            "downloadActivationCode": "activation-code-01",
        }
    }
    request_timeout_body = {
        "vmwareAccount": {"downloadToken": REQUEST_TIMEOUT_TOKEN}
    }
    throttled_body = {"vmwareAccount": {"downloadToken": THROTTLED_TOKEN}}
    server_error_body = {"vmwareAccount": {"downloadToken": SERVER_ERROR_TOKEN}}
    transport_error_body = {
        "vmwareAccount": {"downloadToken": TRANSPORT_ERROR_TOKEN}
    }
    exhausted_body = {"vmwareAccount": {"downloadToken": EXHAUSTED_TOKEN}}
    expected_bodies = [
        primary_body,
        primary_body,
        primary_body,
        client_error_body,
        request_timeout_body,
        request_timeout_body,
        throttled_body,
        throttled_body,
        server_error_body,
        server_error_body,
        transport_error_body,
        transport_error_body,
        exhausted_body,
        exhausted_body,
        exhausted_body,
    ]
    expected_scenarios = [
        "primary",
        "primary",
        "primary",
        "clientError",
        "requestTimeout",
        "requestTimeout",
        "throttled",
        "throttled",
        "serverError",
        "serverError",
        "transportError",
        "transportError",
        "exhausted",
        "exhausted",
        "exhausted",
    ]
    expected_scenario_attempts = [1, 2, 3, 1, 1, 2, 1, 2, 1, 2, 1, 2, 1, 2, 3]
    expected_statuses: list[int | str] = [
        503,
        202,
        202,
        400,
        408,
        202,
        429,
        202,
        502,
        202,
        "disconnect",
        202,
        503,
        503,
        503,
    ]
    expected_effects = [
        True,
        False,
        False,
        False,
        False,
        True,
        False,
        True,
        False,
        True,
        True,
        False,
        True,
        False,
        False,
    ]
    expected_effect_counts = [1, 1, 1, 1, 1, 2, 2, 3, 3, 4, 5, 5, 6, 6, 6]

    for index, record in enumerate(puts):
        if record["query"] != "":
            fail(f"depot PUT {index + 1} must not have a query string")
        try:
            body = json.loads(str(record["body"]))
        except json.JSONDecodeError as error:
            fail(f"depot PUT {index + 1} body is not JSON: {error}")
        if body != expected_bodies[index]:
            fail(
                f"depot PUT {index + 1} has the wrong exact property set or values: "
                f"{body!r}"
            )

        account = body.get("vmwareAccount")
        if not isinstance(account, dict):
            fail(f"depot PUT {index + 1} has no vmwareAccount object")
        forbidden_account_fields = {"username", "password", "status", "message"}
        leaked_fields = forbidden_account_fields.intersection(account)
        if leaked_fields:
            fail(
                f"depot PUT {index + 1} sent unset optional account fields: "
                f"{sorted(leaked_fields)!r}"
            )
        forbidden_settings_fields = {"offlineAccount", "depotConfiguration"}
        leaked_settings = forbidden_settings_fields.intersection(body)
        if leaked_settings:
            fail(
                f"depot PUT {index + 1} sent unset optional settings fields: "
                f"{sorted(leaked_settings)!r}"
            )
        if index < 3 and "downloadActivationCode" in account:
            fail("unset downloadActivationCode was serialized on a primary attempt")

        headers = record["headers"]
        if not isinstance(headers, dict):
            fail("request headers were not captured")
        content_type = (
            str(headers.get("content-type", ""))
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type != "application/json":
            fail(
                f"depot PUT {index + 1} Content-Type is not application/json: "
                f"{content_type!r}"
            )
        if "application/json" not in str(headers.get("accept", "")).lower():
            fail(f"depot PUT {index + 1} does not accept application/json")
        if headers.get("authorization") != "Bearer loopback-access-token":
            fail(f"depot PUT {index + 1} did not use the SDK bearer connection")

        if record.get("putSequence") != index + 1:
            fail("mock PUT sequence was not deterministic")
        if record.get("scenario") != expected_scenarios[index]:
            fail(f"depot PUT {index + 1} entered the wrong retry scenario")
        if record.get("scenarioAttempt") != expected_scenario_attempts[index]:
            fail(f"depot PUT {index + 1} has the wrong scenario attempt number")
        if record.get("plannedStatus") != expected_statuses[index]:
            fail(f"depot PUT {index + 1} received the wrong mock outcome")
        if record.get("effectApplied") is not expected_effects[index]:
            fail(f"depot PUT {index + 1} has an unexpected state-change result")
        if record.get("effectCount") != expected_effect_counts[index]:
            fail("repeated identical PUT created a duplicate configuration effect")

    identical_groups = [
        (0, 1, 2),
        (4, 5),
        (6, 7),
        (8, 9),
        (10, 11),
        (12, 13, 14),
    ]
    for group in identical_groups:
        wire_bodies = {str(puts[index]["body"]) for index in group}
        if len(wire_bodies) != 1:
            fail(
                "retry did not reuse the exact serialized PUT representation "
                f"for requests {[index + 1 for index in group]}"
            )

    token_posts = [
        record
        for record in records
        if record["method"] == "POST" and record["path"] == "/v1/tokens"
    ]
    connection_gets = [
        record
        for record in records
        if record["method"] == "GET"
        and record["path"] == SDK_CONNECTION_ROUTE[1]
    ]
    appliance_gets = [
        record
        for record in records
        if record["method"] == "GET"
        and record["path"] == "/v1/system/appliance-info"
    ]
    if len(token_posts) != 1 or len(connection_gets) != 1:
        fail("the genuine SDK connection must use one token and one version probe")
    if appliance_gets:
        fail("the pinned SDK unexpectedly used getApplianceInfo during connection")
    if token_posts[0]["query"] or connection_gets[0]["query"]:
        fail("connection operations must not use query parameters")
    if connection_gets[0]["body"] != "":
        fail("the SDK connection version probe must not send a request body")
    headers = connection_gets[0]["headers"]
    if not isinstance(headers, dict) or headers.get("authorization") != (
        "Bearer loopback-access-token"
    ):
        fail("the SDK connection version probe did not use its bearer token")


def run_integration() -> None:
    python = shutil.which("python3")
    pwsh = shutil.which("pwsh")
    if python is None or pwsh is None:
        fail("python3 and pwsh are required environment prerequisites")

    with tempfile.TemporaryDirectory(prefix="vcf91-0200-") as temporary:
        temp = Path(temporary)
        log_path = temp / "requests.jsonl"
        port_file = temp / "port.txt"
        output_file = temp / "result.json"
        server = subprocess.Popen(
            [
                python,
                str(ROOT / "tests/mock_vcf_installer.py"),
                "--contract",
                str(ROOT / "docs/contract.json"),
                "--log",
                str(log_path),
                "--port-file",
                str(port_file),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not port_file.exists():
                if server.poll() is not None:
                    stdout, stderr = server.communicate(timeout=2)
                    fail(f"loopback mock exited early:\n{stdout}\n{stderr}")
                if time.monotonic() >= deadline:
                    fail("loopback mock did not become ready")
                time.sleep(0.02)
            port = int(port_file.read_text(encoding="ascii"))

            environment = os.environ.copy()
            environment["NO_PROXY"] = "127.0.0.1,localhost"
            environment["no_proxy"] = "127.0.0.1,localhost"
            completed = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(ROOT / "tests/exercise.ps1"),
                    "-Port",
                    str(port),
                    "-OutputFile",
                    str(output_file),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if completed.returncode != 0:
                fail(
                    "PowerShell integration failed:\n"
                    + completed.stdout
                    + "\n"
                    + completed.stderr
                )

            result = json.loads(output_file.read_text(encoding="utf-8-sig"))
            expected_result = {
                "firstDownloadToken": PRIMARY_TOKEN,
                "repeatedDownloadToken": PRIMARY_TOKEN,
                "clientErrorRejected": True,
                "transientCasesRetried": 4,
                "exhaustedStatus": 503,
            }
            if any(result.get(key) != value for key, value in expected_result.items()):
                fail(f"PowerShell result changed: {result!r}")
            chain = result.get("exhaustedChain") or []
            if isinstance(chain, str):
                chain = [chain]
            if not any(
                str(name).startswith("VMware.Binding.OpenApi.Client.")
                for name in chain
            ):
                fail(
                    "retry exhaustion did not preserve the genuine SDK "
                    f"exception: {chain!r}"
                )
            verify_wire(read_log(log_path))
        finally:
            server.terminate()
            try:
                server.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate(timeout=5)


def main() -> int:
    try:
        verify_contract()
        verify_candidate_shape()
        run_integration()
    except (AssertionError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF Installer depot token retries are contract-correct and idempotent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
