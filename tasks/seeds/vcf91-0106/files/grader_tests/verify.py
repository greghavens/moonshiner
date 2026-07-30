"""Deterministic protected acceptance checks for the vcf_diag exercise."""

from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, unquote, urlsplit

from mock_vcenter import MockState, MockVCenter, read_request_log


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SESSION_ID = "fixture-session-vcf91"
HOST_ID = "host-42"
DESCRIPTION = "Collect TPM logs \u2013 reboot 731"

EXPECTED_SOURCE = {
    "repository_commit_sha": "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
    "spec_path": "specifications/vsphere/openapi/automation/vcenter.yaml",
    "operation_ids": [
        "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm_list",
        "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get",
        "Appliance.SupportBundle_create$Task",
    ],
}
EXPECTED_CONTRACT_SHA256 = (
    "a19d7ee66f54d2faa35647706292d100773bc3dac24e49ea2dfecb2d4f0253ec"
)
EXPECTED_SOURCES_SHA256 = (
    "5cfb5e452a9fb28bf5b30c3c310f440b6c4759f75527980b2feb89028745b0c3"
)


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_inputs() -> None:
    contract_path = ROOT / "docs" / "contract.json"
    sources_path = ROOT / "docs" / "official_sources.json"
    contract_digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    sources_digest = hashlib.sha256(sources_path.read_bytes()).hexdigest()
    if contract_digest != EXPECTED_CONTRACT_SHA256:
        fail("docs/contract.json differs from the protected contract")
    if sources_digest != EXPECTED_SOURCES_SHA256:
        fail("docs/official_sources.json differs from the protected provenance")

    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    for key, expected in EXPECTED_SOURCE.items():
        if sources.get(key) != expected:
            fail(f"official source field {key!r} is not pinned correctly")


def check_stdlib_only() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "dependencies = []" not in metadata:
        fail("pyproject.toml must keep an empty dependency list")
    for path in sorted((ROOT / "vcf_diag").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".", 1)[0]]
            for name in names:
                if name not in sys.stdlib_module_names:
                    fail(f"{path.name} imports non-stdlib module {name!r}")


def fixture_state(*, active: bool, truncated: bool, suffix: str) -> MockState:
    tpm_id = f"tpm-{suffix}"
    event_bytes = f"TCG2 fixture event stream {suffix}".encode("ascii")
    digest = bytes.fromhex("42" * 32)
    return MockState(
        tpms=[
            {
                "tpm": tpm_id,
                "major_version": 2,
                "minor_version": 0,
                "active": active,
            }
        ],
        event_log={
            "type": "EFI_TCG2_EVENT_LOG_FORMAT_TCG_2",
            "data": base64.b64encode(event_bytes).decode("ascii"),
            "truncated": truncated,
            "banks": [
                {
                    "algorithm": "SHA256",
                    "pcrs": {"0": base64.b64encode(digest).decode("ascii")},
                }
            ],
        },
        support_bundle_task=f"task-support-{suffix}",
    )


def invoke_cli(base_url: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "vcf_diag",
            "--base-url",
            base_url,
            "--session-id",
            SESSION_ID,
            "--host",
            HOST_ID,
            "--description",
            DESCRIPTION,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )


@dataclass
class OfflineResponse:
    status: int
    value: object

    def __enter__(self) -> "OfflineResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            self.value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")


class OfflineContractTransport:
    """Socket-denied fallback that records the same urllib request wire data."""

    base_url = "http://127.0.0.1:1/api"

    def __init__(self, state: MockState, request_log: Path):
        self.state = state
        self.request_log = request_log
        self.request_log.write_text("", encoding="utf-8")

    def urlopen(self, request, timeout: float) -> OfflineResponse:
        del timeout
        body = request.data or b""
        record = {
            "method": request.get_method(),
            "raw_path": urlsplit(request.full_url).path
            + (
                "?" + urlsplit(request.full_url).query
                if urlsplit(request.full_url).query
                else ""
            ),
            "headers": {
                key.lower(): value for key, value in request.header_items()
            },
            "body": body.decode("utf-8"),
        }
        with self.request_log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
        target = urlsplit(request.full_url)
        if request.get_method() == "GET" and re.fullmatch(
            r"/api/vcenter/trusted-infrastructure/hosts/"
            r"[^/]+/hardware/tpm",
            target.path,
        ):
            return OfflineResponse(200, self.state.tpms)
        if (
            request.get_method() == "GET"
            and not target.query
            and re.fullmatch(
                r"/api/vcenter/trusted-infrastructure/hosts/"
                r"[^/]+/hardware/tpm/[^/]+/event-log",
                target.path,
            )
        ):
            return OfflineResponse(200, self.state.event_log)
        if (
            request.get_method() == "POST"
            and target.path == "/api/appliance/support-bundle"
            and target.query == "vmw-task=true"
        ):
            return OfflineResponse(202, self.state.support_bundle_task)
        fail(f"request is outside the pinned contract: {request.full_url}")


def invoke_cli_offline(
    base_url: str, transport: OfflineContractTransport
) -> subprocess.CompletedProcess[str]:
    import vcf_diag.client as client_module
    from vcf_diag.__main__ import main as cli_main

    stdout = io.StringIO()
    stderr = io.StringIO()
    arguments = [
        "--base-url",
        base_url,
        "--session-id",
        SESSION_ID,
        "--host",
        HOST_ID,
        "--description",
        DESCRIPTION,
    ]
    with (
        mock.patch.object(
            client_module, "urlopen", transport.urlopen, create=True
        ),
        contextlib.redirect_stdout(stdout),
        contextlib.redirect_stderr(stderr),
    ):
        returncode = cli_main(arguments)
    return subprocess.CompletedProcess(
        args=[sys.executable, "-m", "vcf_diag", *arguments],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def run_cli_with_fixture(
    state: MockState, request_log: Path
) -> subprocess.CompletedProcess[str]:
    try:
        with MockVCenter(ROOT, state, request_log) as service:
            return invoke_cli(service.base_url)
    except PermissionError as exc:
        if exc.errno != 1:
            raise
        transport = OfflineContractTransport(state, request_log)
        return invoke_cli_offline(transport.base_url, transport)


def assert_common_wire_shape(
    requests: list[dict[str, object]], state: MockState
) -> None:
    if len(requests) != 3:
        fail(f"expected exactly three API requests, got {len(requests)}")
    list_request, event_request, bundle_request = requests
    expected_event_path = (
        f"/api/vcenter/trusted-infrastructure/hosts/{HOST_ID}"
        f"/hardware/tpm/{state.tpms[0]['tpm']}/event-log"
    )
    expected = [
        (
            "GET",
            f"/api/vcenter/trusted-infrastructure/hosts/{HOST_ID}/hardware/tpm",
        ),
        ("GET", expected_event_path),
        ("POST", "/api/appliance/support-bundle"),
    ]
    for request, (method, path) in zip(requests, expected, strict=True):
        target = urlsplit(str(request["raw_path"]))
        if request["method"] != method or target.path != path:
            fail(f"wrong request target: {request!r}")
        headers = request["headers"]
        if not isinstance(headers, dict):
            fail("request headers were not logged")
        if headers.get("vmware-api-session-id") != SESSION_ID:
            fail("missing or incorrect vmware-api-session-id header")
        if headers.get("accept") != "application/json":
            fail("every request must advertise Accept: application/json")

    for request in (list_request, event_request):
        target = urlsplit(str(request["raw_path"]))
        if target.query:
            fail("unset GET filters must be omitted, not sent empty")
        if request["body"] != "":
            fail("GET requests must have an empty body")
        if "content-type" in request["headers"]:
            fail("GET requests must not send a content type")

    bundle_target = urlsplit(str(bundle_request["raw_path"]))
    if bundle_target.query != "vmw-task=true":
        fail("support-bundle task query is not exact")
    if bundle_request["headers"].get("content-type") != "application/json":
        fail("support-bundle request must send application/json")
    try:
        payload = json.loads(str(bundle_request["body"]))
    except json.JSONDecodeError as exc:
        fail(f"support-bundle body is not JSON: {exc}")
    expected_payload = {
        "description": DESCRIPTION,
        "content_type": "LOGS",
    }
    if payload != expected_payload:
        fail(
            "support-bundle JSON must contain only description and LOGS; "
            "unset components and partition must be absent"
        )


def run_scenario(
    temporary: Path,
    *,
    active: bool,
    truncated: bool,
    suffix: str,
    expected_code: str,
) -> None:
    state = fixture_state(active=active, truncated=truncated, suffix=suffix)
    request_log = temporary / f"requests-{suffix}.jsonl"
    completed = run_cli_with_fixture(state, request_log)
    if completed.returncode != 0:
        fail(
            f"CLI failed with {completed.returncode}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fail(f"CLI did not print one JSON report: {exc}")
    if report.get("host") != HOST_ID:
        fail("report host does not match the collected host")
    if report.get("tpm") != state.tpms[0]:
        fail("report does not contain the returned TPM summary")
    if report.get("event_log") != state.event_log:
        fail("report does not contain the returned event log")
    if report.get("support_bundle_task") != state.support_bundle_task:
        fail("report does not contain the returned support-bundle task")
    diagnosis = report.get("diagnosis")
    if not isinstance(diagnosis, dict) or diagnosis.get("code") != expected_code:
        fail(f"wrong evidence classification: {diagnosis!r}")
    if not isinstance(diagnosis.get("summary"), str) or not diagnosis["summary"]:
        fail("diagnosis must include a concise evidence-based summary")
    assert_common_wire_shape(read_request_log(request_log), state)


def check_optional_filter_wire_shape(temporary: Path) -> None:
    import vcf_diag.client as client_module
    from vcf_diag import VCenterClient

    state = fixture_state(active=False, truncated=False, suffix="filters")
    request_log = temporary / "requests-filters.jsonl"
    try:
        with MockVCenter(ROOT, state, request_log) as service:
            client = VCenterClient(service.base_url, SESSION_ID, timeout=2)
            result = client.list_tpms(
                HOST_ID,
                active=False,
                major_versions=(2, 3),
            )
    except PermissionError as exc:
        if exc.errno != 1:
            raise
        transport = OfflineContractTransport(state, request_log)
        with mock.patch.object(
            client_module, "urlopen", transport.urlopen, create=True
        ):
            client = VCenterClient(transport.base_url, SESSION_ID, timeout=2)
            result = client.list_tpms(
                HOST_ID,
                active=False,
                major_versions=(2, 3),
            )
    if result != state.tpms:
        fail("list_tpms did not decode the JSON array")
    requests = read_request_log(request_log)
    if len(requests) != 1:
        fail("list_tpms must make exactly one request")
    pairs = parse_qsl(urlsplit(requests[0]["raw_path"]).query, keep_blank_values=True)
    if pairs != [
        ("active", "false"),
        ("major_versions", "2"),
        ("major_versions", "3"),
    ]:
        fail(f"optional filters have the wrong exploded query shape: {pairs!r}")


def check_path_segment_encoding(temporary: Path) -> None:
    import vcf_diag.client as client_module
    from vcf_diag import VCenterClient

    state = fixture_state(active=True, truncated=False, suffix="segments")
    request_log = temporary / "requests-segments.jsonl"
    host = "../outside/api"
    tpm = "../../support-bundle"
    try:
        with MockVCenter(ROOT, state, request_log) as service:
            client = VCenterClient(service.base_url, SESSION_ID, timeout=2)
            result = client.get_tpm_event_log(host, tpm)
    except PermissionError as exc:
        if exc.errno != 1:
            raise
        transport = OfflineContractTransport(state, request_log)
        with mock.patch.object(
            client_module, "urlopen", transport.urlopen, create=True
        ):
            client = VCenterClient(transport.base_url, SESSION_ID, timeout=2)
            result = client.get_tpm_event_log(host, tpm)
    if result != state.event_log:
        fail("get_tpm_event_log did not decode the JSON object")
    requests = read_request_log(request_log)
    if len(requests) != 1 or requests[0]["method"] != "GET":
        fail("get_tpm_event_log must make exactly one GET request")
    target = urlsplit(str(requests[0]["raw_path"]))
    prefix = "/api/vcenter/trusted-infrastructure/hosts/"
    if not target.path.startswith(prefix) or target.query:
        fail("get_tpm_event_log escaped the supplied API root")
    host_segment, separator, remainder = target.path[len(prefix) :].partition(
        "/hardware/tpm/"
    )
    tpm_segment, event_separator, tail = remainder.partition("/event-log")
    if (
        separator != "/hardware/tpm/"
        or event_separator != "/event-log"
        or tail
        or unquote(host_segment) != host
        or unquote(tpm_segment) != tpm
    ):
        fail("host and TPM identifiers were not encoded as single path segments")


def check_log_bundle_optional_wire_shape(temporary: Path) -> None:
    import vcf_diag.client as client_module
    from vcf_diag import VCenterClient

    state = fixture_state(active=True, truncated=False, suffix="bundle-options")
    request_log = temporary / "requests-bundle-options.jsonl"
    components = {
        "host": ["hostd", "vpxa"],
        "vcenter": ["vpxd"],
    }
    partition = "diagnostics"
    try:
        with MockVCenter(ROOT, state, request_log) as service:
            client = VCenterClient(service.base_url, SESSION_ID, timeout=2)
            result = client.create_log_bundle(
                DESCRIPTION,
                components=components,
                partition=partition,
            )
    except PermissionError as exc:
        if exc.errno != 1:
            raise
        transport = OfflineContractTransport(state, request_log)
        with mock.patch.object(
            client_module, "urlopen", transport.urlopen, create=True
        ):
            client = VCenterClient(transport.base_url, SESSION_ID, timeout=2)
            result = client.create_log_bundle(
                DESCRIPTION,
                components=components,
                partition=partition,
            )
    if result != state.support_bundle_task:
        fail("create_log_bundle did not return the task identifier")
    requests = read_request_log(request_log)
    if len(requests) != 1:
        fail("create_log_bundle must make exactly one request")
    request = requests[0]
    target = urlsplit(str(request["raw_path"]))
    if (
        request["method"] != "POST"
        or target.path != "/api/appliance/support-bundle"
        or target.query != "vmw-task=true"
    ):
        fail("create_log_bundle used the wrong request target")
    payload = json.loads(str(request["body"]))
    if payload != {
        "description": DESCRIPTION,
        "components": components,
        "content_type": "LOGS",
        "partition": partition,
    }:
        fail("create_log_bundle did not preserve its set optional properties")


def check_tpm_selection_failures(temporary: Path) -> None:
    base_state = fixture_state(
        active=True,
        truncated=False,
        suffix="cardinality",
    )
    cases = {
        "zero": [],
        "ambiguous": [
            base_state.tpms[0],
            {**base_state.tpms[0], "tpm": "tpm-cardinality-second"},
        ],
        "malformed": [
            {**base_state.tpms[0], "major_version": "2"},
        ],
    }
    for suffix, tpms in cases.items():
        state = MockState(
            tpms=tpms,
            event_log=base_state.event_log,
            support_bundle_task=base_state.support_bundle_task,
        )
        request_log = temporary / f"requests-{suffix}.jsonl"
        completed = run_cli_with_fixture(state, request_log)
        if completed.returncode == 0:
            fail(f"{suffix} TPM selection unexpectedly succeeded")
        if completed.stdout:
            fail(f"{suffix} TPM selection printed a success report")
        stderr_lines = completed.stderr.strip().splitlines()
        if len(stderr_lines) != 1 or not stderr_lines[0].strip():
            fail(f"{suffix} TPM selection did not print concise stderr")
        requests = read_request_log(request_log)
        if len(requests) != 1:
            fail(
                f"{suffix} TPM selection must stop after the list request, "
                f"got {len(requests)} requests"
            )
        target = urlsplit(str(requests[0]["raw_path"]))
        expected_path = (
            f"/api/vcenter/trusted-infrastructure/hosts/{HOST_ID}/hardware/tpm"
        )
        if (
            requests[0]["method"] != "GET"
            or target.path != expected_path
            or target.query
        ):
            fail(f"{suffix} TPM selection used the wrong list request")


def main() -> int:
    check_protected_inputs()
    check_stdlib_only()
    with tempfile.TemporaryDirectory(prefix="vcf91-0106-") as directory:
        temporary = Path(directory)
        run_scenario(
            temporary,
            active=True,
            truncated=True,
            suffix="truncated",
            expected_code="TPM_EVENT_LOG_TRUNCATED",
        )
        run_scenario(
            temporary,
            active=False,
            truncated=True,
            suffix="inactive",
            expected_code="TPM_INACTIVE",
        )
        run_scenario(
            temporary,
            active=True,
            truncated=False,
            suffix="unresolved",
            expected_code="UNRESOLVED_REVIEW_EVENT_LOG",
        )
        check_optional_filter_wire_shape(temporary)
        check_path_segment_encoding(temporary)
        check_log_bundle_optional_wire_shape(temporary)
        check_tpm_selection_failures(temporary)
    print("vcf91-0106 acceptance: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"vcf91-0106 acceptance: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
