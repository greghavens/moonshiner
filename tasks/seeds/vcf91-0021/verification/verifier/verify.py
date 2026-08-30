#!/usr/bin/env python3
"""Deterministic protected verification for the VCF SDDC Manager client."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_OPERATIONS = {
    ("getDomains", "GET", "/v1/domains"),
    ("createDomain", "POST", "/v1/domains"),
    ("getTask", "GET", "/v1/tasks/{id}"),
}
EXPECTED_CONTRACT_SHA256 = "be45a778e430490f17d1423160d32b045619af85f392dd5bc157d50396b20b09"
EXPECTED_SOURCES_SHA256 = "e332cdffcb43e91d8af9f8a34334840aa7a6d75a929c1577a90a84d16b4850f8"


def check(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(relative: str) -> object:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def verify_protected_contract() -> None:
    contract = load_json("docs/contract.json")
    sources = load_json("docs/official_sources.json")
    check(sha256("docs/contract.json") == EXPECTED_CONTRACT_SHA256,
          "protected contract content changed")
    check(sha256("docs/official_sources.json") == EXPECTED_SOURCES_SHA256,
          "official source provenance changed")
    check(contract["product_version"] == "9.1.0.0", "wrong VCF contract version")
    check(contract["derived_from"]["commit_sha"] == EXPECTED_COMMIT,
          "contract is not pinned to the expected repository commit")
    operations = {
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    }
    check(operations == EXPECTED_OPERATIONS, "contract operation set changed")
    check(sources["repository_commit_sha"] == EXPECTED_COMMIT,
          "official sources commit does not match contract")
    check(
        {item["operationId"] for item in sources["operations"]}
        == {item[0] for item in EXPECTED_OPERATIONS},
        "official sources do not record every exact operationId",
    )
    check(
        all(item["spec_path"] == sources["spec_path"]
            and item["repository_commit_sha"] == EXPECTED_COMMIT
            for item in sources["operations"]),
        "operation provenance is incomplete",
    )


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_sddc"
    sources = sorted(package.rglob("*.py")) if package.is_dir() else []
    check(sources, "vcf_sddc package has not been implemented")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module]
            for name in imported:
                root = name.partition(".")[0]
                check(
                    root in stdlib or root == "vcf_sddc",
                    f"non-stdlib runtime import {name!r} in {source.relative_to(ROOT)}",
                )


def verify_mock_scope() -> None:
    from test_support.mock_sddc_manager import MockSddcManager

    with tempfile.TemporaryDirectory() as directory:
        log_path = Path(directory) / "scope.ndjson"
        with MockSddcManager(log_path) as mock:
            try:
                urlopen(mock.url + "/v1/clusters", timeout=2)
            except HTTPError as error:
                check(error.code == 404, "unknown mock operation did not return 404")
            else:
                raise AssertionError("mock served an operation absent from the contract")
        entries = mock.read_log()
        check(len(entries) == 1 and entries[0]["path"] == "/v1/clusters",
              "request log is not readable after mock shutdown")


def verify_client() -> None:
    try:
        from vcf_sddc import (
            SddcManagerClient,
            SddcManagerError,
            TaskFailedError,
            TaskTimeoutError,
        )
    except Exception as error:
        raise AssertionError(f"could not import required public API: {error}") from error

    check(issubclass(TaskFailedError, Exception), "TaskFailedError is not an exception")
    check(issubclass(TaskTimeoutError, Exception), "TaskTimeoutError is not an exception")
    spec = load_json("fixtures/domain_creation_spec.json")

    from test_support.mock_sddc_manager import MockSddcManager

    with tempfile.TemporaryDirectory() as directory:
        log_path = Path(directory) / "requests.ndjson"
        with MockSddcManager(log_path) as mock:
            client = SddcManagerClient(
                mock.url + "/",
                "verifier-token",
                timeout=2.0,
            )
            terminal = client.create_domain_and_wait(
                spec,
                poll_interval=0.0,
                timeout=2.0,
            )
            check(isinstance(terminal, dict), "terminal task must be a JSON object")
            check(terminal.get("id") == "task-domain-001",
                  "combined operation returned the wrong task")
            check(terminal.get("status") == "SUCCESSFUL",
                  "combined operation returned before successful terminal state")

            first = client.list_domains()
            second = client.list_domains()
            expected_names = ["alpha", "Mike", "Zulu"]
            check([item["name"] for item in first] == expected_names,
                  "first collection response was not sorted by name")
            check([item["name"] for item in second] == expected_names,
                  "flipped collection response was not sorted by name")
            check(first == second, "collection output changes with server order")
            check(mock.collection_orders == [
                ["Zulu", "alpha", "Mike"],
                ["Mike", "alpha", "Zulu"],
            ], "mock did not flip element order on every collection response")

            entries_while_running = mock.read_log()
            check(len(entries_while_running) == 6,
                  "unexpected number of client HTTP requests")

        entries = mock.read_log()
        expected_paths = [
            ("POST", "/v1/domains"),
            ("GET", "/v1/tasks/task-domain-001"),
            ("GET", "/v1/tasks/task-domain-001"),
            ("GET", "/v1/tasks/task-domain-001"),
            ("GET", "/v1/domains"),
            ("GET", "/v1/domains"),
        ]
        check([(item["method"], item["path"]) for item in entries] == expected_paths,
              "client did not submit, poll to terminal state, then list as required")
        check(entries[0]["json"] == spec, "domain creation JSON was not preserved")
        for entry in entries:
            headers = entry["headers"]
            check(headers.get("authorization") == "Bearer verifier-token",
                  "missing or incorrect bearer authorization")
            check("application/json" in headers.get("accept", ""),
                  "missing JSON Accept header")
        check("application/json" in entries[0]["headers"].get("content-type", ""),
              "missing JSON Content-Type on POST")

    with tempfile.TemporaryDirectory() as directory:
        log_path = Path(directory) / "failed.ndjson"
        with MockSddcManager(
            log_path,
            task_statuses=("IN_PROGRESS", "FAILED"),
        ) as mock:
            client = SddcManagerClient(mock.url, "verifier-token", timeout=2.0)
            try:
                client.create_domain_and_wait(spec, poll_interval=0.0, timeout=2.0)
            except TaskFailedError as error:
                check(isinstance(error.task, dict), "TaskFailedError.task is missing")
                check(error.task.get("status") == "FAILED",
                      "TaskFailedError does not retain terminal task")
            else:
                raise AssertionError("FAILED task was not raised as TaskFailedError")
        entries = mock.read_log()
        check(
            [(item["method"], item["path"]) for item in entries]
            == [
                ("POST", "/v1/domains"),
                ("GET", "/v1/tasks/task-domain-001"),
                ("GET", "/v1/tasks/task-domain-001"),
            ],
            "failure workflow did not poll until its terminal response",
        )

    with tempfile.TemporaryDirectory() as directory:
        log_path = Path(directory) / "unauthorized.ndjson"
        with MockSddcManager(log_path) as mock:
            client = SddcManagerClient(mock.url, "wrong-token", timeout=2.0)
            try:
                client.list_domains()
            except SddcManagerError as error:
                check(error.method == "GET", "HTTP error lost request method")
                check(error.status == 401, "HTTP error lost status")
                check(error.url.endswith("/v1/domains"), "HTTP error lost URL")
                check(isinstance(error.body, dict), "HTTP error body was not decoded")
            else:
                raise AssertionError("non-2xx response did not raise SddcManagerError")


def main() -> int:
    try:
        verify_protected_contract()
        verify_mock_scope()
        verify_stdlib_only()
        verify_client()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 contract client polls tasks and sorts collection output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
