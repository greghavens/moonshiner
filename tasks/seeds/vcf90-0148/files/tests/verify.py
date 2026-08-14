#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from mock_vcfa import MockState, start_mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfAutomation.Projects" / "VcfAutomation.Projects.psd1"
RUNNER_PATH = ROOT / "tests" / "run_fixture.ps1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_protected_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    source = contract["source"]
    require(
        source["kind"] == "reference-documentation",
        "contract source kind must be reference-documentation",
    )
    require(
        "reference documentation rather than a published specification"
        in source["statement"],
        "contract must state that its source is reference documentation, not a published specification",
    )
    require(
        "vmware/vcf-api-specs" in source["specificationRepositoryNote"],
        "contract must record the VCF Automation specification gap",
    )

    operations = contract["operations"]
    require(
        [
            (item["id"], item["referenceOperation"], item["method"], item["path"])
            for item in operations
        ]
        == [("getProjects", "Get Projects", "GET", "/iaas/api/projects")],
        "focused contract operation changed",
    )
    parameters = [item["name"] for item in operations[0]["queryParameters"]]
    require(
        parameters == ["apiVersion", "$top", "$skip", "$orderBy", "$count", "$filter"],
        "Get Projects query contract changed",
    )

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))["sources"]
    require(len(sources) == 2, "official source ledger must contain both pages used")
    require(
        {(item["operation"]["name"], item["operation"]["method"], item["operation"]["path"]) for item in sources}
        == {
            ("Get Projects", "GET", "/iaas/api/projects"),
            ("Retrieve Auth Token", "POST", "/iaas/api/login"),
        },
        "official source operations changed",
    )
    for source_item in sources:
        require(source_item["fetchedOn"] == "2026-08-13", "every source needs its fetch date")
        require(
            source_item["url"].startswith("https://developer.broadcom.com/xapis/")
            and "/9.0/" in source_item["url"],
            "source URLs must be version-specific Broadcom xAPIs pages",
        )

    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    require(
        "VMware.Sdk.Vcf.Installer" in manifest
        and "13.4.0.24798382" in manifest,
        "VCF PowerCLI prerequisite declaration changed",
    )
    # Scan only seed-owned product paths. The trace harness places its PowerShell
    # toolchain beneath the runtime work directory, so scanning all of ROOT would
    # mistake genuine harness prerequisites for vendored seed artifacts.
    module_root = ROOT / "VcfAutomation.Projects"
    vendored = [
        path
        for path in module_root.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".dll", ".nupkg"}
            or path.name.startswith("VMware.Sdk.Vcf.")
        )
    ]
    require(not vendored, f"VCF PowerCLI artifacts must not be vendored: {vendored}")
    return contract


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def verify_wire(
    log: list[dict[str, Any]],
    access_token: str,
    expected_targets: list[str],
) -> None:
    require(
        len(log) == len(expected_targets),
        f"expected {len(expected_targets)} requests, got {len(log)}",
    )

    for index, (request, expected_target) in enumerate(zip(log, expected_targets), start=1):
        require(request["method"] == "GET", f"request {index}: method must be GET")
        require(
            request["target"] == expected_target,
            f"request {index}: raw target mismatch: {request['target']!r}",
        )
        headers = request["headers"]
        require(
            headers.get("authorization") == [f"Bearer {access_token}"],
            f"request {index}: Authorization header mismatch or multiplicity error",
        )
        require(
            headers.get("accept") == ["application/json"],
            f"request {index}: Accept header mismatch or multiplicity error",
        )
        require("content-type" not in headers, f"request {index}: bodyless GET must omit Content-Type")
        require("content-length" not in headers, f"request {index}: bodyless GET must omit Content-Length")
        require("transfer-encoding" not in headers, f"request {index}: bodyless GET must omit transfer encoding")
        require(request["bodyLength"] == 0 and request["body"] == "", f"request {index}: GET body was sent")

        require("=" not in request["target"] or not request["target"].endswith("="), f"request {index}: empty query value")


def invoke_fixture(
    pwsh: str,
    temp: Path,
    access_token: str,
    *,
    label: str,
    api_version: str | None = None,
    filter_value: str | None = None,
    zero_page_at: int | None = None,
    expect_failure: bool = False,
    page_size: int = 3,
) -> tuple[dict[str, Any], list[dict[str, Any]], MockState]:
    request_log = temp / f"{label}-requests.jsonl"
    result_path = temp / f"{label}-result.json"
    server, state = start_mock(
        CONTRACT_PATH,
        request_log,
        access_token,
        api_version,
        filter_value,
        zero_page_at,
        page_size,
    )
    host, port = server.server_address
    arguments = [
        pwsh,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(RUNNER_PATH),
        "-ModulePath",
        str(MANIFEST_PATH),
        "-Server",
        f"http://{host}:{port}",
        "-AccessToken",
        access_token,
        "-ResultPath",
        str(result_path),
        "-PageSize",
        str(page_size),
    ]
    if api_version is not None:
        arguments.extend(["-ApiVersion", api_version])
    if filter_value is not None:
        arguments.extend(["-Filter", filter_value])
    if expect_failure:
        arguments.append("-ExpectFailure")

    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    try:
        completed = subprocess.run(
            arguments,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    require(
        completed.returncode == 0,
        f"PowerShell fixture {label!r} failed:\n"
        + completed.stdout
        + completed.stderr,
    )
    require(result_path.exists(), f"PowerShell fixture {label!r} did not write its result")
    return (
        json.loads(result_path.read_text(encoding="utf-8")),
        read_log(request_log),
        state,
    )


def main() -> None:
    contract = verify_protected_contract()
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required")

    access_token = "fixture-access-token"
    with tempfile.TemporaryDirectory(prefix="vcfa-projects-") as temporary:
        temp = Path(temporary)
        probe_log = temp / "probe-requests.jsonl"
        server, state = start_mock(CONTRACT_PATH, probe_log, access_token)
        require(
            state.named_operations
            == {(item["method"], item["path"]) for item in contract["operations"]},
            "mock route table is not pinned to the contract operations",
        )
        host, port = server.server_address
        try:
            try:
                urllib.request.urlopen(f"http://{host}:{port}/off-contract", timeout=2)
                raise AssertionError("mock served an operation not named by the contract")
            except urllib.error.HTTPError as error:
                require(error.code == 404, "unknown mock operation must return 404")
        finally:
            server.shutdown()
            server.server_close()

        result, requests, state = invoke_fixture(
            pwsh, temp, access_token, label="default"
        )
        verify_wire(
            requests,
            access_token,
            [
                "/iaas/api/projects?%24top=3&%24skip=0",
                "/iaas/api/projects?%24top=3&%24skip=2",
                "/iaas/api/projects?%24top=3&%24skip=4",
            ],
        )
        require(result["succeeded"] is True, "default inventory call did not succeed")

        version = tuple(int(part) for part in result["prerequisiteVersion"].split(".")[:2])
        require(version >= (13, 4), "VCF PowerCLI prerequisite is older than the manifest minimum")
        projects = result["projects"]
        require(len(projects) == len(state.expected_projects()), "project collection is incomplete")
        expected_projects = sorted(
            state.expected_projects(), key=lambda item: (item["name"], item["id"])
        )
        require(
            projects == expected_projects,
            "projects were not preserved in stable ordinal name/id order",
        )

        api_version = "2026-08-13"
        filter_value = "name eq 'café & tea'"
        result, requests, _ = invoke_fixture(
            pwsh,
            temp,
            access_token,
            label="optionals",
            api_version=api_version,
            filter_value=filter_value,
            page_size=4,
        )
        encoded_prefix = (
            "apiVersion=2026-08-13&%24top=4&%24skip="
        )
        encoded_filter = "%24filter=name%20eq%20%27caf%C3%A9%20%26%20tea%27"
        verify_wire(
            requests,
            access_token,
            [
                f"/iaas/api/projects?{encoded_prefix}{skip}&{encoded_filter}"
                for skip in (0, 2, 4)
            ],
        )
        require(result["succeeded"] is True, "call with optional query fields did not succeed")

        result, requests, _ = invoke_fixture(
            pwsh,
            temp,
            access_token,
            label="zero-page",
            zero_page_at=2,
            expect_failure=True,
        )
        verify_wire(
            requests,
            access_token,
            [
                "/iaas/api/projects?%24top=3&%24skip=0",
                "/iaas/api/projects?%24top=3&%24skip=2",
            ],
        )
        require(result["succeeded"] is False, "premature zero-item page did not fail")

    print("PASS: complete VCF Automation project pagination and exact wire shape verified")


if __name__ == "__main__":
    main()
