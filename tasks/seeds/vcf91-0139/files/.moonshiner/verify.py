#!/usr/bin/env python3
"""Protected deterministic verification for vcf91-0139."""

from __future__ import annotations

import ast
import base64
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
CLIENT_PATH = ROOT / "vcf_vks_sync" / "client.py"
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"


def fail(message: str) -> NoReturn:
    raise AssertionError(message)


def assert_equal(actual: object, expected: object, context: str) -> None:
    if actual != expected:
        fail(f"{context}: expected {expected!r}, got {actual!r}")


def validate_protected_contract() -> None:
    contract_text = CONTRACT_PATH.read_text(encoding="utf-8")
    sources_text = SOURCES_PATH.read_text(encoding="utf-8")
    if ".invalid" in contract_text or ".invalid" in sources_text:
        fail("official source metadata contains a prohibited .invalid URL")
    contract = json.loads(contract_text)
    source = contract["source"]
    assert_equal(source["repositoryCommitSha"], COMMIT, "contract commit")
    assert_equal(source["specPath"], SPEC_PATH, "contract spec path")
    assert_equal(source["apiVersion"], "9.1.0.0", "contract API version")
    assert_equal(source["basePath"], "/api", "contract base path")

    operations = contract["operations"]
    assert_equal(
        [item["operationId"] for item in operations],
        [
            "Vcenter.Authentication.Token_issue",
            "Vcenter.Namespaces.User.Instances_list",
        ],
        "contract operationIds",
    )
    assert_equal(
        [(item["method"], item["path"]) for item in operations],
        [
            ("POST", "/api/vcenter/authentication/token"),
            ("GET", "/api/vcenter/namespaces-user/namespaces"),
        ],
        "contract vCenter routes",
    )
    issue = operations[0]["requestBody"]
    assert_equal(
        issue["propertiesInSpecOrder"],
        [
            "grant_type",
            "resource",
            "audience",
            "scope",
            "requested_token_type",
            "subject_token",
            "subject_token_type",
            "actor_token",
            "actor_token_type",
        ],
        "IssueSpec property projection",
    )
    assert_equal(
        [
            (item["name"], item["required"])
            for item in operations[1]["parameters"]
        ],
        [("filter", False), ("groups", False)],
        "namespace optional parameters",
    )
    kube = contract["kubernetesApi"]
    assert_equal(
        [
            (item["name"], item["method"], item["path"])
            for item in kube["operations"]
        ],
        [
            (
                "kubernetes.cluster.get",
                "GET",
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters/{cluster}",
            ),
            (
                "kubernetes.cluster.patch",
                "PATCH",
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters/{cluster}",
            ),
        ],
        "Kubernetes contract routes",
    )

    sources = json.loads(sources_text)
    assert_equal(sources["repositoryCommitSha"], COMMIT, "sources commit")
    assert_equal(sources["specPath"], SPEC_PATH, "sources spec path")
    assert_equal(
        sources["operationIds"],
        [
            "Vcenter.Authentication.Token_issue",
            "Vcenter.Namespaces.User.Instances_list",
        ],
        "sources operationIds",
    )
    for operation in sources["operations"]:
        assert_equal(
            operation["repositoryCommitSha"],
            COMMIT,
            f"{operation['operationId']} source commit",
        )
        assert_equal(
            operation["specPath"],
            SPEC_PATH,
            f"{operation['operationId']} source path",
        )


def validate_stdlib_only() -> None:
    if not CLIENT_PATH.is_file():
        fail("vcf_vks_sync/client.py was not created")
    tree = ast.parse(CLIENT_PATH.read_text(encoding="utf-8"), filename=str(CLIENT_PATH))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module:
                names = [node.module]
        for name in names:
            top = name.split(".", 1)[0]
            if top not in stdlib and top != "vcf_vks_sync":
                fail(f"client imports non-stdlib module {name!r}")
            if top in {"socket", "subprocess"}:
                fail(f"client imports prohibited module {name!r}")


def wait_for_ready(
    process: subprocess.Popen[str], ready_path: Path
) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ready_path.exists():
            data = json.loads(ready_path.read_text(encoding="utf-8"))
            return int(data["port"])
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            fail(
                "loopback mock exited before readiness: "
                + (stdout + stderr)[-1000:]
            )
        time.sleep(0.02)
    fail("loopback mock did not become ready")


def wait_for_log(log_path: Path, count: int) -> list[dict[str, object]]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            if len(lines) >= count:
                return [json.loads(line) for line in lines]
        time.sleep(0.02)
    fail(f"request log did not reach {count} entries")


def header_map(entry: dict[str, object]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw in entry["headers"]:  # type: ignore[union-attr]
        name, value = raw
        result.setdefault(name, []).append(value)
    return result


def assert_headers(
    entry: dict[str, object],
    expected: dict[str, str],
    context: str,
) -> None:
    actual = header_map(entry)
    contract_names = {
        "accept",
        "authorization",
        "content-type",
        "vmware-api-session-id",
    }
    assert_equal(
        set(actual).intersection(contract_names),
        set(expected),
        f"{context} contract header names",
    )
    for name, value in expected.items():
        assert_equal(actual[name], [value], f"{context} header {name}")


def request_body(entry: dict[str, object]) -> bytes:
    return base64.b64decode(str(entry["body_base64"]), validate=True)


def run_case() -> None:
    marker = secrets.token_hex(7)
    namespace = f"team blue/{marker}"
    clusters = [
        f"checkout one/{marker}",
        f"payments+snow-雪/{marker}",
    ]
    before_versions = [
        f"before-{secrets.token_hex(8)}",
        f"before-{secrets.token_hex(8)}",
    ]
    after_versions = [
        f"after-{secrets.token_hex(8)}",
        f"after-{secrets.token_hex(8)}",
    ]
    subject_token = f"subject-{secrets.token_urlsafe(23)}"
    old_token = f"access-old-{secrets.token_urlsafe(19)}"
    new_token = f"access-new-{secrets.token_urlsafe(19)}"
    managed_by = f"platform operator/{secrets.token_hex(6)}"

    with tempfile.TemporaryDirectory(prefix="vcf91-0139-") as temp_name:
        temp = Path(temp_name)
        ready_path = temp / "ready.json"
        log_path = temp / "requests.jsonl"
        command = [
            sys.executable,
            "-B",
            str(ROOT / ".moonshiner" / "mock_server.py"),
            "--contract",
            str(CONTRACT_PATH),
            "--ready-file",
            str(ready_path),
            "--log-file",
            str(log_path),
            "--namespace",
            namespace,
            "--clusters-json",
            json.dumps(clusters, ensure_ascii=False),
            "--before-versions-json",
            json.dumps(before_versions),
            "--after-versions-json",
            json.dumps(after_versions),
            "--subject-token",
            subject_token,
            "--old-access-token",
            old_token,
            "--new-access-token",
            new_token,
        ]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            port = wait_for_ready(process, ready_path)
            from vcf_vks_sync import reconcile_cluster_annotations

            base_arguments = (
                f"http://127.0.0.1:{port}",
                subject_token,
                SUBJECT_TOKEN_TYPE,
                namespace,
                clusters,
                managed_by,
            )

            invalid_cases = [
                (
                    "vCenter URL with credentials",
                    lambda: reconcile_cluster_annotations(
                        f"http://user@127.0.0.1:{port}",
                        *base_arguments[1:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "vCenter URL with a path",
                    lambda: reconcile_cluster_annotations(
                        f"http://127.0.0.1:{port}/api",
                        *base_arguments[1:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "vCenter URL with a bare query",
                    lambda: reconcile_cluster_annotations(
                        f"http://127.0.0.1:{port}?",
                        *base_arguments[1:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "vCenter URL with a bare fragment",
                    lambda: reconcile_cluster_annotations(
                        f"http://127.0.0.1:{port}#",
                        *base_arguments[1:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "blank subject token",
                    lambda: reconcile_cluster_annotations(
                        base_arguments[0],
                        " ",
                        *base_arguments[2:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "header-unsafe subject token type",
                    lambda: reconcile_cluster_annotations(
                        base_arguments[0],
                        base_arguments[1],
                        "jwt\r\ninjected: value",
                        *base_arguments[3:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "blank namespace",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:3],
                        " ",
                        *base_arguments[4:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "non-UTF-8 namespace",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:3],
                        "\ud800",
                        *base_arguments[4:],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "string cluster_names",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:4],
                        "one-cluster",
                        base_arguments[5],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "empty cluster_names",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:4],
                        [],
                        base_arguments[5],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "duplicate cluster_names",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:4],
                        ["duplicate", "duplicate"],
                        base_arguments[5],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "non-UTF-8 Cluster name",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:4],
                        ["\ud800"],
                        base_arguments[5],
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "blank managed_by",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:5],
                        " ",
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "non-UTF-8 managed_by",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments[:5],
                        "\ud800",
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "blank change_ticket",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments,
                        change_ticket=" ",
                        kubernetes_scheme="http",
                    ),
                ),
                (
                    "unsupported Kubernetes scheme",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments,
                        kubernetes_scheme="ftp",
                    ),
                ),
                (
                    "nonpositive timeout",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments,
                        kubernetes_scheme="http",
                        timeout=0,
                    ),
                ),
                (
                    "nonfinite timeout",
                    lambda: reconcile_cluster_annotations(
                        *base_arguments,
                        kubernetes_scheme="http",
                        timeout=math.inf,
                    ),
                ),
            ]
            for context, invoke in invalid_cases:
                try:
                    invoke()
                except Exception:
                    pass
                else:
                    fail(f"{context} was accepted")
                if log_path.exists() and log_path.read_text(
                    encoding="utf-8"
                ):
                    fail(f"{context} caused network traffic")

            result = reconcile_cluster_annotations(
                *base_arguments[:4],
                (cluster for cluster in clusters),
                base_arguments[5],
                kubernetes_scheme="http",
                timeout=3.0,
            )
            entries = wait_for_log(log_path, 8)

            change_ticket = f"change/{secrets.token_hex(7)}"
            ticket_result = reconcile_cluster_annotations(
                *base_arguments[:4],
                list(reversed(clusters)),
                base_arguments[5],
                change_ticket=change_ticket,
                kubernetes_scheme="http",
                timeout=3.0,
            )
            ticket_entries = wait_for_log(log_path, 14)[8:]
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=3)
            if process.returncode not in (-15, 0):
                fail(
                    "loopback mock failed: " + (stdout + stderr)[-1000:]
                )

    assert_equal(
        list(result),
        ["namespace", "updated", "access_token_refreshes"],
        "result key order",
    )
    assert_equal(result["namespace"], namespace, "result namespace")
    assert_equal(result["access_token_refreshes"], 1, "refresh count")
    assert_equal(
        result["updated"],
        [
            {"name": clusters[0], "resource_version": after_versions[0]},
            {"name": clusters[1], "resource_version": after_versions[1]},
        ],
        "updated results",
    )
    for item in result["updated"]:
        assert_equal(
            list(item),
            ["name", "resource_version"],
            "updated item key order",
        )
    assert_equal(
        ticket_result,
        {
            "namespace": namespace,
            "updated": [
                {
                    "name": clusters[1],
                    "resource_version": after_versions[1],
                },
                {
                    "name": clusters[0],
                    "resource_version": after_versions[0],
                },
            ],
            "access_token_refreshes": 0,
        },
        "change-ticket result",
    )

    assert_equal(len(entries), 8, "request count")
    assert_equal(
        [entry["sequence"] for entry in entries],
        list(range(8)),
        "request log sequence",
    )
    assert_equal(
        [entry["operation"] for entry in entries],
        [
            "vcenter.token.issue",
            "vcenter.namespace.listAuthorized",
            "kubernetes.cluster.get",
            "kubernetes.cluster.patch",
            "kubernetes.cluster.get",
            "vcenter.token.issue",
            "kubernetes.cluster.get",
            "kubernetes.cluster.patch",
        ],
        "operation order",
    )
    assert_equal(
        [entry["method"] for entry in entries],
        ["POST", "GET", "GET", "PATCH", "GET", "POST", "GET", "PATCH"],
        "method order",
    )
    assert_equal(
        [entry["status"] for entry in entries],
        [200, 200, 200, 200, 401, 200, 200, 200],
        "response status sequence",
    )

    authority = f"127.0.0.1:{port}"
    token_target = "/api/vcenter/authentication/token"
    namespace_target = "/api/vcenter/namespaces-user/namespaces"
    cluster_targets = [
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        + urllib.parse.quote(namespace, safe="")
        + "/clusters/"
        + urllib.parse.quote(cluster, safe="")
        for cluster in clusters
    ]
    assert_equal(
        [entry["raw_target"] for entry in entries],
        [
            token_target,
            namespace_target,
            cluster_targets[0],
            cluster_targets[0],
            cluster_targets[1],
            token_target,
            cluster_targets[1],
            cluster_targets[1],
        ],
        "raw request targets",
    )
    if any("?" in str(entry["raw_target"]) for entry in entries):
        fail("an optional query or bare query delimiter was sent")

    form_body = urllib.parse.urlencode(
        [
            ("grant_type", GRANT_TYPE),
            ("subject_token", subject_token),
            ("subject_token_type", SUBJECT_TOKEN_TYPE),
        ]
    ).encode("ascii")
    expected_patch_bodies = [
        json.dumps(
            {
                "metadata": {
                    "annotations": {
                        "platform.vcf.vmware.com/managed-by": managed_by
                    }
                }
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        for _cluster in clusters
    ]
    assert_equal(request_body(entries[0]), form_body, "initial exchange body")
    assert_equal(request_body(entries[5]), form_body, "refresh exchange body")
    assert_equal(
        urllib.parse.parse_qsl(
            form_body.decode("ascii"), keep_blank_values=True
        ),
        [
            ("grant_type", GRANT_TYPE),
            ("subject_token", subject_token),
            ("subject_token_type", SUBJECT_TOKEN_TYPE),
        ],
        "token form fields and order",
    )
    for index in (1, 2, 4, 6):
        assert_equal(request_body(entries[index]), b"", f"GET {index} body")
        assert_equal(entries[index]["body_length"], 0, f"GET {index} length")
    assert_equal(
        request_body(entries[3]), expected_patch_bodies[0], "first patch body"
    )
    assert_equal(
        request_body(entries[7]), expected_patch_bodies[1], "second patch body"
    )
    for index in (3, 7):
        body_text = request_body(entries[index]).decode("utf-8")
        for forbidden in (
            "platform.vcf.vmware.com/change-ticket",
            '"spec"',
            '"status"',
            '"resourceVersion"',
        ):
            if forbidden in body_text:
                fail(f"PATCH {index} serialized unset field {forbidden}")

    base_get_headers = {"accept": "application/json"}
    token_headers = {
        **base_get_headers,
        "authorization": f"Bearer {subject_token}",
        "content-type": "application/x-www-form-urlencoded",
    }
    assert_headers(entries[0], token_headers, "initial token exchange")
    assert_headers(entries[5], token_headers, "refresh token exchange")
    assert_headers(
        entries[1],
        {
            **base_get_headers,
            "vmware-api-session-id": old_token,
        },
        "namespace list",
    )
    expected_kube_tokens = [old_token, old_token, old_token, new_token, new_token]
    for index, token in zip((2, 3, 4, 6, 7), expected_kube_tokens):
        headers = {
            **base_get_headers,
            "authorization": f"Bearer {token}",
        }
        if index in (3, 7):
            headers["content-type"] = "application/merge-patch+json"
        assert_headers(entries[index], headers, f"Kubernetes request {index}")

    if sum(
        entry["raw_target"] == namespace_target for entry in entries
    ) != 1:
        fail("namespace discovery was restarted")
    if sum(
        entry["raw_target"] == cluster_targets[0] for entry in entries
    ) != 2:
        fail("completed first-Cluster work was replayed")
    if sum(entry["method"] == "PATCH" for entry in entries) != 2:
        fail("a PATCH was omitted or retried")
    if sum(entry["raw_target"] == token_target for entry in entries) != 2:
        fail("token exchange count was not initial plus one refresh")

    assert_equal(
        [entry["operation"] for entry in ticket_entries],
        [
            "vcenter.token.issue",
            "vcenter.namespace.listAuthorized",
            "kubernetes.cluster.get",
            "kubernetes.cluster.patch",
            "kubernetes.cluster.get",
            "kubernetes.cluster.patch",
        ],
        "change-ticket operation order",
    )
    assert_equal(
        [entry["raw_target"] for entry in ticket_entries],
        [
            token_target,
            namespace_target,
            cluster_targets[1],
            cluster_targets[1],
            cluster_targets[0],
            cluster_targets[0],
        ],
        "change-ticket raw request targets",
    )
    ticket_patch_body = json.dumps(
        {
            "metadata": {
                "annotations": {
                    "platform.vcf.vmware.com/managed-by": managed_by,
                    "platform.vcf.vmware.com/change-ticket": change_ticket,
                }
            }
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert_equal(
        request_body(ticket_entries[3]),
        ticket_patch_body,
        "first change-ticket patch body",
    )
    assert_equal(
        request_body(ticket_entries[5]),
        ticket_patch_body,
        "second change-ticket patch body",
    )
    assert_headers(ticket_entries[0], token_headers, "change-ticket exchange")
    assert_headers(
        ticket_entries[1],
        {
            **base_get_headers,
            "vmware-api-session-id": new_token,
        },
        "change-ticket namespace list",
    )
    for index in (2, 3, 4, 5):
        headers = {
            **base_get_headers,
            "authorization": f"Bearer {new_token}",
        }
        if index in (3, 5):
            headers["content-type"] = "application/merge-patch+json"
        assert_headers(
            ticket_entries[index],
            headers,
            f"change-ticket Kubernetes request {index}",
        )


def main() -> None:
    validate_protected_contract()
    validate_stdlib_only()
    run_case()
    print("PASS: vcf91-0139 contract, refresh, omission, and wire checks")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
