#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src" / "VksFailureEvidenceClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tools" / "contract_mock.py"

PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_OPERATION = "Vcenter.Namespaces.User.Instances_list"
EVENTS_OPERATION = "core/v1:namespaced-events:list"
LOG_OPERATION = "core/v1:namespaced-pod-log:read"

# verify.py is protected by the harness. These hashes protect every other fixture.
PROTECTED_SHA256 = {
    "tests/TestMain.java": "4917bc9df91d2cc14c57514801a9d9d32fa0bbefad36690dfaa511e5266e540b",
    "tools/contract_mock.py": "2b3b8ea536d973ac6d45fce4b2638406ac1fbe473ba4a9e6c50f28eb996a074b",
    "docs/contract.json": "b473ed9eb2f66656dccfaa086c3cf0eb665a07e02e22a2b0c6be789cee97734b",
    "docs/official_sources.json": "a63b66713359cf62a08b33af023e7d7d822e8dd600430d99c9817f5d0a3837ec",
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
    for item in (contract.get("source", {}), sources):
        require(
            item.get("repositoryCommitSha") == PINNED_COMMIT,
            "wrong source commit",
        )
        require(item.get("specBlobSha") == PINNED_BLOB, "wrong spec blob")
        require(item.get("specPath") == PINNED_SPEC, "wrong spec path")
        require(item.get("license") == "Apache-2.0", "wrong source license")

    require(
        sources.get("operationIds") == [VCENTER_OPERATION],
        "official operationId list changed",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and len(source_operations) == 1
        and source_operations[0].get("operationId") == VCENTER_OPERATION
        and source_operations[0].get("method") == "GET"
        and source_operations[0].get("path")
        == "/vcenter/namespaces-user/namespaces",
        "official operation projection changed",
    )

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations missing")
    require(
        [item.get("contractName") for item in operations]
        == [
            "listAuthorizedSupervisorNamespaces",
            "listPodWarningEvents",
            "readPreviousPodLog",
        ],
        "contract route allow-list changed",
    )
    vcenter, events, log = operations
    require(
        vcenter.get("sourceKind") == "vcenter-openapi-operation"
        and vcenter.get("operationId") == VCENTER_OPERATION
        and vcenter.get("method") == "GET"
        and vcenter.get("specPathItem")
        == "/vcenter/namespaces-user/namespaces"
        and vcenter.get("pathTemplate")
        == "/api/vcenter/namespaces-user/namespaces"
        and vcenter.get("successStatus") == 200,
        "vCenter contract changed",
    )
    require(
        [item.get("name") for item in vcenter.get("queryParameters", [])]
        == ["filter", "groups"]
        and all(
            item.get("required") is False
            and item.get("omitWhenUnset") is True
            for item in vcenter["queryParameters"]
        ),
        "vCenter optional omission contract changed",
    )
    require(
        events.get("sourceKind") == "native-kubernetes-api"
        and "operationId" not in events
        and events.get("operationKey") == EVENTS_OPERATION
        and events.get("method") == "GET"
        and events.get("pathTemplate")
        == "/api/v1/namespaces/{namespace}/events"
        and events.get("setQueryFields") == ["fieldSelector", "limit"]
        and events.get("successStatus") == 200,
        "EventList contract changed",
    )
    require(
        log.get("sourceKind") == "native-kubernetes-api"
        and "operationId" not in log
        and log.get("operationKey") == LOG_OPERATION
        and log.get("method") == "GET"
        and log.get("pathTemplate")
        == "/api/v1/namespaces/{namespace}/pods/{pod}/log"
        and log.get("setQueryFields")
        == ["container", "previous", "tailLines", "timestamps"]
        and log.get("successStatus") == 200,
        "Pod log contract changed",
    )
    require(
        all(
            item.get("sourceKind") != "native-kubernetes-api"
            or "operationId" not in item
            for item in operations
        ),
        "fictional Kubernetes operationId",
    )

    schemas = contract.get("schemas", {})
    summary = schemas.get("Vcenter.Namespaces.User.Instances.Summary", {})
    require(
        summary.get("required") == ["master_host", "namespace"],
        "namespace Summary projection changed",
    )
    filter_spec = schemas.get(
        "Vcenter.Namespaces.User.Instances.FilterSpec", {}
    )
    require(
        filter_spec.get("required") == []
        and filter_spec.get("properties", {})
        .get("username", {})
        .get("omitWhenUnset")
        is True,
        "FilterSpec projection changed",
    )


def encode_component(value: str) -> str:
    return urllib.parse.quote(
        value, safe="-._~", encoding="utf-8", errors="strict"
    )


def fixture_values(scenario: str) -> dict[str, str]:
    suffix = secrets.token_hex(6)
    return {
        "scenario": scenario,
        "vcenterSession": "vc_" + secrets.token_urlsafe(18),
        "bearerToken": "k8s_" + secrets.token_urlsafe(20),
        "serverBodyMarker": "body_" + secrets.token_urlsafe(15),
        "supervisorNamespace": "team-" + suffix,
        "otherSupervisorNamespace": "other-" + suffix,
        "supervisorMasterHost": "sup-" + suffix + ".example.test:6443",
        "workloadNamespace": "checkout-" + suffix,
        "podName": "checkout-api-" + suffix,
        "otherPodName": "other-api-" + suffix,
        "containerName": "checkout",
        "upstreamHost": "inventory-" + suffix + ".svc.cluster.local",
    }


def write_fixture(path: Path, values: dict[str, str]) -> None:
    require(
        all(
            "\n" not in key
            and "\r" not in key
            and "=" not in key
            and "\n" not in value
            and "\r" not in value
            for key, value in values.items()
        ),
        "unsafe generated fixture",
    )
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
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


def assert_bodyless(entry: dict[str, object]) -> None:
    require(body_bytes(entry) == b"", "request body was not empty")
    for name in ("Content-Type", "Content-Length", "Transfer-Encoding"):
        require(not header_values(entry, name), f"unexpected {name}")


def assert_vcenter_wire(
        entry: dict[str, object], values: dict[str, str]
) -> None:
    require(
        entry["operation"] == "listAuthorizedSupervisorNamespaces",
        "vCenter route mismatch",
    )
    require(entry["method"] == "GET", "vCenter method mismatch")
    require(
        entry["rawTarget"]
        == "/api/vcenter/namespaces-user/namespaces",
        "vCenter raw target mismatch",
    )
    require(
        header_values(entry, "Accept") == ["application/json"],
        "vCenter Accept mismatch",
    )
    require(
        header_values(entry, "vmware-api-session-id")
        == [values["vcenterSession"]],
        "vCenter session mismatch",
    )
    require(
        not header_values(entry, "Authorization"),
        "Kubernetes credential crossed into vCenter",
    )
    require("?" not in entry["rawTarget"], "unset vCenter filter serialized")
    assert_bodyless(entry)


def assert_events_wire(
        entry: dict[str, object], values: dict[str, str]
) -> None:
    selector = (
        "involvedObject.kind=Pod,"
        "involvedObject.name="
        + values["podName"]
        + ",type=Warning"
    )
    expected = (
        "/api/v1/namespaces/"
        + encode_component(values["workloadNamespace"])
        + "/events?fieldSelector="
        + encode_component(selector)
        + "&limit=50"
    )
    require(
        entry["operation"] == "listPodWarningEvents",
        "events route mismatch",
    )
    require(entry["method"] == "GET", "events method mismatch")
    require(entry["rawTarget"] == expected, "events raw target mismatch")
    require(
        header_values(entry, "Accept") == ["application/json"],
        "events Accept mismatch",
    )
    require(
        header_values(entry, "Authorization")
        == ["Bearer " + values["bearerToken"]],
        "events bearer mismatch",
    )
    require(
        not header_values(entry, "vmware-api-session-id"),
        "vCenter credential crossed into events",
    )
    query = urllib.parse.urlsplit(entry["rawTarget"]).query
    require(query and not query.endswith("&"), "events query shape invalid")
    parsed = urllib.parse.parse_qs(
        query, keep_blank_values=True, strict_parsing=True
    )
    require(
        list(parsed) == ["fieldSelector", "limit"]
        and parsed["fieldSelector"] == [selector]
        and parsed["limit"] == ["50"],
        "events optional fields or values changed",
    )
    assert_bodyless(entry)


def assert_log_wire(
        entry: dict[str, object], values: dict[str, str]
) -> None:
    expected = (
        "/api/v1/namespaces/"
        + encode_component(values["workloadNamespace"])
        + "/pods/"
        + encode_component(values["podName"])
        + "/log?container="
        + encode_component(values["containerName"])
        + "&previous=true&tailLines=200&timestamps=true"
    )
    require(
        entry["operation"] == "readPreviousPodLog",
        "log route mismatch",
    )
    require(entry["method"] == "GET", "log method mismatch")
    require(entry["rawTarget"] == expected, "log raw target mismatch")
    require(
        header_values(entry, "Accept") == ["text/plain"],
        "log Accept mismatch",
    )
    require(
        header_values(entry, "Authorization")
        == ["Bearer " + values["bearerToken"]],
        "log bearer mismatch",
    )
    require(
        not header_values(entry, "vmware-api-session-id"),
        "vCenter credential crossed into log request",
    )
    query = urllib.parse.urlsplit(entry["rawTarget"]).query
    parsed = urllib.parse.parse_qs(
        query, keep_blank_values=True, strict_parsing=True
    )
    require(
        list(parsed)
        == ["container", "previous", "tailLines", "timestamps"]
        and parsed["container"] == [values["containerName"]]
        and parsed["previous"] == ["true"]
        and parsed["tailLines"] == ["200"]
        and parsed["timestamps"] == ["true"]
        and all(items != [""] for items in parsed.values()),
        "log optional fields or values changed",
    )
    assert_bodyless(entry)


def load_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def verify_log(
        scenario: str,
        entries: list[dict[str, object]],
        values: dict[str, str],
) -> None:
    expected_count = {
        "correlated": 3,
        "event_only": 3,
        "log_only": 3,
        "unauthorized_namespace": 1,
        "vcenter_failure": 1,
        "bad_events": 2,
        "validation": 0,
    }[scenario]
    require(
        len(entries) == expected_count,
        f"{scenario}: expected {expected_count} requests, got {len(entries)}",
    )
    if not entries:
        return
    assert_vcenter_wire(entries[0], values)
    if len(entries) >= 2:
        assert_events_wire(entries[1], values)
    if len(entries) >= 3:
        assert_log_wire(entries[2], values)
    require(
        all(item["operation"] is not None for item in entries),
        "request escaped contract allow-list",
    )


def start_mock(
        temp: Path, fixture: Path, log: Path, port_file: Path
) -> tuple[subprocess.Popen[str], str | None]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(MOCK),
            "--contract",
            str(CONTRACT),
            "--log",
            str(log),
            "--fixture",
            str(fixture),
            "--port-file",
            str(port_file),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            if (
                "PermissionError" in stderr
                or "Operation not permitted" in stderr
            ):
                return process, None
            raise VerificationError(
                f"mock exited early: {stdout[-400:]} {stderr[-400:]}"
            )
        if port_file.exists():
            value = json.loads(port_file.read_text(encoding="utf-8"))
            port = value.get("port")
            require(
                isinstance(port, int) and 0 < port < 65536,
                "invalid mock port",
            )
            return process, f"http://127.0.0.1:{port}"
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=2)
    raise VerificationError("mock did not publish its port")


def stop_mock(
        process: subprocess.Popen[str], permission_fallback: bool
) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    if permission_fallback:
        return
    stdout, stderr = process.communicate()
    require(process.returncode in (-15, 0), f"mock exit {process.returncode}")
    require(not stdout.strip(), f"unexpected mock stdout: {stdout[-400:]}")
    require(not stderr.strip(), f"unexpected mock stderr: {stderr[-400:]}")


def run_scenario(
        classes: Path, temp: Path, scenario: str
) -> None:
    scenario_dir = temp / scenario
    scenario_dir.mkdir()
    fixture = scenario_dir / "fixture.properties"
    log = scenario_dir / "requests.jsonl"
    port_file = scenario_dir / "port.json"
    values = fixture_values(scenario)
    write_fixture(fixture, values)
    process, origin = start_mock(scenario_dir, fixture, log, port_file)
    permission_fallback = origin is None
    if permission_fallback:
        java_arguments = [
            scenario,
            "fallback",
            str(fixture),
            str(CONTRACT),
            str(log),
        ]
    else:
        java_arguments = [scenario, origin, str(fixture)]
    try:
        result = subprocess.run(
            [
                "java",
                "-cp",
                str(classes),
                "TestMain",
                *java_arguments,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    finally:
        stop_mock(process, permission_fallback)
    require(
        result.returncode == 0,
        f"{scenario} failed:\n{result.stdout[-1000:]}\n{result.stderr[-2000:]}",
    )
    require(
        result.stdout.strip() == f"OK {scenario}",
        f"{scenario}: unexpected TestMain output",
    )
    require(not result.stderr.strip(), f"{scenario}: unexpected stderr")
    verify_log(scenario, load_log(log), values)


def main() -> int:
    try:
        verify_protected_files()
        verify_contract()
        require(CLIENT.is_file(), "missing client source")
        production_sources = list((ROOT / "src").rglob("*.java"))
        require(
            production_sources == [CLIENT],
            "exactly one production Java source is allowed",
        )
        with tempfile.TemporaryDirectory(prefix="vcf91-0163-") as directory:
            temp = Path(directory)
            classes = temp / "classes"
            classes.mkdir()
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
                capture_output=True,
                timeout=20,
                check=False,
            )
            require(
                compile_result.returncode == 0,
                "compilation failed:\n" + compile_result.stderr[-3000:],
            )
            for scenario in (
                "correlated",
                "event_only",
                "log_only",
                "unauthorized_namespace",
                "vcenter_failure",
                "bad_events",
                "validation",
            ):
                run_scenario(classes, temp, scenario)
        print("verification passed")
        return 0
    except (VerificationError, OSError, subprocess.SubprocessError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
