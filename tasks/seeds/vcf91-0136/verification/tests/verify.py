#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0136."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfVksDiagnostics" / "VcfVksDiagnostics.psd1"
MODULE_PATH = ROOT / "VcfVksDiagnostics" / "VcfVksDiagnostics.psm1"
MOCK_PATH = ROOT / ".moonshiner" / "mock_server.py"
INVOKER_PATH = ROOT / ".moonshiner" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
VCENTER_IDS = ["Vcenter.Namespaces.User.Instances_list"]
KUBERNETES_KEYS = [
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
    "core/v1:namespaced-events:list",
    "core/v1:namespaced-pods:list",
    "core/v1:namespaced-pod-log:get",
]
KUBERNETES_NAMES = [
    "kubernetes.cluster.get",
    "kubernetes.events.list",
    "kubernetes.pods.list",
    "kubernetes.podLog.get",
]
EVENT_OPTIONALS = {
    "allowWatchBookmarks",
    "continue",
    "labelSelector",
    "limit",
    "pretty",
    "resourceVersion",
    "resourceVersionMatch",
    "sendInitialEvents",
    "timeoutSeconds",
    "watch",
}
POD_OPTIONALS = {
    "allowWatchBookmarks",
    "continue",
    "fieldSelector",
    "limit",
    "pretty",
    "resourceVersion",
    "resourceVersionMatch",
    "sendInitialEvents",
    "timeoutSeconds",
    "watch",
}
LOG_OPTIONALS = {
    "follow",
    "insecureSkipTLSVerifyBackend",
    "limitBytes",
    "pretty",
    "previous",
    "sinceSeconds",
    "sinceTime",
    "tailLines",
    "timestamps",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract spec blob changed")
    require(source["license"] == "Apache-2.0", "source license changed")
    require(source["openapi"] == "3.0.3", "OpenAPI version changed")
    require(source["apiVersion"] == "9.1.0.0", "vSphere API version changed")
    require(source["basePath"] == "/api", "vCenter base path changed")
    require(
        contract["securitySchemes"]["api_key_auth"]
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter security projection changed",
    )

    operations = contract["operations"]
    require(
        [item["operationId"] for item in operations] == VCENTER_IDS,
        "focused vCenter operationId changed",
    )
    operation = operations[0]
    require(
        (operation["method"], operation["path"])
        == ("GET", "/api/vcenter/namespaces-user/namespaces"),
        "focused vCenter route changed",
    )
    require(
        operation["generatedBinding"]
        == {
            "type": (
                "VMware.Bindings.vSphere.Api."
                "IVcenterNamespacesUserInstancesApi"
            ),
            "method": "VcenterNamespacesUserInstancesList",
        },
        "generated binding projection changed",
    )
    require(
        [
            (
                item["name"],
                item["in"],
                item["required"],
                item["unsetBehavior"],
            )
            for item in operation["parameters"]
        ]
        == [
            ("filter", "query", False, "omit"),
            ("groups", "query", False, "omit"),
        ],
        "vCenter optional parameter projection changed",
    )
    filter_schema = contract["schemas"][
        "Vcenter.Namespaces.User.Instances.FilterSpec"
    ]
    require(
        filter_schema["required"] == []
        and filter_schema["properties"]["username"]["required"] is False
        and filter_schema["properties"]["username"]["unsetBehavior"] == "omit",
        "nested vCenter filter omission changed",
    )
    summary = contract["schemas"][
        "Vcenter.Namespaces.User.Instances.Summary"
    ]
    require(
        set(summary["required"]) == {"master_host", "namespace"},
        "namespace summary projection changed",
    )

    kubernetes = contract["kubernetesApi"]
    kop = kubernetes["operations"]
    require(
        [item["operationKey"] for item in kop] == KUBERNETES_KEYS,
        "Kubernetes operation keys changed",
    )
    require(
        [item["name"] for item in kop] == KUBERNETES_NAMES,
        "Kubernetes operation names changed",
    )
    require(
        [(item["method"], item["path"]) for item in kop]
        == [
            (
                "GET",
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                "{namespace}/clusters/{cluster}",
            ),
            ("GET", "/api/v1/namespaces/{namespace}/events"),
            ("GET", "/api/v1/namespaces/{controllerNamespace}/pods"),
            (
                "GET",
                "/api/v1/namespaces/{controllerNamespace}/pods/{pod}/log",
            ),
        ],
        "Kubernetes route projection changed",
    )
    require(
        kop[0]["optionalQueryFields"] == ["pretty"],
        "Cluster GET optionals changed",
    )
    require(
        set(kop[1]["optionalQueryFields"]) == EVENT_OPTIONALS,
        "Event list optionals changed",
    )
    require(
        kop[1]["requiredQueryFields"]
        == [
            {
                "name": "fieldSelector",
                "valueTemplate": (
                    "involvedObject.uid={uid},"
                    "involvedObject.kind=Cluster,"
                    "involvedObject.name={cluster},"
                    "involvedObject.namespace={namespace}"
                ),
                "position": 1,
            }
        ],
        "Event selector contract changed",
    )
    require(
        set(kop[2]["optionalQueryFields"]) == POD_OPTIONALS,
        "Pod list optionals changed",
    )
    require(
        kop[2]["requiredQueryFields"]
        == [
            {
                "name": "labelSelector",
                "value": "app.kubernetes.io/name=vks-cluster-controller",
                "position": 1,
            }
        ],
        "Pod selector contract changed",
    )
    require(
        set(kop[3]["optionalQueryFields"]) == LOG_OPTIONALS,
        "Pod log optionals changed",
    )
    require(
        kop[3]["requiredQueryFields"]
        == [{"name": "container", "value": "manager", "position": 1}],
        "Pod log required query changed",
    )
    require(
        kop[3]["responseContentType"] == "text/plain"
        and kop[3]["responseFormat"] == "newline-delimited JSON",
        "Pod log response contract changed",
    )
    require(
        "not represented as VMware operationIds"
        in kubernetes["provenanceNote"],
        "Kubernetes operations must not be fictional VMware operationIds",
    )

    require(
        sources["repositoryCommitSha"] == COMMIT,
        "official source commit changed",
    )
    require(sources["specPath"] == SPEC_PATH, "official spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "official spec blob changed")
    require(sources["license"] == "Apache-2.0", "official license changed")
    require(sources["operationIds"] == VCENTER_IDS, "source operationIds changed")
    require(
        COMMIT in sources["specUrl"]
        and sources["specUrl"].endswith(SPEC_PATH),
        "official specification URL is not immutable",
    )
    require(
        [
            {
                "operationId": item["operationId"],
                "method": item["method"],
                "path": item["path"],
                "repositoryCommitSha": item["repositoryCommitSha"],
                "specPath": item["specPath"],
            }
            for item in sources["operations"]
        ]
        == [
            {
                "operationId": VCENTER_IDS[0],
                "method": "GET",
                "path": "/vcenter/namespaces-user/namespaces",
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            }
        ],
        "each official operation must carry its commit and spec path",
    )
    require(
        sources["kubernetesIntegration"]["operations"] == KUBERNETES_KEYS,
        "Kubernetes source boundary changed",
    )
    return contract


def verify_prerequisites_and_shape() -> None:
    command = (
        "$m = Get-Module -ListAvailable VMware.Sdk.Vcf.SddcManager "
        "| Where-Object Version -EQ ([version]'13.5.0.25380678') "
        "| Select-Object -First 1; if ($null -eq $m) { exit 4 }; "
        "$manifest = Test-ModuleManifest -Path '"
        + str(MANIFEST_PATH).replace("'", "''")
        + "'; if ($manifest.ExportedFunctions.Keys.Count -ne 1 -or "
        + "-not $manifest.ExportedFunctions.ContainsKey("
        + "'Get-VcfVksFailureDiagnosis')) { exit 5 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
        env={
            **os.environ,
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        },
    )
    require(
        result.returncode == 0,
        "VCF PowerCLI 9.1 prerequisite or protected manifest is invalid",
    )

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    require(
        "vmware.bindings.vsphere.api."
        "ivcenternamespacesuserinstancesapi" in folded,
        "module must accept the genuine namespace binding",
    )
    require(
        "vcenternamespacesuserinstanceslist" in folded,
        "module must invoke the specification-derived generated method",
    )
    require(
        "system.net.http.httpclient" in folded
        or "net.http.httpclient" in folded,
        "module must use HttpClient for Kubernetes",
    )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.diagnostics.process",
        "tcpclient",
        "webclient",
        "curl",
    ):
        require(
            forbidden not in folded,
            f"forbidden implementation mechanism found: {forbidden}",
        )


def start_mock(
    temp: Path, config_path: Path
) -> tuple[subprocess.Popen[str], int, Path]:
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
            ready = load_json(ready_path)
            require(ready["host"] == "127.0.0.1", "mock must bind loopback")
            return process, int(ready["port"]), log_path
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(f"mock exited early: {stdout}\n{stderr}")
        time.sleep(0.02)
    process.terminate()
    raise VerificationError("mock did not become ready")


def one_header(record: dict[str, Any], name: str) -> str:
    values = record["headers"].get(name, [])
    require(
        len(values) == 1,
        f"{record['operation']} must send exactly one {name} header",
    )
    return values[0]


def verify_report(report: dict[str, Any], config: dict[str, str]) -> None:
    require(
        list(report)
        == [
            "Status",
            "SupervisorNamespace",
            "Cluster",
            "Phase",
            "EventReason",
            "ControllerPod",
            "CorrelationId",
            "Diagnosis",
            "MissingStorageClass",
        ],
        "result property order or shape changed",
    )
    require(report["Status"] == "Failed", "status must report failure")
    require(
        report["SupervisorNamespace"] == config["namespace"],
        "namespace result mismatch",
    )
    require(
        report["Cluster"] == config["cluster_name"],
        "cluster result mismatch",
    )
    require(report["Phase"] == "Provisioning", "phase result mismatch")
    require(
        report["EventReason"] == "ReconcileError",
        "event reason result mismatch",
    )
    require(
        report["ControllerPod"] == config["controller_pod"],
        "controller pod was not discovered",
    )
    require(
        report["CorrelationId"] == config["correlation_id"],
        "Event and log correlation was not preserved",
    )
    require(
        report["Diagnosis"] == "StorageClassNotFound",
        "correlated log cause mismatch",
    )
    require(
        report["MissingStorageClass"] == config["missing_storage_class"],
        "storage class was not extracted from the correlated log",
    )
    rendered = json.dumps(report, separators=(",", ":"))
    require(
        config["kubernetes_bearer_token"] not in rendered
        and config["vcenter_session_id"] not in rendered,
        "credentials leaked into result",
    )


def verify_requests(
    records: list[dict[str, Any]], config: dict[str, str]
) -> None:
    require(len(records) == 5, "expected exactly five requests")
    require(
        [item["operation"] for item in records]
        == ["namespace.listAuthorized", *KUBERNETES_NAMES],
        "request operation order changed",
    )
    require(
        [item["method"] for item in records] == ["GET"] * 5,
        "all diagnostic operations must be GET",
    )

    namespace = quote(config["namespace"], safe="")
    cluster = quote(config["cluster_name"], safe="")
    controller_ns = quote(config["controller_namespace"], safe="")
    pod = quote(config["controller_pod"], safe="")
    selector = (
        f"involvedObject.uid={config['cluster_uid']},"
        "involvedObject.kind=Cluster,"
        f"involvedObject.name={config['cluster_name']},"
        f"involvedObject.namespace={config['namespace']}"
    )
    expected_targets = [
        "/api/vcenter/namespaces-user/namespaces",
        (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            f"{namespace}/clusters/{cluster}"
        ),
        (
            f"/api/v1/namespaces/{namespace}/events?fieldSelector="
            + quote(selector, safe="")
        ),
        (
            f"/api/v1/namespaces/{controller_ns}/pods?labelSelector="
            + quote(
                "app.kubernetes.io/name=vks-cluster-controller",
                safe="",
            )
        ),
        (
            f"/api/v1/namespaces/{controller_ns}/pods/{pod}/log"
            "?container=manager"
        ),
    ]
    require(
        [item["raw_target"] for item in records] == expected_targets,
        "raw request targets, query order, or escaping changed",
    )
    require(
        records[0]["query"] == ""
        and records[1]["query"] == ""
        and records[2]["query"].startswith("fieldSelector=")
        and records[3]["query"].startswith("labelSelector=")
        and records[4]["query"] == "container=manager",
        "required query omission or ordering changed",
    )

    for record in records:
        require(record["body_bytes"] == 0, "GET request carried a body")
        require(record["body"] is None, "GET body decoded unexpectedly")
        require(
            "content-type" not in record["headers"],
            "bodyless request sent Content-Type",
        )
        require(
            "content-length" not in record["headers"],
            "bodyless request sent Content-Length",
        )
        require(
            "transfer-encoding" not in record["headers"],
            "bodyless request sent Transfer-Encoding",
        )

    require(
        one_header(records[0], "vmware-api-session-id")
        == config["vcenter_session_id"],
        "vCenter session header mismatch",
    )
    require(
        "authorization" not in records[0]["headers"],
        "Kubernetes bearer token leaked to vCenter",
    )
    require(
        one_header(records[0], "accept") == "application/json",
        "vCenter Accept header mismatch",
    )

    for record in records[1:]:
        require(
            one_header(record, "authorization")
            == "Bearer " + config["kubernetes_bearer_token"],
            f"{record['operation']} bearer header mismatch",
        )
        require(
            "vmware-api-session-id" not in record["headers"],
            f"vCenter session leaked to {record['operation']}",
        )
    for record in records[1:4]:
        require(
            one_header(record, "accept") == "application/json",
            f"{record['operation']} Accept header mismatch",
        )
    require(
        one_header(records[4], "accept") == "text/plain",
        "pod log Accept header mismatch",
    )

    forbidden_fragments = {
        "pretty",
        "allowWatchBookmarks",
        "continue",
        "limit",
        "resourceVersion",
        "resourceVersionMatch",
        "sendInitialEvents",
        "timeoutSeconds",
        "watch",
        "follow",
        "insecureSkipTLSVerifyBackend",
        "limitBytes",
        "previous",
        "sinceSeconds",
        "sinceTime",
        "tailLines",
        "timestamps",
    }
    for record in records:
        for optional in forbidden_fragments:
            require(
                optional not in record["query"],
                f"unset optional query field was sent: {optional}",
            )
        require(
            not record["raw_target"].endswith("?"),
            "request ended in a bare query delimiter",
        )


def invoke_case(
    port: int,
    config_path: Path,
    output_path: Path,
    config: dict[str, str],
) -> tuple[str, str]:
    result = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(INVOKER_PATH),
            "-Port",
            str(port),
            "-ConfigPath",
            str(config_path),
            "-OutputPath",
            str(output_path),
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
    require(
        result.returncode == 0,
        "PowerShell invocation failed without a valid diagnostic result",
    )
    combined = result.stdout + result.stderr
    for sensitive in (
        config["kubernetes_bearer_token"],
        config["vcenter_session_id"],
        config["decoy_correlation_id"],
        "NetworkUnavailable",
        "wrong-" + config["suffix"],
    ):
        require(sensitive not in combined, "sensitive or raw log content leaked")
    require(output_path.exists(), "PowerShell did not write a result")
    return result.stdout, result.stderr


def main() -> int:
    try:
        verify_contract()
        verify_prerequisites_and_shape()
        with tempfile.TemporaryDirectory(prefix="vcf91-0136-") as temp_name:
            temp = Path(temp_name)
            suffix = secrets.token_hex(6)
            config = {
                "suffix": suffix,
                "namespace": "team-" + suffix,
                "cluster_name": "orders-" + suffix,
                "controller_namespace": "vks-system-" + suffix,
                "controller_pod": "vks-cluster-controller-" + suffix,
                "cluster_uid": str(uuid.uuid4()),
                "resource_version": str(secrets.randbelow(8_000_000) + 1_000_000),
                "correlation_id": "corr-" + secrets.token_hex(12),
                "decoy_correlation_id": "corr-" + secrets.token_hex(12),
                "missing_storage_class": "gold-" + secrets.token_hex(8),
                "vcenter_session_id": "vc-" + secrets.token_urlsafe(24),
                "kubernetes_bearer_token": "k8s-" + secrets.token_urlsafe(28),
            }
            config_path = temp / "config.json"
            output_path = temp / "result.json"
            config_path.write_text(
                json.dumps(config, separators=(",", ":")),
                encoding="utf-8",
            )
            process, port, log_path = start_mock(temp, config_path)
            try:
                invoke_case(port, config_path, output_path, config)
                time.sleep(0.05)
                report = load_json(output_path)
                records = [
                    json.loads(line)
                    for line in log_path.read_text(encoding="utf-8").splitlines()
                    if line
                ]
                verify_report(report, config)
                verify_requests(records, config)
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=3)
        print("verified vcf91-0136")
        return 0
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
