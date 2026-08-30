"""Deterministic acceptance verifier for vcf90-0120."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ROOT / ".protected"
SRC = ROOT / "src"


def fail(message):
    raise SystemExit("FAIL: " + message)


def check(condition, message):
    if not condition:
        fail(message)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail("cannot read %s: %s" % (path.relative_to(ROOT), exc))


def check_provenance():
    contract = load_json(ROOT / "docs" / "contract.json")
    sources = load_json(ROOT / "docs" / "official_sources.json")
    spec_path = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
    sha = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
    operation_ids = [
        "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
        "Snapservice.Tasks_get",
    ]
    check(contract.get("source", {}).get("tag") == "9.0.0.0", "contract source tag is not 9.0.0.0")
    check(contract.get("source", {}).get("api_version") == "9.0.0.0", "contract is not the 9.0 revision")
    check(contract.get("source", {}).get("commit_sha") == sha, "contract source SHA changed")
    check(contract.get("source", {}).get("spec_path") == spec_path, "contract spec path changed")
    check(sources.get("repository_tag") == "9.0.0.0", "official source tag is not recorded")
    check(sources.get("repository_commit_sha") == sha, "official source SHA changed")
    check(sources.get("spec_path") == spec_path, "official spec path changed")
    check("/" + sha + "/" in sources.get("spec_url", ""), "official spec URL is not commit-pinned")
    check([item.get("operationId") for item in contract.get("operations", [])] == operation_ids,
          "contract must name exactly the two selected operationIds")
    check([item.get("operationId") for item in sources.get("operations", [])] == operation_ids,
          "official_sources must record every selected operationId")
    for item in sources.get("operations", []):
        check(item.get("spec_path") == spec_path, "an operation omits its spec path")
        check(item.get("repository_tag") == "9.0.0.0", "an operation omits its source tag")
        check(item.get("repository_commit_sha") == sha, "an operation omits its source SHA")


def check_stdlib_only():
    stdlib = set(sys.stdlib_module_names)
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                continue
            for root in roots:
                check(root in stdlib, "%s imports non-stdlib module %s" % (path.relative_to(ROOT), root))
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("dependencies = []" in project, "pyproject.toml must have no runtime dependencies")


def load_package():
    sys.path.insert(0, str(SRC))
    try:
        import vsan_dp
    except Exception as exc:
        fail("could not import vsan_dp: %r" % (exc,))
    for name in (
        "SnapshotClient",
        "RetentionPeriod",
        "VsanDpError",
        "ApiError",
        "TaskFailedError",
    ):
        check(hasattr(vsan_dp, name), "vsan_dp does not export %s" % name)
    return vsan_dp


def run_expiry_scenario(module, tmp):
    sys.path.insert(0, str(PROTECTED))
    from mock_appliance import (
        FRESH_ACCESS_TOKEN,
        MockAppliance,
        OLD_ACCESS_TOKEN,
        SNAPSHOT_ID,
    )

    refresh_calls = []

    def refresh_access_token():
        refresh_calls.append("refresh")
        return FRESH_ACCESS_TOKEN

    log_path = tmp / "requests.jsonl"
    with MockAppliance(log_path) as appliance:
        client = module.SnapshotClient(
            appliance.base_url,
            OLD_ACCESS_TOKEN,
            refresh_access_token,
            poll_interval=0,
            timeout=2,
        )
        result = client.take_snapshot(
            "cluster/primary A", "pg west/1", "quarter-close"
        )
        entries = appliance.requests()

    check(result == SNAPSHOT_ID, "take_snapshot did not resume to the successful task result")
    check(refresh_calls == ["refresh"], "the one expired token must trigger one refresh")
    check(client.access_token == FRESH_ACCESS_TOKEN, "replacement token was not stored on the client")
    check(len(entries) == 4, "expected POST, rejected poll, and two resumed polls; got %d requests" % len(entries))

    posts = [entry for entry in entries if entry["method"] == "POST"]
    check(len(posts) == 1, "snapshot creation was restarted or duplicated after refresh")
    post = posts[0]
    check(
        post["path"]
        == "/api/snapservice/clusters/cluster%2Fprimary%20A/protection-groups/pg%20west%2F1/snapshots",
        "path parameters were not escaped as individual URL segments: %r" % post["path"],
    )
    check(post["raw_query"] == "vmw-task=true", "create query wire shape changed")
    try:
        post_body = json.loads(post["body_utf8"])
    except ValueError:
        fail("create body was not valid JSON: %r" % post["body_utf8"])
    check(post_body == {"name": "quarter-close"},
          "unset retention must be omitted; body was %r" % post_body)
    check(post["body_length"] == len(post["body_utf8"].encode("utf-8")), "Content-Length/body mismatch")
    check(post["headers"].get("content-type") == "application/json", "create Content-Type changed")
    check(post["headers"].get("vmware-api-session-id") == OLD_ACCESS_TOKEN, "create used the wrong token")
    check(post["response_status"] == 202, "create was not accepted before expiry")

    polls = [entry for entry in entries if entry["method"] == "GET"]
    check([entry["response_status"] for entry in polls] == [401, 200, 200],
          "poll did not resume after the 401: %r" % [entry["response_status"] for entry in polls])
    check([entry["headers"].get("vmware-api-session-id") for entry in polls]
          == [OLD_ACCESS_TOKEN, FRESH_ACCESS_TOKEN, FRESH_ACCESS_TOKEN],
          "the failed request was not retried with the replacement token")
    check(all(entry["path"] == "/api/snapservice/tasks/task-snapshot-72" for entry in polls),
          "refresh lost or replaced the accepted task identifier")
    check(all(entry["raw_query"] == "" for entry in polls), "task polls must not add a query")
    check(all(entry["body_utf8"] == "" for entry in polls), "GET task requests must not carry a body")


def run_retention_scenario(module, tmp):
    from mock_appliance import FRESH_ACCESS_TOKEN, MockAppliance, OLD_ACCESS_TOKEN

    refresh_calls = []
    log_path = tmp / "retention.jsonl"
    with MockAppliance(log_path) as appliance:
        client = module.SnapshotClient(
            appliance.base_url,
            OLD_ACCESS_TOKEN,
            lambda: refresh_calls.append(True) or FRESH_ACCESS_TOKEN,
            poll_interval=0,
            timeout=2,
        )
        task_id = client.create_snapshot(
            "cluster-a", "pg-a", "retained", module.RetentionPeriod("DAY", 3)
        )
        entries = appliance.requests()
    check(task_id == "task-snapshot-72", "create_snapshot did not decode the bare JSON task identifier")
    check(refresh_calls == [], "successful create must not refresh the token")
    check(len(entries) == 1, "retention check expected one request")
    try:
        body = json.loads(entries[0]["body_utf8"])
    except ValueError:
        fail("retained create body was not valid JSON: %r" % entries[0]["body_utf8"])
    check(body == {
        "name": "retained",
        "retention": {"unit": "DAY", "duration": 3},
    }, "set retention did not match CreateSpec wire shape: %r" % body)


def run_error_scenarios(module, tmp):
    from mock_appliance import FRESH_ACCESS_TOKEN, MockAppliance, OLD_ACCESS_TOKEN

    refresh_calls = []
    with MockAppliance(tmp / "create-retry.jsonl") as appliance:
        client = module.SnapshotClient(
            appliance.base_url,
            "expired-before-create",
            lambda: refresh_calls.append("refresh") or OLD_ACCESS_TOKEN,
            poll_interval=0,
            timeout=2,
        )
        task_id = client.create_snapshot("cluster-a", "pg-a", "retry-create")
        entries = appliance.requests()
    check(task_id == "task-snapshot-72", "a 401 snapshot create was not retried successfully")
    check(refresh_calls == ["refresh"], "a 401 snapshot create must trigger one refresh")
    check(client.access_token == OLD_ACCESS_TOKEN, "create retry token was not stored on the client")
    check(len(entries) == 2, "a 401 snapshot create must make exactly one retry")
    check([entry["response_status"] for entry in entries] == [401, 202],
          "snapshot create did not retry after 401: %r"
          % [entry["response_status"] for entry in entries])
    check([entry["headers"].get("vmware-api-session-id") for entry in entries]
          == ["expired-before-create", OLD_ACCESS_TOKEN],
          "snapshot create retry did not use the replacement token")
    for field in ("method", "raw_target", "body_utf8"):
        check(entries[0][field] == entries[1][field],
              "snapshot create retry changed request %s" % field)

    refresh_calls = []
    with MockAppliance(tmp / "retry-failed.jsonl") as appliance:
        client = module.SnapshotClient(
            appliance.base_url,
            FRESH_ACCESS_TOKEN,
            lambda: refresh_calls.append("refresh") or FRESH_ACCESS_TOKEN,
            poll_interval=0,
            timeout=2,
        )
        try:
            client.create_snapshot("cluster-a", "pg-a", "rejected")
        except module.ApiError as exc:
            check(exc.status == 401, "failed refreshed request surfaced HTTP %s" % exc.status)
        else:
            fail("a retried 401 must surface ApiError")
        entries = appliance.requests()
    check(refresh_calls == ["refresh"], "a failed refreshed request must not hide the first refresh")
    check([entry["response_status"] for entry in entries] == [401, 401],
          "an unsuccessful refreshed request must surface after one retry")

    refresh_calls = []
    with MockAppliance(tmp / "non-auth-error.jsonl") as appliance:
        client = module.SnapshotClient(
            appliance.base_url,
            OLD_ACCESS_TOKEN,
            lambda: refresh_calls.append("refresh") or FRESH_ACCESS_TOKEN,
            poll_interval=0,
            timeout=2,
        )
        client.create_snapshot("cluster-a", "pg-a", "accepted")
        try:
            client.create_snapshot("cluster-a", "pg-a", "duplicate")
        except module.ApiError as exc:
            check(exc.status == 400, "non-401 error surfaced HTTP %s" % exc.status)
        else:
            fail("a non-401 appliance error must surface ApiError")
        entries = appliance.requests()
    check(refresh_calls == [], "a non-401 error must not refresh the access token")
    check([entry["response_status"] for entry in entries] == [202, 400],
          "a non-401 error must surface without retrying the failed request")


def check_mock_rejects_unnamed_route(tmp):
    from mock_appliance import MockAppliance

    with MockAppliance(tmp / "unknown.jsonl") as appliance:
        try:
            urllib.request.urlopen(appliance.base_url + "/snapservice/info/about", timeout=2)
        except urllib.error.HTTPError as exc:
            check(exc.code == 404, "unnamed operation returned HTTP %s" % exc.code)
        else:
            fail("mock served an operation not named by docs/contract.json")


def main():
    check_provenance()
    check_stdlib_only()
    module = load_package()
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        run_expiry_scenario(module, tmp)
        run_retention_scenario(module, tmp)
        run_error_scenarios(module, tmp)
        check_mock_rejects_unnamed_route(tmp)
    print("PASS: vcf90-0120")


if __name__ == "__main__":
    main()
