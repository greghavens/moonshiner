#!/usr/bin/env python3
"""Protected, deterministic acceptance checks for the PowerShell deliverable."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "mock_server.py"
MODULE_PATH = ROOT / "VksSupervisor.psd1"
INVOKER_PATH = ROOT / "grader_tests" / "invoke_case.ps1"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_OPERATION_IDS = [
    "Vcenter.Namespaces.Instances_createV2",
    "Vcenter.Namespaces.Instances_getV2",
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
    require(source["commit_sha"] == PINNED_COMMIT, "contract commit pin changed")
    require(source["path"] == PINNED_SPEC_PATH, "contract spec path changed")
    require(source["license"] == "Apache-2.0", "source license must be Apache-2.0")
    require(contract["product_version"] == "9.1.0.0", "wrong API version")
    require(source["openapi"] == "3.0.3", "wrong OpenAPI version")
    actual_ids = [item["operationId"] for item in contract["operations"]]
    require(actual_ids == VCENTER_OPERATION_IDS, "unexpected vCenter operationIds")
    require(
        sources["repository_commit_sha"] == PINNED_COMMIT,
        "official source commit pin changed",
    )
    require(sources["spec_path"] == PINNED_SPEC_PATH, "official spec path changed")
    source_ids = [item["operationId"] for item in sources["operationIds"]]
    require(source_ids == VCENTER_OPERATION_IDS, "official source operationIds changed")
    require(
        contract["security"]
        == {
            "scheme": "api_key_auth",
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter security scheme no longer matches the specification",
    )
    return contract


def validate_prerequisite() -> None:
    command = (
        "$m = Get-Module -ListAvailable VMware.Sdk.Vcf.SddcManager "
        "| Sort-Object Version -Descending | Select-Object -First 1; "
        "if ($null -eq $m) { exit 3 }; $m.Version.ToString()"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(result.returncode == 0, "VMware.Sdk.Vcf.SddcManager prerequisite missing")
    require(result.stdout.strip().startswith("13.5."), "VCF PowerCLI 9.1 SDK required")


def start_mock(
    temp: Path, scenario: str
) -> tuple[subprocess.Popen[str], str, Path]:
    log_path = temp / "requests.jsonl"
    ready_path = temp / "ready.json"
    process = subprocess.Popen(
        [
            sys.executable,
            str(MOCK_PATH),
            "--contract",
            str(CONTRACT_PATH),
            "--log",
            str(log_path),
            "--ready",
            str(ready_path),
            "--scenario",
            scenario,
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


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def run_case(
    contract: dict[str, Any],
    config: dict[str, Any],
    scenario: str,
    expect_failure: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0129-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "case.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        process, base_uri, log_path = start_mock(temp, scenario)
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
                    str(MODULE_PATH),
                    "-BaseUri",
                    base_uri,
                    "-CaseConfig",
                    str(config_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
                env={**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1"},
            )
            records = read_log(log_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        combined_error = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if expect_failure:
            require(result.returncode != 0, "terminal failure case unexpectedly succeeded")
            require("Failed" in combined_error, "terminal phase missing from thrown error")
            return records, None, combined_error

        require(
            result.returncode == 0,
            f"PowerShell case failed:\n{result.stdout}\n{result.stderr}",
        )
        marker = next(
            (line for line in result.stdout.splitlines() if line.startswith("CASE_RESULT=")),
            None,
        )
        require(marker is not None, "PowerShell result marker missing")
        parsed = json.loads(marker.split("=", 1)[1])
        return records, parsed, combined_error


def route_path(
    contract: dict[str, Any], name: str, **values: str
) -> str:
    all_routes = contract["operations"] + contract["kubernetes_routes"]
    route = next(item for item in all_routes if item["name"] == name)
    path = route["path"]
    for key, value in values.items():
        path = path.replace("{" + key + "}", value)
    if name.startswith("namespace."):
        path = contract["server_base_path"] + path
    return path


def expected_namespace_body(config: dict[str, Any]) -> dict[str, Any]:
    storage: dict[str, Any] = {"policy": config["storage_policy"]}
    if "storage_limit_mib" in config:
        storage["limit"] = config["storage_limit_mib"]
    body: dict[str, Any] = {
        "supervisor": config["supervisor"],
        "namespace": config["namespace"],
        "storage_specs": [storage],
    }
    if "description" in config:
        body["description"] = config["description"]
    return body


def expected_cluster_body(config: dict[str, Any]) -> dict[str, Any]:
    topology: dict[str, Any] = {
        "class": config["cluster_class"],
        "version": config["kubernetes_version"],
        "controlPlane": {"replicas": config["control_plane_replicas"]},
        "workers": {
            "machineDeployments": [
                {
                    "class": config["worker_class"],
                    "name": config["worker_name"],
                    "replicas": config["worker_replicas"],
                }
            ]
        },
        "variables": [
            {"name": "vmClass", "value": config["vm_class"]},
            {"name": "storageClass", "value": config["storage_policy"]},
        ],
    }
    spec: dict[str, Any] = {"topology": topology}
    if "service_cidr" in config and "pod_cidr" in config:
        spec["clusterNetwork"] = {
            "services": {"cidrBlocks": [config["service_cidr"]]},
            "pods": {"cidrBlocks": [config["pod_cidr"]]},
            "serviceDomain": "cluster.local",
        }
    return {
        "apiVersion": "cluster.x-k8s.io/v1beta1",
        "kind": "Cluster",
        "metadata": {
            "name": config["cluster_name"],
            "namespace": config["namespace"],
        },
        "spec": spec,
    }


def assert_headers(record: dict[str, Any], plane: str, has_body: bool) -> None:
    headers = record["headers"]
    require(headers.get("accept") == "application/json", "Accept header is not exact")
    if has_body:
        require(
            headers.get("content-type", "").split(";", 1)[0] == "application/json",
            "JSON POST Content-Type missing",
        )
        require(record["body"] is not None, "POST body missing")
    else:
        require("content-type" not in headers, "bodyless GET sent Content-Type")
        require(record["body"] is None and record["body_bytes"] == 0, "GET sent a body")
    if plane == "vcenter":
        require(
            headers.get("vmware-api-session-id") == "vc-session-token",
            "vCenter session header missing",
        )
        require("authorization" not in headers, "Kubernetes token leaked to vCenter")
    else:
        require(
            headers.get("authorization") == "Bearer k8s-bearer-token",
            "Kubernetes bearer header missing",
        )
        require(
            "vmware-api-session-id" not in headers,
            "vCenter session leaked to Kubernetes",
        )


def assert_wire(
    contract: dict[str, Any],
    records: list[dict[str, Any]],
    config: dict[str, Any],
    cluster_get_count: int,
) -> None:
    namespace = config["namespace"]
    cluster = config["cluster_name"]
    expected = [
        (
            "POST",
            route_path(contract, "namespace.createV2"),
            "vcenter",
            expected_namespace_body(config),
        ),
        (
            "GET",
            route_path(contract, "namespace.getV2", namespace=namespace),
            "vcenter",
            None,
        ),
        (
            "GET",
            route_path(contract, "namespace.getV2", namespace=namespace),
            "vcenter",
            None,
        ),
        (
            "POST",
            route_path(
                contract, "kubernetes.cluster.create", namespace=namespace
            ),
            "kubernetes",
            expected_cluster_body(config),
        ),
    ]
    expected.extend(
        (
            "GET",
            route_path(
                contract,
                "kubernetes.cluster.get",
                namespace=namespace,
                cluster=cluster,
            ),
            "kubernetes",
            None,
        )
        for _ in range(cluster_get_count)
    )
    require(
        len(records) == len(expected),
        f"unexpected request count: got {len(records)}, expected {len(expected)}",
    )
    for index, (record, wanted) in enumerate(zip(records, expected), start=1):
        method, path, plane, body = wanted
        require(record["method"] == method, f"request {index}: wrong method")
        require(record["path"] == path, f"request {index}: wrong path")
        require(record["query"] == "", f"request {index}: unexpected query")
        require(record["operation"] is not None, f"request {index}: route not contracted")
        require(record["body"] == body, f"request {index}: JSON body differs")
        assert_headers(record, plane, body is not None)


def assert_result(result: dict[str, Any], config: dict[str, Any]) -> None:
    require(result["Namespace"] == config["namespace"], "wrong result namespace")
    require(result["NamespaceStatus"] == "RUNNING", "namespace not terminal")
    require(result["Cluster"] == config["cluster_name"], "wrong result cluster")
    require(result["ClusterPhase"] == "Provisioned", "wrong cluster phase")
    require(result["Ready"] is True, "result was emitted before Ready=True")
    require(result["NamespacePollCount"] == 2, "namespace was not polled")
    require(result["ClusterPollCount"] == 3, "cluster was not fully polled")


def base_config() -> dict[str, Any]:
    return {
        "supervisor": "supervisor-21",
        "namespace": "payments",
        "storage_policy": "gold-storage",
        "cluster_name": "payments-vks",
        "kubernetes_version": "v1.32.0+vmware.6-vkr.2",
        "cluster_class": "builtin-generic-v3.5.0",
        "vm_class": "best-effort-medium",
        "control_plane_replicas": 3,
        "worker_replicas": 2,
        "worker_class": "node-pool",
        "worker_name": "workers",
    }


def main() -> int:
    try:
        contract = validate_contract()
        validate_prerequisite()

        unset_config = base_config()
        records, result, _ = run_case(contract, unset_config, "ready", False)
        assert_wire(contract, records, unset_config, cluster_get_count=3)
        require(result is not None, "ready case produced no result")
        assert_result(result, unset_config)

        set_config = {
            **base_config(),
            "namespace": "analytics",
            "cluster_name": "analytics-vks",
            "description": "analytics workloads",
            "storage_limit_mib": 8192,
            "service_cidr": "10.96.0.0/12",
            "pod_cidr": "192.168.0.0/16",
        }
        records, result, _ = run_case(contract, set_config, "ready", False)
        assert_wire(contract, records, set_config, cluster_get_count=3)
        require(result is not None, "optional-field case produced no result")
        assert_result(result, set_config)

        failure_config = {
            **base_config(),
            "namespace": "failure-lab",
            "cluster_name": "failure-vks",
        }
        records, _, _ = run_case(
            contract, failure_config, "cluster_failed", True
        )
        assert_wire(contract, records, failure_config, cluster_get_count=1)
    except (VerificationError, KeyError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("PASS: contract, exact wire shape, omission semantics, and polling verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
