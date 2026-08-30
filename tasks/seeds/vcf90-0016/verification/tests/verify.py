#!/usr/bin/env python3
"""Protected deterministic verifier for vcf90-0016.

Starts the loopback SDDC Manager mock, drives ``vcfsupport.diagnose_failure``
against it in a subprocess, and asserts both the diagnosis it produced and the
exact wire shape of every request it sent. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tests"))

import sddc_mock  # noqa: E402  (protected fixture, imported after sys.path setup)

PINNED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_TAG = "9.0.0.0"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"

EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getTasks": ("GET", "/v1/tasks"),
    "getTask": ("GET", "/v1/tasks/{id}"),
    "getNotifications": ("GET", "/v1/notifications"),
    "startSupportBundle": ("POST", "/v1/system/support-bundles"),
    "getSupportBundleStatus": ("GET", "/v1/system/support-bundles/{id}"),
    "retryTask": ("PATCH", "/v1/tasks/{id}"),
}

# The complete Logs schema at tag 9.0.0.0. hcxLogs and vmsLogs arrived in 9.1.0.0.
LOGS_PROPERTIES_9_0 = {
    "vcLogs", "nsxLogs", "esxLogs", "wcpLogs", "sddcManagerLogs", "apiLogs",
    "systemDebugLogs", "vmScreenshots", "vraLogs", "vropsLogs", "vrliLogs",
    "vrslcmLogs", "automationLogs", "operationsLogs", "operationsForLogs",
    "lifecycleLogs",
}
LOGS_PROPERTIES_ONLY_IN_9_1 = {"hcxLogs", "vmsLogs"}

PROTECTED_SHA256 = {
    "README.md": "145c15bf500bedcc53321c8ef5ce528e571b34ab5e00b6ce18b271131cffba96",
    "docs/contract.json": "e87b6e4f714301fceede02fb61bfc20f2181b97b869b1b4e7c29b3da684149a4",
    "docs/official_sources.json": "ac0fb6bf7fb70d3a1f352b31ae72df05074e788bbb727fe5cc2395a1040d8ac2",
    "tests/sddc_mock.py": "e6249c06dbbefc7cff3c4dad202d8a0ceb69024325d37135bd0825341d50f02c",
    "tests/fixtures.json": "b2434125302302eb2a6c4fa88f023a7c3a7a2e6c839cb5e1e8cc88a1a7ccbd66",
}

FIXTURES = json.loads((ROOT / "tests" / "fixtures.json").read_text(encoding="utf-8"))
CREDENTIALS = FIXTURES["credentials"]
BUNDLE = FIXTURES["supportBundle"]

FAILED_TASK_ID = "a3f7c1e8-0d94-4b62-8e15-7c2a9f4d6b03"
FAILED_TASK_NAME = "Add hosts to cluster sfo-w01-cl01"
OTHER_FAILED_TASK_ID = "b8d2e40f-5a17-4c93-9f26-3e8b1d7a5c92"
LISTED_FAILED_TASK_IDS = {FAILED_TASK_ID, OTHER_FAILED_TASK_ID}

EXPECTED_REPORT = {
    "failedTaskId": FAILED_TASK_ID,
    "failedTaskName": FAILED_TASK_NAME,
    "rootCauseErrorCode": "NSXT_TN_REALIZATION_TIMEOUT",
    "rootCauseReferenceToken": "R7F3K2QD",
    "affectedComponentTypes": ["ESXI", "NSXT_MANAGER", "SDDC_MANAGER"],
    "correlatedNotificationTypes": ["SDDC_MANAGER_TASK_DISPATCH_BACKLOG"],
    "logsRequested": ["esxLogs", "nsxLogs", "sddcManagerLogs"],
    "supportBundleId": BUNDLE["id"],
    "supportBundleName": BUNDLE["bundleName"],
    "supportBundleStatus": "COMPLETED_WITH_SUCCESS",
    "retried": True,
}

EXPECTED_SUPPORT_BUNDLE_BODY = {
    "scope": {
        "domains": [{"domainName": "sfo-w01", "clusterNames": ["sfo-w01-cl01"]}]
    },
    "logs": {"esxLogs": True, "nsxLogs": True, "sddcManagerLogs": True},
}

RUNNER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import vcfsupport
report = vcfsupport.diagnose_failure(
    base_url=sys.argv[2], username=sys.argv[3], password=sys.argv[4]
)
with open(sys.argv[5], "w", encoding="utf-8") as handle:
    json.dump(report, handle)
"""


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


# -- static checks --------------------------------------------------------
def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected fixture missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_provenance() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("repository") != "https://github.com/vmware/vcf-api-specs":
        fail("contract is not derived from the official vcf-api-specs repository")
    if source.get("commit_sha") != PINNED_SHA or source.get("spec_path") != SPEC_PATH:
        fail("contract source is not pinned to the selected specification revision")
    if source.get("tag") != PINNED_TAG or source.get("api_version") != PINNED_TAG:
        fail("contract is not pinned to the 9.0.0.0 tag of the specification")
    if source.get("license") != "Apache-2.0":
        fail("contract license changed")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")

    logs = next(
        item for item in operations if item["operationId"] == "startSupportBundle"
    )["request"]["properties"]["logs"]["properties"]
    if set(logs) != LOGS_PROPERTIES_9_0:
        fail("contract Logs schema is not the complete 9.0.0.0 Logs schema")
    if set(logs) & LOGS_PROPERTIES_ONLY_IN_9_1:
        fail("contract Logs schema contains properties introduced in the 9.1 revision")

    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    if sources.get("repository_commit_sha") != PINNED_SHA:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official source spec path changed")
    recorded = {
        entry.get("operationId") for entry in sources.get("operations", [])
    }
    if recorded != set(EXPECTED_OPERATIONS):
        fail("official_sources.json does not record every contract operationId")
    for entry in sources.get("operations", []):
        if entry.get("repository_commit_sha") != PINNED_SHA:
            fail(f"operation {entry.get('operationId')} is not pinned to the tag commit")
        if entry.get("spec_path") != SPEC_PATH:
            fail(f"operation {entry.get('operationId')} does not record the spec path")


def check_stdlib_only() -> None:
    package = ROOT / "vcfsupport"
    if not package.is_dir():
        fail("the vcfsupport package directory is missing")
    modules = sorted(package.rglob("*.py"))
    if not modules:
        fail("the vcfsupport package contains no Python modules")
    allowed = set(getattr(sys, "stdlib_module_names", set())) | {"vcfsupport"}
    if not allowed:
        return
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name not in allowed:
                    fail(
                        f"{module.relative_to(ROOT)} imports '{name}', which is not in "
                        "the Python standard library"
                    )


# -- dynamic run ----------------------------------------------------------
def run_diagnosis(log_path: Path, report_path: Path) -> None:
    # Always execute the sources as they are on disk, never a stale .pyc.
    for cached in (ROOT / "vcfsupport").rglob("__pycache__"):
        shutil.rmtree(cached, ignore_errors=True)
    httpd = sddc_mock.build_server(log_path)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                RUNNER,
                str(ROOT),
                f"http://127.0.0.1:{port}",
                CREDENTIALS["username"],
                CREDENTIALS["password"],
                str(report_path),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=150,
        )
    except subprocess.TimeoutExpired:
        fail("vcfsupport.diagnose_failure did not finish within 150 seconds")
    finally:
        httpd.shutdown()
        httpd.server_close()
    if completed.returncode != 0:
        fail(
            "vcfsupport.diagnose_failure raised:\n"
            f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}"
        )


def check_report(report_path: Path) -> None:
    if not report_path.is_file():
        fail("vcfsupport.diagnose_failure produced no report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        fail("diagnose_failure must return a dict")
    for key, expected in EXPECTED_REPORT.items():
        if key not in report:
            fail(f"report is missing the '{key}' key")
        actual = report[key]
        if actual != expected:
            fail(f"report['{key}'] is {actual!r}, expected {expected!r}")


# -- wire shape -----------------------------------------------------------
def _walk(value, path="body"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
    yield path, value


def check_no_empty_payload_values(record: dict) -> None:
    if record["body_json"] is None:
        return
    for path, value in _walk(record["body_json"]):
        if value is None:
            fail(
                f"{record['operationId']} sent {path} as null; unset optional fields "
                "must be omitted from the request body"
            )
        if value == "" or (isinstance(value, (list, dict)) and len(value) == 0):
            fail(
                f"{record['operationId']} sent {path} as an empty "
                f"{type(value).__name__}; unset optional fields must be omitted"
            )


def check_requests(log_path: Path) -> None:
    if not log_path.is_file():
        fail("the mock recorded no requests")
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    if not records:
        fail("the mock recorded no requests")

    for record in records:
        if record["operationId"] is None:
            fail(
                f"{record['method']} {record['path']} is not an operation named by "
                "docs/contract.json"
            )
        if record["status"] >= 400:
            fail(
                f"{record['operationId']} was rejected with HTTP {record['status']}: "
                f"{record['body'][:200]}"
            )
        check_no_empty_payload_values(record)
        for name, values in record["query_params"].items():
            for value in values:
                if value.lower() in {"", "false", "null", "none", "[]", "{}"}:
                    fail(
                        f"{record['operationId']} sent optional query parameter "
                        f"{name}={value!r}; unset optional parameters must be omitted"
                    )

    def indexes(operation_id):
        return [i for i, record in enumerate(records) if record["operationId"] == operation_id]

    for operation_id, count in (
        ("createToken", 1), ("getTasks", 1), ("getNotifications", 1),
        ("startSupportBundle", 1), ("retryTask", 1),
    ):
        found = len(indexes(operation_id))
        if found != count:
            fail(f"expected exactly {count} {operation_id} request(s), saw {found}")

    # createToken: unauthenticated, only the two credential properties.
    token_record = records[indexes("createToken")[0]]
    if token_record["seq"] != 1 or indexes("createToken")[0] != 0:
        fail("createToken must be the first request")
    if "authorization" in token_record["headers"]:
        fail("createToken must not send an Authorization header")
    if token_record["body_json"] != {
        "username": CREDENTIALS["username"],
        "password": CREDENTIALS["password"],
    }:
        fail(
            "createToken body must contain exactly username and password; the unset "
            f"apiKey and idToken properties must be omitted, got {token_record['body']}"
        )
    if token_record["headers"].get("content-type", "").split(";")[0].strip() != "application/json":
        fail("createToken must send Content-Type: application/json")

    expected_authorization = "Bearer " + CREDENTIALS["accessToken"]
    for record in records[1:]:
        if record["headers"].get("authorization") != expected_authorization:
            fail(
                f"{record['operationId']} must send the createToken access token as "
                f"'Authorization: {expected_authorization}'"
            )

    # getTasks must identify the failed-task population. Any other optional
    # parameter that is set must carry a real value (checked above).
    tasks_record = records[indexes("getTasks")[0]]
    if tasks_record["query_params"].get("taskStatus") != ["Failed"]:
        fail(
            "getTasks must filter for taskStatus=Failed, got query "
            f"'{tasks_record['query']}'"
        )

    # getTask: the ids come from the listing, never from a guess.
    task_indexes = indexes("getTask")
    if not task_indexes:
        fail("the failed task detail was never fetched with getTask")
    if min(task_indexes) < indexes("getTasks")[0]:
        fail("getTask was called before the task listing it must come from")
    fetched = {records[i]["path_params"]["id"] for i in task_indexes}
    if not fetched <= LISTED_FAILED_TASK_IDS:
        fail(f"getTask was called with ids that the listing never returned: {fetched}")
    if FAILED_TASK_ID not in fetched:
        fail("getTask was never called for the most recent failed task")
    if any(records[i]["query"] for i in task_indexes):
        fail("getTask declares no query parameters; none may be sent")

    notifications_index = indexes("getNotifications")[0]
    if records[notifications_index]["query"]:
        fail("getNotifications declares no query parameters; none may be sent")

    # startSupportBundle: sent after the evidence was gathered, and carrying
    # exactly the derived scope and log selection.
    bundle_index = indexes("startSupportBundle")[0]
    if bundle_index < max(task_indexes) or bundle_index < notifications_index:
        fail(
            "the support bundle was requested before the task detail and the "
            "notifications had been read"
        )
    bundle_record = records[bundle_index]
    body = bundle_record["body_json"]
    if not isinstance(body, dict):
        fail("startSupportBundle must send a JSON object body")
    if set(body) != set(EXPECTED_SUPPORT_BUNDLE_BODY):
        fail(
            "startSupportBundle body must contain exactly "
            f"{sorted(EXPECTED_SUPPORT_BUNDLE_BODY)}; the unset 'options' object must "
            f"be omitted, got {sorted(body)}"
        )
    if set(body.get("scope", {})) != {"domains"}:
        fail(
            "SupportBundleSpec.scope must contain only 'domains'; the unset "
            f"'includeFreeHosts' flag must be omitted, got {sorted(body.get('scope', {}))}"
        )
    selected = body.get("logs", {})
    if set(selected) != set(EXPECTED_SUPPORT_BUNDLE_BODY["logs"]):
        fail(
            "SupportBundleSpec.logs must name exactly the log flags for the affected "
            f"components ({sorted(EXPECTED_SUPPORT_BUNDLE_BODY['logs'])}); unselected "
            f"flags must be omitted rather than sent as false, got {sorted(selected)}"
        )
    if body != EXPECTED_SUPPORT_BUNDLE_BODY:
        fail(
            f"startSupportBundle body {json.dumps(body, sort_keys=True)} does not match "
            f"{json.dumps(EXPECTED_SUPPORT_BUNDLE_BODY, sort_keys=True)}"
        )
    if bundle_record["headers"].get("content-type", "").split(";")[0].strip() != "application/json":
        fail("startSupportBundle must send Content-Type: application/json")

    # getSupportBundleStatus: polled until the collection reached a terminal state.
    poll_indexes = indexes("getSupportBundleStatus")
    minimum_polls = BUNDLE["inProgressPolls"] + 1
    if len(poll_indexes) != minimum_polls:
        fail(
            f"the support bundle was polled {len(poll_indexes)} time(s); it only "
            f"reaches its first terminal status on poll {minimum_polls}"
        )
    if min(poll_indexes) < bundle_index:
        fail("the support bundle status was polled before the collection was started")
    for index in poll_indexes:
        if records[index]["path_params"]["id"] != BUNDLE["id"]:
            fail("the support bundle status was polled for the wrong bundle id")
    for previous, current in zip(poll_indexes, poll_indexes[1:]):
        interval = records[current]["received_at"] - records[previous]["received_at"]
        if interval < 0.99:
            fail(
                "getSupportBundleStatus was polled faster than once per second "
                f"({interval:.3f}s between requests)"
            )

    # retryTask: last, bodyless PATCH against the diagnosed task.
    retry_index = indexes("retryTask")[0]
    if retry_index != len(records) - 1:
        fail("retryTask must be the last request; remediation follows the diagnosis")
    if retry_index < max(poll_indexes):
        fail("the task was retried before the support bundle collection finished")
    retry_record = records[retry_index]
    if retry_record["method"] != "PATCH":
        fail(f"retryTask must use PATCH, saw {retry_record['method']}")
    if retry_record["body"].strip():
        fail("retryTask declares no request body; none may be sent")
    if retry_record["path_params"]["id"] != FAILED_TASK_ID:
        fail("retryTask was called for a task other than the diagnosed one")


def main() -> None:
    check_protected_files()
    check_provenance()
    check_stdlib_only()
    with tempfile.TemporaryDirectory() as directory:
        log_path = Path(directory) / "requests.jsonl"
        report_path = Path(directory) / "report.json"
        run_diagnosis(log_path, report_path)
        check_report(report_path)
        check_requests(log_path)
    print("VERIFY OK")


if __name__ == "__main__":
    main()
