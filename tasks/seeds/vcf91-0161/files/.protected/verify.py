#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src" / "VksClusterProvisionClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tools" / "contract_mock.py"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_OPERATION = "Vcenter.Namespaces.Instances_getV2"
KUBERNETES_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:create"
)

# The harness protects verify.py itself. These hashes protect every other fixture.
PROTECTED_SHA256 = {
    "tests/TestMain.java": "6c26ae4aeb382bd9e4be1e4be7e5439f16025294308b4ec2a615ead0209347a8",
    "tools/contract_mock.py": "8196bda9b45eafe8b3bccd41dcbe8ca312d756c011d97a882520b6a2acb52f07",
    "docs/contract.json": "7ff4de96ca9fae48bc8ebc57bfdea4e6be94d65fe224d6579457d9669c689c44",
    "docs/official_sources.json": "fe902e1128d401e73cb16a788f61a5b9b3f3672c762e940bb950ad87c5825609",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        require(path.is_file(), f"missing protected file: {relative}")
        require(sha256(path) == expected, f"protected file changed: {relative}")


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    source = contract.get("source", {})
    for item in (source, sources):
        require(
            item.get("repositoryCommitSha") == PINNED_COMMIT,
            "wrong source commit",
        )
        require(item.get("specBlobSha") == PINNED_BLOB, "wrong spec blob")
        require(item.get("specPath") == PINNED_SPEC, "wrong spec path")
        require(item.get("license") == "Apache-2.0", "wrong source license")

    require(
        sources.get("operationIds") == [VCENTER_OPERATION],
        "official operationId list changed",
    )
    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations missing")
    require(
        [item.get("contractName") for item in operations]
        == ["getSupervisorNamespace", "createVksCluster"],
        "contract operation allow-list changed",
    )
    require(
        operations[0].get("operationId") == VCENTER_OPERATION,
        "vCenter operationId changed",
    )
    require(
        operations[0].get("method") == "GET"
        and operations[0].get("pathTemplate")
        == "/api/vcenter/namespaces/instances/v2/{namespace}"
        and operations[0].get("successStatus") == 200,
        "vCenter wire projection changed",
    )
    native = operations[1]
    require(
        native.get("sourceKind") == "native-kubernetes-api",
        "Kubernetes source label changed",
    )
    require("operationId" not in native, "fictional Kubernetes operationId")
    require(
        native.get("operationKey") == KUBERNETES_OPERATION,
        "Kubernetes operation key changed",
    )
    require(
        native.get("method") == "POST"
        and native.get("pathTemplate")
        == (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            "{namespace}/clusters"
        )
        and native.get("successStatus") == 201,
        "Kubernetes wire contract changed",
    )
    require(
        native.get("optionalQueryFields")
        == ["dryRun", "fieldManager", "fieldValidation", "pretty"],
        "optional query contract changed",
    )

    schemas = contract.get("schemas", {})
    info = schemas.get("Vcenter.Namespaces.Instances.InfoV2", {})
    require(
        info.get("required")
        == [
            "access_list",
            "config_status",
            "description",
            "messages",
            "stats",
            "storage_specs",
            "supervisor",
        ],
        "InfoV2 focused schema changed",
    )
    cluster = schemas.get("cluster.x-k8s.io/v1beta2.ClusterCreate", {})
    require(cluster.get("unsetBehavior") == "omit", "unset behavior changed")
    require(
        cluster.get("optionalFields")
        == [
            "spec.topology.classNamespace",
            "spec.topology.controlPlane",
            "spec.topology.workers",
            "spec.clusterNetwork",
        ],
        "optional create fields changed",
    )


def encode_segment(value: str) -> str:
    return urllib.parse.quote(
        value, safe="-._~", encoding="utf-8", errors="strict"
    )


def header_values(entry: dict[str, object], name: str) -> list[str]:
    wanted = name.casefold()
    values: list[str] = []
    for pair in entry["headers"]:
        if pair[0].casefold() == wanted:
            values.append(pair[1])
    return values


def body_bytes(entry: dict[str, object]) -> bytes:
    return base64.b64decode(entry["bodyBase64"], validate=True)


def fixture_values(scenario: str) -> dict[str, str]:
    suffix = secrets.token_hex(5)
    return {
        "scenario": scenario,
        "vcenterSession": "session_" + secrets.token_urlsafe(18),
        "bearerToken": "token_" + secrets.token_urlsafe(20),
        "supervisorNamespace": "team-" + suffix,
        "supervisor": "supervisor-" + suffix,
        "differentSupervisor": "other-supervisor-" + suffix,
        "clusterName": "payments-" + suffix,
        "differentClusterName": "other-cluster-" + suffix,
        "clusterClass": "builtin-generic-v3.5.0",
        "kubernetesVersion": "v1.33.6+vmware.1-fips-vkr.2",
        "vmClass": "guaranteed-small",
        "storageClass": "vsan-policy-" + suffix,
        "classNamespace": "vmware-system-vks-public",
        "controlPlaneReplicas": "3",
        "workerPoolName": "worker-" + suffix,
        "workerReplicas": "0",
        "serviceDomain": "svc-" + suffix + ".local",
        "uid": secrets.token_hex(16),
        "resourceVersion": str(secrets.randbelow(8_000_000) + 1_000_000),
        "phase": "Provisioning",
    }


def write_fixture(path: Path, values: dict[str, str]) -> None:
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    path.write_text(text, encoding="utf-8")


def expected_body(values: dict[str, str], full: bool) -> bytes:
    topology: dict[str, object] = {
        "class": values["clusterClass"],
        "version": values["kubernetesVersion"],
    }
    if full:
        topology["classNamespace"] = values["classNamespace"]
    topology["variables"] = [
        {"name": "vmClass", "value": values["vmClass"]},
        {"name": "storageClass", "value": values["storageClass"]},
    ]
    if full:
        topology["controlPlane"] = {
            "replicas": int(values["controlPlaneReplicas"])
        }
        topology["workers"] = {
            "machineDeployments": [
                {
                    "class": "node-pool",
                    "name": values["workerPoolName"],
                    "replicas": int(values["workerReplicas"]),
                }
            ]
        }
    spec: dict[str, object] = {"topology": topology}
    if full:
        spec["clusterNetwork"] = {
            "serviceDomain": values["serviceDomain"]
        }
    value = {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": values["clusterName"],
            "namespace": values["supervisorNamespace"],
        },
        "spec": spec,
    }
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def assert_precheck_wire(
    entry: dict[str, object], values: dict[str, str]
) -> None:
    require(
        entry["operation"] == "getSupervisorNamespace",
        "precheck route mismatch",
    )
    require(entry["method"] == "GET", "precheck method mismatch")
    require(
        entry["rawTarget"]
        == (
            "/api/vcenter/namespaces/instances/v2/"
            + encode_segment(values["supervisorNamespace"])
        ),
        "precheck raw target mismatch",
    )
    require("?" not in entry["rawTarget"], "precheck query was sent")
    require(body_bytes(entry) == b"", "precheck request body was sent")
    require(
        header_values(entry, "Accept") == ["application/json"],
        "precheck Accept wire shape",
    )
    require(
        header_values(entry, "vmware-api-session-id")
        == [values["vcenterSession"]],
        "precheck session wire shape",
    )
    require(
        header_values(entry, "Authorization") == [],
        "Kubernetes bearer leaked to vCenter",
    )
    require(
        header_values(entry, "Content-Type") == [],
        "bodyless GET sent Content-Type",
    )
    require(
        header_values(entry, "Transfer-Encoding") == [],
        "bodyless GET used transfer encoding",
    )


def assert_create_wire(
    entry: dict[str, object],
    values: dict[str, str],
    full: bool,
) -> None:
    require(
        entry["operation"] == "createVksCluster",
        "create route mismatch",
    )
    require(entry["method"] == "POST", "create method mismatch")
    require(
        entry["rawTarget"]
        == (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            + encode_segment(values["supervisorNamespace"])
            + "/clusters"
        ),
        "create raw target mismatch",
    )
    require("?" not in entry["rawTarget"], "unset create query was sent")
    require(
        header_values(entry, "Accept") == ["application/json"],
        "create Accept wire shape",
    )
    require(
        header_values(entry, "Authorization")
        == ["Bearer " + values["bearerToken"]],
        "create bearer wire shape",
    )
    require(
        header_values(entry, "Content-Type") == ["application/json"],
        "create Content-Type wire shape",
    )
    require(
        header_values(entry, "vmware-api-session-id") == [],
        "vCenter session leaked to Kubernetes",
    )
    actual = body_bytes(entry)
    expected = expected_body(values, full)
    require(actual == expected, "create JSON bytes differ from contract")
    decoded = json.loads(actual)
    serialized = actual.decode("utf-8")
    require("null" not in serialized, "null stand-in was serialized")
    topology = decoded["spec"]["topology"]
    if full:
        require(
            topology["workers"]["machineDeployments"][0]["replicas"] == 0,
            "explicit zero replicas were not preserved",
        )
    else:
        require("classNamespace" not in topology, "unset classNamespace sent")
        require("controlPlane" not in topology, "empty controlPlane sent")
        require("workers" not in topology, "empty workers sent")
        require(
            "clusterNetwork" not in decoded["spec"],
            "empty clusterNetwork sent",
        )


def read_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def wait_for_port(
    path: Path, process: subprocess.Popen[str]
) -> int | None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return int(value["port"])
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            if (
                "PermissionError" in stderr
                or "Operation not permitted" in stderr
            ):
                return None
            raise VerificationError(
                "contract mock exited early: " + stdout + stderr
            )
        time.sleep(0.02)
    raise VerificationError("contract mock did not publish a port")


def run_scenario(
    classes: Path, temporary: Path, scenario: str
) -> None:
    values = fixture_values(scenario)
    fixture = temporary / f"{scenario}.properties"
    log = temporary / f"{scenario}.jsonl"
    port_file = temporary / f"{scenario}.port.json"
    write_fixture(fixture, values)

    command = [
        sys.executable,
        "-B",
        str(MOCK),
        "--contract",
        str(CONTRACT),
        "--log",
        str(log),
        "--fixture",
        str(fixture),
        "--port-file",
        str(port_file),
    ]
    mock = subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        port = wait_for_port(port_file, mock)
        if port is None:
            java_arguments = [
                "fallback",
                str(fixture),
                scenario,
                str(CONTRACT),
                str(log),
            ]
        else:
            java_arguments = [
                f"http://127.0.0.1:{port}",
                str(fixture),
                scenario,
            ]
        result = subprocess.run(
            [
                "java",
                "-cp",
                str(classes),
                "TestMain",
                *java_arguments,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        require(
            result.returncode == 0,
            (
                f"TestMain failed for {scenario}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            ),
        )
    finally:
        if mock.poll() is None:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

    entries = read_log(log)
    if scenario == "invalid_request":
        require(entries == [], "invalid request caused network traffic")
        return

    require(entries, f"no request log for {scenario}")
    require(
        all(entry["operation"] is not None for entry in entries),
        "client used an off-contract route",
    )
    assert_precheck_wire(entries[0], values)
    if scenario in {
        "namespace_not_ready",
        "supervisor_mismatch",
        "malformed_precheck",
    }:
        require(
            len(entries) == 1,
            f"failed precheck mutated state in {scenario}",
        )
        return

    require(len(entries) == 2, f"wrong operation count for {scenario}")
    assert_create_wire(entries[1], values, scenario == "full")


def compile_harness(classes: Path) -> None:
    result = subprocess.run(
        [
            "javac",
            "--release",
            "17",
            "-d",
            str(classes),
            str(CLIENT),
            str(TEST_MAIN),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(
        result.returncode == 0,
        "javac failed\n" + result.stdout + result.stderr,
    )


def main() -> int:
    verify_protected_files()
    verify_contract()
    require(CLIENT.is_file(), "missing src/VksClusterProvisionClient.java")

    with tempfile.TemporaryDirectory(prefix="vcf91-0161-") as directory:
        temporary = Path(directory)
        classes = temporary / "classes"
        classes.mkdir()
        compile_harness(classes)
        for scenario in [
            "minimal",
            "full",
            "namespace_not_ready",
            "supervisor_mismatch",
            "malformed_precheck",
            "create_bad_identity",
            "create_rejected",
            "invalid_request",
        ]:
            run_scenario(classes, temporary, scenario)

    print("PASS: guarded VKS cluster create contract verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
