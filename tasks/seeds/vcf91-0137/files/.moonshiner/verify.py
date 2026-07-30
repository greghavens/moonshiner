#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0137."""

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
MANIFEST_PATH = ROOT / "VcfVksProvisioning" / "VcfVksProvisioning.psd1"
MODULE_PATH = ROOT / "VcfVksProvisioning" / "VcfVksProvisioning.psm1"
MOCK_PATH = ROOT / ".moonshiner" / "mock_server.py"
INVOKER_PATH = ROOT / ".moonshiner" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
VCENTER_IDS = ["Vcenter.Namespaces.User.Instances_list"]
KUBERNETES_KEYS = [
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:create",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
]
KUBERNETES_NAMES = [
    "kubernetes.clusters.list",
    "kubernetes.clusters.create",
    "kubernetes.cluster.get",
]
LIST_OPTIONALS = {
    "allowWatchBookmarks",
    "continue",
    "fieldSelector",
    "fieldValidation",
    "labelSelector",
    "limit",
    "pretty",
    "resourceVersion",
    "resourceVersionMatch",
    "sendInitialEvents",
    "timeoutSeconds",
    "watch",
}
CREATE_OPTIONALS = {
    "dryRun",
    "fieldManager",
    "fieldValidation",
    "pretty",
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
    collection_path = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        "{namespace}/clusters"
    )
    require(
        [(item["method"], item["path"]) for item in kop]
        == [
            ("GET", collection_path),
            ("POST", collection_path),
            ("GET", collection_path + "/{cluster}"),
        ],
        "Kubernetes route projection changed",
    )
    require(
        set(kop[0]["optionalQueryFields"]) == LIST_OPTIONALS,
        "Cluster list optionals changed",
    )
    require(
        kop[0]["clientOutputOrdering"]
        == {
            "key": "metadata.name",
            "comparison": "ordinal",
            "direction": "ascending",
        },
        "Cluster collection ordering requirement changed",
    )
    require(
        set(kop[1]["optionalQueryFields"]) == CREATE_OPTIONALS,
        "Cluster create optionals changed",
    )
    require(
        kop[1]["successStatus"] == 201
        and kop[1]["contentType"] == "application/json"
        and kop[1]["requestBody"] is True,
        "Cluster create wire contract changed",
    )
    require(
        kop[1]["body"]["requiredOrder"]
        == ["apiVersion", "kind", "metadata", "spec"]
        and kop[1]["body"]["metadataRequiredOrder"]
        == ["name", "namespace"]
        and kop[1]["body"]["topologyRequiredOrder"]
        == ["classRef", "version", "controlPlane", "workers"],
        "Cluster create body projection changed",
    )
    require(
        kop[2]["optionalQueryFields"] == ["pretty"],
        "Cluster GET optionals changed",
    )
    async_rule = kubernetes["asynchronousProvisioning"]
    require(
        async_rule["startOperation"] == KUBERNETES_NAMES[1]
        and async_rule["pollOperation"] == KUBERNETES_NAMES[2]
        and async_rule["phaseField"] == "status.phase"
        and async_rule["nonTerminal"] == ["Pending", "Provisioning"]
        and async_rule["terminalSuccess"] == ["Provisioned"]
        and async_rule["terminalFailure"] == ["Failed"],
        "asynchronous terminal-state contract changed",
    )
    require(
        "201 create response is acceptance, not completion"
        in async_rule["rule"],
        "create acceptance must not be treated as completion",
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
        sources["operations"][0]["specLine"] == 66261,
        "operationId source line changed",
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
        + "'New-VcfVksClusterAndWait')) { exit 5 }"
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
    require(
        "start-sleep" in folded,
        "module must pace non-terminal polling attempts",
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


def expected_manifest(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": config["cluster_name"],
            "namespace": config["namespace"],
        },
        "spec": {
            "topology": {
                "classRef": {
                    "name": config["cluster_class"],
                },
                "version": config["kubernetes_version"],
                "controlPlane": {
                    "replicas": 1,
                },
                "workers": {
                    "machineDeployments": [
                        {
                            "class": "node-pool",
                            "name": "primary",
                            "replicas": 1,
                        }
                    ]
                },
            }
        },
    }


def verify_report(report: dict[str, Any], config: dict[str, Any]) -> None:
    require(
        list(report)
        == [
            "Status",
            "SupervisorNamespace",
            "Cluster",
            "Phase",
            "PollCount",
            "ObservedPhases",
            "ClustersBefore",
            "ClustersAfter",
        ],
        "result property order or shape changed",
    )
    require(report["Status"] == "Succeeded", "status must report success")
    require(
        report["SupervisorNamespace"] == config["namespace"],
        "namespace result mismatch",
    )
    require(
        report["Cluster"] == config["cluster_name"],
        "cluster result mismatch",
    )
    require(report["Phase"] == "Provisioned", "terminal phase mismatch")
    require(report["PollCount"] == 3, "create was not polled to terminal state")
    require(
        report["ObservedPhases"] == ["Pending", "Provisioning", "Provisioned"],
        "non-terminal phases were skipped or terminal polling changed",
    )
    expected_before = sorted(config["existing_cluster_names"])
    expected_after = sorted(
        [*config["existing_cluster_names"], config["cluster_name"]]
    )
    require(
        report["ClustersBefore"] == expected_before,
        "initial collection output was not sorted ordinally",
    )
    require(
        report["ClustersAfter"] == expected_after,
        "final collection output was not sorted ordinally",
    )
    require(
        list(reversed(expected_before)) != expected_before,
        "fixture must expose an unsorted first server response",
    )
    rendered = json.dumps(report, separators=(",", ":"))
    require(
        config["kubernetes_bearer_token"] not in rendered
        and config["vcenter_session_id"] not in rendered,
        "credentials leaked into result",
    )


def verify_requests(
    records: list[dict[str, Any]], config: dict[str, Any]
) -> None:
    require(len(records) == 7, "expected exactly seven requests")
    require(
        [item["operation"] for item in records]
        == [
            "namespace.listAuthorized",
            "kubernetes.clusters.list",
            "kubernetes.clusters.create",
            "kubernetes.cluster.get",
            "kubernetes.cluster.get",
            "kubernetes.cluster.get",
            "kubernetes.clusters.list",
        ],
        "request operation order changed",
    )
    require(
        [item["method"] for item in records]
        == ["GET", "GET", "POST", "GET", "GET", "GET", "GET"],
        "request methods changed",
    )

    namespace = quote(config["namespace"], safe="")
    cluster = quote(config["cluster_name"], safe="")
    collection = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"{namespace}/clusters"
    )
    expected_targets = [
        "/api/vcenter/namespaces-user/namespaces",
        collection,
        collection,
        f"{collection}/{cluster}",
        f"{collection}/{cluster}",
        f"{collection}/{cluster}",
        collection,
    ]
    require(
        [item["raw_target"] for item in records] == expected_targets,
        "raw request targets or path escaping changed",
    )
    for record in records:
        require(record["query"] == "", "all optional query fields must be absent")
        require(
            not record["raw_target"].endswith("?"),
            "request ended in a bare query delimiter",
        )

    for position, record in enumerate(records):
        if position == 2:
            continue
        require(record["body_bytes"] == 0, "bodyless request carried a body")
        require(record["body"] is None, "bodyless request decoded a body")
        require(
            "content-type" not in record["headers"],
            "bodyless request sent Content-Type",
        )

    expected_body = expected_manifest(config)
    expected_raw = json.dumps(expected_body, separators=(",", ":"))
    create = records[2]
    require(create["body"] == expected_body, "Cluster manifest shape changed")
    require(
        create["body_raw"] == expected_raw,
        "Cluster manifest key order, compactness, or escaping changed",
    )
    require(
        create["body_bytes"] == len(expected_raw.encode("utf-8")),
        "Cluster manifest byte count changed",
    )
    require(
        one_header(create, "content-type") == "application/json",
        "Cluster create Content-Type mismatch",
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
        require(
            one_header(record, "accept") == "application/json",
            f"{record['operation']} Accept header mismatch",
        )

    forbidden_fragments = LIST_OPTIONALS | CREATE_OPTIONALS | {"pretty"}
    for record in records:
        for optional in forbidden_fragments:
            require(
                optional not in record["query"],
                f"unset optional query field was sent: {optional}",
            )


def invoke_case(
    port: int,
    config_path: Path,
    output_path: Path,
    config: dict[str, Any],
) -> None:
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
        "PowerShell invocation failed without a valid provisioning result: "
        + result.stderr[-800:],
    )
    combined = result.stdout + result.stderr
    for sensitive in (
        config["kubernetes_bearer_token"],
        config["vcenter_session_id"],
        "OUTSIDE_FOCUSED_CONTRACT",
    ):
        require(sensitive not in combined, "sensitive response content leaked")
    require(output_path.exists(), "PowerShell did not write a result")


def main() -> int:
    try:
        verify_contract()
        verify_prerequisites_and_shape()
        with tempfile.TemporaryDirectory(prefix="vcf91-0137-") as temp_name:
            temp = Path(temp_name)
            suffix = secrets.token_hex(5)
            existing_names = [
                "accounts-" + suffix,
                "warehouse-" + suffix,
                "payments-" + suffix,
            ]
            cluster_name = "orders-" + suffix
            all_names = [*existing_names, cluster_name]
            config: dict[str, Any] = {
                "suffix": suffix,
                "namespace": "team-" + suffix,
                "cluster_name": cluster_name,
                "cluster_class": "builtin-generic-" + suffix,
                "kubernetes_version": "v1.33." + str(
                    secrets.randbelow(8) + 1
                ),
                "existing_cluster_names": existing_names,
                "cluster_uids": {
                    name: str(uuid.uuid4())
                    for name in all_names
                },
                "resource_version": str(
                    secrets.randbelow(8_000_000) + 1_000_000
                ),
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
        print("verified vcf91-0137")
        return 0
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
