"""Deterministic protected verification for the VCF 9.1 retry workflow."""

from __future__ import annotations

import json
import math
import secrets
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mock_api import ContractMockServer  # noqa: E402
from vcf_vks_retry import VksRetryClient  # noqa: E402


def fail(message: str) -> None:
    raise AssertionError(message)


def expect_raises(
    error_types: tuple[type[BaseException], ...],
    call: Callable[[], object],
    label: str,
) -> None:
    try:
        call()
    except error_types:
        return
    except Exception as error:
        fail(f"{label}: raised unexpected {type(error).__name__}")
    fail(f"{label}: did not raise")


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def header_map(record: dict[str, Any]) -> dict[str, str]:
    pairs = [(key.lower(), value) for key, value in record["headers"]]
    names = [key for key, _ in pairs]
    if len(names) != len(set(names)):
        fail(f"request {record['sequence']} has duplicate headers")
    return dict(pairs)


def validate_source_contract() -> None:
    contract = json.loads(
        (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
    )
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
    )
    expected_commit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
    expected_blob = "8028b0824c4ff3503d05f44814f967938a795c40"
    expected_path = "specifications/vsphere/openapi/automation/vcenter.yaml"
    expected_operation = "Vcenter.Namespaces.Instances_getV2"

    source = contract.get("source", {})
    if source.get("commitSha") != expected_commit:
        fail("contract does not pin the required repository commit")
    if source.get("specBlobSha") != expected_blob:
        fail("contract does not pin the researched vcenter.yaml blob")
    if source.get("specPath") != expected_path:
        fail("contract has the wrong specification path")
    if source.get("apiVersion") != "9.1.0.0":
        fail("contract is not derived from the VCF 9.1 vSphere spec")

    vcenter_operations = [
        operation
        for operation in contract.get("operations", [])
        if operation.get("sourceKind") == "vcenter-openapi-operation"
    ]
    if [item.get("operationId") for item in vcenter_operations] != [
        expected_operation
    ]:
        fail("contract names the wrong vCenter operationId set")
    if set(item.get("contractName") for item in contract["operations"]) != {
        "getSupervisorNamespace",
        "getVksCluster",
        "patchVksClusterMetadata",
    }:
        fail("contract operation allow-list is not exact")
    if any(
        "operationId" in item
        for item in contract["operations"]
        if item.get("sourceKind") == "supervisor-kubernetes-resource"
    ):
        fail("Kubernetes routes must not claim vcenter.yaml operationIds")

    if sources.get("repositoryCommitSha") != expected_commit:
        fail("official_sources.json commit differs from the contract")
    if sources.get("specBlobSha") != expected_blob:
        fail("official_sources.json blob differs from the contract")
    if sources.get("specPath") != expected_path:
        fail("official_sources.json has the wrong specification path")
    if sources.get("operationIds") != [expected_operation]:
        fail("official_sources.json has the wrong operationId list")
    for item in sources.get("operations", []):
        if (
            item.get("operationId") != expected_operation
            or item.get("repositoryCommitSha") != expected_commit
            or item.get("specPath") != expected_path
        ):
            fail("each official source operation must repeat its provenance")


def validate_headers(
    records: list[dict[str, Any]],
    *,
    host: str,
    session_id: str,
    token: str,
) -> None:
    for record in records:
        headers = header_map(record)
        expected = {
            "host": host,
            "accept": "application/json",
        }
        if record["target"].startswith("/api/vcenter/"):
            expected["vmware-api-session-id"] = session_id
        else:
            expected["authorization"] = f"Bearer {token}"
        if record["method"] == "PATCH":
            expected["content-type"] = "application/merge-patch+json"
            expected["content-length"] = str(record["body_length"])
        if headers != expected:
            fail(
                f"request {record['sequence']} headers differ: "
                f"expected {expected!r}, got {headers!r}"
            )


def main() -> None:
    validate_source_contract()

    suffix = secrets.token_hex(6)
    namespace = f"team blue/ñ-{suffix}"
    cluster_after = f"payments-{suffix}"
    cluster_before = f"search-{suffix}"
    supervisor_id = f"supervisor-{secrets.token_hex(8)}"
    session_id = secrets.token_urlsafe(25)
    token = secrets.token_urlsafe(31)
    maintenance_id = f"maint π/{secrets.token_hex(7)}"
    existing_key = f"existing.example/{secrets.token_hex(4)}"
    existing_value = secrets.token_hex(9)
    uid_after = secrets.token_hex(16)
    uid_before = secrets.token_hex(16)

    namespace_info = {
        "supervisor": supervisor_id,
        "config_status": "RUNNING",
        "messages": [],
        "stats": {},
        "description": f"runtime-{secrets.token_hex(5)}",
        "access_list": [],
        "storage_specs": [],
    }

    def cluster(name: str, uid: str, resource_version: str) -> dict[str, Any]:
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "uid": uid,
                "resourceVersion": resource_version,
                "annotations": {existing_key: existing_value},
            },
            "spec": {
                "topology": {
                    "class": "builtin-generic-v3.6.0",
                    "version": "v1.33.6+vmware.1",
                }
            },
        }

    initial_clusters = {
        cluster_after: cluster(cluster_after, uid_after, "410"),
        cluster_before: cluster(cluster_before, uid_before, "830"),
    }

    with tempfile.TemporaryDirectory(prefix="vcf-vks-retry-") as temporary:
        request_log = Path(temporary) / "requests.jsonl"
        server = ContractMockServer(
            ("127.0.0.1", 0),
            contract_path=ROOT / "docs" / "contract.json",
            request_log=request_log,
            namespace=namespace,
            namespace_info=namespace_info,
            clusters=initial_clusters,
            drop_modes={
                cluster_after: "after",
                cluster_before: "before",
            },
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = VksRetryClient(
                server.root_url + "/",
                server.root_url,
                session_id,
                token,
                timeout=5.0,
            )
            if read_log(request_log):
                fail("client construction performed network traffic")

            invalid_calls = [
                (
                    "blank namespace",
                    lambda: client.mark_clusters(
                        namespace=" ",
                        cluster_names=[cluster_after],
                        maintenance_id=maintenance_id,
                    ),
                ),
                (
                    "string cluster_names",
                    lambda: client.mark_clusters(
                        namespace=namespace,
                        cluster_names=cluster_after,
                        maintenance_id=maintenance_id,
                    ),
                ),
                (
                    "duplicate cluster name",
                    lambda: client.mark_clusters(
                        namespace=namespace,
                        cluster_names=[cluster_after, cluster_after],
                        maintenance_id=maintenance_id,
                    ),
                ),
                (
                    "blank optional note",
                    lambda: client.mark_clusters(
                        namespace=namespace,
                        cluster_names=[cluster_after],
                        maintenance_id=maintenance_id,
                        note=" ",
                    ),
                ),
            ]
            for label, call in invalid_calls:
                expect_raises((TypeError, ValueError), call, label)
            if read_log(request_log):
                fail("input validation was not completed before traffic")

            result = client.mark_clusters(
                namespace=namespace,
                cluster_names=(name for name in [cluster_after, cluster_before]),
                maintenance_id=maintenance_id,
                note=None,
            )
            result_again = client.mark_clusters(
                namespace=namespace,
                cluster_names=[cluster_after, cluster_before],
                maintenance_id=maintenance_id,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        records = read_log(request_log)
        stats = server.snapshot_stats()

    if len(result) != 2 or [
        item.get("metadata", {}).get("name") for item in result
    ] != [cluster_after, cluster_before]:
        fail("result does not preserve caller Cluster order")
    if result_again != result:
        fail("repeat reconciliation did not return the same final objects")

    annotation_id = "platform.vcf.vmware.com/maintenance-id"
    annotation_note = "platform.vcf.vmware.com/maintenance-note"
    for item in result:
        annotations = item["metadata"]["annotations"]
        if annotations.get(annotation_id) != maintenance_id:
            fail("final Cluster is missing the desired maintenance id")
        if annotations.get(existing_key) != existing_value:
            fail("merge patch did not preserve an unrelated annotation")
        if annotation_note in annotations:
            fail("unset optional note was not omitted")

    if stats["patch_attempts"] != {
        cluster_after: 1,
        cluster_before: 2,
    }:
        fail(f"unsafe PATCH attempt counts: {stats['patch_attempts']!r}")
    if stats["mutation_counts"] != {
        cluster_after: 1,
        cluster_before: 1,
    }:
        fail(f"mutation was duplicated: {stats['mutation_counts']!r}")

    encoded_namespace = quote(namespace, safe="")
    after_path = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"{encoded_namespace}/clusters/{quote(cluster_after, safe='')}"
    )
    before_path = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"{encoded_namespace}/clusters/{quote(cluster_before, safe='')}"
    )
    namespace_path = (
        f"/api/vcenter/namespaces/instances/v2/{encoded_namespace}"
    )
    expected_transcript = [
        ("GET", namespace_path),
        ("GET", after_path),
        ("PATCH", after_path),
        ("GET", after_path),
        ("GET", before_path),
        ("PATCH", before_path),
        ("GET", before_path),
        ("PATCH", before_path),
        ("GET", namespace_path),
        ("GET", after_path),
        ("GET", before_path),
    ]
    actual_transcript = [
        (record["method"], record["target"]) for record in records
    ]
    if actual_transcript != expected_transcript:
        fail(
            "ordered wire transcript differs:\n"
            f"expected {expected_transcript!r}\n"
            f"actual   {actual_transcript!r}"
        )
    if any("?" in record["target"] for record in records):
        fail("an unset query option or bare query delimiter was sent")

    host = urlsplit(server.root_url).netloc
    validate_headers(
        records,
        host=host,
        session_id=session_id,
        token=token,
    )
    for record in records:
        if record["method"] == "GET" and (
            record["body_length"] != 0 or record["body_utf8"] != ""
        ):
            fail(f"GET request {record['sequence']} was not bodyless")

    def expected_patch(resource_version: str) -> str:
        return json.dumps(
            {
                "metadata": {
                    "resourceVersion": resource_version,
                    "annotations": {annotation_id: maintenance_id},
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    patch_records = [
        record for record in records if record["method"] == "PATCH"
    ]
    expected_patch_bodies = [
        expected_patch("410"),
        expected_patch("830"),
        expected_patch("830"),
    ]
    actual_patch_bodies = [
        record["body_utf8"] for record in patch_records
    ]
    if actual_patch_bodies != expected_patch_bodies:
        fail(
            "PATCH bytes or member order differ:\n"
            f"expected {expected_patch_bodies!r}\n"
            f"actual   {actual_patch_bodies!r}"
        )
    for body_text in actual_patch_bodies:
        body = json.loads(body_text)
        metadata = body.get("metadata")
        if set(body) != {"metadata"} or set(metadata) != {
            "resourceVersion",
            "annotations",
        }:
            fail("PATCH contains fields outside the focused contract")
        if set(metadata["annotations"]) != {annotation_id}:
            fail("unset optional annotation or another field was serialized")

    constructor_cases = [
        ("credential newline", (server.root_url, server.root_url, "bad\nid", token, 1.0)),
        ("boolean timeout", (server.root_url, server.root_url, session_id, token, True)),
        ("infinite timeout", (server.root_url, server.root_url, session_id, token, math.inf)),
        (
            "origin path",
            (server.root_url + "/api", server.root_url, session_id, token, 1.0),
        ),
    ]
    for label, arguments in constructor_cases:
        expect_raises(
            (TypeError, ValueError),
            lambda arguments=arguments: VksRetryClient(
                arguments[0],
                arguments[1],
                arguments[2],
                arguments[3],
                timeout=arguments[4],
            ),
            label,
        )

    print("verified: contract-pinned VKS ambiguous mutation retry is duplicate-safe")


if __name__ == "__main__":
    main()
