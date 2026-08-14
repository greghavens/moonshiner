#!/usr/bin/env python3
"""Protected, deterministic acceptance verifier for vcf90-0121."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from mock_vsan import ContractMock, DEFAULT_RECORDS


ROOT = Path(__file__).resolve().parent
OPERATION_ID = "Snapservice.Reports.Clusters.ProtectionGroups.Snapshots_list"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
TAG_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
OPERATION_PATH = "/snapservice/reports/clusters/{cluster}/protection-groups/snapshots"


EXPECTED_CONTRACT = {
    "openapi": "3.0.3",
    "info": {"title": "Snapshot Appliance API", "version": "9.0.0.0"},
    "server": {"base_path": "/api"},
    "operations": [
        {
            "operationId": OPERATION_ID,
            "method": "GET",
            "path": OPERATION_PATH,
            "security": {
                "type": "apiKey",
                "in": "header",
                "name": "vmware-api-session-id",
            },
            "parameters": [
                {
                    "name": "cluster",
                    "in": "path",
                    "required": True,
                    "type": "string",
                },
                {
                    "name": "filter",
                    "in": "query",
                    "required": False,
                    "style": "form",
                    "explode": True,
                    "properties": {
                        "start_time": {"type": "string", "format": "date-time"},
                        "end_time": {"type": "string", "format": "date-time"},
                    },
                },
                {
                    "name": "pgs",
                    "in": "query",
                    "required": False,
                    "style": "form",
                    "explode": True,
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                {
                    "name": "iterate",
                    "in": "query",
                    "required": False,
                    "style": "form",
                    "explode": True,
                    "properties": {
                        "page_size": {"type": "integer", "format": "int64"},
                        "offset": {"type": "integer", "format": "int64"},
                    },
                },
            ],
            "response": {
                "status": 200,
                "required": ["snapshots", "total_count"],
                "items_field": "snapshots",
                "total_field": "total_count",
                "stable_primary_order": "creation_time",
            },
        }
    ],
}


EXPECTED_SOURCES = {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "license": "Apache-2.0",
    "tag": "9.0.0.0",
    "tag_commit_sha": TAG_COMMIT,
    "spec_path": SPEC_PATH,
    "source_url": (
        "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
        f"{TAG_COMMIT}/{SPEC_PATH}"
    ),
    "operationIds": [OPERATION_ID],
}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def load_json(relative):
    path = ROOT / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AssertionError(f"missing {relative}") from error
    except json.JSONDecodeError as error:
        raise AssertionError(f"invalid JSON in {relative}: {error}") from error


def verify_documents():
    contract = load_json("docs/contract.json")
    sources = load_json("docs/official_sources.json")
    check(contract == EXPECTED_CONTRACT, "docs/contract.json is not the exact 9.0 spec-derived subset")
    check(sources == EXPECTED_SOURCES, "docs/official_sources.json has incorrect provenance")
    check("9.1" not in json.dumps(contract) + json.dumps(sources), "9.1 material is not allowed")


def verify_stdlib_only():
    package = ROOT / "vsan_dp"
    check(package.is_dir(), "vsan_dp package is missing")
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            root = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    check(
                        root in sys.stdlib_module_names or root == "vsan_dp",
                        f"non-stdlib import {alias.name!r} in {path.relative_to(ROOT)}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                root = node.module.split(".", 1)[0]
                check(
                    root in sys.stdlib_module_names or root == "vsan_dp",
                    f"non-stdlib import {node.module!r} in {path.relative_to(ROOT)}",
                )


def stable_records(records):
    return sorted(
        (dict(record) for record in records),
        key=lambda item: (
            item["creation_time"],
            item["pg"],
            item["name"],
            item.get("snapshot") or "",
        ),
    )


def expected_entry(path, pairs, session_id):
    query = urlencode(pairs, doseq=True)
    raw_target = path + (f"?{query}" if query else "")
    return {
        "method": "GET",
        "raw_target": raw_target,
        "path": path,
        "query": list(pairs),
        "session_id": session_id,
        "accept": "application/json",
        "content_length": 0,
        "body": b"",
    }


def verify_mock_scope():
    with ContractMock(ROOT / "docs/contract.json") as service:
        check(service.served_operation_ids == (OPERATION_ID,), "mock exposed unexpected operations")
        try:
            urlopen(service.base_url + "/not-in-contract", timeout=3)
        except HTTPError as error:
            check(error.code == 404, "unknown mock operation did not return 404")
        else:
            raise AssertionError("mock served an operation absent from the contract")


def verify_default_wire_and_pagination(client_type):
    cluster = "cluster east"
    session_id = "session-default"
    encoded_path = "/api" + OPERATION_PATH.replace("{cluster}", quote(cluster, safe=""))
    with ContractMock(ROOT / "docs/contract.json") as service:
        client = client_type(service.base_url, session_id, timeout=3)
        records = client.list_protection_group_snapshots(cluster)
        check(records == stable_records(DEFAULT_RECORDS), "collection is incomplete or unstably ordered")
        expected = [
            expected_entry(encoded_path, [], session_id),
            expected_entry(encoded_path, [("offset", "2")], session_id),
            expected_entry(encoded_path, [("offset", "4")], session_id),
            expected_entry(encoded_path, [("offset", "6")], session_id),
        ]
        check(service.request_log == expected, f"default request wire shape was {service.request_log!r}")
        check(
            all(value != "" for entry in service.request_log for _, value in entry["query"]),
            "unset optional query fields were sent empty",
        )


def verify_explicit_wire_and_path_encoding(client_type):
    cluster = "cluster/one east"
    session_id = "session-explicit"
    encoded_path = "/api" + OPERATION_PATH.replace("{cluster}", quote(cluster, safe=""))
    common = [
        ("start_time", "2026-01-01T00:00:00Z"),
        ("end_time", "2026-02-01T00:00:00Z"),
        ("pgs", "pg/z"),
        ("pgs", "pg alpha"),
        ("page_size", "3"),
    ]
    with ContractMock(ROOT / "docs/contract.json") as service:
        client = client_type(service.base_url + "/", session_id, timeout=3)
        records = client.list_protection_group_snapshots(
            cluster,
            page_size=3,
            pgs=["pg/z", "pg alpha"],
            start_time="2026-01-01T00:00:00Z",
            end_time="2026-02-01T00:00:00Z",
        )
        check(records == stable_records(DEFAULT_RECORDS), "explicit-query collection was incomplete")
        expected = [
            expected_entry(encoded_path, common, session_id),
            expected_entry(encoded_path, common + [("offset", "2")], session_id),
            expected_entry(encoded_path, common + [("offset", "5")], session_id),
        ]
        check(service.request_log == expected, f"explicit request wire shape was {service.request_log!r}")


def main():
    verify_documents()
    verify_stdlib_only()
    from vsan_dp import VsanDataProtectionClient

    verify_mock_scope()
    verify_default_wire_and_pagination(VsanDataProtectionClient)
    verify_explicit_wire_and_path_encoding(VsanDataProtectionClient)
    print("vcf90-0121 verification passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise
