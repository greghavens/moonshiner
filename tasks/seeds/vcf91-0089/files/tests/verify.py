#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 asynchronous vCenter clone task."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "mock" / "vcenter_contract_mock.py"
MANIFEST_PATH = (
    ROOT
    / "src"
    / "VcfVCenterAutomation"
    / "VcfVCenterAutomation.psd1"
)

PINNED_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB_SHA = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATIONS = {
    "Vcenter.VM_clone$Task": (
        "POST",
        "/api/vcenter/vm",
        "/vcenter/vm?action=clone&vmw-task=true",
    ),
    "Cis.Tasks_get": (
        "GET",
        "/api/cis/tasks/{task}",
        "/cis/tasks/{task}",
    ),
}
PROTECTED_HASHES = {
    "README.md": "09cb83f72f5ca37fa87adead23f31edfde212ebe0016de3d3dea28a8ccaee9f2",
    "docs/contract.json": "af5b04fb5a85ca6b2c81e9a19bf8082022c39d55318b8f10818f1be356d01437",
    "docs/official_sources.json": "452789510ab5ad7aa537b22949b08c7299547a66d3cc390328df8589015a580a",
    "mock/vcenter_contract_mock.py": "42176aed298722a1d11c5f6a1314d79816bbaf84cebabf36bb5179a91ab59e34",
    "src/VcfVCenterAutomation/VcfVCenterAutomation.psd1": "6e7783403453f408f0322f7ddf4ec8728fe15871e1cfd3bd24995c321cf481f2",
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_protected_inputs() -> dict[str, Any]:
    for relative, expected_hash in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"protected file is missing: {relative}")
        require(
            sha256(path) == expected_hash,
            f"protected file changed: {relative}",
        )

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    source = contract.get("source", {})
    require(source.get("commitSha") == PINNED_SHA, "contract commit SHA changed")
    require(
        source.get("specBlobSha") == PINNED_BLOB_SHA,
        "contract spec blob SHA changed",
    )
    require(
        source.get("specPath") == PINNED_SPEC_PATH,
        "contract spec path changed",
    )
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.3", "contract OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "contract API version changed")
    require(source.get("serverBasePath") == "/api", "contract base path changed")
    require(
        contract.get("security")
        == {
            "scheme": "api_key_auth",
            "header": "vmware-api-session-id",
            "in": "header",
        },
        "contract security scheme changed",
    )

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations must be an array")
    by_id = {item.get("operationId"): item for item in operations}
    require(
        set(by_id) == set(EXPECTED_OPERATIONS),
        "contract must contain exactly the selected operationIds",
    )
    for operation_id, (method, path, spec_path_item) in EXPECTED_OPERATIONS.items():
        operation = by_id[operation_id]
        require(operation.get("method") == method, f"{operation_id} method changed")
        require(operation.get("path") == path, f"{operation_id} path changed")
        require(
            operation.get("specPathItem") == spec_path_item,
            f"{operation_id} specification path item changed",
        )

    clone = by_id["Vcenter.VM_clone$Task"]
    require(
        clone.get("fixedQuery")
        == [
            {"name": "action", "value": "clone"},
            {"name": "vmw-task", "value": "true"},
        ],
        "clone fixed query changed",
    )
    clone_schema = contract.get("schemas", {}).get("Vcenter.VM.CloneSpec", {})
    require(
        clone_schema.get("required") == ["name", "source"],
        "clone required properties changed",
    )
    require(
        set(clone_schema.get("properties", {}))
        == {
            "source",
            "name",
            "placement",
            "disks_to_remove",
            "disks_to_update",
            "power_on",
            "guest_customization_spec",
        },
        "clone properties changed",
    )
    require(
        contract["schemas"]["Cis.Task.Status"]["terminal"]
        == ["SUCCEEDED", "FAILED"],
        "task terminal states changed",
    )

    require(
        sources.get("repositoryCommitSha") == PINNED_SHA,
        "official source commit SHA changed",
    )
    require(
        sources.get("specBlobSha") == PINNED_BLOB_SHA,
        "official source blob SHA changed",
    )
    require(
        sources.get("specPath") == PINNED_SPEC_PATH,
        "official source spec path changed",
    )
    require(sources.get("license") == "Apache-2.0", "official source license changed")
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and {item.get("operationId") for item in source_operations}
        == set(EXPECTED_OPERATIONS),
        "official_sources.json must record every selected operationId",
    )
    for item in source_operations:
        require(
            item.get("repositoryCommitSha") == PINNED_SHA
            and item.get("specPath") == PINNED_SPEC_PATH,
            f"source provenance missing for {item.get('operationId')}",
        )

    required_module = "VMware.Sdk.Vcf.SddcManager"
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    require(
        required_module in manifest_text and "13.5.0" in manifest_text,
        "manifest no longer declares the VMware.Sdk.Vcf prerequisite",
    )
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path != MANIFEST_PATH
        and (
            path.name.startswith("VMware.Sdk.")
            or path.name.startswith("VMware.PowerCLI")
        )
    ]
    require(not vendored, "VMware SDK modules must not be vendored in the task")
    return contract


def wait_for_port_file(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(
                f"mock exited during startup ({process.returncode})\n{stdout}\n{stderr}"
            )
        if port_file.exists():
            text = port_file.read_text(encoding="ascii").strip()
            if text:
                return int(text)
        time.sleep(0.02)
    raise VerificationError("timed out waiting for loopback mock")


def powershell_program() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Import-Module $env:VCF_VCENTER_MANIFEST -Force -ErrorAction Stop

$newCommand = Get-Command New-VcfVCenterClient -ErrorAction Stop
$connectionType = $newCommand.Parameters['Connection'].ParameterType.FullName

$client = New-VcfVCenterClient `
    -Server ([uri] $env:VCF_VCENTER_SERVER) `
    -SessionToken $env:VCF_VCENTER_SESSION

$clone = Invoke-VcfVmClone `
    -Client $client `
    -SourceVm $env:VCF_VCENTER_SOURCE_VM `
    -Name $env:VCF_VCENTER_CLONE_NAME `
    -TimeoutSeconds 5 `
    -PollIntervalMilliseconds 10

[ordered]@{
    connectionParameterType = $connectionType
    taskId = $clone.TaskId
    status = $clone.Status
    result = $clone.Result
    pollCount = $clone.PollCount
} |
    ConvertTo-Json -Depth 20 |
    Set-Content -LiteralPath $env:VCF_VCENTER_RESULT -Encoding utf8
"""


def run_powershell(
    port: int,
    result_path: Path,
    work: Path,
) -> tuple[str, str]:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required")

    seed = PINNED_SHA[:12]
    source_vm = f"vm-source-{seed}"
    clone_name = f"VCF 9.1 clone {seed}"
    script_path = work / "acceptance.ps1"
    script_path.write_text(powershell_program(), encoding="utf-8")

    cache_home = work / "xdg-cache"
    cache_home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
            # Keep pwsh startup/module caches out of the workspace HOME so
            # verification never leaves untracked files behind.
            "XDG_CACHE_HOME": str(cache_home),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "VCF_VCENTER_MANIFEST": str(MANIFEST_PATH),
            "VCF_VCENTER_SERVER": f"http://127.0.0.1:{port}",
            "VCF_VCENTER_SESSION": "loopback-vcenter-session",
            "VCF_VCENTER_SOURCE_VM": source_vm,
            "VCF_VCENTER_CLONE_NAME": clone_name,
            "VCF_VCENTER_RESULT": str(result_path),
        }
    )

    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        raise VerificationError(
            "PowerShell acceptance program failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    require(result_path.exists(), "PowerShell did not write its result")
    return source_vm, clone_name


def load_entries(request_log: Path) -> list[dict[str, Any]]:
    require(request_log.is_file(), "mock request log is missing")
    entries = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    require(entries, "mock request log is empty")
    return entries


def check_result_and_wire(
    result_path: Path,
    request_log: Path,
    source_vm: str,
    clone_name: str,
) -> None:
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    require(
        result.get("connectionParameterType")
        == "VMware.Sdk.OpenApi.Cmdlets.IServerConnection",
        "New-VcfVCenterClient must retain the VMware SDK connection type",
    )
    require(result.get("status") == "SUCCEEDED", "clone task did not succeed")
    require(
        isinstance(result.get("pollCount"), int) and result["pollCount"] == 3,
        "HTTP 202 was treated as completion or polling evidence is incorrect",
    )

    entries = load_entries(request_log)
    require(len(entries) == 4, "expected exactly one clone and three task reads")
    require(
        [entry.get("operationId") for entry in entries]
        == [
            "Vcenter.VM_clone$Task",
            "Cis.Tasks_get",
            "Cis.Tasks_get",
            "Cis.Tasks_get",
        ],
        "request order or operation selection is incorrect",
    )
    require(
        [entry.get("sequence") for entry in entries] == [1, 2, 3, 4],
        "request sequence is not deterministic",
    )

    clone = entries[0]
    require(clone.get("method") == "POST", "clone method must be POST")
    require(
        clone.get("rawTarget")
        == "/api/vcenter/vm?action=clone&vmw-task=true",
        "clone request target is not the exact contract target",
    )
    require(
        clone.get("query") == {"action": ["clone"], "vmw-task": ["true"]},
        "clone fixed query values are incorrect",
    )
    require(
        clone.get("sessionToken") == "loopback-vcenter-session",
        "clone request omitted the vmware-api-session-id header",
    )
    require(clone.get("accept") == "application/json", "clone Accept header changed")
    require(
        str(clone.get("contentType", "")).lower()
        == "application/json; charset=utf-8",
        "clone Content-Type must be application/json with UTF-8",
    )

    expected_body = {"source": source_vm, "name": clone_name}
    expected_raw_body = json.dumps(
        expected_body,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    require(clone.get("body") == expected_body, "clone JSON body values changed")
    require(
        set(clone["body"]) == {"source", "name"},
        "unset optional clone fields were serialized",
    )
    require(
        clone.get("rawBody") == expected_raw_body,
        "clone JSON wire representation is not exact",
    )
    require(
        clone.get("contentLength")
        == len(expected_raw_body.encode("utf-8")),
        "clone Content-Length does not match the UTF-8 body",
    )
    forbidden = {
        "placement",
        "disks_to_remove",
        "disks_to_update",
        "power_on",
        "guest_customization_spec",
    }
    require(
        forbidden.isdisjoint(clone["body"]),
        "an unset optional clone field was sent",
    )

    task_id = clone.get("issuedTaskId")
    require(isinstance(task_id, str) and task_id, "mock issued no task identifier")
    require(result.get("taskId") == task_id, "client polled a different task")
    require(
        result.get("result") == clone.get("eventualResult"),
        "client did not return the successful task result",
    )

    polls = entries[1:]
    expected_target = f"/api/cis/tasks/{task_id}"
    require(
        all(entry.get("method") == "GET" for entry in polls),
        "task status method must be GET",
    )
    require(
        all(entry.get("rawTarget") == expected_target for entry in polls),
        "task reads changed the task path or sent the optional spec query",
    )
    require(
        all(entry.get("query") == {} for entry in polls),
        "unset task spec query must be omitted",
    )
    require(
        all(entry.get("sessionToken") == "loopback-vcenter-session" for entry in polls),
        "task read omitted the vmware-api-session-id header",
    )
    require(
        all(entry.get("accept") == "application/json" for entry in polls),
        "task read Accept header changed",
    )
    require(
        all(
            entry.get("contentType") is None
            and entry.get("contentLength") == 0
            and entry.get("rawBody") is None
            for entry in polls
        ),
        "GET task reads must not send a body or Content-Type",
    )
    require(
        [entry.get("returnedStatus") for entry in polls]
        == ["PENDING", "RUNNING", "SUCCEEDED"],
        "mock terminal-state sequence was not fully observed",
    )
    require(
        [entry.get("pollCount") for entry in polls] == [1, 2, 3],
        "poll counts are incorrect",
    )


def verify() -> None:
    validate_protected_inputs()
    with tempfile.TemporaryDirectory(prefix="vcf91-0089-") as temporary:
        work = Path(temporary)
        request_log = work / "requests.jsonl"
        port_file = work / "port"
        result_path = work / "result.json"

        process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--request-log",
                str(request_log),
                "--port-file",
                str(port_file),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_port_file(port_file, process)
            source_vm, clone_name = run_powershell(port, result_path, work)
            check_result_and_wire(
                result_path,
                request_log,
                source_vm,
                clone_name,
            )
        finally:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            if process.returncode not in (0, -15):
                sys.stderr.write(stdout)
                sys.stderr.write(stderr)


def main() -> int:
    try:
        verify()
    except (
        VerificationError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 vCenter clone wire contract and task polling verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
