#!/usr/bin/env python3
"""Protected deterministic verifier for the VCF Networks batch module."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "src" / "Vcf.OperationsNetworks"
MANIFEST = MODULE_DIR / "Vcf.OperationsNetworks.psd1"
IMPLEMENTATION = MODULE_DIR / "Vcf.OperationsNetworks.psm1"
EXERCISE = ROOT / "tests" / "exercise.ps1"
MOCK_PATH = ROOT / "tests" / "fixtures" / "vcf_networks_mock.py"


def fail(message: str) -> None:
    raise AssertionError(message)


def load_mock() -> Any:
    spec = importlib.util.spec_from_file_location("vcf_networks_mock", MOCK_PATH)
    if spec is None or spec.loader is None:
        fail("could not load loopback mock")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_seed_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text("utf-8"))
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text("utf-8")
    )
    expected_sha = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
    expected_path = (
        "specifications/vcf-operations/"
        "vcf-operations-for-networks-openapi.yaml"
    )
    if contract["derived_from"]["commit_sha"] != expected_sha:
        fail("contract revision changed")
    if contract["derived_from"]["path"] != expected_path:
        fail("contract source path changed")
    operations = {
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    }
    expected_operations = {
        ("create", "POST", "/auth/token"),
        ("addVcenterDatasource", "POST", "/data-sources/vcenters"),
    }
    if operations != expected_operations:
        fail(f"contract operations changed: {operations!r}")
    if sources["revision"]["commit_sha"] != expected_sha:
        fail("official source revision changed")
    if sources["specification_path"] != expected_path:
        fail("official source path changed")
    if {item["operationId"] for item in sources["operations"]} != {
        "create",
        "addVcenterDatasource",
    }:
        fail("official source operationIds changed")


def assert_powercli_dependency() -> None:
    manifest_text = MANIFEST.read_text("utf-8")
    if "VMware.Sdk.Vcf.Ops" not in manifest_text:
        fail("module manifest must retain the VMware.Sdk.Vcf.Ops prerequisite")
    ast_check = r"""
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_NETWORKS_IMPLEMENTATION_PATH, [ref] $tokens, [ref] $errors)
if ($errors.Count -gt 0) { exit 3 }
$matches = @($ast.FindAll({
    param($node)
    if ($node -isnot [System.Management.Automation.Language.CommandAst]) {
        return $false
    }
    $name = $node.GetCommandName()
    return $name -ieq 'Initialize-VcfOpsUsernamePassword' -or
        $name -ieq 'VMware.Sdk.Vcf.Ops\Initialize-VcfOpsUsernamePassword'
}, $true))
if ($matches.Count -lt 1) { exit 4 }
"""
    parse_environment = os.environ.copy()
    parse_environment["VCF_NETWORKS_IMPLEMENTATION_PATH"] = str(IMPLEMENTATION)
    parsed = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            ast_check,
        ],
        cwd=ROOT,
        env=parse_environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if parsed.returncode != 0:
        fail("implementation must call the VMware.Sdk.Vcf.Ops credential initializer")
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path != MODULE_DIR and path.name.lower().startswith("vmware.sdk.vcf")
    ]
    if vendored:
        fail(f"VMware SDK content must not be vendored: {vendored!r}")


def require_content_type(entry: dict[str, Any], index: int) -> None:
    content_type = entry["headers"].get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        fail(f"request {index} did not use application/json: {content_type!r}")


def assert_wire_log(entries: list[dict[str, Any]]) -> None:
    auth_body = {
        "username": "netops@example.com",
        "password": "test-password",
    }
    first_body = {
        "fqdn": "vc-a.lab.example",
        "proxy_id": "10000:901:collector-a",
        "nickname": "vc-a",
        "enabled": False,
        "credentials": {
            "username": "svc-a@vsphere.local",
            "password": "vc-a-password",
        },
        "ipfix_request": {"enable_all": False},
        "tags": [
            {
                "tag_key": "environment",
                "tag_value": "production",
            }
        ],
    }
    second_body = {
        "ip": "192.0.2.42",
        "proxy_id": "10000:901:collector-b",
        "nickname": "vc-b",
        "credentials": {
            "username": "svc-b@vsphere.local",
            "password": "vc-b-password",
        },
    }
    expected = [
        ("/api/ni/auth/token", None, auth_body),
        (
            "/api/ni/data-sources/vcenters",
            "NetworkInsight token-expiring",
            first_body,
        ),
        (
            "/api/ni/data-sources/vcenters",
            "NetworkInsight token-expiring",
            second_body,
        ),
        ("/api/ni/auth/token", None, auth_body),
        (
            "/api/ni/data-sources/vcenters",
            "NetworkInsight token-refreshed",
            second_body,
        ),
    ]
    if len(entries) != len(expected):
        fail(f"expected exactly five requests, got {len(entries)}: {entries!r}")

    for index, (entry, (path, authorization, body)) in enumerate(
        zip(entries, expected, strict=True)
    ):
        if entry["method"] != "POST":
            fail(f"request {index} method was {entry['method']!r}, expected POST")
        if entry["path"] != path or entry["query"] != "":
            fail(
                f"request {index} target was {entry['path']!r}?{entry['query']}, "
                f"expected {path!r} with no query"
            )
        require_content_type(entry, index)
        actual_authorization = entry["headers"].get("authorization")
        if actual_authorization != authorization:
            fail(
                f"request {index} Authorization was {actual_authorization!r}, "
                f"expected {authorization!r}"
            )
        if entry["body"] != body:
            fail(
                f"request {index} JSON wire shape mismatch:\n"
                f"actual:   {entry['body']!r}\nexpected: {body!r}"
            )

    first_creates = [
        entry
        for entry in entries
        if entry["path"] == "/api/ni/data-sources/vcenters"
        and isinstance(entry["body"], dict)
        and entry["body"].get("nickname") == "vc-a"
    ]
    if len(first_creates) != 1:
        fail("the completed vc-a item was replayed after token refresh")


def assert_result(result_path: Path) -> None:
    result = json.loads(result_path.read_text("utf-8"))
    if not isinstance(result, list):
        fail(f"function result must be an array, got {type(result).__name__}")
    if [item.get("entity_id") for item in result] != ["vc-1", "vc-2"]:
        fail(f"created results were not returned once in input order: {result!r}")
    if [item.get("nickname") for item in result] != ["vc-a", "vc-b"]:
        fail(f"created result nicknames are wrong: {result!r}")


def main() -> None:
    assert_seed_contract()
    assert_powercli_dependency()
    mock_module = load_mock()

    with tempfile.TemporaryDirectory(prefix="vcf-networks-verify-") as temp_name:
        temp_dir = Path(temp_name)
        log_path = temp_dir / "requests.jsonl"
        result_path = temp_dir / "result.json"
        server = mock_module.create_server(log_path)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_uri = f"http://127.0.0.1:{server.server_port}"
        try:
            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-File",
                    str(EXERCISE),
                    "-BaseUri",
                    base_uri,
                    "-OutputPath",
                    str(result_path),
                ],
                cwd=ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        if completed.returncode != 0:
            fail(
                "PowerShell scenario failed\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        if not result_path.is_file():
            fail("PowerShell scenario did not write its result")
        entries = [
            json.loads(line)
            for line in log_path.read_text("utf-8").splitlines()
            if line.strip()
        ]
        assert_wire_log(entries)
        assert_result(result_path)

    print("PASS: exact contract wire shape, token refresh, and batch progress verified")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
