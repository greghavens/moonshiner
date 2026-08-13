#!/usr/bin/env python3
"""Protected acceptance verifier for the VCF Installer depot-token module."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_TAG = "9.0.0.0"
EXPECTED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATION_IDS = {
    "createToken",
    "getApplianceInfo",
    "updateDepotSettings",
}
DOWNLOAD_TOKEN = "0123456789abcdef0123456789abcdef"
EXPECTED_DEPOT_BODY = {"vmwareAccount": {"downloadToken": DOWNLOAD_TOKEN}}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_provenance(root: Path) -> None:
    source = load_json(root / "docs" / "official_sources.json")
    contract = load_json(root / "docs" / "contract.json")
    require(source["tag"] == EXPECTED_TAG, "official source tag is not 9.0.0.0")
    require(source["commit"] == EXPECTED_SHA, "official source commit changed")
    require(
        source["specPath"] == EXPECTED_SPEC_PATH,
        "official source spec path changed",
    )
    require(
        set(source["operationIds"]) == EXPECTED_OPERATION_IDS,
        "official source operationIds do not match the fixture",
    )
    require(contract["info"]["version"] == "9.0.0.0", "contract is not VCF 9.0")

    found = set()
    for path_item in contract["paths"].values():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                found.add(operation["operationId"])
    require(found == EXPECTED_OPERATION_IDS, "contract operationIds changed")

    put = contract["paths"]["/v1/system/settings/depot"]["put"]
    require(put["operationId"] == "updateDepotSettings", "wrong PUT operationId")
    require(
        put["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/DepotSettings",
        "wrong updateDepotSettings request schema",
    )
    account_properties = set(
        contract["components"]["schemas"]["DepotAccount"]["properties"]
    )
    require(
        account_properties
        == {"username", "password", "status", "message", "downloadToken"},
        "DepotAccount contract does not match the pinned 9.0 schema",
    )


def validate_module_source(root: Path) -> None:
    module_path = root / "src" / "VcfInstallerDepot" / "VcfInstallerDepot.psm1"
    manifest_path = root / "src" / "VcfInstallerDepot" / "VcfInstallerDepot.psd1"
    manifest = manifest_path.read_text(encoding="utf-8")

    require(
        "VMware.Sdk.Vcf.Installer" in manifest,
        "module manifest does not require VMware.Sdk.Vcf.Installer",
    )
    require(
        "VMware.OpenAPI" in manifest and "13.4.0.24798382" in manifest,
        "module manifest does not pin the Installer runtime dependency",
    )
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "PowerShell 7 (pwsh) is required")
    inspected = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(root / ".moonshiner" / "inspect_candidate.ps1"),
            "-ModulePath",
            str(module_path),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=20,
    )
    require(
        inspected.returncode == 0,
        "candidate source inspection failed\n"
        f"stdout:\n{inspected.stdout}\nstderr:\n{inspected.stderr}",
    )


def wait_for_ready(ready_path: Path, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ready_path.exists() and ready_path.stat().st_size:
            return load_json(ready_path)
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(
                f"mock exited before ready (code {process.returncode})\n{stdout}\n{stderr}"
            )
        time.sleep(0.03)
    raise VerificationError("timed out waiting for the loopback mock")


def read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_acceptance(
    root: Path, *, first_put_status: int, expect_success: bool
) -> list[dict]:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "PowerShell 7 (pwsh) is required")

    module_check = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "if (Get-Module -ListAvailable VMware.Sdk.Vcf.Installer | "
            "Where-Object Version -EQ '13.4.0.24798382') { exit 0 } else { exit 7 }",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=20,
    )
    require(
        module_check.returncode == 0,
        "required prerequisite VMware.Sdk.Vcf.Installer 13.4.0.24798382 is missing",
    )

    with tempfile.TemporaryDirectory(prefix="vcf90-0104-") as temp_name:
        temp = Path(temp_name)
        log_path = temp / "requests.ndjson"
        ready_path = temp / "ready.json"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(root / ".moonshiner" / "mock_vcf_installer.py"),
                "--contract",
                str(root / "docs" / "contract.json"),
                "--log",
                str(log_path),
                "--ready",
                str(ready_path),
                "--first-put-status",
                str(first_put_status),
            ],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready = wait_for_ready(ready_path, mock)
            completed = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(root / ".moonshiner" / "invoke_candidate.ps1"),
                    "-BaseUri",
                    ready["baseUri"],
                    "-ModulePath",
                    str(root / "src" / "VcfInstallerDepot" / "VcfInstallerDepot.psd1"),
                    "-DownloadToken",
                    DOWNLOAD_TOKEN,
                ],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=40,
                env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"},
            )
            if expect_success:
                require(
                    completed.returncode == 0,
                    "candidate invocation failed\n"
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
                )
                require(completed.stdout.strip(), "candidate produced no SDK result")
            else:
                require(
                    completed.returncode != 0,
                    f"candidate unexpectedly retried HTTP {first_put_status}",
                )
            return read_log(log_path)
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=3)


def validate_wire(records: list[dict]) -> None:
    require(records, "mock request log is empty")
    allowed = {
        ("POST", "/v1/tokens", "createToken"),
        ("GET", "/v1/system/appliance-info", "getApplianceInfo"),
        ("PUT", "/v1/system/settings/depot", "updateDepotSettings"),
    }
    observed = {
        (record["method"], record["path"], record["operationId"])
        for record in records
    }
    require(observed <= allowed, f"mock received a route outside the contract: {observed - allowed}")
    require(
        all(record["status"] != 404 for record in records),
        "candidate or SDK requested an operation outside the contract",
    )

    puts = [
        record
        for record in records
        if record["operationId"] == "updateDepotSettings"
    ]
    require(len(puts) == 2, f"expected one PUT plus one retry, observed {len(puts)}")
    require([record["status"] for record in puts] == [500, 202], "wrong retry sequence")
    require(puts[0]["bodyHex"] == puts[1]["bodyHex"], "retry body bytes changed")
    require(
        puts[0]["effectApplied"] is True and puts[1]["effectApplied"] is False,
        "the retried PUT duplicated its effect",
    )
    require(puts[-1]["effectCount"] == 1, "depot setting was applied more than once")

    for index, record in enumerate(puts, start=1):
        require(record["method"] == "PUT", f"request {index} method is not PUT")
        require(
            record["path"] == "/v1/system/settings/depot",
            f"request {index} path is wrong",
        )
        require(record["query"] == "", f"request {index} unexpectedly has a query")
        require(
            record["headers"].get("content-type", "").split(";", 1)[0].strip().lower()
            == "application/json",
            f"request {index} Content-Type is not application/json",
        )
        require(
            record["headers"].get("authorization") == "Bearer fixture-access-token",
            f"request {index} is missing the SDK connection bearer token",
        )
        try:
            body = json.loads(record["bodyUtf8"])
        except json.JSONDecodeError as error:
            raise VerificationError(f"request {index} body is not JSON: {error}") from error
        require(
            body == EXPECTED_DEPOT_BODY,
            f"request {index} has the wrong exact JSON shape: {body!r}",
        )
        require(
            set(body) == {"vmwareAccount"}
            and set(body["vmwareAccount"]) == {"downloadToken"},
            f"request {index} sent unset optional fields",
        )


def validate_nonretry(records: list[dict], expected_status: int) -> None:
    puts = [
        record
        for record in records
        if record["operationId"] == "updateDepotSettings"
    ]
    require(
        len(puts) == 1,
        f"HTTP {expected_status} was retried; observed {len(puts)} PUTs",
    )
    require(
        puts[0]["status"] == expected_status,
        f"non-retry scenario did not return HTTP {expected_status}",
    )
    require(puts[0]["effectCount"] == 0, "rejected PUT unexpectedly changed state")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        validate_provenance(root)
        validate_module_source(root)
        records = run_acceptance(root, first_put_status=500, expect_success=True)
        validate_wire(records)
        for status in (400, 503):
            nonretry_records = run_acceptance(
                root, first_put_status=status, expect_success=False
            )
            validate_nonretry(nonretry_records, status)
    except (VerificationError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF Installer SDK request is exact and retry-safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
