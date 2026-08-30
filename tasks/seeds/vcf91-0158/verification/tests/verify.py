#!/usr/bin/env python3
"""Deterministic protected verifier for the paginated VCF/VKS Java task."""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise AssertionError(message)


def load_mock_class():
    path = ROOT / "tools" / "contract_mock.py"
    spec = importlib.util.spec_from_file_location("contract_mock", path)
    if spec is None or spec.loader is None:
        fail("could not load contract mock")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ContractMock


def validate_provenance() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text())
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text())
    expected_sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
    expected_path = (
        "specifications/vsphere/openapi/automation/vcenter.yaml")
    expected_operation = "Vcenter.Namespaces.User.Instances_list"

    if contract["source"]["repositoryCommitSha"] != expected_sha:
        fail("contract commit is not pinned")
    if contract["source"]["specPath"] != expected_path:
        fail("contract specification path changed")
    if [op["operationId"] for op in contract["operations"]] != [
            expected_operation]:
        fail("contract operation allow-list changed")
    if sources["repositoryCommitSha"] != expected_sha:
        fail("official source commit changed")
    if sources["specPath"] != expected_path:
        fail("official source path changed")
    if sources["operationIds"] != [expected_operation]:
        fail("official operationId list changed")


def headers(entry: dict[str, object], name: str) -> list[str]:
    pairs = entry["headers"]
    return [
        value for key, value in pairs
        if key.lower() == name.lower()
    ]


def assert_request_log(
        entries: list[dict[str, object]],
        session: str,
        token: str) -> None:
    encoded = {
        name: f"{name}%2Bnext%2F%3D%3F%26two"
        for name in ("alpha-team", "zeta-team")
    }
    one_call = [
        "/api/vcenter/namespaces-user/namespaces",
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        "alpha-team/clusters?limit=2",
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"alpha-team/clusters?continue={encoded['alpha-team']}&limit=2",
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        "zeta-team/clusters?limit=2",
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"zeta-team/clusters?continue={encoded['zeta-team']}&limit=2",
    ]
    expected_targets = one_call + one_call
    if len(entries) != len(expected_targets):
        fail(
            f"expected {len(expected_targets)} requests, got {len(entries)}")

    unset = {
        "pretty",
        "allowWatchBookmarks",
        "fieldSelector",
        "labelSelector",
        "resourceVersion",
        "resourceVersionMatch",
        "sendInitialEvents",
        "timeoutSeconds",
        "watch",
    }

    for index, (entry, expected_target) in enumerate(
            zip(entries, expected_targets, strict=True)):
        if entry["method"] != "GET":
            fail(f"request {index} used the wrong method")
        if entry["target"] != expected_target:
            fail(
                f"request {index} target mismatch: {entry['target']!r}")
        if entry["body"] != b"":
            fail(f"request {index} had a body")
        if headers(entry, "Accept") != ["application/json"]:
            fail(f"request {index} must have one exact Accept header")
        if headers(entry, "Content-Type"):
            fail(f"request {index} unexpectedly sent Content-Type")
        if headers(entry, "Transfer-Encoding"):
            fail(f"request {index} unexpectedly used Transfer-Encoding")
        lengths = headers(entry, "Content-Length")
        if any(value != "0" for value in lengths):
            fail(f"request {index} had a positive Content-Length")

        is_vcenter = index in (0, 5)
        if is_vcenter:
            if headers(entry, "vmware-api-session-id") != [session]:
                fail("vCenter request had the wrong session header")
            if headers(entry, "Authorization"):
                fail("vCenter request leaked Kubernetes authorization")
            if "?" in str(entry["target"]):
                fail("vCenter request serialized unset filters")
        else:
            if headers(entry, "Authorization") != [f"Bearer {token}"]:
                fail("Kubernetes request had the wrong bearer header")
            if headers(entry, "vmware-api-session-id"):
                fail("Kubernetes request leaked the vCenter session")
            query = str(entry["target"]).split("?", 1)[1]
            names = {piece.split("=", 1)[0] for piece in query.split("&")}
            if names & unset:
                fail("Kubernetes request serialized an unset list option")
            if any(piece.endswith("=") for piece in query.split("&")):
                fail("Kubernetes request serialized an empty query value")


def probe_unknown_route(mock) -> None:
    parsed = urlsplit(mock.base_url)
    connection = http.client.HTTPConnection(
        parsed.hostname, parsed.port, timeout=2)
    try:
        connection.request("GET", "/not-in-contract")
        response = connection.getresponse()
        response.read()
        if response.status != 404:
            fail("mock served a route not named by the contract")
    finally:
        connection.close()
    mock.clear_log()


def main() -> None:
    validate_provenance()
    ContractMock = load_mock_class()
    mock = ContractMock()
    mock.start()
    session = "verify-session-7c8f3"
    token = "verify-k8s-token-d14a9"
    try:
        probe_unknown_route(mock)
        with tempfile.TemporaryDirectory(prefix="vcf91-0158-") as temp:
            build = Path(temp)
            compile_result = subprocess.run(
                [
                    "javac",
                    "--release",
                    "17",
                    "-d",
                    str(build),
                    str(ROOT / "src" / "VcfVksPagedInventoryClient.java"),
                    str(ROOT / "tests" / "TestMain.java"),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            if compile_result.returncode:
                fail("javac failed:\n" + compile_result.stderr)

            environment = os.environ.copy()
            environment.update({
                "VCF_VKS_MOCK_BASE": mock.base_url,
                "VCF_VKS_SESSION": session,
                "VCF_VKS_TOKEN": token,
            })
            run_result = subprocess.run(
                ["java", "-cp", str(build), "TestMain"],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            if run_result.returncode:
                fail(
                    "TestMain failed:\n"
                    + run_result.stdout + run_result.stderr)
            if run_result.stdout.strip() != "TEST_MAIN_OK":
                fail("TestMain did not report success")

        if mock.failures():
            fail("mock rejected client traffic: " + "; ".join(mock.failures()))
        assert_request_log(mock.request_log(), session, token)
    finally:
        mock.stop()

    print("verification passed")


if __name__ == "__main__":
    main()
