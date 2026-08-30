#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0250.

Compiles the client together with the pinned loopback mock, runs one sweep, and asserts the exact
request wire shape recorded by the mock. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"

PINNED_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
EXPECTED_OPERATIONS = {
    "Snapservice.Sessions_create": ("POST", "/snapservice/sessions"),
    "Snapservice.Clusters.ProtectionGroups_list": (
        "GET",
        "/snapservice/clusters/{cluster}/protection-groups",
    ),
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task": (
        "POST",
        "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots",
    ),
    "Snapservice.Tasks_get": ("GET", "/snapservice/tasks/{task}"),
}

USERNAME = "svc-dataprotection@vsphere.local"
PASSWORD = "Sn@pSvc-9!lab"
CLUSTER = "domain-c9"
NAME_PREFIX = "vcf91-sweep"
BASE_PATH = "/api"
ACTIVE_GROUPS = ["pg-1001", "pg-1002", "pg-1004", "pg-1005"]
PAUSED_GROUP = "pg-1003"
RETENTION = {
    "pg-1002": {"unit": "HOUR", "duration": 12},
    "pg-1005": {"unit": "DAY", "duration": 30},
}

PROTECTED_SHA256 = {
    "docs/contract.json": "8567157d2c0faa770178a6adcc72a7b9d4c1f7f24ae73371403887f3de57afb7",
    "docs/official_sources.json": "0918f8364d5f6925cd4501810a8d369b0dcb71f59cfa693479729f1ada69b297",
    "tests/MockSnapserviceServer.java": "a8e712031db57e48796edd4393f6fbe71d672a4f132016753507a355e076e1c2",
    "tests/TestMain.java": "06fcb73a8fc2b63142e81736fda9655f27f681b3c71ade9590a9fdd1dade97fe",
    "tests/Json.java": "320c32806899d7abbfa48b78821b95c6d852487f64eec5ea8a18f4b41595691b",
}


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


# --------------------------------------------------------------------- fixtures


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
        fail("contract repository is not the official vcf-api-specs repository")
    if source.get("commit_sha") != PINNED_SHA or source.get("spec_path") != SPEC_PATH:
        fail("contract source is not pinned to the selected specification revision")
    if source.get("license") != "Apache-2.0" or source.get("api_version") != "9.1.0.0":
        fail("contract license or VCF API version changed")

    security = contract.get("security", {}).get("session", {})
    if (security.get("scheme"), security.get("in"), security.get("name")) != (
        "api_key_auth",
        "header",
        "vmware-api-session-id",
    ):
        fail("contract session security scheme differs from the specification")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")

    create = next(
        item
        for item in operations
        if item["operationId"] == "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
    )
    body = create["request_body"]
    if body.get("required_properties") != ["name"] or body.get("optional_properties") != ["retention"]:
        fail("CreateSpec required/optional property split changed")
    if body["properties"]["retention"].get("omit_when_unset") is not True:
        fail("contract no longer records that CreateSpec.retention is omitted when unset")

    listing = next(
        item for item in operations if item["operationId"] == "Snapservice.Clusters.ProtectionGroups_list"
    )
    optional_query = {
        parameter["name"]
        for parameter in listing["parameters"]
        if parameter["in"] == "query" and not parameter.get("required")
    }
    if optional_query != {"pgs", "names", "states"}:
        fail("contract optional query parameter set for the list operation changed")

    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))
    if sources.get("repository_commit_sha") != PINNED_SHA or sources.get("spec_path") != SPEC_PATH:
        fail("docs/official_sources.json is not pinned to the selected specification revision")
    if sources.get("license") != "Apache-2.0":
        fail("docs/official_sources.json license changed")
    recorded = {entry["operationId"] for entry in sources.get("operations", [])}
    if recorded != set(EXPECTED_OPERATIONS):
        fail("docs/official_sources.json does not record exactly the contract operationIds")


# ------------------------------------------------------------------------ run


def run_sweep() -> tuple[list[dict], dict]:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    classes = BUILD / "classes"
    classes.mkdir(parents=True)

    sources = [str(ROOT / "src/SnapserviceSweepClient.java")] + sorted(
        str(path) for path in (ROOT / "tests").glob("*.java")
    )
    compile_result = subprocess.run(
        ["javac", "-d", str(classes), *sources],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if compile_result.returncode != 0:
        fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

    run_result = subprocess.run(
        ["java", "-cp", str(classes), "TestMain", str(ROOT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    if run_result.returncode != 0:
        fail("the sweep did not complete:\n" + run_result.stdout + run_result.stderr)

    log_path = BUILD / "requests.jsonl"
    result_path = BUILD / "sweep-result.json"
    if not log_path.is_file() or not result_path.is_file():
        fail("the harness did not produce build/requests.jsonl and build/sweep-result.json")
    requests = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    return requests, result


# ------------------------------------------------------------------ assertions


def query_pairs(raw: str | None) -> list[tuple[str, str]]:
    if raw is None:
        return []
    pairs = []
    for chunk in raw.split("&"):
        key, _, value = chunk.partition("=")
        pairs.append((key, value))
    return pairs


def check_contract_only(requests: list[dict]) -> None:
    for entry in requests:
        if entry["operation_id"] is None:
            fail(
                "request {seq} went to {method} {path} which no operation in the contract names".format(
                    **entry
                )
            )


def check_sessions(requests: list[dict]) -> list[dict]:
    sessions = [item for item in requests if item["operation_id"] == "Snapservice.Sessions_create"]
    if len(sessions) != 2:
        fail(
            f"expected exactly 2 Snapservice.Sessions_create calls (one login, one refresh after the "
            f"token expired) but saw {len(sessions)}"
        )
    expected_auth = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    for entry in sessions:
        if entry["path"] != BASE_PATH + "/snapservice/sessions":
            fail(f"Snapservice.Sessions_create used path {entry['path']}")
        if entry["query"] is not None:
            fail("Snapservice.Sessions_create takes no parameters but sent query " + entry["query"])
        if entry["authorization"] != expected_auth:
            fail("Snapservice.Sessions_create did not present the basic_auth credentials")
        if entry["session_header"] is not None:
            fail("Snapservice.Sessions_create must not carry a vmware-api-session-id header")
        if entry["body"] != "":
            fail("Snapservice.Sessions_create has no request body but sent " + repr(entry["body"]))
        if entry["status"] != 201:
            fail(f"Snapservice.Sessions_create answered {entry['status']}, expected 201")
    return sessions


def check_listing(requests: list[dict]) -> None:
    listings = [
        item
        for item in requests
        if item["operation_id"] == "Snapservice.Clusters.ProtectionGroups_list"
    ]
    if len(listings) != 1:
        fail(
            f"expected exactly 1 Snapservice.Clusters.ProtectionGroups_list call (the sweep must not "
            f"restart after refreshing the token) but saw {len(listings)}"
        )
    entry = listings[0]
    if entry["path"] != f"{BASE_PATH}/snapservice/clusters/{CLUSTER}/protection-groups":
        fail(f"protection group listing used path {entry['path']}")
    pairs = query_pairs(entry["query"])
    if pairs != [("states", "ACTIVE")]:
        fail(
            "the listing query must contain only the states filter that is actually set; unset "
            f"optional parameters must be omitted, not sent empty. Saw: {entry['query']!r}"
        )
    if entry["body"] != "":
        fail("a GET listing must not carry a request body")
    if entry["status"] != 200:
        fail(f"protection group listing answered {entry['status']}, expected 200")


def check_snapshot_creates(requests: list[dict]) -> dict[str, str]:
    creates = [
        item
        for item in requests
        if item["operation_id"] == "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
    ]
    accepted = [item for item in creates if item["status"] == 202]
    prefix = f"{BASE_PATH}/snapservice/clusters/{CLUSTER}/protection-groups/"

    accepted_tasks = {}
    for entry in accepted:
        if not entry["path"].startswith(prefix) or not entry["path"].endswith("/snapshots"):
            fail(f"snapshot create used path {entry['path']}")
        protection_group = entry["path"][len(prefix) : -len("/snapshots")]
        if protection_group in accepted_tasks:
            fail(f"snapshot creation for {protection_group} was accepted more than once")
        try:
            task_id = json.loads(entry["response_body"])
        except (KeyError, ValueError):
            fail(f"snapshot create for {protection_group} did not return a JSON task identifier")
        if not isinstance(task_id, str) or not task_id:
            fail(f"snapshot create for {protection_group} returned invalid task id {task_id!r}")
        accepted_tasks[protection_group] = task_id

        if query_pairs(entry["query"]) != [("vmw-task", "true")]:
            fail(
                "the $Task snapshot create is selected by vmw-task=true alone; saw query "
                f"{entry['query']!r}"
            )
        if (entry["content_type"] or "").split(";")[0].strip() != "application/json":
            fail(f"snapshot create sent Content-Type {entry['content_type']!r}")

        try:
            spec = json.loads(entry["body"])
        except ValueError:
            fail(f"snapshot create body is not valid JSON: {entry['body']!r}")
        if not isinstance(spec, dict):
            fail(f"snapshot create body must be a CreateSpec object, saw {entry['body']!r}")

        expected_name = f"{NAME_PREFIX}-{protection_group}"
        if spec.get("name") != expected_name:
            fail(f"snapshot for {protection_group} was named {spec.get('name')!r}, expected {expected_name!r}")

        expected_retention = RETENTION.get(protection_group)
        if expected_retention is None:
            if set(spec) != {"name"}:
                fail(
                    f"{protection_group} has no retention period, so CreateSpec.retention must be "
                    f"omitted rather than sent empty or null. Body was {entry['body']!r}"
                )
        else:
            if set(spec) != {"name", "retention"}:
                fail(f"CreateSpec for {protection_group} carried unexpected properties: {entry['body']!r}")
            retention = spec["retention"]
            if not isinstance(retention, dict) or set(retention) != {"unit", "duration"}:
                fail(f"RetentionPeriod for {protection_group} is malformed: {entry['body']!r}")
            if retention != expected_retention:
                fail(
                    f"retention for {protection_group} was {retention}, expected {expected_retention}"
                )

    if sorted(accepted_tasks) != ACTIVE_GROUPS:
        fail(
            "exactly one snapshot must be created for each ACTIVE protection group "
            f"{ACTIVE_GROUPS} -- no work lost, none repeated. Saw {sorted(accepted_tasks)}"
        )
    if any(PAUSED_GROUP in entry["path"] for entry in creates):
        fail(f"{PAUSED_GROUP} is PAUSED and must not be snapshotted")
    return accepted_tasks


def check_phases(requests: list[dict]) -> None:
    listing_sequences = [
        item["seq"]
        for item in requests
        if item["operation_id"] == "Snapservice.Clusters.ProtectionGroups_list"
    ]
    create_sequences = [
        item["seq"]
        for item in requests
        if item["operation_id"]
        == "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
    ]
    poll_sequences = [
        item["seq"]
        for item in requests
        if item["operation_id"] == "Snapservice.Tasks_get"
    ]
    if not listing_sequences or not create_sequences or not poll_sequences:
        fail("the sweep did not execute all three phases: list, create, then poll")
    if not (max(listing_sequences) < min(create_sequences)):
        fail("all protection groups must be listed before any snapshot is created")
    if not (max(create_sequences) < min(poll_sequences)):
        fail("all snapshot creates must finish before any task polling begins")


def check_token_refresh(requests: list[dict]) -> None:
    rejected = [index for index, entry in enumerate(requests) if entry["status"] == 401]
    if len(rejected) != 1:
        fail(
            f"expected the run to hit the expired token exactly once, saw {len(rejected)} rejected "
            "requests; do not retry a request more than once per expiry"
        )
    index = rejected[0]
    if index + 2 >= len(requests):
        fail("the run stopped after the token expired instead of refreshing and continuing")

    refused = requests[index]
    refresh = requests[index + 1]
    replay = requests[index + 2]

    if refresh["operation_id"] != "Snapservice.Sessions_create":
        fail(
            "the request after the 401 must be Snapservice.Sessions_create, saw "
            f"{refresh['operation_id']}"
        )
    for field in ("method", "path", "query", "body"):
        if replay[field] != refused[field]:
            fail(
                "after refreshing the session the refused request must be replayed unchanged; "
                f"{field} went from {refused[field]!r} to {replay[field]!r}"
            )
    if replay["status"] != 202:
        fail(f"the replayed request answered {replay['status']}, expected 202")

    old_token = refused["session_header"]
    new_token = replay["session_header"]
    if old_token is None or new_token is None or old_token == new_token:
        fail("the replay must use the newly issued session token, not the expired one")

    for entry in requests[:index]:
        if entry["operation_id"] == "Snapservice.Sessions_create":
            continue
        if entry["session_header"] != old_token:
            fail(f"request {entry['seq']} used an unexpected session token before the refresh")
    for entry in requests[index + 2 :]:
        if entry["operation_id"] == "Snapservice.Sessions_create":
            fail("the session was refreshed more than once; refresh only in response to a 401")
        if entry["session_header"] != new_token:
            fail(f"request {entry['seq']} did not use the refreshed session token")


def check_authentication_discipline(requests: list[dict]) -> None:
    for entry in requests:
        if entry["operation_id"] == "Snapservice.Sessions_create":
            continue
        if entry["session_header"] is None:
            fail(f"request {entry['seq']} omitted the vmware-api-session-id header")
        if entry["authorization"] is not None:
            fail(
                f"request {entry['seq']} sent an Authorization header; only Snapservice.Sessions_create "
                "uses basic_auth, every other operation uses api_key_auth"
            )


def check_task_polling(
    requests: list[dict], result: dict, accepted_tasks: dict[str, str]
) -> None:
    polls: dict[str, int] = {}
    for entry in requests:
        if entry["operation_id"] != "Snapservice.Tasks_get":
            continue
        if entry["query"] is not None:
            fail("Snapservice.Tasks_get takes no query parameters, saw " + entry["query"])
        if entry["body"] != "":
            fail("a GET task poll must not carry a request body")
        task = entry["path"].rsplit("/", 1)[-1]
        polls[task] = polls.get(task, 0) + 1

    started_task_ids = set(accepted_tasks.values())
    if set(polls) != started_task_ids:
        fail(
            f"polled tasks {sorted(polls)} do not match the tasks returned by snapshot creates "
            f"{sorted(started_task_ids)}"
        )
    reported_tasks = {
        entry["protection_group"]: entry["task_id"] for entry in result["entries"]
    }
    if reported_tasks != accepted_tasks:
        fail(
            "result task identifiers are not associated with the protection groups whose create "
            f"responses returned them: reported {reported_tasks}, created {accepted_tasks}"
        )
    for task, count in polls.items():
        if count < 2:
            fail(f"task {task} was not polled until it reached a terminal state")
        if count > 4:
            fail(f"task {task} was polled {count} times; polling should stop at a terminal state")


def check_result(result: dict) -> None:
    if result.get("failure") is not None:
        fail("the sweep reported a failure: " + str(result["failure"]))
    if result.get("sessions_created") != 2:
        fail(
            f"the client reported {result.get('sessions_created')} sessions; exactly one login plus "
            "one reactive refresh is expected"
        )
    groups = [entry["protection_group"] for entry in result["entries"]]
    if groups != ACTIVE_GROUPS:
        fail(f"sweep result covered {groups}, expected {ACTIVE_GROUPS} in listing order")
    for entry in result["entries"]:
        if entry["status"] != "SUCCEEDED":
            fail(f"{entry['protection_group']} finished with status {entry['status']}")
        expected_name = f"{NAME_PREFIX}-{entry['protection_group']}"
        if entry["snapshot_name"] != expected_name:
            fail(f"{entry['protection_group']} reported snapshot name {entry['snapshot_name']!r}")
        if not entry["task_id"]:
            fail(f"{entry['protection_group']} did not report a task identifier")


def main() -> None:
    check_protected_files()
    check_provenance()
    requests, result = run_sweep()
    check_contract_only(requests)
    check_sessions(requests)
    check_listing(requests)
    accepted_tasks = check_snapshot_creates(requests)
    check_phases(requests)
    check_token_refresh(requests)
    check_authentication_discipline(requests)
    check_task_polling(requests, result, accepted_tasks)
    check_result(result)
    print(f"VERIFY OK: {len(requests)} requests, session refreshed once, 4 snapshots taken")


if __name__ == "__main__":
    main()
