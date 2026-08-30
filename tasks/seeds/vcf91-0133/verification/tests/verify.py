#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0133."""

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
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = (
    ROOT / "VcfVksCoordinatedChange" / "VcfVksCoordinatedChange.psd1"
)
MODULE_PATH = (
    ROOT / "VcfVksCoordinatedChange" / "VcfVksCoordinatedChange.psm1"
)
MOCK_PATH = ROOT / ".moonshiner" / "mock_server.py"
INVOKER_PATH = ROOT / ".moonshiner" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
VCENTER_IDS = [
    "Vcenter.Namespaces.Instances_getV2",
    "Vcenter.Namespaces.Instances_update",
]
KUBERNETES_KEYS = [
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch",
]
UPDATE_OPTIONALS = {
    "description",
    "resource_spec",
    "access_list",
    "storage_specs",
    "vm_service_spec",
    "content_libraries",
    "network_spec",
    "zones",
    "edges",
    "infrastructure_policies",
}
PATCH_QUERY_OPTIONALS = {
    "dryRun",
    "fieldManager",
    "fieldValidation",
    "force",
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
        "focused vCenter operationIds changed",
    )
    require(
        [(item["method"], item["path"]) for item in operations]
        == [
            (
                "GET",
                "/api/vcenter/namespaces/instances/v2/{namespace}",
            ),
            (
                "PATCH",
                "/api/vcenter/namespaces/instances/{namespace}",
            ),
        ],
        "focused vCenter routes changed",
    )
    require(
        [item["generatedBinding"]["method"] for item in operations]
        == [
            "VcenterNamespacesInstancesGetV2",
            "VcenterNamespacesInstancesUpdate",
        ],
        "generated binding projection changed",
    )
    info_schema = contract["schemas"][
        "Vcenter.Namespaces.Instances.InfoV2"
    ]
    require(
        info_schema["required"]
        == [
            "access_list",
            "config_status",
            "description",
            "messages",
            "stats",
            "storage_specs",
            "supervisor",
        ],
        "InfoV2 required-property projection changed",
    )
    require(
        set(info_schema["properties"]) == set(info_schema["required"]),
        "focused InfoV2 contract must describe every required property",
    )
    update_schema = contract["schemas"][
        "Vcenter.Namespaces.Instances.UpdateSpec"
    ]
    require(update_schema["partialUpdate"] is True, "update must be partial")
    require(
        set(update_schema["properties"]) == UPDATE_OPTIONALS,
        "UpdateSpec property projection changed",
    )
    require(
        all(
            item["required"] is False
            and item["unsetBehavior"] == "omit-and-leave-unchanged"
            for item in update_schema["properties"].values()
        ),
        "UpdateSpec unset semantics changed",
    )
    require(
        update_schema["properties"]["edges"]["addedIn"] == "9.1.0.0"
        and update_schema["properties"]["infrastructure_policies"]["addedIn"]
        == "9.1.0.0",
        "VCF 9.1 namespace fields changed",
    )

    kubernetes = contract["kubernetesApi"]
    require(
        [item["operationKey"] for item in kubernetes["operations"]]
        == KUBERNETES_KEYS,
        "Kubernetes operation keys changed",
    )
    require(
        [
            (item["method"], item["path"])
            for item in kubernetes["operations"]
        ]
        == [
            (
                "GET",
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                "{namespace}/clusters/{cluster}",
            ),
            (
                "PATCH",
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                "{namespace}/clusters/{cluster}",
            ),
        ],
        "Kubernetes focused routes changed",
    )
    patch = kubernetes["operations"][1]
    require(
        set(patch["optionalQueryFields"]) == PATCH_QUERY_OPTIONALS,
        "Kubernetes PATCH optionals changed",
    )
    require(
        patch["requestBody"]["contentType"] == "application/merge-patch+json",
        "Kubernetes patch media type changed",
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
                "path": "/vcenter/namespaces/instances/v2/{namespace}",
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
            {
                "operationId": VCENTER_IDS[1],
                "method": "PATCH",
                "path": "/vcenter/namespaces/instances/{namespace}",
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
        ],
        "each official operation must carry its commit and spec path",
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
        + "'Invoke-VcfVksCoordinatedChange')) { exit 5 }"
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
        "vmware.bindings.vsphere.api.ivcenternamespacesinstancesapi"
        in folded,
        "module must accept the genuine namespace binding",
    )
    require(
        "vcenternamespacesinstancesgetv2" in folded
        and "vcenternamespacesinstancesupdate" in folded,
        "module must invoke both specification-derived generated methods",
    )
    require(
        "vmware.bindings.vsphere.model."
        "vcenternamespacesinstancesupdatespec" in folded,
        "module must construct the genuine generated UpdateSpec",
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


def no_empty_json(value: Any, path: str = "$") -> None:
    if value is None:
        raise VerificationError(f"null serialized at {path}")
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


def verify_report(report: dict[str, Any], config: dict[str, str]) -> None:
    require(
        list(report)
        == ["OverallStatus", "NamespaceChange", "ClusterChange"],
        "result must contain exactly the three ordered report properties",
    )
    require(report["OverallStatus"] == "Failed", "overall result must fail")

    namespace_change = report["NamespaceChange"]
    require(
        list(namespace_change)
        == ["OperationId", "Namespace", "Status", "Changed"],
        "namespace ledger shape changed",
    )
    require(
        namespace_change
        == {
            "OperationId": "Vcenter.Namespaces.Instances_update",
            "Namespace": config["namespace"],
            "Status": "Succeeded",
            "Changed": True,
        },
        "committed namespace change was not reported accurately",
    )

    cluster_change = report["ClusterChange"]
    require(
        list(cluster_change)
        == [
            "OperationKey",
            "Namespace",
            "Name",
            "Status",
            "Changed",
            "HttpStatus",
        ],
        "cluster ledger shape changed",
    )
    require(
        cluster_change
        == {
            "OperationKey": (
                "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch"
            ),
            "Namespace": config["namespace"],
            "Name": config["cluster_name"],
            "Status": "Failed",
            "Changed": False,
            "HttpStatus": 422,
        },
        "rejected VKS change was not reported accurately",
    )


def verify_requests(
    records: list[dict[str, Any]], config: dict[str, str]
) -> None:
    encoded_namespace = quote(config["namespace"], safe="")
    encoded_cluster = quote(config["cluster_name"], safe="")
    cluster_path = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        + encoded_namespace
        + "/clusters/"
        + encoded_cluster
    )
    expected = [
        (
            "namespace.getV2",
            "GET",
            "/api/vcenter/namespaces/instances/v2/" + encoded_namespace,
        ),
        ("kubernetes.cluster.get", "GET", cluster_path),
        (
            "namespace.update",
            "PATCH",
            "/api/vcenter/namespaces/instances/" + encoded_namespace,
        ),
        ("kubernetes.cluster.patch", "PATCH", cluster_path),
    ]
    require(len(records) == 4, "unexpected request count")

    for record, (operation, method, raw_target) in zip(
        records, expected, strict=True
    ):
        require(record["operation"] == operation, "operation order changed")
        require(record["method"] == method, f"wrong method for {operation}")
        require(
            record["raw_target"] == raw_target,
            f"wrong raw target for {operation}",
        )
        require(record["path"] == raw_target, f"wrong path for {operation}")
        require(record["query"] == "", f"query must be absent for {operation}")
        require(
            one_header(record, "accept") == "application/json",
            f"wrong Accept for {operation}",
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
            require(record["body"] is None, f"GET JSON sent for {operation}")
            require(
                "content-type" not in record["headers"],
                f"body media type sent for bodyless {operation}",
            )
            require(
                "content-length" not in record["headers"],
                f"Content-Length sent for bodyless {operation}",
            )
        else:
            require(record["body_bytes"] > 0, f"PATCH body missing for {operation}")
            require(
                one_header(record, "content-length")
                == str(record["body_bytes"]),
                f"wrong Content-Length for {operation}",
            )
            no_empty_json(record["body"])

    namespace_body = {"description": config["new_description"]}
    require(
        records[2]["body"] == namespace_body,
        "UpdateSpec must serialize only description",
    )
    require(
        records[2]["body_raw"]
        == json.dumps(namespace_body, separators=(",", ":")),
        "generated UpdateSpec JSON bytes changed",
    )
    require(
        one_header(records[2], "content-type")
        == "application/json; charset=utf-8",
        "generated vCenter PATCH Content-Type changed",
    )

    cluster_body = {
        "spec": {"topology": {"version": config["target_version"]}}
    }
    require(
        records[3]["body"] == cluster_body,
        "VKS merge patch has the wrong exact shape",
    )
    require(
        records[3]["body_raw"]
        == json.dumps(cluster_body, separators=(",", ":")),
        "VKS merge patch JSON bytes changed",
    )
    require(
        one_header(records[3], "content-type")
        == "application/merge-patch+json",
        "VKS PATCH must use the merge-patch media type",
    )

    require(
        [record["operation"] for record in records]
        == [item[0] for item in expected],
        "an operation was duplicated, skipped, or reordered",
    )


def main() -> int:
    verify_contract()
    verify_prerequisites_and_shape()

    suffix = secrets.token_hex(6)
    config = {
        "vcenter_session_id": "vc-" + secrets.token_urlsafe(18),
        "kubernetes_bearer_token": "k8s-" + secrets.token_urlsafe(20),
        "supervisor": "supervisor-" + suffix,
        "namespace": "team-" + suffix,
        "cluster_name": "orders-" + suffix,
        "cluster_class": "builtin-generic-v3",
        "old_description": "before-" + suffix,
        "new_description": "maintenance-" + suffix,
        "old_version": "v1.32.4+vmware.1-fips-vkr.1",
        "target_version": "v1.33.1+vmware.1-fips-vkr.2",
        "failure_marker": "sensitive-rejection-" + secrets.token_urlsafe(16),
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0133-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        output_path = temp / "result.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        mock, port, log_path = start_mock(temp, config_path)
        try:
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
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

        combined = result.stdout + "\n" + result.stderr
        for secret in (
            config["vcenter_session_id"],
            config["kubernetes_bearer_token"],
            config["failure_marker"],
        ):
            require(secret not in combined, "sensitive value leaked to process output")
        require(
            result.returncode == 0,
            "PowerShell exercise failed without a safe partial-success report",
        )
        require(output_path.exists(), "result file was not produced")
        report = load_json(output_path)
        verify_report(report, config)

        for secret in (
            config["vcenter_session_id"],
            config["kubernetes_bearer_token"],
            config["failure_marker"],
        ):
            require(
                secret not in output_path.read_text(encoding="utf-8"),
                "sensitive value leaked to the change report",
            )

        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        verify_requests(records, config)

    print(
        "verified: generated vCenter binding wire shape, VKS optional omission, "
        "and accurate committed-first/rejected-later reporting"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
