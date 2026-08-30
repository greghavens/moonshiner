#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from mock_vcf import API_VERSION, CONTRACTED, EXPECTED_CONTRACTED, RequestRecord, start_mock


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "loopback-test-token"


def fail(message: str) -> None:
    raise AssertionError(message)


def run(command: list[str], *, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )
    if expect_success and result.returncode != 0:
        fail(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}\n{result.stderr}")
    if not expect_success and result.returncode == 0:
        fail(f"command unexpectedly succeeded: {' '.join(command)}\n{result.stdout}")
    return result


def assert_lookup_ids(records: list[RequestRecord]) -> None:
    seen_projects: set[str] = set()
    seen_integrations: set[str] = set()
    for record in records:
        if record.method == "GET" and record.path == "/iaas/api/projects" and record.status == 200:
            seen_projects.update(record.returned_ids)
        elif record.method == "GET" and record.path == "/iaas/api/integrations" and record.status == 200:
            seen_integrations.update(record.returned_ids)
        elif record.method == "PATCH":
            project_id = record.path.removeprefix("/iaas/api/projects/")
            from urllib.parse import unquote

            project_id = unquote(project_id)
            integration_id = record.body.get("customProperties", {}).get("integrationId") if isinstance(record.body, dict) else None
            if project_id not in seen_projects:
                fail(f"PATCH used project identifier not returned by an earlier project lookup: {project_id!r}")
            if integration_id not in seen_integrations:
                fail(f"PATCH used integration identifier not returned by an earlier integration lookup: {integration_id!r}")


def assert_common(records: list[RequestRecord]) -> None:
    if any(record.status < 200 or record.status >= 300 for record in records):
        fail(f"client made a rejected request: {records!r}")
    for record in records:
        if record.query.get("apiVersion") != [API_VERSION]:
            fail(f"request did not use pinned apiVersion: {record!r}")
        if record.headers.get("authorization") != f"Bearer {TOKEN}":
            fail(f"request did not use bearer authentication: {record!r}")
        if "application/json" not in record.headers.get("accept", "").lower():
            fail(f"request did not accept JSON: {record!r}")
    assert_lookup_ids(records)


def invoke(build: Path, server: Any, project: str, integration: str, first: str, second: str, *, success: bool = True):
    return run(
        [
            "java",
            "-cp",
            str(build),
            "TestMain",
            server.base_uri,
            TOKEN,
            project,
            integration,
            first,
            second,
        ],
        expect_success=success,
    )


def test_update_then_retry(build: Path) -> None:
    project_name = "Edge O'Brien Ω"
    integration_name = "Ansible Production"
    project_id = "project/lookup-91"
    integration_id = "integration-lookup-91"
    original_properties = {
        "owner": "platform",
        "unicode": "café",
        "enabled": True,
        "retries": 3,
        "nested": {"regions": ["us", "eu"], "nullable": None},
    }
    server, thread = start_mock(
        token=TOKEN,
        projects=[
            {"id": "project-decoy", "name": "Different Project", "customProperties": {}},
            {"id": project_id, "name": project_name, "customProperties": original_properties},
        ],
        integrations=[
            {"id": integration_id, "name": integration_name, "integrationType": "ansible", "integrationProperties": {}},
            {"id": "integration-decoy", "name": "Ansible Staging", "integrationType": "ansible", "integrationProperties": {}},
        ],
    )
    try:
        result = invoke(build, server, project_name, integration_name, "updated", "unchanged")
        if result.stdout.strip() != "updated,unchanged":
            fail(f"unexpected TestMain output: {result.stdout!r}")
        records = list(server.state.requests)
        assert_common(records)
        if [record.method for record in records] != ["GET", "GET", "PATCH", "GET", "GET"]:
            fail(f"unexpected request sequence: {records!r}")
        patches = [record for record in records if record.method == "PATCH"]
        if len(patches) != 1 or server.state.effects != 1:
            fail(f"retry duplicated or omitted the mutation: patches={len(patches)}, effects={server.state.effects}")
        patch = patches[0]
        expected_properties = {**original_properties, "integrationId": integration_id}
        if patch.body != {"name": project_name, "customProperties": expected_properties}:
            fail(f"PATCH did not preserve custom properties and set the lookup integration ID: {patch.body!r}")
        project_filters = [record.query.get("$filter") for record in records if record.path == "/iaas/api/projects"]
        integration_filters = [record.query.get("$filter") for record in records if record.path == "/iaas/api/integrations"]
        if project_filters != [["name eq 'Edge O''Brien Ω'"]] * 2:
            fail(f"project lookup did not use an escaped exact-name filter: {project_filters!r}")
        if integration_filters != [["name eq 'Ansible Production'"]] * 2:
            fail(f"integration lookup did not use an exact-name filter: {integration_filters!r}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_already_linked(build: Path) -> None:
    project_name = "Payments"
    integration_name = "Salt ü"
    integration_id = "integration-existing-91"
    server, thread = start_mock(
        token=TOKEN,
        projects=[
            {
                "id": "project-existing-91",
                "name": project_name,
                "customProperties": {"costCenter": "041", "integrationId": integration_id},
            }
        ],
        integrations=[
            {"id": integration_id, "name": integration_name, "integrationType": "salt", "integrationProperties": {}}
        ],
    )
    try:
        invoke(build, server, project_name, integration_name, "unchanged", "unchanged")
        records = list(server.state.requests)
        assert_common(records)
        if [record.method for record in records] != ["GET", "GET", "GET", "GET"]:
            fail(f"already-linked calls did not perform both required lookups: {records!r}")
        if any(record.method == "PATCH" for record in records) or server.state.effects != 0:
            fail("already-linked project was mutated")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def assert_failed_without_mutation(
    build: Path,
    *,
    projects: list[dict[str, Any]],
    integrations: list[dict[str, Any]],
    project_name: str,
    integration_name: str,
    forced_statuses: dict[tuple[str, str], int] | None = None,
) -> list[RequestRecord]:
    server, thread = start_mock(
        token=TOKEN,
        projects=projects,
        integrations=integrations,
        forced_statuses=forced_statuses,
    )
    try:
        result = invoke(build, server, project_name, integration_name, "<throws>", "unused")
        if result.stdout.strip() != "threw":
            fail(f"failure harness produced unexpected output: {result.stdout!r}")
        records = list(server.state.requests)
        if any(record.method == "PATCH" and 200 <= record.status < 300 for record in records):
            fail(f"failed operation submitted a successful PATCH: {records!r}")
        if server.state.effects != 0:
            fail(f"failed operation mutated project state: {records!r}")
        return records
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_missing_and_ambiguous_lookups_fail_before_mutation(build: Path) -> None:
    integration = {
        "id": "integration-present",
        "name": "Present Integration",
        "integrationType": "vro",
        "integrationProperties": {},
    }
    project = {"id": "project-present", "name": "Present Project", "customProperties": {}}

    records = assert_failed_without_mutation(
        build,
        projects=[],
        integrations=[integration],
        project_name="Missing Project",
        integration_name="Present Integration",
    )
    project_records = [record for record in records if record.path == "/iaas/api/projects"]
    if len(project_records) != 1 or project_records[0].status != 200 or project_records[0].returned_ids:
        fail(f"missing project was not detected from its collection lookup: {records!r}")
    if any(record.method != "GET" for record in records):
        fail(f"missing project caused a non-lookup request: {records!r}")

    records = assert_failed_without_mutation(
        build,
        projects=[project],
        integrations=[],
        project_name="Present Project",
        integration_name="Missing Integration",
    )
    integration_records = [record for record in records if record.path == "/iaas/api/integrations"]
    if len(integration_records) != 1 or integration_records[0].status != 200 or integration_records[0].returned_ids:
        fail(f"missing integration was not detected from its collection lookup: {records!r}")
    if any(record.method != "GET" for record in records):
        fail(f"missing integration caused a non-lookup request: {records!r}")

    records = assert_failed_without_mutation(
        build,
        projects=[
            {"id": "ambiguous-project-a", "name": "Duplicate Project", "customProperties": {}},
            {"id": "ambiguous-project-b", "name": "Duplicate Project", "customProperties": {}},
        ],
        integrations=[integration],
        project_name="Duplicate Project",
        integration_name="Present Integration",
    )
    project_records = [record for record in records if record.path == "/iaas/api/projects"]
    if len(project_records) != 1 or len(project_records[0].returned_ids) != 2:
        fail(f"ambiguous project was not detected from its collection lookup: {records!r}")
    if any(record.method != "GET" for record in records):
        fail(f"ambiguous project caused a non-lookup request: {records!r}")

    duplicate_integration = {
        "name": "Duplicate Integration",
        "integrationType": "vro",
        "integrationProperties": {},
    }
    records = assert_failed_without_mutation(
        build,
        projects=[project],
        integrations=[
            {**duplicate_integration, "id": "ambiguous-integration-a"},
            {**duplicate_integration, "id": "ambiguous-integration-b"},
        ],
        project_name="Present Project",
        integration_name="Duplicate Integration",
    )
    integration_records = [record for record in records if record.path == "/iaas/api/integrations"]
    if len(integration_records) != 1 or len(integration_records[0].returned_ids) != 2:
        fail(f"ambiguous integration was not detected from its collection lookup: {records!r}")
    if any(record.method != "GET" for record in records):
        fail(f"ambiguous integration caused a non-lookup request: {records!r}")


def test_http_failures_do_not_mutate(build: Path) -> None:
    project = {"id": "failure-project", "name": "Failure Project", "customProperties": {"keep": "yes"}}
    integration = {
        "id": "failure-integration",
        "name": "Failure Integration",
        "integrationType": "vro",
        "integrationProperties": {},
    }

    records = assert_failed_without_mutation(
        build,
        projects=[project],
        integrations=[integration],
        project_name="Failure Project",
        integration_name="Failure Integration",
        forced_statuses={("GET", "/iaas/api/projects"): 503},
    )
    project_failures = [
        record for record in records if record.path == "/iaas/api/projects" and record.status == 503
    ]
    if len(project_failures) != 1 or any(record.method != "GET" for record in records):
        fail(f"project lookup failure caused an invalid request flow: {records!r}")

    records = assert_failed_without_mutation(
        build,
        projects=[project],
        integrations=[integration],
        project_name="Failure Project",
        integration_name="Failure Integration",
        forced_statuses={("GET", "/iaas/api/integrations"): 503},
    )
    integration_failures = [
        record for record in records if record.path == "/iaas/api/integrations" and record.status == 503
    ]
    if len(integration_failures) != 1 or any(record.method != "GET" for record in records):
        fail(f"integration lookup failure caused an invalid request flow: {records!r}")

    records = assert_failed_without_mutation(
        build,
        projects=[project],
        integrations=[integration],
        project_name="Failure Project",
        integration_name="Failure Integration",
        forced_statuses={("PATCH", "/iaas/api/projects/failure-project"): 503},
    )
    if sorted((record.method, record.path, record.status) for record in records) != sorted(
        [
            ("GET", "/iaas/api/projects", 200),
            ("GET", "/iaas/api/integrations", 200),
            ("PATCH", "/iaas/api/projects/failure-project", 503),
        ]
    ):
        fail(f"project update failure used an unexpected request flow: {records!r}")


def validate_docs() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    if contract.get("contractKind") != "reference-documentation-derived":
        fail("contract must state that it is reference-documentation-derived")
    notice = contract.get("sourceNotice", "").lower()
    if "not from a published api specification" not in notice:
        fail("contract source notice does not distinguish reference documentation from a published specification")
    operations = {item["operation"] for item in contract.get("operations", [])}
    source_operations = {item["operation"] for item in sources.get("sources", [])}
    if operations != source_operations or CONTRACTED != EXPECTED_CONTRACTED:
        fail("official source records and mock routes must exactly match the named contract operations")
    for source in sources.get("sources", []):
        if not source.get("url", "").startswith("https://developer.broadcom.com/xapis/"):
            fail(f"source is not an official Broadcom xAPIs page: {source!r}")
        if source.get("dateFetched") != sources.get("fetchedDate"):
            fail(f"source fetch date is missing or inconsistent: {source!r}")


def main() -> int:
    validate_docs()
    with tempfile.TemporaryDirectory(prefix="vcf91-0350-") as output:
        build = Path(output)
        run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                str(build),
                str(ROOT / "src" / "VcfAutomationClient.java"),
                str(ROOT / "tests" / "TestMain.java"),
            ]
        )
        test_update_then_retry(build)
        test_already_linked(build)
        test_missing_and_ambiguous_lookups_fail_before_mutation(build)
        test_http_failures_do_not_mutate(build)
    print("PASS: VCF Automation project-integration client")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
