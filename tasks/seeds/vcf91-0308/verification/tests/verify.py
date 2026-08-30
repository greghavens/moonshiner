#!/usr/bin/env python3
"""Deterministic protected verifier for the VCF Automation action sweep."""

from __future__ import annotations

import base64
import json
import select
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MANIFEST = ROOT / "src" / "VcfAutomation" / "VcfAutomation.psd1"
MODULE = ROOT / "src" / "VcfAutomation" / "VcfAutomation.psm1"
MOCK = HERE / "mock_server.py"
DRIVER = HERE / "run_sweep.ps1"
PAGE_SIZE = 2
DRIVER_TIMEOUT = 30

FAILURES: list[str] = []


class Fatal(Exception):
    pass


def check(condition: bool, message: str) -> bool:
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def fixture_for(scenario: str) -> dict:
    ordered = [
        {
            "id": "dep-alpha-0001",
            "name": "vcfa-alpha-01",
            "projectId": "project-alpha",
            "status": "CREATE_SUCCESSFUL",
            "createdAt": "2026-01-10T08:15:00Z",
            "requestId": "request-alpha-0001",
        },
        {
            "id": "dep-bravo-0002",
            "name": "vcfa-bravo-02",
            "projectId": "project-bravo",
            "status": "CREATE_SUCCESSFUL",
            "createdAt": "2026-02-11T08:15:01Z",
            "requestId": "request-bravo-0002",
        },
        {
            "id": "dep-charlie-0003",
            "name": "vcfa-charlie-03",
            "projectId": "project-charlie",
            "status": "CREATE_SUCCESSFUL",
            "createdAt": "2026-03-12T08:15:02Z",
            "requestId": "request-charlie-0003",
        },
    ]
    fixture = {
        "scenario": scenario,
        "tenant": "org-verifier",
        "orgId": "urn:vcloud:org:verifier",
        "apiToken": "vidb_fixed+part/slash=equals&ampersand",
        "initialAccessToken": "initial.access.token.verifier",
        "refreshedAccessToken": "rotated.access.token.verifier",
        "tokenType": "Bearer",
        "expiresIn": 3600,
        "expireAfterAuthorizedRequests": 3,
        "deployments": [ordered[1], ordered[2], ordered[0]],
        "orderedDeployments": ordered,
        "requestedBy": "svc-sweep-verifier",
        "requestName": "Day-2 action sweep",
        "requestStatus": "INPROGRESS",
        "requestCreatedAt": "2026-08-05T09:30:00.000Z",
        "actionId": "Deployment.PowerOff",
        "reason": "Quarterly maintenance window verifier",
        "inputs": None,
    }
    if scenario == "inputs_without_reason":
        fixture["deployments"] = [ordered[0]]
        fixture["orderedDeployments"] = [ordered[0]]
        fixture["expireAfterAuthorizedRequests"] = 99
        fixture["reason"] = None
        fixture["inputs"] = {"force": True}
    return fixture


def require_prerequisites() -> None:
    for path in (CONTRACT, SOURCES, MANIFEST, MODULE, MOCK, DRIVER):
        if not path.is_file():
            raise Fatal("missing required seed file: %s" % path.relative_to(ROOT))

    try:
        probe = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "if ($PSVersionTable.PSVersion -lt [version]'7.2') { exit 1 }",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Fatal("could not run the required PowerShell 7.2+ executable: %s" % exc) from exc
    if probe.returncode != 0:
        raise Fatal("PowerShell 7.2 or later is required")

    try:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        json.loads(SOURCES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Fatal("protected documentation is not valid JSON: %s" % exc) from exc
    operations = [item.get("operationId") for item in contract.get("operations", [])]
    expected = ["getAccessToken", "getDeployments", "submitDeploymentActionRequest"]
    if operations != expected:
        raise Fatal("contract must name exactly the three task operations in documented order")


def start_mock(workdir: Path, fixture: dict) -> tuple[subprocess.Popen, int, Path]:
    config_path = workdir / "mock_config.json"
    log_path = workdir / "requests.jsonl"
    config_path.write_text(json.dumps(fixture), encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(MOCK), str(CONTRACT), str(config_path), str(log_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    readable, _, _ = select.select([process.stdout], [], [], 10) if process.stdout else ([], [], [])
    if not readable:
        process.kill()
        raise Fatal("mock did not bind within 10 seconds")
    line = process.stdout.readline() if process.stdout else ""
    if not line.strip().isdigit():
        process.kill()
        detail = process.stderr.read() if process.stderr else ""
        raise Fatal("mock did not report a port: %r %s" % (line, detail))
    return process, int(line.strip()), log_path


def run_driver(workdir: Path, fixture: dict, port: int) -> tuple[Path, Path, subprocess.CompletedProcess]:
    result_path = workdir / "result.json"
    error_path = workdir / "error.txt"
    command = [
        "pwsh",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(DRIVER),
        "-ManifestPath",
        str(MANIFEST),
        "-ApiEndpoint",
        "http://127.0.0.1:%d" % port,
        "-Tenant",
        fixture["tenant"],
        "-ApiToken",
        fixture["apiToken"],
        "-AccessToken",
        fixture["initialAccessToken"],
        "-ActionId",
        fixture["actionId"],
        "-PageSize",
        str(PAGE_SIZE),
        "-ResultPath",
        str(result_path),
        "-ErrorPath",
        str(error_path),
    ]
    if fixture["reason"] is not None:
        command.extend(["-Reason", fixture["reason"]])
    if fixture["inputs"] is not None:
        command.extend(
            ["-InputsJson", json.dumps(fixture["inputs"], separators=(",", ":"))]
        )
    completed = subprocess.run(command, capture_output=True, text=True, timeout=DRIVER_TIMEOUT)
    return result_path, error_path, completed


def execute(scenario: str) -> tuple[dict, list[dict], str, str, subprocess.CompletedProcess]:
    fixture = fixture_for(scenario)
    temporary = tempfile.TemporaryDirectory(prefix="vcfa-verify-%s-" % scenario)
    workdir = Path(temporary.name)
    mock, port, log_path = start_mock(workdir, fixture)
    try:
        result_path, error_path, completed = run_driver(workdir, fixture, port)
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.wait(timeout=5)
    entries: list[dict] = []
    if log_path.is_file():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    result_text = result_path.read_text(encoding="utf-8") if result_path.is_file() else ""
    error_text = error_path.read_text(encoding="utf-8") if error_path.is_file() else ""
    temporary.cleanup()
    return fixture, entries, result_text, error_text, completed


def values(entry: dict, header: str) -> list[str]:
    wanted = header.lower()
    return [value for key, value in entry["headers"] if key.lower() == wanted]


def body(entry: dict) -> bytes:
    return base64.b64decode(entry["bodyB64"])


def label(entry: dict) -> str:
    return "request %s (%s %s)" % (entry["seq"], entry["method"], entry["target"])


def bearer(entry: dict, token: str, case: str) -> None:
    check(
        values(entry, "Authorization") == ["Bearer " + token],
        "%s: %s must carry exactly the expected Authorization header" % (case, label(entry)),
    )


def exact_action(entry: dict, deployment: dict, expected_body: bytes, token: str, case: str) -> None:
    check(entry["method"] == "POST", "%s: %s must be POST" % (case, label(entry)))
    check(
        entry["path"] == "/deployment/api/deployments/%s/requests" % deployment["id"],
        "%s: action request order/target is wrong at %s" % (case, label(entry)),
    )
    check(entry["query"] == "", "%s: action requests must have no query" % case)
    bearer(entry, token, case)
    check(
        values(entry, "Content-Type") == ["application/json"],
        "%s: action Content-Type must be exactly application/json" % case,
    )
    check(body(entry) == expected_body, "%s: action JSON bytes are not contract-exact" % case)


def verify_result(text: str, fixture: dict, case: str) -> None:
    for secret in (
        fixture["apiToken"],
        fixture["initialAccessToken"],
        fixture["refreshedAccessToken"],
    ):
        check(secret not in text, "%s: returned objects disclose a token" % case)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        check(False, "%s: result is not JSON: %s" % (case, exc))
        return
    expected_items = fixture["orderedDeployments"]
    if not check(isinstance(payload, list), "%s: result must be a collection" % case):
        return
    if not check(len(payload) == len(expected_items), "%s: wrong result count" % case):
        return
    for index, (got, expected) in enumerate(zip(payload, expected_items)):
        check(isinstance(got, dict), "%s: result[%d] must be an object" % (case, index))
        if not isinstance(got, dict):
            continue
        check(
            set(got) == {"DeploymentId", "DeploymentName", "RequestId", "Status"},
            "%s: result[%d] does not have exactly the required properties" % (case, index),
        )
        check(got.get("DeploymentId") == expected["id"], "%s: wrong DeploymentId" % case)
        check(got.get("DeploymentName") == expected["name"], "%s: results are not name-sorted" % case)
        check(got.get("RequestId") == expected["requestId"], "%s: wrong RequestId" % case)
        check(got.get("Status") == fixture["requestStatus"], "%s: wrong Status" % case)


def verify_happy() -> None:
    case = "expiry_resume"
    fixture, entries, result, error, completed = execute(case)
    if not check(completed.returncode == 0, "%s: sweep failed: %s" % (case, error)):
        return
    if not check(len(entries) == 7, "%s: expected exactly seven requests, saw %d" % (case, len(entries))):
        return
    d0, d1, d2 = fixture["orderedDeployments"]
    initial = fixture["initialAccessToken"]
    rotated = fixture["refreshedAccessToken"]
    expected_body = json.dumps(
        {"actionId": fixture["actionId"], "reason": fixture["reason"]},
        separators=(",", ":"),
    ).encode()

    for index, page in ((0, 0), (1, 1)):
        entry = entries[index]
        check(entry["operationId"] == "getDeployments", "%s: pages must precede actions" % case)
        check(
            entry["target"] == "/deployment/api/deployments?page=%d&size=2" % page,
            "%s: deployment query is not exact or requests a page past last" % case,
        )
        check(entry["status"] == 200, "%s: page request was not accepted" % case)
        bearer(entry, initial, case)
        check(values(entry, "Content-Type") == [], "%s: GET must omit Content-Type" % case)
        check(body(entry) == b"", "%s: GET must be bodyless" % case)

    exact_action(entries[2], d0, expected_body, initial, case)
    check(entries[2]["status"] == 200, "%s: first action must succeed" % case)
    exact_action(entries[3], d1, expected_body, initial, case)
    check(entries[3]["status"] == 401, "%s: second action must encounter expiry" % case)

    refresh = entries[4]
    check(refresh["operationId"] == "getAccessToken", "%s: request 5 must refresh" % case)
    check(refresh["method"] == "POST", "%s: refresh must use POST" % case)
    check(
        refresh["target"] == "/tm/oauth/tenant/org-verifier/token",
        "%s: refresh target must be exact and query-free" % case,
    )
    check(values(refresh, "Authorization") == [], "%s: refresh must omit Authorization" % case)
    check(values(refresh, "Accept") == ["application/json"], "%s: refresh Accept is not exact" % case)
    check(
        values(refresh, "Content-Type") == ["application/x-www-form-urlencoded"],
        "%s: refresh Content-Type is not exact" % case,
    )
    raw = body(refresh).decode("utf-8", "replace")
    pieces = raw.split("&")
    check(len(pieces) == 2, "%s: refresh form must have exactly two members" % case)
    if len(pieces) == 2:
        first = pieces[0].partition("=")
        second = pieces[1].partition("=")
        check(first == ("grant_type", "=", "refresh_token"), "%s: grant_type is not exact" % case)
        check(second[0] == "refresh_token" and second[1] == "=", "%s: refresh_token must be second" % case)
        encoded = second[2]
        check(unquote(encoded) == fixture["apiToken"], "%s: API token encoding changes its value" % case)
        check(not any(char in encoded for char in "+/=&"), "%s: reserved token characters are not encoded" % case)

    exact_action(entries[5], d1, expected_body, rotated, case)
    check(entries[5]["status"] == 200, "%s: interrupted request was not resumed" % case)
    check(
        entries[5]["path"] == entries[3]["path"] and body(entries[5]) == body(entries[3]),
        "%s: resumed action is not byte-identical" % case,
    )
    exact_action(entries[6], d2, expected_body, rotated, case)
    check(entries[6]["status"] == 200, "%s: final action must succeed" % case)
    verify_result(result, fixture, case)


def verify_optional_inputs() -> None:
    case = "inputs_without_reason"
    fixture, entries, result, error, completed = execute(case)
    if not check(completed.returncode == 0, "%s: sweep failed: %s" % (case, error)):
        return
    if not check(len(entries) == 2, "%s: expected one page and one action" % case):
        return
    check(entries[0]["target"] == "/deployment/api/deployments?page=0&size=2", "%s: wrong page target" % case)
    expected = b'{"actionId":"Deployment.PowerOff","inputs":{"force":true}}'
    exact_action(
        entries[1], fixture["orderedDeployments"][0], expected,
        fixture["initialAccessToken"], case,
    )
    check(b"reason" not in body(entries[1]), "%s: unset reason must be absent" % case)
    verify_result(result, fixture, case)


def verify_failure(scenario: str, inspect_log) -> None:
    fixture, entries, result, error, completed = execute(scenario)
    check(completed.returncode != 0, "%s: required terminating error was not raised" % scenario)
    check(bool(error.strip()), "%s: terminating error was not recorded" % scenario)
    for secret in (
        fixture["apiToken"],
        fixture["initialAccessToken"],
        fixture["refreshedAccessToken"],
    ):
        check(secret not in error, "%s: error text discloses a token" % scenario)
    inspect_log(entries)


def main() -> int:
    try:
        require_prerequisites()
        verify_happy()
        verify_optional_inputs()
        verify_failure(
            "page_401",
            lambda entries: (
                check(len(entries) == 1 and entries[0]["status"] == 401, "page_401: page must fail once"),
                check(not any(e["operationId"] == "getAccessToken" for e in entries), "page_401: must not refresh"),
            ),
        )
        verify_failure(
            "action_500",
            lambda entries: (
                check(entries[-1]["status"] == 500, "action_500: expected HTTP 500") if entries else check(False, "action_500: no request logged"),
                check(not any(e["operationId"] == "getAccessToken" for e in entries), "action_500: must not refresh"),
            ),
        )
        verify_failure(
            "second_401",
            lambda entries: check(
                len([e for e in entries if e["operationId"] == "getAccessToken"]) == 1
                and entries[-1]["status"] == 401,
                "second_401: retry once after exactly one refresh, then terminate",
            ),
        )
        verify_failure(
            "malformed_page",
            lambda entries: check(
                len(entries) == 1 and entries[0]["operationId"] == "getDeployments",
                "malformed_page: must reject the page before submitting actions",
            ),
        )
        verify_failure(
            "malformed_deployment",
            lambda entries: check(
                len(entries) == 1 and entries[0]["operationId"] == "getDeployments",
                "malformed_deployment: reject blank deployment fields before submitting actions",
            ),
        )
        verify_failure(
            "malformed_action_id",
            lambda entries: check(
                len(entries) == 3 and entries[-1]["operationId"] == "submitDeploymentActionRequest",
                "malformed_action_id: reject a blank accepted request id before continuing",
            ),
        )
        verify_failure(
            "malformed_action_status",
            lambda entries: check(
                len(entries) == 3 and entries[-1]["operationId"] == "submitDeploymentActionRequest",
                "malformed_action_status: reject a blank accepted request status before continuing",
            ),
        )
        verify_failure(
            "malformed_token",
            lambda entries: check(
                len(entries) == 5 and entries[-1]["operationId"] == "getAccessToken",
                "malformed_token: reject a blank issued token without resending the action",
            ),
        )
        verify_failure(
            "transport_drop",
            lambda entries: check(
                bool(entries) and all(entry["status"] == 0 for entry in entries),
                "transport_drop: must terminate without reaching another operation",
            ),
        )
    except (Fatal, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print("FAIL: verifier infrastructure error: %s" % exc)
        return 1

    if FAILURES:
        print("FAIL: %d check(s) failed." % len(FAILURES))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("PASS: wire contract, ordering, optional members, one-shot refresh, and failures verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
