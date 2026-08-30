#!/usr/bin/env python3
"""Protected acceptance verifier for the VCF Operations for Logs Java client."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
OPERATION_IDS = ["POST_sessions", "GET_events-+path"]


def normalize_percent_escapes(value: str) -> str:
    return re.sub(r"%[0-9a-fA-F]{2}", lambda match: match.group(0).upper(), value)


def assert_contract_provenance() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))

    assert contract["openapi"] == "3.0.1"
    assert contract["info"] == {"title": "VCF Operations for Logs", "version": "v2"}
    assert contract["serverBasePath"] == "/api/v2"
    assert contract["securitySchemes"] == {
        "Bearer": {"type": "http", "scheme": "Bearer", "header": "Authorization"}
    }
    assert [item["operationId"] for item in contract["operations"]] == OPERATION_IDS

    sessions, events = contract["operations"]
    assert (sessions["method"], sessions["path"]) == ("POST", "/sessions")
    assert sessions["requestBody"]["contentType"] == "application/json"
    assert sessions["requestBody"]["schema"]["required"] == [
        "password",
        "provider",
        "username",
    ]
    assert sessions["requestBody"]["schema"]["properties"]["provider"]["enum"] == [
        "Local",
        "ActiveDirectory",
        "vIDM",
    ]
    assert sessions["responses"]["200"]["schema"]["required"] == [
        "userId",
        "sessionId",
        "ttl",
    ]

    assert (events["method"], events["path"]) == ("GET", "/events/{+path}")
    assert events["security"] == ["Bearer"]
    parameters = events["parameters"]
    assert [(item["name"], item["in"], item["required"]) for item in parameters] == [
        ("+path", "path", True),
        ("limit", "query", False),
        ("timeout", "query", False),
        ("view", "query", False),
        ("content-pack-fields", "query", False),
        ("order-by-direction", "query", False),
    ]
    assert parameters[1]["schema"] == {"type": "integer", "default": 100}
    assert parameters[2]["schema"] == {"type": "integer", "default": 30000}
    assert parameters[3]["schema"]["enum"] == ["DEFAULT", "SIMPLE"]
    assert parameters[4]["repeatable"] is True
    assert parameters[5]["schema"]["enum"] == ["ASC", "DESC"]
    assert set(events["responses"]) == {"200", "401", "440"}
    assert events["responses"]["440"]["schema"] == {"type": "string"}

    assert sources["repository"] == "https://github.com/vmware/vcf-api-specs"
    assert sources["license"] == "Apache-2.0"
    assert sources["specification"] == {
        "path": SPEC_PATH,
        "tag": "9.0.0.0",
        "commitSha": SPEC_COMMIT,
        "rawUrl": (
            "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
            + SPEC_COMMIT
            + "/"
            + SPEC_PATH
        ),
    }
    assert sources["operations"] == [
        {"operationId": "POST_sessions", "jsonPointer": "/paths/~1sessions/post"},
        {
            "operationId": "GET_events-+path",
            "jsonPointer": "/paths/~1events~1{+path}/get",
        },
    ]


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"mock exited early\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists() and port_file.stat().st_size:
            return int(port_file.read_text(encoding="ascii"))
        time.sleep(0.02)
    raise AssertionError("mock did not publish its loopback port")


def assert_request_log(request_log: Path) -> None:
    entries = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 5, entries
    assert [entry["sequence"] for entry in entries] == [1, 2, 3, 4, 5]
    assert [entry["method"] for entry in entries] == ["POST", "GET", "GET", "POST", "GET"]

    login_body = {
        "username": "svc-logs",
        "password": "p@ss\"word\\9",
        "provider": "Local",
    }
    for index in (0, 3):
        request = entries[index]
        assert request["target"] == "/api/v2/sessions"
        assert request["path"] == "/api/v2/sessions"
        assert request["query"] == ""
        parsed_body = json.loads(request["body"])
        assert {key: parsed_body.get(key) for key in login_body} == login_body
        content_type = request["headers"].get("content-type", "")
        assert content_type.partition(";")[0].strip().lower() == "application/json"
        assert "authorization" not in request["headers"]

    first_path = (
        "/api/v2/events/text/CONTAINS%20alpha%2Bsnow%20%E9%9B%AA%25"
        "/timestamp/LAST%2060000"
    )
    second_target = "/api/v2/events/text/CONTAINS%20beta/timestamp/LAST%2060000"
    assert normalize_percent_escapes(entries[1]["path"]) == first_path
    assert parse_qs(
        entries[1]["query"], keep_blank_values=True, strict_parsing=True
    ) == {
        "limit": ["2"],
        "timeout": ["2500"],
        "view": ["DEFAULT"],
        "content-pack-fields": ["core fields", "ops/fields+雪?"],
        "order-by-direction": ["ASC"],
    }
    assert entries[2]["target"] == second_target
    assert entries[4]["target"] == second_target
    assert entries[2]["query"] == ""
    assert entries[4]["query"] == ""

    assert entries[1]["headers"].get("authorization") == "Bearer session-1-token"
    assert entries[2]["headers"].get("authorization") == "Bearer session-1-token"
    assert entries[4]["headers"].get("authorization") == "Bearer session-2-token"
    for index in (1, 2, 4):
        request = entries[index]
        assert request["body"] == ""
        target = request["target"]
        if index != 1:
            assert "timeout=" not in target
        assert not any(
            marker in target
            for marker in (
                "limit=&",
                "timeout=&",
                "view=&",
                "content-pack-fields=&",
                "order-by-direction=&",
            )
        )


def run_acceptance() -> None:
    assert_contract_provenance()
    with tempfile.TemporaryDirectory(prefix="vcf90-0097-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        source_path = temp / "empty-source-path"
        source_path.mkdir()
        request_log = temp / "requests.jsonl"
        port_file = temp / "port"

        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-classpath",
                str(classes),
                "-sourcepath",
                str(source_path),
                "-d",
                str(classes),
                str(ROOT / "src/VcfOperationsLogsClient.java"),
                str(ROOT / "src/TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if compile_result.returncode:
            raise AssertionError(
                "javac failed\n" + compile_result.stdout + compile_result.stderr
            )

        mock = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "test_support/mock_vcf_logs.py"),
                "--contract",
                str(ROOT / "docs/contract.json"),
                "--request-log",
                str(request_log),
                "--port-file",
                str(port_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(port_file, mock)
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{port}",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
            )
            if run_result.returncode:
                raise AssertionError(
                    "TestMain failed\n" + run_result.stdout + run_result.stderr
                )
            assert run_result.stdout.strip() == "TestMain OK"
            assert_request_log(request_log)
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)


if __name__ == "__main__":
    run_acceptance()
    print("verification OK")
