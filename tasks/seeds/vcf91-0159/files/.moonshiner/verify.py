#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tools" / "contract_mock.py"
CLIENT = ROOT / "VksClusterApplyClient.java"
TEST_MAIN = ROOT / "TestMain.java"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
NAMESPACE_OPERATION = "Vcenter.Namespaces.Instances_getV2"
APPLY_OPERATION_KEY = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:server-side-apply"
)
EXPECTED_CONTRACT_OPERATIONS = [
    "getSupervisorNamespace",
    "applyVksCluster",
]

# The harness protects this verifier separately, so it does not self-hash.
PROTECTED_SHA256 = {
    "TestMain.java": "18d6b34ea12445ac812871c09d95ccb468d21a2d81e8e5a74cd95f0915165d60",
    "docs/contract.json": "2feaad0704298be0c97d91059b49e0a897686f0bbe89ad2bb39120576e707a75",
    "docs/official_sources.json": "b65ec03ee1763779166f827ef3330a59747367ba4d48ac28de15fa57666afab9",
    "tools/contract_mock.py": "766400d7dd12fee5cce1bf3eef727510511c1ff0dacd6b320d18e6e33997f4ac",
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
        require(item.get("openapi") == "3.0.3", "wrong OpenAPI version")
        require(item.get("apiVersion") == "9.1.0.0", "wrong API version")

    require(
        sources.get("operationIds") == [NAMESPACE_OPERATION],
        "official operationId list changed",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list) and len(source_operations) == 1,
        "official operation records changed",
    )
    official = source_operations[0]
    require(
        official.get("operationId") == NAMESPACE_OPERATION,
        "official operationId changed",
    )
    require(
        official.get("repositoryCommitSha") == PINNED_COMMIT,
        "operation commit missing",
    )
    require(
        official.get("specPath") == PINNED_SPEC,
        "operation spec path missing",
    )

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations missing")
    require(
        [operation.get("contractName") for operation in operations]
        == EXPECTED_CONTRACT_OPERATIONS,
        "contract operation order changed",
    )
    require(
        operations[0].get("sourceKind") == "openapi",
        "vCenter source label changed",
    )
    require(
        operations[0].get("operationId") == NAMESPACE_OPERATION,
        "vCenter operationId changed",
    )
    require(
        operations[0].get("method") == "GET"
        and operations[0].get("pathTemplate")
        == "/api/vcenter/namespaces/instances/v2/{namespace}",
        "vCenter route changed",
    )
    native = operations[1]
    require(
        native.get("sourceKind") == "kubernetes-resource",
        "Kubernetes source label changed",
    )
    require("operationId" not in native, "fictional Kubernetes operationId")
    require(
        native.get("operationKey") == APPLY_OPERATION_KEY,
        "Kubernetes operation key changed",
    )
    require(
        native.get("method") == "PATCH"
        and native.get("pathTemplate")
        == (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            "{namespace}/clusters/{cluster}"
        ),
        "Kubernetes route changed",
    )
    projected = contract.get("openapiProjection", {}).get("operations")
    require(
        isinstance(projected, list)
        and [item.get("operationId") for item in projected]
        == [NAMESPACE_OPERATION],
        "OpenAPI projection operationIds changed",
    )


def encode_component(value: str) -> str:
    return urllib.parse.quote(
        value,
        safe="-._~",
        encoding="utf-8",
        errors="strict",
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


def expected_apply_body(
    scenario: str, config: dict[str, object]
) -> bytes:
    topology: dict[str, object] = {
        "class": config["clusterClass"],
        "version": config["version"],
        "variables": [
            {"name": "vmClass", "value": config["vmClass"]},
            {"name": "storageClass", "value": config["storageClass"]},
        ],
        "controlPlane": {"replicas": 3},
    }
    spec: dict[str, object] = {"topology": topology}
    if scenario == "explicit_zero_false":
        topology["workers"] = {
            "machineDeployments": [
                {"class": "node-pool", "name": "worker", "replicas": 0}
            ]
        }
        spec["clusterNetwork"] = {
            "pods": {
                "cidrBlocks": [
                    "10.244.0.0/16",
                    "fd00:10:244::/56",
                ]
            }
        }
    elif scenario == "ambiguous":
        topology["workers"] = {
            "machineDeployments": [
                {"class": "node-pool", "name": "worker", "replicas": 2}
            ]
        }
        spec["clusterNetwork"] = {
            "services": {"cidrBlocks": ["10.96.0.0/12"]}
        }

    value = {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": config["cluster"],
            "namespace": config["namespace"],
        },
        "spec": spec,
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def assert_get_wire(
    entry: dict[str, object], config: dict[str, object]
) -> None:
    target = (
        "/api/vcenter/namespaces/instances/v2/"
        + encode_component(str(config["namespace"]))
    )
    require(entry["operation"] == "getSupervisorNamespace", "GET operation")
    require(entry["method"] == "GET", "GET method")
    require(entry["rawTarget"] == target, "GET raw target")
    require("?" not in str(entry["rawTarget"]), "GET query delimiter")
    require(body_bytes(entry) == b"", "GET body")
    require(
        header_values(entry, "Accept") == ["application/json"],
        "GET Accept",
    )
    require(
        header_values(entry, "vmware-api-session-id")
        == [config["session"]],
        "GET session",
    )
    require(header_values(entry, "Authorization") == [], "GET bearer leak")
    require(header_values(entry, "Content-Type") == [], "GET Content-Type")
    require(header_values(entry, "Content-Length") == [], "GET Content-Length")
    require(
        header_values(entry, "Transfer-Encoding") == [],
        "GET Transfer-Encoding",
    )


def assert_patch_wire(
    entry: dict[str, object],
    scenario: str,
    config: dict[str, object],
) -> None:
    query = "fieldManager=" + encode_component(str(config["fieldManager"]))
    if scenario == "explicit_zero_false":
        query += "&force=false"
    elif scenario == "ambiguous":
        query += "&force=true"
    target = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        + encode_component(str(config["namespace"]))
        + "/clusters/"
        + encode_component(str(config["cluster"]))
        + "?"
        + query
    )
    expected_body = expected_apply_body(scenario, config)

    require(entry["operation"] == "applyVksCluster", "PATCH operation")
    require(entry["method"] == "PATCH", "PATCH method")
    require(entry["rawTarget"] == target, "PATCH raw target")
    require(
        header_values(entry, "Accept") == ["application/json"],
        "PATCH Accept",
    )
    require(
        header_values(entry, "Authorization")
        == ["Bearer " + str(config["token"])],
        "PATCH bearer",
    )
    require(
        header_values(entry, "vmware-api-session-id") == [],
        "PATCH session leak",
    )
    require(
        header_values(entry, "Content-Type")
        == ["application/apply-patch+yaml"],
        "PATCH Content-Type",
    )
    require(
        header_values(entry, "Content-Length")
        == [str(len(expected_body))],
        "PATCH Content-Length",
    )
    require(
        header_values(entry, "Transfer-Encoding") == [],
        "PATCH Transfer-Encoding",
    )
    require(body_bytes(entry) == expected_body, "PATCH body bytes")

    parsed = json.loads(expected_body)
    serialized = body_bytes(entry).decode("utf-8")
    require("null" not in serialized, "PATCH null placeholder")
    require('"status"' not in serialized, "PATCH status field")
    require('"uid"' not in serialized, "PATCH uid field")
    require('"resourceVersion"' not in serialized, "PATCH resourceVersion")
    require('"generation"' not in serialized, "PATCH generation")
    if scenario == "minimal":
        topology = parsed["spec"]["topology"]
        require("workers" not in topology, "unset workers present")
        require(
            "clusterNetwork" not in parsed["spec"],
            "empty clusterNetwork present",
        )
        require("&force=" not in str(entry["rawTarget"]), "unset force present")
        for optional in ("dryRun", "fieldValidation", "pretty"):
            require(optional not in str(entry["rawTarget"]), f"{optional} present")


def load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    entries: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            value = json.loads(line)
            require(isinstance(value, dict), "invalid request log entry")
            entries.append(value)
    return entries


def wait_for_ready(process: subprocess.Popen[str], ready: Path) -> str:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if ready.exists():
            value = json.loads(ready.read_text(encoding="utf-8"))
            endpoint = value.get("endpoint")
            require(
                isinstance(endpoint, str)
                and endpoint.startswith("http://127.0.0.1:"),
                "mock did not bind IPv4 loopback",
            )
            return endpoint
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before ready: " + stdout + stderr
            )
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def run_scenario(
    classes: Path,
    scenario: str,
) -> None:
    nonce = secrets.token_hex(5)
    config: dict[str, object] = {
        "scenario": scenario,
        "session": "session-" + nonce + "+/",
        "token": "token-" + secrets.token_hex(9),
        "supervisor": "supervisor +/%?-" + nonce,
        "namespace": "team blue/edge%?-" + nonce,
        "cluster": "vks +/canary?-" + nonce,
        "fieldManager": "moon/client +?=" + nonce,
        "clusterClass": "builtin-generic-v3.5.0",
        "version": "v1.33.6+vmware.1-fips",
        "vmClass": "best-effort-medium",
        "storageClass": "vsan-default",
        "uid": "uid-" + secrets.token_hex(8),
        "resourceVersion": str(10_000 + secrets.randbelow(80_000)),
        "generation": 23,
        "cpuUsed": 137,
        "memoryUsed": 911,
        "storageUsed": 4099,
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0159-case-") as directory:
        case_dir = Path(directory)
        config_path = case_dir / "config.json"
        log_path = case_dir / "requests.jsonl"
        state_path = case_dir / "state.json"
        ready_path = case_dir / "ready.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--config",
                str(config_path),
                "--log",
                str(log_path),
                "--state",
                str(state_path),
                "--ready",
                str(ready_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            fallback = False
            try:
                endpoint = wait_for_ready(process, ready_path)
            except VerificationError as error:
                if "Operation not permitted" not in str(error):
                    raise
                fallback = True
                endpoint = "http://127.0.0.1:1"
            expected_attempts = 2 if scenario == "ambiguous" else 1
            command = [
                "java",
                "-cp",
                str(classes),
                "TestMain",
                scenario,
                endpoint,
                str(config["session"]),
                str(config["token"]),
                str(config["supervisor"]),
                str(config["namespace"]),
                str(config["cluster"]),
                str(config["fieldManager"]),
                str(config["clusterClass"]),
                str(config["version"]),
                str(config["vmClass"]),
                str(config["storageClass"]),
                str(config["uid"]),
                str(config["resourceVersion"]),
                str(config["generation"]),
                str(expected_attempts),
            ]
            if fallback:
                command.extend(
                    [
                        "in-memory",
                        str(log_path),
                        str(state_path),
                        str(CONTRACT),
                    ]
                )
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            require(
                completed.returncode == 0,
                (
                    f"TestMain failed for {scenario}\n"
                    + completed.stdout
                    + completed.stderr
                ),
            )
            require(
                completed.stdout.strip() == f"OK {scenario}",
                f"unexpected TestMain output for {scenario}",
            )
        finally:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)

        entries = load_jsonl(log_path)
        state = json.loads(state_path.read_text(encoding="utf-8"))

    if scenario in {"invalid_request", "invalid_config"}:
        require(entries == [], f"{scenario} performed network traffic")
        require(state == {"effects": 0, "patchAttempts": 0}, "invalid state")
        return

    assert_get_wire(entries[0], config)
    require(
        all(entry["operation"] is not None for entry in entries),
        "off-contract request was sent",
    )
    if scenario in {"namespace_not_ready", "redirect"}:
        require(len(entries) == 1, f"{scenario} did not stop after GET")
        require(state == {"effects": 0, "patchAttempts": 0}, "blocked mutation")
        return

    expected_count = 3 if scenario == "ambiguous" else 2
    require(len(entries) == expected_count, f"wrong request count for {scenario}")
    for patch in entries[1:]:
        assert_patch_wire(patch, scenario, config)

    if scenario == "ambiguous":
        require(
            entries[1]["rawTarget"] == entries[2]["rawTarget"],
            "retry target changed",
        )
        require(
            entries[1]["headers"] == entries[2]["headers"],
            "retry headers changed",
        )
        require(
            entries[1]["bodyBase64"] == entries[2]["bodyBase64"],
            "retry body changed",
        )
        require(
            state == {"effects": 1, "patchAttempts": 2},
            "ambiguous replay duplicated its effect",
        )
    else:
        require(
            state == {"effects": 1, "patchAttempts": 1},
            f"wrong mutation count for {scenario}",
        )


def main() -> None:
    verify_protected_files()
    verify_contract()

    with tempfile.TemporaryDirectory(prefix="vcf91-0159-classes-") as directory:
        classes = Path(directory)
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                str(CLIENT),
                str(TEST_MAIN),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        require(
            compile_result.returncode == 0,
            "javac failed\n" + compile_result.stdout + compile_result.stderr,
        )
        for scenario in (
            "minimal",
            "explicit_zero_false",
            "ambiguous",
            "namespace_not_ready",
            "redirect",
            "invalid_request",
            "invalid_config",
        ):
            run_scenario(classes, scenario)

    print("verification passed")


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, subprocess.TimeoutExpired) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
