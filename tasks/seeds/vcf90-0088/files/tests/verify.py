#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF Logs client task."""

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mock_vcf_logs import EVENTS, TIED_EVENTS, run_mock  # noqa: E402


EXPECTED_CONTRACT_SHA256 = (
    "9904b504d3193f7eeaf642f5e8443a0fd678c7a5eccf1a487c60449e23b46d3a"
)
EXPECTED_SOURCES_SHA256 = (
    "3d02941c1824e7c767e8ffda3609afa513d149af0c8171e5179fb248bc32dfda"
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_pinned_documents():
    contract_path = ROOT / "docs" / "contract.json"
    sources_path = ROOT / "docs" / "official_sources.json"
    require(
        hashlib.sha256(contract_path.read_bytes()).hexdigest()
        == EXPECTED_CONTRACT_SHA256,
        "docs/contract.json differs from the protected VCF 9.0 projection",
    )
    require(
        hashlib.sha256(sources_path.read_bytes()).hexdigest() == EXPECTED_SOURCES_SHA256,
        "docs/official_sources.json differs from the protected source record",
    )
    contract = json.loads(contract_path.read_text())
    sources = json.loads(sources_path.read_text())
    expected_ids = ["POST_sessions", "GET_events-+path"]
    require(contract["derived_from"]["tag"] == "9.0.0.0", "wrong spec tag")
    require(
        contract["derived_from"]["commit_sha"]
        == "85151f6b1bb58f13b6ac0304bfec53904bea085f",
        "wrong tag commit",
    )
    require(list(contract["operations"]) == expected_ids, "wrong contract operations")
    require(
        [item["operationId"] for item in sources["operations"]] == expected_ids,
        "official source record must name every contract operationId",
    )


def verify_stdlib_only():
    allowed = set(sys.stdlib_module_names) | {"vcf_logs"}
    for path in sorted((ROOT / "vcf_logs").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                require(name in allowed, f"non-stdlib import {name!r} in {path.name}")


def verify_implemented():
    production = "\n".join(
        path.read_text() for path in sorted((ROOT / "vcf_logs").glob("*.py"))
    )
    require(
        "NotImplementedError" not in production,
        "vcf_logs still contains its baseline implementation placeholders",
    )


def expected_get_paths(page_size=2):
    cursors = [1700000000000, 1700000002000, 1700000004000]
    return [
        f"/api/v2/events/timestamp/GT%20{cursor}?limit={page_size}&order-by-direction=ASC"
        for cursor in cursors
    ]


def verify_wire(log, *, optional=False, provider="Local"):
    require(len(log) == 4, f"expected one session and three event requests, got {len(log)}")
    session = log[0]
    require(session["method"] == "POST", "session request must use POST")
    require(session["raw_path"] == "/api/v2/sessions", "wrong session path")
    require(
        session["headers"].get("content-type", "").split(";", 1)[0].strip().lower()
        == "application/json",
        "session Content-Type must be application/json",
    )
    require(
        session["headers"].get("accept") == "application/json",
        "session Accept must be application/json",
    )
    require(
        json.loads(session["body"])
        == {
            "username": "analyst",
            "password": "fixture-secret",
            "provider": provider,
        },
        "session JSON wire fields differ from the contract",
    )

    for index, record in enumerate(log[1:]):
        require(record["method"] == "GET", "event request must use GET")
        require(record["body"] == b"", "GET requests must not send a body")
        require(
            record["headers"].get("authorization")
            == "Bearer fixture-session-token",
            "wrong Bearer authorization header",
        )
        require(
            record["headers"].get("accept") == "application/json",
            "event Accept must be application/json",
        )
        split = urlsplit(record["raw_path"])
        expected_split = urlsplit(expected_get_paths()[index])
        require(split.path == expected_split.path, f"wrong page path: {split.path}")
        pairs = parse_qsl(split.query, keep_blank_values=True)
        if optional:
            expected_pairs = [
                ("limit", "2"),
                ("order-by-direction", "ASC"),
                ("timeout", "45000"),
                ("view", "DEFAULT"),
                ("content-pack-fields", "ops pack"),
                ("content-pack-fields", "audit"),
            ]
            require(
                sorted(pairs) == sorted(expected_pairs),
                f"wrong optional query wire shape: {pairs}",
            )
        else:
            require(
                sorted(pairs)
                == sorted([("limit", "2"), ("order-by-direction", "ASC")]),
                f"unset optional query fields were not omitted: {pairs}",
            )
        require(all(value != "" for _name, value in pairs), "empty query value sent")


def new_client(base_url):
    from vcf_logs import VCFLogsClient

    return VCFLogsClient(
        base_url,
        "analyst",
        "fixture-secret",
        provider="Local",
    )


def verify_client_and_omissions():
    with run_mock() as mock:
        events = new_client(mock.base_url).list_events(
            since_timestamp=1700000000000,
            page_size=2,
            timeout=None,
            view=None,
            content_pack_fields=[],
        )
        require(events == EVENTS, "client did not return every event in stable order")
        verify_wire(mock.request_log)


def verify_optional_query_fields():
    with run_mock() as mock:
        events = new_client(mock.base_url).list_events(
            since_timestamp=1700000000000,
            page_size=2,
            timeout=45000,
            view="DEFAULT",
            content_pack_fields=["ops pack", "audit"],
        )
        require(events == EVENTS, "optional fields changed pagination results")
        verify_wire(mock.request_log, optional=True)


def verify_stable_secondary_order():
    with run_mock(events=TIED_EVENTS) as mock:
        events = new_client(mock.base_url).list_events(
            since_timestamp=1700000000000,
            page_size=3,
        )
        expected = sorted(TIED_EVENTS, key=lambda event: (event["timestamp"], event["text"]))
        require(events == expected, "events are not in stable (timestamp, text) order")
        require(
            [urlsplit(record["raw_path"]).path for record in mock.request_log[1:]]
            == [
                "/api/v2/events/timestamp/GT%201700000000000",
                "/api/v2/events/timestamp/GT%201700000002000",
            ],
            "tied-timestamp fixture did not exercise the expected safe page boundary",
        )


def verify_incomplete_rejected():
    with run_mock(incomplete_cursor=1700000000000) as mock:
        try:
            new_client(mock.base_url).list_events(
                since_timestamp=1700000000000, page_size=2
            )
        except Exception:
            pass
        else:
            raise AssertionError("client emitted an incomplete API result")
        require(len(mock.request_log) == 2, "client continued after incomplete response")


def verify_cli():
    with run_mock(provider="ActiveDirectory") as mock:
        command = [
            sys.executable,
            "-m",
            "vcf_logs",
            "--base-url",
            mock.base_url,
            "--username",
            "analyst",
            "--password",
            "fixture-secret",
            "--provider",
            "ActiveDirectory",
            "--since",
            "1700000000000",
            "--page-size",
            "2",
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        require(result.returncode == 0, f"CLI failed: {result.stderr}")
        expected = "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            for event in EVENTS
        )
        require(result.stdout == expected, "CLI output is not stable compact JSON Lines")
        verify_wire(mock.request_log, provider="ActiveDirectory")


def main():
    verify_pinned_documents()
    verify_stdlib_only()
    verify_implemented()
    verify_client_and_omissions()
    verify_optional_query_fields()
    verify_stable_secondary_order()
    verify_incomplete_rejected()
    verify_cli()
    print("verification passed")


if __name__ == "__main__":
    main()
