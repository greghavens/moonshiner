"""Deterministic protected acceptance checks for the vks_diag exercise."""

from __future__ import annotations

import ast
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

from mock_kubernetes import (
    MockKubernetes,
    MockKubernetesState,
    read_request_log as read_kubernetes_log,
)
from mock_vcenter import (
    MockVCenter,
    MockVCenterState,
    read_request_log as read_vcenter_log,
)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION_ID = "fixture-session-vcf91"
KUBE_TOKEN = "fixture-vks-token"
NAMESPACE = "payments-prod"
SELECTOR = "app=checkout"

EXPECTED_SOURCE = {
    "repository_commit_sha": "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
    "spec_path": "specifications/vsphere/openapi/automation/vcenter.yaml",
    "operation_ids": [
        "Vcenter.Namespaces.Instances_getV2",
        "Vcenter.NamespaceManagement.Supervisors.Summary_get",
    ],
}
EXPECTED_CONTRACT_SHA256 = (
    "067b4ccf375bd490f5b18ee6328cd38e8ef411e047443dca321f6d2242510405"
)
EXPECTED_SOURCES_SHA256 = (
    "0bee509a49493436b80eb4a7d567ab25faa89d3c0d9094c0a7bb7b0b4508ab09"
)


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_inputs() -> None:
    contract_path = ROOT / "docs" / "contract.json"
    sources_path = ROOT / "docs" / "official_sources.json"
    if hashlib.sha256(contract_path.read_bytes()).hexdigest() != (
        EXPECTED_CONTRACT_SHA256
    ):
        fail("docs/contract.json differs from the protected contract")
    if hashlib.sha256(sources_path.read_bytes()).hexdigest() != (
        EXPECTED_SOURCES_SHA256
    ):
        fail("docs/official_sources.json differs from the protected provenance")

    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    for key, expected in EXPECTED_SOURCE.items():
        if sources.get(key) != expected:
            fail(f"official source field {key!r} is not pinned correctly")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = [
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    ]
    if operations != [
        (
            "Vcenter.Namespaces.Instances_getV2",
            "GET",
            "/vcenter/namespaces/instances/v2/{namespace}",
        ),
        (
            "Vcenter.NamespaceManagement.Supervisors.Summary_get",
            "GET",
            "/vcenter/namespace-management/supervisors/{supervisor}/summary",
        ),
    ]:
        fail("contract operation extraction is not the expected VCF 9.1 subset")


def check_stdlib_only() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "dependencies = []" not in metadata:
        fail("pyproject.toml must keep an empty dependency list")
    for path in sorted((ROOT / "vks_diag").glob("*.py")):
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


def fixture_states(
    suffix: str,
    *,
    include_backoff: bool = True,
    expired_log: bool = True,
) -> tuple[MockVCenterState, MockKubernetesState]:
    supervisor = f"supervisor-{suffix}"
    pod_name = f"checkout-{suffix}"
    pod_uid = f"pod-uid-{suffix}"
    vcenter = MockVCenterState(
        namespace_info={
            "supervisor": supervisor,
            "config_status": "RUNNING",
            "messages": [],
            "stats": {
                "cpu_used": 125,
                "memory_used": 384,
                "storage_used": 2048,
            },
            "description": f"payments namespace {suffix}",
            "access_list": [],
            "storage_specs": [],
        },
        supervisor_summary={
            "name": f"edge-supervisor-{suffix}",
            "config_status": "RUNNING",
            "kubernetes_status": "READY",
            "stats": {
                "cpu_used": 4000,
                "cpu_capacity": 32000,
                "memory_used": 16384,
                "memory_capacity": 131072,
                "storage_used": 524288,
                "storage_capacity": 2097152,
            },
            "messages": [],
        },
    )
    events: list[dict[str, object]] = [
        {
            "metadata": {"name": f"pulled-{suffix}"},
            "type": "Normal",
            "reason": "Pulled",
            "message": "Container image is already present",
            "involvedObject": {"uid": pod_uid, "name": pod_name, "kind": "Pod"},
        }
    ]
    if include_backoff:
        events.append(
            {
                "metadata": {"name": f"backoff-{suffix}"},
                "type": "Warning",
                "reason": "BackOff",
                "message": "Back-off restarting failed container app",
                "count": 9,
                "involvedObject": {
                    "uid": pod_uid,
                    "name": pod_name,
                    "kind": "Pod",
                },
            }
        )
    if expired_log:
        container_log = (
            "2026-07-30T18:12:44Z checkout: upstream request failed: "
            "x509: certificate has expired or is not yet valid\n"
        )
    else:
        container_log = (
            "2026-07-30T18:12:44Z checkout: upstream returned HTTP 503\n"
        )
    kubernetes = MockKubernetesState(
        pods=[
            {
                "metadata": {
                    "name": pod_name,
                    "uid": pod_uid,
                    "namespace": NAMESPACE,
                },
                "spec": {
                    "containers": [
                        {"name": "app"},
                        {"name": "metrics"},
                    ]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "app",
                            "ready": False,
                            "restartCount": 9,
                            "state": {
                                "waiting": {"reason": "CrashLoopBackOff"}
                            },
                        },
                        {
                            "name": "metrics",
                            "ready": True,
                            "restartCount": 0,
                            "state": {"running": {}},
                        },
                    ],
                },
            }
        ],
        events=events,
        container_log=container_log,
    )
    return vcenter, kubernetes


@dataclass
class OfflineResponse:
    status: int
    payload: bytes

    def __enter__(self) -> "OfflineResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class OfflineEnvironment:
    """Socket-denied fallback preserving the same urllib wire record."""

    vcenter_url = "http://127.0.0.1:1/api"
    kube_url = "http://127.0.0.1:2"

    def __init__(
        self,
        vcenter_state: MockVCenterState,
        kube_state: MockKubernetesState,
        vcenter_log: Path,
        kube_log: Path,
    ):
        self.vcenter_state = vcenter_state
        self.kube_state = kube_state
        self.vcenter_log = vcenter_log
        self.kube_log = kube_log
        self.vcenter_log.write_text("", encoding="utf-8")
        self.kube_log.write_text("", encoding="utf-8")

    @staticmethod
    def _json_response(value: object) -> OfflineResponse:
        return OfflineResponse(
            200,
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
        )

    @staticmethod
    def _record(path: Path, request) -> None:
        target = urlsplit(request.full_url)
        raw_path = target.path + (f"?{target.query}" if target.query else "")
        body = request.data or b""
        record = {
            "method": request.get_method(),
            "raw_path": raw_path,
            "headers": {
                key.lower(): value for key, value in request.header_items()
            },
            "body": body.decode("utf-8"),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )

    def urlopen(self, request, timeout: float) -> OfflineResponse:
        del timeout
        target = urlsplit(request.full_url)
        if target.port == 1:
            self._record(self.vcenter_log, request)
            if (
                request.get_method() == "GET"
                and not target.query
                and re.fullmatch(
                    r"/api/vcenter/namespaces/instances/v2/[^/]+",
                    target.path,
                )
            ):
                return self._json_response(self.vcenter_state.namespace_info)
            if (
                request.get_method() == "GET"
                and not target.query
                and re.fullmatch(
                    r"/api/vcenter/namespace-management/supervisors/"
                    r"[^/]+/summary",
                    target.path,
                )
            ):
                return self._json_response(
                    self.vcenter_state.supervisor_summary
                )
        elif target.port == 2:
            self._record(self.kube_log, request)
            if request.get_method() == "GET" and re.fullmatch(
                r"/api/v1/namespaces/[^/]+/pods",
                target.path,
            ):
                return self._json_response(
                    {
                        "apiVersion": "v1",
                        "kind": "PodList",
                        "items": self.kube_state.pods,
                    }
                )
            if request.get_method() == "GET" and re.fullmatch(
                r"/api/v1/namespaces/[^/]+/events",
                target.path,
            ):
                return self._json_response(
                    {
                        "apiVersion": "v1",
                        "kind": "EventList",
                        "items": self.kube_state.events,
                    }
                )
            if request.get_method() == "GET" and re.fullmatch(
                r"/api/v1/namespaces/[^/]+/pods/[^/]+/log",
                target.path,
            ):
                return OfflineResponse(
                    200,
                    self.kube_state.container_log.encode("utf-8"),
                )
        fail(f"request is outside the fixture surfaces: {request.full_url}")


def invoke_cli(
    vcenter_url: str,
    kube_url: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "vks_diag",
            "--vcenter-url",
            vcenter_url,
            "--session-id",
            SESSION_ID,
            "--kube-url",
            kube_url,
            "--kube-token",
            KUBE_TOKEN,
            "--namespace",
            NAMESPACE,
            "--selector",
            SELECTOR,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )


def invoke_cli_offline(
    environment: OfflineEnvironment,
) -> subprocess.CompletedProcess[str]:
    from vks_diag.__main__ import main as cli_main

    stdout = io.StringIO()
    stderr = io.StringIO()
    argv = [
        "--vcenter-url",
        environment.vcenter_url,
        "--session-id",
        SESSION_ID,
        "--kube-url",
        environment.kube_url,
        "--kube-token",
        KUBE_TOKEN,
        "--namespace",
        NAMESPACE,
        "--selector",
        SELECTOR,
    ]
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        return_code = cli_main(argv)
    return subprocess.CompletedProcess(
        args=[sys.executable, "-m", "vks_diag"],
        returncode=return_code,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _assert_get_headers(
    request: dict[str, object],
    *,
    authorization_name: str,
    authorization_value: str,
    accept: str,
) -> None:
    headers = request["headers"]
    if not isinstance(headers, dict):
        fail("request log did not record headers")
    if headers.get(authorization_name) != authorization_value:
        fail(f"request omitted or changed {authorization_name}")
    if headers.get("accept") != accept:
        fail(f"request used the wrong Accept value: {headers.get('accept')!r}")
    if "content-type" in headers:
        fail("GET request must not send a Content-Type header")
    if request["body"] != "":
        fail("GET request must have an empty body")


def assert_scenario_wire(
    vcenter_requests: list[dict[str, object]],
    kube_requests: list[dict[str, object]],
    suffix: str,
) -> None:
    expected_vcenter = [
        f"/api/vcenter/namespaces/instances/v2/{NAMESPACE}",
        (
            "/api/vcenter/namespace-management/supervisors/"
            f"supervisor-{suffix}/summary"
        ),
    ]
    if [item["raw_path"] for item in vcenter_requests] != expected_vcenter:
        fail("vCenter operation order or exact target is wrong")
    for request in vcenter_requests:
        if request["method"] != "GET":
            fail("vCenter evidence operations must use GET")
        _assert_get_headers(
            request,
            authorization_name="vmware-api-session-id",
            authorization_value=SESSION_ID,
            accept="application/json",
        )

    expected_kube = [
        (
            f"/api/v1/namespaces/{NAMESPACE}/pods"
            "?labelSelector=app%3Dcheckout"
        ),
        (
            f"/api/v1/namespaces/{NAMESPACE}/events"
            f"?fieldSelector=involvedObject.uid%3Dpod-uid-{suffix}"
        ),
        (
            f"/api/v1/namespaces/{NAMESPACE}/pods/checkout-{suffix}/log"
            "?container=app&tailLines=200"
        ),
    ]
    if [item["raw_path"] for item in kube_requests] != expected_kube:
        fail("Kubernetes evidence order or exact target is wrong")
    for index, request in enumerate(kube_requests):
        if request["method"] != "GET":
            fail("Kubernetes evidence operations must use GET")
        _assert_get_headers(
            request,
            authorization_name="authorization",
            authorization_value=f"Bearer {KUBE_TOKEN}",
            accept="text/plain" if index == 2 else "application/json",
        )

    forbidden = (
        "labelSelector=",
        "fieldSelector=",
        "limit=",
        "previous=",
        "timestamps=",
        "None",
    )
    for raw_path in [str(item["raw_path"]) for item in kube_requests]:
        for marker in forbidden:
            if marker in raw_path and marker in {
                "limit=",
                "previous=",
                "timestamps=",
                "None",
            }:
                fail(f"unset optional field leaked into request target: {raw_path}")


def run_scenario(
    temporary: Path,
    suffix: str,
    *,
    include_backoff: bool,
    expired_log: bool,
    expected_code: str,
) -> None:
    vcenter_state, kube_state = fixture_states(
        suffix,
        include_backoff=include_backoff,
        expired_log=expired_log,
    )
    vcenter_log = temporary / f"vcenter-{suffix}.jsonl"
    kube_log = temporary / f"kube-{suffix}.jsonl"
    with (
        contextlib.ExitStack() as stack
    ):
        try:
            vcenter = stack.enter_context(
                MockVCenter(ROOT, vcenter_state, vcenter_log)
            )
            kubernetes = stack.enter_context(
                MockKubernetes(kube_state, kube_log)
            )
        except PermissionError as exc:
            if exc.errno != 1:
                raise
            environment = OfflineEnvironment(
                vcenter_state,
                kube_state,
                vcenter_log,
                kube_log,
            )
            import vks_diag.client as client_module

            with mock.patch.object(
                client_module,
                "urlopen",
                environment.urlopen,
                create=True,
            ):
                completed = invoke_cli_offline(environment)
        else:
            completed = invoke_cli(vcenter.base_url, kubernetes.base_url)
    if completed.returncode != 0:
        fail(
            f"CLI scenario {suffix!r} failed with {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    if completed.stderr:
        fail(f"CLI scenario {suffix!r} wrote unexpected stderr")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fail(f"CLI scenario {suffix!r} did not print JSON: {exc}")
    if report.get("namespace") != NAMESPACE:
        fail("report changed the namespace")
    if report.get("namespace_info") != vcenter_state.namespace_info:
        fail("report omitted or changed vCenter namespace evidence")
    if report.get("supervisor_summary") != vcenter_state.supervisor_summary:
        fail("report omitted or changed Supervisor evidence")
    pod = report.get("pod")
    if (
        not isinstance(pod, dict)
        or pod.get("metadata", {}).get("name") != f"checkout-{suffix}"
    ):
        fail("report did not retain the selected pod evidence")
    if report.get("events") != kube_state.events:
        fail("report omitted or changed Kubernetes Events")
    if report.get("container_log") != kube_state.container_log:
        fail("report omitted or changed the container log")
    diagnosis = report.get("diagnosis")
    if not isinstance(diagnosis, dict) or diagnosis.get("code") != expected_code:
        fail(
            f"scenario {suffix!r} returned the wrong diagnosis: "
            f"{diagnosis!r}"
        )
    evidence = diagnosis.get("evidence")
    if not isinstance(evidence, dict) or evidence.get("container") != "app":
        fail("diagnosis did not name the evidence container")
    warning_reasons = evidence.get("warning_reasons")
    expected_reasons = ["BackOff"] if include_backoff else []
    if warning_reasons != expected_reasons:
        fail("diagnosis did not derive Warning reasons from Events")

    assert_scenario_wire(
        read_vcenter_log(vcenter_log),
        read_kubernetes_log(kube_log),
        suffix,
    )


def check_optional_query_wire(temporary: Path) -> None:
    import vks_diag.client as client_module
    from vks_diag import KubernetesClient

    vcenter_state, state = fixture_states("options")
    request_log = temporary / "kube-options.jsonl"
    vcenter_log = temporary / "vcenter-options.jsonl"

    def exercise(client: KubernetesClient) -> None:
        client.list_pods(NAMESPACE)
        client.list_events(NAMESPACE)
        client.get_pod_log(NAMESPACE, "checkout-options")
        client.list_pods(
            NAMESPACE,
            label_selector="app=checkout",
            field_selector="status.phase!=Succeeded",
            limit=0,
        )
        client.list_events(
            NAMESPACE,
            label_selector="component=checkout",
            field_selector="type=Warning",
            limit=7,
        )
        client.get_pod_log(
            NAMESPACE,
            "checkout-options",
            container="app",
            tail_lines=0,
            previous=False,
            timestamps=False,
        )

    try:
        with MockKubernetes(state, request_log) as service:
            exercise(
                KubernetesClient(service.base_url, KUBE_TOKEN, timeout=2)
            )
    except PermissionError as exc:
        if exc.errno != 1:
            raise
        environment = OfflineEnvironment(
            vcenter_state,
            state,
            vcenter_log,
            request_log,
        )
        with mock.patch.object(
            client_module,
            "urlopen",
            environment.urlopen,
            create=True,
        ):
            exercise(
                KubernetesClient(
                    environment.kube_url,
                    KUBE_TOKEN,
                    timeout=2,
                )
            )
    requests = read_kubernetes_log(request_log)
    actual = [str(item["raw_path"]) for item in requests]
    expected = [
        f"/api/v1/namespaces/{NAMESPACE}/pods",
        f"/api/v1/namespaces/{NAMESPACE}/events",
        f"/api/v1/namespaces/{NAMESPACE}/pods/checkout-options/log",
        (
            f"/api/v1/namespaces/{NAMESPACE}/pods"
            "?labelSelector=app%3Dcheckout"
            "&fieldSelector=status.phase%21%3DSucceeded&limit=0"
        ),
        (
            f"/api/v1/namespaces/{NAMESPACE}/events"
            "?labelSelector=component%3Dcheckout"
            "&fieldSelector=type%3DWarning&limit=7"
        ),
        (
            f"/api/v1/namespaces/{NAMESPACE}/pods/checkout-options/log"
            "?container=app&tailLines=0&previous=false&timestamps=false"
        ),
    ]
    if actual != expected:
        fail(f"optional query serialization mismatch: {actual!r}")
    for raw_path in actual[:3]:
        if "?" in raw_path:
            fail("all-unset optional arguments must not produce a query string")


def check_path_segment_encoding(temporary: Path) -> None:
    import vks_diag.client as client_module
    from vks_diag import KubernetesClient, VCenterClient

    vcenter_state, kube_state = fixture_states("encoding")
    vcenter_log = temporary / "vcenter-encoding.jsonl"
    kube_log = temporary / "kube-encoding.jsonl"
    namespace = "team/blue #1"
    supervisor = "domain/supervisor ?"
    pod = "checkout/canary #2"
    def exercise(vcenter_url: str, kube_url: str) -> None:
        vcenter = VCenterClient(vcenter_url, SESSION_ID, timeout=2)
        kubernetes = KubernetesClient(kube_url, KUBE_TOKEN, timeout=2)
        vcenter.get_namespace(namespace)
        vcenter.get_supervisor_summary(supervisor)
        kubernetes.get_pod_log(namespace, pod)

    try:
        with (
            MockVCenter(ROOT, vcenter_state, vcenter_log) as vcenter_service,
            MockKubernetes(kube_state, kube_log) as kube_service,
        ):
            exercise(vcenter_service.base_url, kube_service.base_url)
    except PermissionError as exc:
        if exc.errno != 1:
            raise
        environment = OfflineEnvironment(
            vcenter_state,
            kube_state,
            vcenter_log,
            kube_log,
        )
        with mock.patch.object(
            client_module,
            "urlopen",
            environment.urlopen,
            create=True,
        ):
            exercise(environment.vcenter_url, environment.kube_url)
    vcenter_requests = read_vcenter_log(vcenter_log)
    kube_requests = read_kubernetes_log(kube_log)
    namespace_segment = urlsplit(
        str(vcenter_requests[0]["raw_path"])
    ).path.rsplit("/", 1)[-1]
    supervisor_segment = urlsplit(
        str(vcenter_requests[1]["raw_path"])
    ).path.split("/")[-2]
    kube_target = urlsplit(str(kube_requests[0]["raw_path"])).path.split("/")
    if (
        unquote(namespace_segment) != namespace
        or unquote(supervisor_segment) != supervisor
        or unquote(kube_target[4]) != namespace
        or unquote(kube_target[6]) != pod
    ):
        fail("identifiers were not encoded as single path segments")


def check_ambiguous_evidence_stops(temporary: Path) -> None:
    vcenter_state, kube_state = fixture_states("ambiguous")
    second = json.loads(json.dumps(kube_state.pods[0]))
    second["metadata"]["name"] = "checkout-ambiguous-second"
    second["metadata"]["uid"] = "pod-uid-ambiguous-second"
    ambiguous = MockKubernetesState(
        pods=[kube_state.pods[0], second],
        events=kube_state.events,
        container_log=kube_state.container_log,
    )
    vcenter_log = temporary / "vcenter-ambiguous.jsonl"
    kube_log = temporary / "kube-ambiguous.jsonl"
    with contextlib.ExitStack() as stack:
        try:
            vcenter = stack.enter_context(
                MockVCenter(ROOT, vcenter_state, vcenter_log)
            )
            kubernetes = stack.enter_context(
                MockKubernetes(ambiguous, kube_log)
            )
        except PermissionError as exc:
            if exc.errno != 1:
                raise
            environment = OfflineEnvironment(
                vcenter_state,
                ambiguous,
                vcenter_log,
                kube_log,
            )
            import vks_diag.client as client_module

            with mock.patch.object(
                client_module,
                "urlopen",
                environment.urlopen,
                create=True,
            ):
                completed = invoke_cli_offline(environment)
        else:
            completed = invoke_cli(vcenter.base_url, kubernetes.base_url)
    if completed.returncode == 0 or completed.stdout:
        fail("ambiguous unhealthy pods must not produce a success report")
    stderr_lines = completed.stderr.strip().splitlines()
    if len(stderr_lines) != 1 or not stderr_lines[0].strip():
        fail("ambiguous evidence failure must print one concise stderr line")
    requests = read_kubernetes_log(kube_log)
    if len(requests) != 1 or "/pods?" not in str(requests[0]["raw_path"]):
        fail("ambiguous pod evidence must stop before Event and log requests")


def main() -> int:
    check_protected_inputs()
    check_stdlib_only()
    with tempfile.TemporaryDirectory(prefix="vcf91-0145-") as directory:
        temporary = Path(directory)
        run_scenario(
            temporary,
            "expired",
            include_backoff=True,
            expired_log=True,
            expected_code="UPSTREAM_TLS_CERTIFICATE_EXPIRED",
        )
        run_scenario(
            temporary,
            "log-only",
            include_backoff=False,
            expired_log=True,
            expected_code="UNRESOLVED_REVIEW_EVENTS_AND_LOGS",
        )
        run_scenario(
            temporary,
            "event-only",
            include_backoff=True,
            expired_log=False,
            expected_code="UNRESOLVED_REVIEW_EVENTS_AND_LOGS",
        )
        check_optional_query_wire(temporary)
        check_path_segment_encoding(temporary)
        check_ambiguous_evidence_stops(temporary)
    print("vcf91-0145 acceptance: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"vcf91-0145 acceptance: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
