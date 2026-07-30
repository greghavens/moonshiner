#!/usr/bin/env python3
"""Deterministic protected verifier for the VKS cluster inventory task."""

from __future__ import annotations

import json
import os
import secrets
import shutil
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
MOCK_PATH = ROOT / ".protected" / "mock_api.py"
EXERCISE_PATH = ROOT / ".protected" / "exercise.ps1"
MODULE_ROOT = ROOT / "VcfVksClusterInventory"
MODULE_PATH = MODULE_ROOT / "VcfVksClusterInventory.psm1"
MANIFEST_PATH = MODULE_ROOT / "VcfVksClusterInventory.psd1"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
DISCOVERY_OPERATION = "Vcenter.Namespaces.User.Instances_list"
DISCOVERY_PATH = "/api/vcenter/namespaces-user/namespaces"
CLUSTER_OPERATION = "VKS.ClusterApi.Clusters.listNamespaced"
CLUSTER_PATH = (
    "/apis/cluster.x-k8s.io/v1beta1/namespaces/{namespace}/clusters"
)
UNSET_KUBERNETES_FIELDS = {
    "fieldSelector",
    "labelSelector",
    "resourceVersion",
    "resourceVersionMatch",
    "timeoutSeconds",
    "watch",
    "allowWatchBookmarks",
    "sendInitialEvents",
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

    require(contract["source"]["openapi"] == "3.0.3", "OpenAPI version changed")
    require(
        contract["source"]["api_version"] == "9.1.0.0",
        "contract must remain at vSphere API 9.1.0.0",
    )
    require(
        contract["source"]["repository_commit_sha"] == PINNED_COMMIT,
        "contract repository commit changed",
    )
    require(
        contract["source"]["spec_blob_sha"] == PINNED_BLOB,
        "contract specification blob changed",
    )
    require(
        contract["source"]["spec_path"] == PINNED_SPEC,
        "contract specification path changed",
    )
    require(
        contract["source"]["license"] == "Apache-2.0",
        "contract source license changed",
    )
    require(contract["server_base_path"] == "/api", "server base path changed")
    require(
        contract["security"]
        == {
            "scheme": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter authentication projection changed",
    )

    require(
        sources["repository_commit_sha"] == PINNED_COMMIT,
        "official source repository commit changed",
    )
    require(
        sources["spec_blob_sha"] == PINNED_BLOB,
        "official source specification blob changed",
    )
    require(
        sources["spec_path"] == PINNED_SPEC,
        "official source path changed",
    )
    require(sources["license"] == "Apache-2.0", "official source license changed")
    require(
        PINNED_COMMIT in sources["spec_url"]
        and sources["spec_url"].endswith(PINNED_SPEC),
        "official source URL must pin the commit and specification path",
    )
    require(
        sources["operations"]
        == [
            {
                "operationId": DISCOVERY_OPERATION,
                "method": "GET",
                "path": "/vcenter/namespaces-user/namespaces",
            }
        ],
        "official source must record the exact sole VMware operationId",
    )

    operations = contract["operations"]
    require(len(operations) == 1, "focused contract must have one VMware operation")
    discovery = operations[0]
    require(
        discovery["operationId"] == DISCOVERY_OPERATION,
        "discovery operationId changed",
    )
    require(
        (discovery["method"], discovery["path"])
        == ("GET", "/vcenter/namespaces-user/namespaces"),
        "discovery route changed",
    )
    require(
        discovery["generated_binding"]
        == {
            "type": (
                "VMware.Bindings.vSphere.Api."
                "IVcenterNamespacesUserInstancesApi"
            ),
            "method": "VcenterNamespacesUserInstancesList",
        },
        "generated binding projection changed",
    )
    parameters = discovery["parameters"]
    require(
        [item["name"] for item in parameters] == ["filter", "groups"],
        "discovery optional parameters changed",
    )
    require(
        all(
            not item["required"] and item["omit_when_unset"]
            for item in parameters
        ),
        "discovery optionals must be omitted when unset",
    )
    summary = contract["schemas"][
        "Vcenter.Namespaces.User.Instances.Summary"
    ]
    require(
        summary["required"] == ["master_host", "namespace"],
        "namespace summary required fields changed",
    )
    require(
        contract["schemas"][
            "Vcenter.Namespaces.User.Instances.FilterSpec"
        ]["properties"]["username"]["omit_when_unset"],
        "filter.username omission rule changed",
    )

    profile = contract["integration_profile"]
    require(profile["name"] == CLUSTER_OPERATION, "VKS operation name changed")
    require(
        (profile["method"], profile["path"]) == ("GET", CLUSTER_PATH),
        "VKS Cluster API route changed",
    )
    require(
        profile["api_group"] == "cluster.x-k8s.io"
        and profile["api_version"] == "v1beta1"
        and profile["resource"] == "clusters"
        and profile["scope"] == "namespaced",
        "VKS resource profile changed",
    )
    require(
        profile["authentication"]
        == {
            "scheme": "Bearer",
            "in": "header",
            "name": "Authorization",
        },
        "Kubernetes authentication profile changed",
    )
    query_names = [item["name"] for item in profile["query_parameters"]]
    require(
        query_names
        == [
            "limit",
            "continue",
            "fieldSelector",
            "labelSelector",
            "resourceVersion",
            "resourceVersionMatch",
            "timeoutSeconds",
            "watch",
            "allowWatchBookmarks",
            "sendInitialEvents",
        ],
        "Kubernetes query profile changed",
    )
    require(
        set(query_names[2:]) == UNSET_KUBERNETES_FIELDS,
        "unset Kubernetes optionals changed",
    )
    require(
        "not represented as a VMware operationId"
        in profile["provenance_note"],
        "integration profile must not invent a VMware operationId",
    )
    return contract


def verify_module_shape() -> None:
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()

    require(
        "VMware.Sdk.Vcf.SddcManager" in manifest,
        "manifest must retain the VCF PowerCLI prerequisite",
    )
    require(
        "13.5.0.25380678" in manifest,
        "manifest must pin the environment-provided VCF PowerCLI version",
    )
    require(
        "VMware.Bindings.vSphere.Api."
        "IVcenterNamespacesUserInstancesApi" in source,
        "module must accept the genuine generated namespace binding",
    )
    require(
        "VcenterNamespacesUserInstancesList" in source,
        "module must invoke the specification-derived generated method",
    )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "curl",
        "start-process",
        "tcpclient",
        "webclient",
    ):
        require(
            forbidden not in folded,
            f"production module contains forbidden transport marker: {forbidden}",
        )
    require(
        not any(
            path.name.casefold().startswith(("vmware.", "powercli"))
            for path in ROOT.rglob("*")
            if path != MODULE_ROOT and path.is_dir()
        ),
        "the seed must not vendor VMware or PowerCLI modules",
    )

    syntax = subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "$e=$null;$t=$null;"
                "[Management.Automation.Language.Parser]::ParseFile("
                f"'{MODULE_PATH}',[ref]$t,[ref]$e)|Out-Null;"
                "if(@($e).Count){$e|ForEach-Object Message;exit 1}"
            ),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    require(
        syntax.returncode == 0,
        f"PowerShell module has parse errors: {syntax.stdout}{syntax.stderr}",
    )


def make_cluster(
    namespace: str,
    name: str,
    uid: str,
    marker: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "cluster.x-k8s.io/v1beta1",
        "kind": "Cluster",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid,
            "labels": {"verification": marker},
        },
        "spec": {
            "clusterNetwork": {
                "services": {"cidrBlocks": ["10.96.0.0/12"]}
            },
            "topology": {
                "class": "tanzukubernetescluster",
                "version": "v1.33.1+vmware.1-fips-vkr.2",
            },
        },
    }


def wait_for_port(
    process: subprocess.Popen[str],
    port_path: Path,
) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before readiness "
                f"({process.returncode}): {stdout}\n{stderr}"
            )
        if port_path.exists():
            value = port_path.read_text(encoding="ascii").strip()
            if value:
                return int(value)
        time.sleep(0.02)
    raise VerificationError("timed out waiting for loopback mock")


def run_exercise(
    port: int,
    session_token: str,
    kubernetes_token: str,
    namespace: str,
    page_size: int,
    output_path: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
            "VMWARE_POWERCLI_CEIP_SETTING": "false",
        }
    )
    return subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(EXERCISE_PATH),
            "-Port",
            str(port),
            "-SessionToken",
            session_token,
            "-KubernetesToken",
            kubernetes_token,
            "-Namespace",
            namespace,
            "-PageSize",
            str(page_size),
            "-OutputPath",
            str(output_path),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=40,
        check=False,
    )


def header_values(request: dict[str, Any], name: str) -> list[str]:
    return [
        item["value"]
        for item in request["headers"]
        if item["name"].casefold() == name.casefold()
    ]


def verify_wire(
    requests: list[dict[str, Any]],
    namespace: str,
    page_size: int,
    markers: list[str],
    session_token: str,
    kubernetes_token: str,
) -> None:
    require(
        len(requests) == 8,
        f"expected two complete four-request runs, got {len(requests)} requests",
    )
    encoded_namespace = quote(namespace, safe="")
    expected_cluster_path = CLUSTER_PATH.replace(
        "{namespace}",
        encoded_namespace,
    )
    expected_cluster_targets = [
        f"{expected_cluster_path}?limit={page_size}",
        (
            f"{expected_cluster_path}?limit={page_size}"
            f"&continue={quote(markers[0], safe='')}"
        ),
        (
            f"{expected_cluster_path}?limit={page_size}"
            f"&continue={quote(markers[1], safe='')}"
        ),
    ]

    for run_index in range(2):
        run = requests[run_index * 4 : (run_index + 1) * 4]
        discovery = run[0]
        require(
            discovery["operation"] == DISCOVERY_OPERATION,
            f"run {run_index + 1} did not start with namespace discovery",
        )
        require(
            (discovery["method"], discovery["raw_target"])
            == ("GET", DISCOVERY_PATH),
            "namespace discovery must be a queryless bodyless GET",
        )
        require(
            discovery["query_pairs"] == [],
            "unset filter, username, and groups must be absent",
        )
        require(
            header_values(discovery, "vmware-api-session-id")
            == [session_token],
            "vCenter request must carry exactly one session header",
        )
        require(
            header_values(discovery, "Authorization") == [],
            "vCenter discovery must not carry Kubernetes Authorization",
        )
        require(
            header_values(discovery, "Accept") == ["application/json"],
            "vCenter discovery must accept JSON",
        )
        require(
            header_values(discovery, "Content-Type") == []
            and header_values(discovery, "Content-Length") == []
            and discovery["body_length"] == 0,
            "vCenter discovery must have no body or entity headers",
        )

        cluster_requests = run[1:]
        require(
            [item["operation"] for item in cluster_requests]
            == [CLUSTER_OPERATION] * 3,
            "only the named VKS collection route may follow discovery",
        )
        require(
            [item["raw_target"] for item in cluster_requests]
            == expected_cluster_targets,
            "VKS pagination targets, order, or escaping are wrong",
        )
        for index, request in enumerate(cluster_requests):
            require(request["method"] == "GET", "VKS collection must use GET")
            expected_pairs: list[list[str]] = [
                ["limit", str(page_size)]
            ]
            if index:
                expected_pairs.append(["continue", markers[index - 1]])
            require(
                request["query_pairs"] == expected_pairs,
                "VKS query pair order or decoded value is wrong",
            )
            present_names = {pair[0] for pair in request["query_pairs"]}
            require(
                present_names.isdisjoint(UNSET_KUBERNETES_FIELDS),
                "an unset Kubernetes query field was sent",
            )
            require(
                header_values(request, "Authorization")
                == [f"Bearer {kubernetes_token}"],
                "Kubernetes request must carry exactly one bearer token",
            )
            require(
                header_values(request, "vmware-api-session-id") == [],
                "vCenter session authentication leaked to Kubernetes",
            )
            require(
                header_values(request, "Accept") == ["application/json"],
                "Kubernetes request must accept JSON",
            )
            require(
                header_values(request, "Content-Type") == []
                and header_values(request, "Content-Length") == []
                and request["body_length"] == 0,
                "Kubernetes GET must have no body or entity headers",
            )

    require(
        all(request["operation"] is not None for request in requests),
        "mock observed a request outside the contract allow-list",
    )


def verify_output(
    output_path: Path,
    expected: list[dict[str, Any]],
) -> None:
    result = load_json(output_path)
    require(
        list(result) == ["first", "second"],
        "exercise output shape changed",
    )
    require(
        result["first"] == expected,
        "first inventory is incomplete, unstable, or did not preserve objects",
    )
    require(
        result["second"] == expected,
        "second inventory changed when wire order reversed",
    )


def main() -> int:
    try:
        contract = verify_contract()
        verify_module_shape()
        require(shutil.which("pwsh") is not None, "PowerShell 7 is required")

        run_id = uuid.uuid4().hex[:20]
        namespace = f"team-{run_id}"
        distractor = f"other-{uuid.uuid4().hex[:20]}"
        session_token = f"vc-{secrets.token_urlsafe(32)}"
        kubernetes_token = f"k8s-{secrets.token_urlsafe(36)}"
        markers = [
            f"cursor/{secrets.token_urlsafe(10)} + =&?",
            f"next/{secrets.token_urlsafe(11)} + =&?",
        ]
        page_size = 2
        verification_marker = secrets.token_hex(16)
        tie_uids = sorted(
            [str(uuid.uuid4()), str(uuid.uuid4())],
        )
        clusters = [
            make_cluster(
                namespace,
                "zulu",
                str(uuid.uuid4()),
                verification_marker,
            ),
            make_cluster(
                namespace,
                "alpha",
                tie_uids[1],
                verification_marker,
            ),
            make_cluster(
                namespace,
                "Beta",
                str(uuid.uuid4()),
                verification_marker,
            ),
            make_cluster(
                namespace,
                "Alpha",
                str(uuid.uuid4()),
                verification_marker,
            ),
            make_cluster(
                namespace,
                "alpha",
                tie_uids[0],
                verification_marker,
            ),
        ]
        pages = [
            [clusters[0], clusters[1]],
            [clusters[2], clusters[3]],
            [clusters[4]],
        ]
        expected = sorted(
            clusters,
            key=lambda item: (
                item["metadata"]["name"],
                item["metadata"]["uid"],
            ),
        )

        with tempfile.TemporaryDirectory(prefix="vcf91-0131-") as temp_name:
            temp = Path(temp_name)
            config_path = temp / "config.json"
            log_path = temp / "requests.jsonl"
            port_path = temp / "port"
            output_path = temp / "result.json"
            config_path.write_text(
                json.dumps(
                    {
                        "namespace": namespace,
                        "distractor_namespace": distractor,
                        "page_size": page_size,
                        "markers": markers,
                        "resource_version": secrets.token_hex(12),
                        "pages": pages,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )

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
                    "--port-file",
                    str(port_path),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                port = wait_for_port(process, port_path)
                completed = run_exercise(
                    port,
                    session_token,
                    kubernetes_token,
                    namespace,
                    page_size,
                    output_path,
                )
                require(
                    completed.returncode == 0,
                    "PowerShell exercise failed:\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}",
                )
                requests = [
                    json.loads(line)
                    for line in log_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
                verify_wire(
                    requests,
                    namespace,
                    page_size,
                    markers,
                    session_token,
                    kubernetes_token,
                )
                verify_output(output_path, expected)
            finally:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                require(
                    process.returncode in (0, -15),
                    "mock shutdown failed "
                    f"({process.returncode}): {stdout}\n{stderr}",
                )

        print("ALL TESTS PASSED")
        return 0
    except (VerificationError, KeyError, TypeError, ValueError) as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
