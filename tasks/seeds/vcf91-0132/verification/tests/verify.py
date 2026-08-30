#!/usr/bin/env python3
"""Deterministic protected verification for vcf91-0132."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfVksEnsure" / "VcfVksEnsure.psd1"
MODULE_PATH = ROOT / "VcfVksEnsure" / "VcfVksEnsure.psm1"
MOCK_PATH = ROOT / ".moonshiner" / "mock_server.py"
INVOKER_PATH = ROOT / ".moonshiner" / "invoke_case.ps1"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
OPERATION_IDS = [
    "Vcenter.Namespaces.Instances_getV2",
    "Vcenter.Namespaces.Instances_createV2",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def validate_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    source = contract["source"]
    require(source["commitSha"] == COMMIT, "contract commit pin changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract spec blob changed")
    require(source["license"] == "Apache-2.0", "wrong source license")
    require(source["apiVersion"] == "9.1.0.0", "wrong vSphere API version")
    require(source["openapi"] == "3.0.3", "wrong OpenAPI version")
    require(
        [item["operationId"] for item in contract["operations"]] == OPERATION_IDS,
        "focused operationId set changed",
    )
    require(
        sources["repositoryCommitSha"] == COMMIT,
        "official source commit pin changed",
    )
    require(sources["specPath"] == SPEC_PATH, "official source path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "official source blob changed")
    require(sources["operationIds"] == OPERATION_IDS, "official operationIds changed")
    require(
        contract["securitySchemes"]["api_key_auth"]
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter security projection changed",
    )
    kubernetes = contract["kubernetesApi"]["operations"]
    require(
        [item["operationKey"] for item in kubernetes]
        == [
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:create",
        ],
        "Kubernetes route projection changed",
    )
    return contract


def validate_prerequisites() -> None:
    command = (
        "$m = Get-Module -ListAvailable VMware.Sdk.Vcf.SddcManager "
        "| Where-Object Version -EQ ([version]'13.5.0.25380678') "
        "| Select-Object -First 1; if ($null -eq $m) { exit 4 }; "
        "$manifest = Test-ModuleManifest -Path "
        + "'"
        + str(MANIFEST_PATH).replace("'", "''")
        + "'; "
        + "if ($manifest.ExportedFunctions.Keys.Count -ne 1 -or "
        + "-not $manifest.ExportedFunctions.ContainsKey('Ensure-VcfVksCluster')) "
        + "{ exit 5 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
        env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"},
    )
    require(
        result.returncode == 0,
        "VCF PowerCLI 9.1 prerequisite or protected manifest is invalid",
    )
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.diagnostics.process",
        "tcpclient",
    ):
        require(forbidden not in source, f"forbidden transport found: {forbidden}")


def start_mock(
    temp: Path, config_path: Path
) -> tuple[subprocess.Popen[str], str, Path]:
    log_path = temp / "requests.jsonl"
    ready_path = temp / "ready.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(MOCK_PATH),
            "--contract",
            str(CONTRACT_PATH),
            "--config",
            str(config_path),
            "--log",
            str(log_path),
            "--ready",
            str(ready_path),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready_path.exists():
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            return process, f"http://{ready['host']}:{ready['port']}", log_path
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(f"mock exited early: {stdout}\n{stderr}")
        time.sleep(0.02)
    process.terminate()
    raise VerificationError("mock did not become ready")


def parse_marker(stdout: str, marker: str) -> dict[str, Any]:
    line = next(
        (item for item in stdout.splitlines() if item.startswith(marker + "=")),
        None,
    )
    require(line is not None, f"{marker} marker missing")
    return json.loads(line.split("=", 1)[1])


def one_header(record: dict[str, Any], name: str) -> str:
    values = record["headers"].get(name, [])
    require(len(values) == 1, f"{record['operation']} must send one {name} header")
    return values[0]


def no_empty_json(value: Any, path: str = "$") -> None:
    if value is None:
        raise VerificationError(f"null optional value serialized at {path}")
    if isinstance(value, str) and value == "":
        raise VerificationError(f"empty string serialized at {path}")
    if isinstance(value, list):
        require(value, f"empty array serialized at {path}")
        for index, item in enumerate(value):
            no_empty_json(item, f"{path}[{index}]")
    if isinstance(value, dict):
        require(value, f"empty object serialized at {path}")
        for key, item in value.items():
            no_empty_json(item, f"{path}.{key}")


def verify_log(
    records: list[dict[str, Any]], config: dict[str, str]
) -> None:
    namespace_path = (
        "/api/vcenter/namespaces/instances/v2/" + config["namespace"]
    )
    namespace_collection = "/api/vcenter/namespaces/instances/v2"
    cluster_path = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        + config["namespace"]
        + "/clusters/"
        + config["cluster_name"]
    )
    cluster_collection = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        + config["namespace"]
        + "/clusters"
    )
    expected = [
        ("namespace.getV2", "GET", namespace_path),
        ("namespace.createV2", "POST", namespace_collection),
        ("namespace.getV2", "GET", namespace_path),
        ("kubernetes.cluster.get", "GET", cluster_path),
        ("kubernetes.cluster.create", "POST", cluster_collection),
        ("namespace.getV2", "GET", namespace_path),
        ("kubernetes.cluster.get", "GET", cluster_path),
    ]
    require(len(records) == len(expected), "unexpected request count")
    for record, (operation, method, target) in zip(records, expected, strict=True):
        require(record["operation"] == operation, "unexpected operation order")
        require(record["method"] == method, f"wrong method for {operation}")
        require(record["raw_target"] == target, f"wrong raw target for {operation}")
        require(record["path"] == target, f"wrong path for {operation}")
        require(record["query"] == "", f"query must be omitted for {operation}")
        require(
            one_header(record, "accept") == "application/json",
            f"wrong Accept header for {operation}",
        )
        if operation.startswith("namespace."):
            require(
                one_header(record, "vmware-api-session-id")
                == config["vcenter_session_id"],
                "wrong vCenter credential",
            )
            require(
                "authorization" not in record["headers"],
                "Kubernetes credential leaked to vCenter",
            )
        else:
            require(
                one_header(record, "authorization")
                == "Bearer " + config["kubernetes_bearer_token"],
                "wrong Kubernetes credential",
            )
            require(
                "vmware-api-session-id" not in record["headers"],
                "vCenter credential leaked to Kubernetes",
            )

        if method == "GET":
            require(record["body_bytes"] == 0, f"GET body sent for {operation}")
            require(record["body"] is None, f"GET JSON body sent for {operation}")
            require(
                "content-type" not in record["headers"],
                f"Content-Type sent without a body for {operation}",
            )
        else:
            require(record["body_bytes"] > 0, f"POST body missing for {operation}")
            require(
                one_header(record, "content-type") == "application/json",
                f"wrong Content-Type for {operation}",
            )
            require(
                one_header(record, "content-length") == str(record["body_bytes"]),
                f"wrong Content-Length for {operation}",
            )
            no_empty_json(record["body"])

    expected_namespace_body = {
        "namespace": config["namespace"],
        "supervisor": config["supervisor"],
    }
    require(
        records[1]["body"] == expected_namespace_body,
        "namespace body must contain only required fields when options are unset",
    )
    require(
        records[1]["body_raw"]
        == json.dumps(expected_namespace_body, separators=(",", ":")),
        "namespace JSON bytes have the wrong property order or encoding",
    )
    expected_cluster_body = {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": config["cluster_name"],
            "namespace": config["namespace"],
        },
        "spec": {
            "topology": {
                "class": config["cluster_class"],
                "version": config["kubernetes_version"],
            }
        },
    }
    require(
        records[4]["body"] == expected_cluster_body,
        "Cluster body has the wrong exact shape",
    )
    require(
        records[4]["body_raw"]
        == json.dumps(expected_cluster_body, separators=(",", ":")),
        "Cluster JSON bytes have the wrong property order or encoding",
    )
    require(
        sum(record["operation"] == "namespace.createV2" for record in records) == 1,
        "namespace create was duplicated",
    )
    require(
        sum(
            record["operation"] == "kubernetes.cluster.create" for record in records
        )
        == 1,
        "Cluster create was duplicated",
    )


def main() -> int:
    validate_contract()
    validate_prerequisites()
    suffix = secrets.token_hex(5)
    config = {
        "vcenter_session_id": "vc-" + secrets.token_urlsafe(18),
        "kubernetes_bearer_token": "k8s-" + secrets.token_urlsafe(20),
        "supervisor": "supervisor-" + suffix,
        "namespace": "team-" + suffix,
        "cluster_name": "payments-" + suffix,
        "kubernetes_version": "v1.33.1+vmware.1-fips-vkr.2",
        "cluster_class": "builtin-generic-v3",
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0132-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        mock, base_uri, log_path = start_mock(temp, config_path)
        try:
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-ModulePath",
                    str(MANIFEST_PATH),
                    "-BaseUri",
                    base_uri,
                    "-ConfigPath",
                    str(config_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=35,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=3)

    require(
        result.returncode == 0,
        f"PowerShell case failed:\n{result.stdout}\n{result.stderr}",
    )
    first = parse_marker(result.stdout, "FIRST_RESULT")
    second = parse_marker(result.stdout, "SECOND_RESULT")
    require(
        list(first) == [
            "Namespace",
            "NamespaceCreated",
            "NamespaceRecoveredAfterAmbiguousCreate",
            "Cluster",
            "ClusterCreated",
        ],
        "first result has the wrong public shape",
    )
    require(first["Namespace"] == config["namespace"], "wrong namespace result")
    require(first["NamespaceCreated"] is True, "first call did not create namespace")
    require(
        first["NamespaceRecoveredAfterAmbiguousCreate"] is True,
        "ambiguous namespace create was not recovered",
    )
    require(first["Cluster"] == config["cluster_name"], "wrong Cluster result")
    require(first["ClusterCreated"] is True, "first call did not create Cluster")
    require(
        second
        == {
            "Namespace": config["namespace"],
            "NamespaceCreated": False,
            "NamespaceRecoveredAfterAmbiguousCreate": False,
            "Cluster": config["cluster_name"],
            "ClusterCreated": False,
        },
        "second ensure call was not a read-only retry",
    )
    verify_log(records, config)
    print("verification passed: ambiguous mutation recovered without duplication")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
