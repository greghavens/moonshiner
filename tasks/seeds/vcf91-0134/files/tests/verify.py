#!/usr/bin/env python3
"""Protected deterministic acceptance checks for VcfVksProvisioning."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "src" / "VcfVksProvisioning.psd1"
MODULE_PATH = ROOT / "src" / "VcfVksProvisioning.psm1"
MOCK_PATH = ROOT / "mock" / "mock_server.py"
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
PINNED_OPERATIONS = [
    "Vcenter.NamespaceManagement.Supervisors.Summary_get",
    "Vcenter.Namespaces.Instances_getV2",
]

VALUES = {
    "supervisor": "supervisor-42",
    "namespace": "team-aurora",
    "name": "aurora-vks",
    "cluster_class": "builtin-generic-v3.6.0",
    "kubernetes_version": "v1.35.0---vmware.2-vkr.4",
    "vm_class": "best-effort-medium",
    "storage_class": "vsan-default-storage-policy",
    "session": "session-contract-0134",
    "token": "kube-contract-0134",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def check_protected_contract() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    derived = contract.get("derivedFrom", {})
    require(derived.get("commit") == PINNED_COMMIT, "contract commit is not pinned")
    require(derived.get("specPath") == PINNED_SPEC, "contract spec path changed")
    require(derived.get("infoVersion") == "9.1.0.0", "contract is not VCF 9.1")
    require(sources.get("commit") == PINNED_COMMIT, "official source commit changed")
    require(sources.get("specPath") == PINNED_SPEC, "official source path changed")
    require(sources.get("license") == "Apache-2.0", "source license missing")
    require(
        sources.get("specUrl", "").startswith(
            f"https://github.com/vmware/vcf-api-specs/blob/{PINNED_COMMIT}/"
        ),
        "official source URL is not commit pinned",
    )
    contract_ids = [
        item.get("operationId") for item in contract.get("vsphereOperations", [])
    ]
    source_ids = [item.get("operationId") for item in sources.get("operationIds", [])]
    require(contract_ids == PINNED_OPERATIONS, "contract operationIds changed")
    require(source_ids == PINNED_OPERATIONS, "official operationIds changed")
    require(
        contract.get("gateOrder")
        == ["getSupervisorSummary", "getNamespaceV2", "createVksCluster"],
        "contract gate order changed",
    )
    return contract


def check_sdk_prerequisite() -> None:
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    require(
        re.search(
            r"RequiredModules\s*=\s*@\(.*VMware\.Sdk\.Vcf\.SddcManager",
            manifest,
            flags=re.DOTALL,
        )
        is not None,
        "module manifest must require VMware.Sdk.Vcf.SddcManager",
    )
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and "vmware.sdk.vcf" in str(path.relative_to(ROOT)).lower()
    ]
    require(not vendored, f"VMware SDK content must not be vendored: {vendored}")
    source = MODULE_PATH.read_text(encoding="utf-8")
    require(
        re.search(r"\bInvoke-VcfGetVcenters\b", source) is not None,
        "Inventory mode must use Invoke-VcfGetVcenters",
    )


def expand(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    require("{" not in template, f"unexpanded contract path: {template}")
    return template


@contextmanager
def mock_server(scenario: str) -> Iterator[tuple[str, Path]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0134-") as temp_dir:
        temp = Path(temp_dir)
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--request-log",
                str(log_path),
                "--ready-file",
                str(ready_path),
                "--scenario",
                scenario,
                "--supervisor-id",
                VALUES["supervisor"],
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not ready_path.exists():
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    raise VerificationError(
                        f"mock exited before ready: {stdout}\n{stderr}"
                    )
                time.sleep(0.02)
            require(ready_path.exists(), "mock did not become ready")
            base_uri = load_json(ready_path)["baseUri"]
            yield base_uri, log_path
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if process.returncode not in (0, -15):
                stdout, stderr = process.communicate()
                raise VerificationError(
                    f"mock failed with {process.returncode}: {stdout}\n{stderr}"
                )


def invoke_module(
    base_uri: str,
    *,
    optional: bool = False,
    invalid_optional: bool = False,
    what_if: bool = False,
    expect_success: bool,
) -> subprocess.CompletedProcess[str]:
    optional_lines = ""
    if optional:
        optional_lines = """
$parameters.ControlPlaneReplicas = 3
$parameters.WorkerReplicas = 5
$parameters.PodCidrBlocks = @('10.244.0.0/16', '10.245.0.0/16')
$parameters.ServiceCidrBlocks = @('10.96.0.0/12')
$parameters.ServiceDomain = 'cluster.local'
"""
    if invalid_optional:
        optional_lines = "$parameters.ServiceDomain = '   '\n"
    if what_if:
        optional_lines += "$parameters.WhatIf = $true\n"
    script = f"""
$ErrorActionPreference = 'Stop'
Import-Module '{str(MANIFEST_PATH).replace("'", "''")}' -Force
$parameters = @{{
    VCenterUri = [uri]'{base_uri}'
    SupervisorId = '{VALUES["supervisor"]}'
    Namespace = '{VALUES["namespace"]}'
    Name = '{VALUES["name"]}'
    ClusterClass = '{VALUES["cluster_class"]}'
    KubernetesVersion = '{VALUES["kubernetes_version"]}'
    VmClass = '{VALUES["vm_class"]}'
    StorageClass = '{VALUES["storage_class"]}'
    VCenterSessionId = '{VALUES["session"]}'
    KubeBearerToken = '{VALUES["token"]}'
    Confirm = $false
}}
{optional_lines}
try {{
    $result = New-VcfVksCluster @parameters
    $result | ConvertTo-Json -Depth 30 -Compress
    exit 0
}}
catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 17
}}
"""
    env = os.environ.copy()
    env["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    # The script goes in as an argument, not down standard input. `-Command -`
    # reads stdin a line at a time: a `try {` on its own line is not a complete
    # command, and PowerShell 7.6 discards the block rather than continuing it.
    # Every call returned success having run nothing but the Import-Module, and
    # the wire log was empty because no request was ever made.
    completed = subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    if expect_success:
        require(
            completed.returncode == 0,
            f"module call failed:\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
    else:
        require(
            completed.returncode != 0,
            "a failed precheck unexpectedly returned success",
        )
    return completed


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def minimal_body() -> dict[str, Any]:
    return {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": VALUES["name"],
            "namespace": VALUES["namespace"],
        },
        "spec": {
            "topology": {
                "class": VALUES["cluster_class"],
                "version": VALUES["kubernetes_version"],
                "variables": [
                    {"name": "vmClass", "value": VALUES["vm_class"]},
                    {"name": "storageClass", "value": VALUES["storage_class"]},
                ],
            }
        },
    }


def full_body() -> dict[str, Any]:
    body = minimal_body()
    body["spec"]["clusterNetwork"] = {
        "pods": {"cidrBlocks": ["10.244.0.0/16", "10.245.0.0/16"]},
        "services": {"cidrBlocks": ["10.96.0.0/12"]},
        "serviceDomain": "cluster.local",
    }
    topology = body["spec"]["topology"]
    topology["controlPlane"] = {"replicas": 3}
    topology["workers"] = {
        "machineDeployments": [
            {"class": "node-pool", "name": "md-0", "replicas": 5}
        ]
    }
    return body


def expected_paths(contract: dict[str, Any]) -> list[str]:
    vsphere = {item["name"]: item for item in contract["vsphereOperations"]}
    kubernetes = {
        item["name"]: item for item in contract["vksKubernetesOperations"]
    }
    return [
        expand(
            vsphere["getSupervisorSummary"]["pathTemplate"],
            supervisor=VALUES["supervisor"],
        ),
        expand(
            vsphere["getNamespaceV2"]["pathTemplate"],
            namespace=VALUES["namespace"],
        ),
        "/supervisor"
        + expand(
            kubernetes["createVksCluster"]["pathTemplate"],
            namespace=VALUES["namespace"],
        ),
    ]


def check_wire(
    entries: list[dict[str, Any]],
    contract: dict[str, Any],
    expected_body: dict[str, Any],
) -> None:
    require(len(entries) == 3, f"expected exactly 3 requests, got {len(entries)}")
    require(
        [entry["operation"] for entry in entries]
        == contract["gateOrder"],
        "request order does not match the precheck gate",
    )
    require(
        [entry["method"] for entry in entries] == ["GET", "GET", "POST"],
        "HTTP methods do not match the contract",
    )
    require(
        [entry["path"] for entry in entries] == expected_paths(contract),
        "request paths do not match the contract",
    )
    require(
        all(entry["query"] == "" for entry in entries),
        "contract requests must not contain a query string",
    )
    require(
        all(entry["rawPath"] == entry["path"] for entry in entries),
        "raw request targets must be the exact contract paths",
    )
    require(
        all(entry["headers"].get("accept") == "application/json" for entry in entries),
        "every operation must request application/json exactly",
    )
    for entry in entries[:2]:
        require(entry["body"] == "", "GET prechecks must not send a request body")
        require(
            "content-type" not in entry["headers"],
            "GET prechecks must not send a content type without a body",
        )
        require(
            entry["headers"].get("vmware-api-session-id") == VALUES["session"],
            "vCenter precheck has the wrong session header",
        )
        require(
            "authorization" not in entry["headers"],
            "Kubernetes bearer token leaked to vCenter",
        )
    mutation = entries[2]
    require(
        mutation["headers"].get("authorization")
        == f"Bearer {VALUES['token']}",
        "Kubernetes mutation has the wrong bearer header",
    )
    require(
        "vmware-api-session-id" not in mutation["headers"],
        "vCenter session leaked to Kubernetes",
    )
    require(
        mutation["headers"].get("content-type", "").lower() == "application/json",
        "Kubernetes mutation content type is not exactly application/json",
    )
    require(mutation["json"] == expected_body, "Kubernetes JSON shape is wrong")
    # Re-encoded rather than compared byte for byte. A byte comparison pins the
    # order of the keys inside `spec`, and neither the task nor the contract
    # says what that order is -- a JSON object does not have one. What the
    # contract does say is which fields the body carries and which it omits,
    # and that survives being written in any order.
    canonical = json.dumps(expected_body, separators=(",", ":"), sort_keys=True)
    require(
        json.dumps(json.loads(mutation["body"]), separators=(",", ":"), sort_keys=True)
        == canonical,
        "Kubernetes request bytes do not decode to exactly the expected document",
    )


def test_ready_minimal(contract: dict[str, Any]) -> None:
    with mock_server("ready") as (base_uri, log_path):
        invoke_module(base_uri, expect_success=True)
        entries = read_log(log_path)
    expected = minimal_body()
    check_wire(entries, contract, expected)
    serialized = entries[2]["body"]
    for forbidden in (
        '"clusterNetwork"',
        '"controlPlane"',
        '"workers"',
        '"labels"',
        '"annotations"',
        ":null",
        ":[]",
        ":{}",
        ':""',
    ):
        require(
            forbidden not in serialized,
            f"unset optional content was serialized: {forbidden}",
        )


def test_ready_optional(contract: dict[str, Any]) -> None:
    with mock_server("ready") as (base_uri, log_path):
        invoke_module(base_uri, optional=True, expect_success=True)
        entries = read_log(log_path)
    check_wire(entries, contract, full_body())


def test_supervisor_gate() -> None:
    with mock_server("supervisor-not-ready") as (base_uri, log_path):
        invoke_module(base_uri, expect_success=False)
        entries = read_log(log_path)
    require(
        [entry["operation"] for entry in entries] == ["getSupervisorSummary"],
        "Supervisor failure must stop before namespace precheck and mutation",
    )


def test_namespace_gate() -> None:
    with mock_server("namespace-error") as (base_uri, log_path):
        invoke_module(base_uri, expect_success=False)
        entries = read_log(log_path)
    require(
        [entry["operation"] for entry in entries]
        == ["getSupervisorSummary", "getNamespaceV2"],
        "namespace failure must stop before the mutation",
    )


def test_empty_optional_rejected() -> None:
    with mock_server("ready") as (base_uri, log_path):
        invoke_module(
            base_uri,
            invalid_optional=True,
            expect_success=False,
        )
        entries = read_log(log_path)
    require(
        entries == [],
        "an explicitly empty optional value must fail before any request",
    )


def test_what_if_gate() -> None:
    with mock_server("ready") as (base_uri, log_path):
        invoke_module(base_uri, what_if=True, expect_success=True)
        entries = read_log(log_path)
    require(
        [entry["operation"] for entry in entries]
        == ["getSupervisorSummary", "getNamespaceV2"],
        "-WhatIf may run prechecks but must suppress the mutation",
    )


def main() -> int:
    checks = [
        ("protected contract provenance", check_protected_contract),
        ("VCF SDK prerequisite", check_sdk_prerequisite),
    ]
    try:
        contract = check_protected_contract()
        print("ok - protected contract provenance")
        check_sdk_prerequisite()
        print("ok - VCF SDK prerequisite")
        test_ready_minimal(contract)
        print("ok - exact minimal wire shape and omission")
        test_ready_optional(contract)
        print("ok - exact populated optional wire shape")
        test_supervisor_gate()
        print("ok - Supervisor precheck gates mutation")
        test_namespace_gate()
        print("ok - namespace precheck gates mutation")
        test_empty_optional_rejected()
        print("ok - explicitly empty optional value is rejected")
        test_what_if_gate()
        print("ok - WhatIf suppresses mutation")
    except (VerificationError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
