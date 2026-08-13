#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0244.

Runs the client against an in-process mock of the vSAN Data Protection snapshot
appliance and asserts the exact request wire shape recorded in the mock's
request log. No network socket or live VMware endpoint is used.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

PINNED_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
REPOSITORY = "https://github.com/vmware/vcf-api-specs"

EXPECTED_OPERATIONS = {
    "Snapservice.Clusters.ProtectionGroups_list": (
        "GET",
        "/api/snapservice/clusters/{cluster}/protection-groups",
    ),
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task": (
        "POST",
        "/api/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots",
    ),
    "Snapservice.Tasks_get": ("GET", "/api/snapservice/tasks/{task}"),
    "Snapservice.Clusters.ProtectionGroups.Snapshots_get": (
        "GET",
        "/api/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots/{snapshot}",
    ),
}

PROTECTED_SHA256 = {
    "docs/contract.json": "c0eb4261a038750302b01cd53cc72e782c124073008ad8fdf923e0e2a190c88a",
    "docs/official_sources.json": "bc9be51703824e8b9338ffebc6267c61ffe5d504828790b295919c94d4499216",
    "tests/mock_appliance.py": "f5fa42bf3a20171b8a48a3890cb6086a6d13ffda605ac09adbd7145f1c17eeeb",
}

CLUSTER = "domain-c21"
SESSION_ID = "8f1d0c2a-6b47-4e93-9a51-2f0c7e5d4b18"


def fail(message):
    raise SystemExit("VERIFY FAILED: " + message)


def check(condition, message):
    if not condition:
        fail(message)


# ---------------------------------------------------------------- fixtures


def check_protected_files():
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail("protected fixture missing: " + relative)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail("protected fixture changed: " + relative)


def check_provenance():
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    check(source.get("repository") == REPOSITORY, "contract repository is not vcf-api-specs")
    check(source.get("commit_sha") == PINNED_SHA, "contract commit sha is not pinned")
    check(source.get("spec_path") == SPEC_PATH, "contract spec_path is not the vSAN DP spec")
    check(source.get("license") == "Apache-2.0", "contract license changed")
    check(source.get("api_version") == "9.1.0.0", "contract api_version is not 9.1.0.0")

    security = contract.get("security", {})
    check(
        (security.get("type"), security.get("in"), security.get("name"))
        == ("apiKey", "header", "vmware-api-session-id"),
        "contract security scheme differs from the specification",
    )

    actual = {
        op.get("operationId"): (op.get("method"), op.get("path"))
        for op in contract.get("operations", [])
    }
    check(
        actual == EXPECTED_OPERATIONS,
        "contract operations differ from the specification: %s" % sorted(actual),
    )

    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))
    check(sources.get("repository") == REPOSITORY, "official_sources repository is wrong")
    check(
        sources.get("repository_commit_sha") == PINNED_SHA,
        "official_sources commit sha is not pinned",
    )
    check(sources.get("spec_path") == SPEC_PATH, "official_sources spec_path is wrong")
    check(sources.get("license") == "Apache-2.0", "official_sources license is wrong")
    recorded = {entry.get("operationId") for entry in sources.get("operations", [])}
    check(
        recorded == set(EXPECTED_OPERATIONS),
        "official_sources does not record every contract operationId",
    )
    for entry in sources.get("operations", []):
        check(
            entry.get("spec_path") == SPEC_PATH
            and entry.get("repository_commit_sha") == PINNED_SHA,
            "official_sources operation %r lacks spec path or commit sha"
            % entry.get("operationId"),
        )


def check_public_api(module):
    expected = {
        "SnapshotClient": [
            "base_url",
            "session_id",
            "poll_interval",
            "poll_timeout",
            "timeout",
        ],
        "list_protection_groups": [
            "self",
            "cluster",
            "pgs",
            "names",
            "states",
            "vms",
            "cluster_pairs",
        ],
        "create_snapshot": ["self", "cluster", "pg", "name", "retention"],
        "get_task": ["self", "task_id"],
        "wait_for_task": ["self", "task_id", "interval", "timeout"],
        "get_snapshot": ["self", "cluster", "pg", "snapshot"],
        "take_snapshot": [
            "self",
            "cluster",
            "pg",
            "name",
            "retention",
            "interval",
            "timeout",
        ],
        "RetentionPeriod": ["unit", "duration"],
    }
    callables = {
        "SnapshotClient": module.SnapshotClient,
        "RetentionPeriod": module.RetentionPeriod,
    }
    for name in expected:
        if name not in callables:
            callables[name] = getattr(module.SnapshotClient, name)
        actual = list(inspect.signature(callables[name]).parameters)
        check(actual == expected[name], "%s public signature changed: %r" % (name, actual))


# ------------------------------------------------------------- log helpers


def snapshots_path(pg, snapshot=None):
    base = "/api/snapservice/clusters/%s/protection-groups/%s/snapshots" % (CLUSTER, pg)
    return base if snapshot is None else base + "/" + snapshot


def require_session_header(entries):
    for entry in entries:
        check(
            entry["headers"].get("vmware-api-session-id") == SESSION_ID,
            "request %s %s did not carry the vmware-api-session-id header"
            % (entry["method"], entry["path"]),
        )


def task_polls(entries):
    return [e for e in entries if e["method"] == "GET" and e["path"].startswith("/api/snapservice/tasks/")]


# ------------------------------------------------------------- the scenarios


def scenario_create_without_retention(module, tmp):
    from mock_appliance import MockAppliance

    log = tmp / "no-retention.jsonl"
    with MockAppliance(log) as appliance:
        client = module.SnapshotClient(
            appliance.base_url, SESSION_ID, poll_interval=0.01, poll_timeout=20.0
        )
        started = time.monotonic()
        info = client.take_snapshot(CLUSTER, "pg-nightly", "pre-upgrade-manual")
        elapsed = time.monotonic() - started
        entries = appliance.requests()

    check(isinstance(info, dict), "take_snapshot did not return a Snapshots.Info object")
    check(info.get("name") == "pre-upgrade-manual", "returned snapshot has the wrong name")
    check(info.get("pg") == "pg-nightly", "returned snapshot has the wrong protection group")
    check(
        "expires_at" not in info,
        "snapshot taken without retention must not report an expiry",
    )

    require_session_header(entries)
    check(len(entries) == 6, "expected 6 requests, got %d: %s" % (
        len(entries), [(e["method"], e["path"]) for e in entries]))

    post = entries[0]
    check(post["method"] == "POST", "the first request must create the snapshot")
    check(
        post["path"] == snapshots_path("pg-nightly"),
        "create used the wrong path: " + post["path"],
    )
    check(
        post["raw_query"] == "vmw-task=true",
        "create must send exactly vmw-task=true, got %r" % post["raw_query"],
    )
    check(
        (post["headers"].get("content-type") or "").split(";")[0].strip()
        == "application/json",
        "create must send Content-Type: application/json",
    )
    check(
        post["body"] == {"name": "pre-upgrade-manual"},
        "CreateSpec must contain only the set properties, got: " + post["raw_body"],
    )
    check(
        "retention" not in post["raw_body"],
        "unset retention must be omitted from the request body, not sent empty: "
        + post["raw_body"],
    )

    polls = task_polls(entries)
    check(
        len(polls) == 4,
        "PENDING, RUNNING, and BLOCKED are all non-terminal; expected 4 polls, got %d"
        % len(polls),
    )
    check(
        elapsed + 0.005 >= 0.01 * (len(polls) - 1),
        "polls did not use the client's configured interval: %d polls in %.4fs"
        % (len(polls), elapsed),
    )
    check(
        all(p["path"] == "/api/snapservice/tasks/task-1" for p in polls),
        "polls did not address the task identifier returned by the 202 response",
    )
    check(
        entries[5]["method"] == "GET"
        and entries[5]["path"] == snapshots_path("pg-nightly", "snap-1"),
        "the snapshot must be read only after the task reached SUCCEEDED",
    )
    check(
        entries[5]["sequence"] > polls[-1]["sequence"],
        "the snapshot was read before the task reached a terminal status",
    )


def scenario_create_with_retention(module, tmp):
    from mock_appliance import MockAppliance

    log = tmp / "retention.jsonl"
    with MockAppliance(log) as appliance:
        client = module.SnapshotClient(
            appliance.base_url, SESSION_ID, poll_interval=0.01, poll_timeout=20.0
        )
        info = client.take_snapshot(
            CLUSTER,
            "pg-nightly",
            "quarter-close",
            retention=module.RetentionPeriod(unit="DAY", duration=7),
        )
        entries = appliance.requests()

    check(info.get("expires_at"), "a retained snapshot must report an expiry")
    post = entries[0]
    check(
        post["body"] == {"name": "quarter-close", "retention": {"unit": "DAY", "duration": 7}},
        "CreateSpec with retention has the wrong wire shape: " + post["raw_body"],
    )


def scenario_task_failure(module, tmp):
    from mock_appliance import MockAppliance

    log = tmp / "failure.jsonl"
    with MockAppliance(log) as appliance:
        client = module.SnapshotClient(
            appliance.base_url, SESSION_ID, poll_interval=0.01, poll_timeout=20.0
        )
        try:
            client.take_snapshot(CLUSTER, "pg-broken", "archive-attempt")
        except module.TaskFailedError as exc:
            failure = exc
        except Exception as exc:  # noqa: BLE001
            fail("a FAILED task must raise TaskFailedError, got %r" % (exc,))
        else:
            fail("a FAILED task must raise TaskFailedError, but take_snapshot returned")
        entries = appliance.requests()

    check(
        "quiesce" in str(failure).lower(),
        "TaskFailedError must carry the appliance's failure message, got: %s" % failure,
    )
    check(
        len(task_polls(entries)) == 3,
        "a failing task must also be polled to its terminal status",
    )
    check(
        not any(
            e["method"] == "GET" and e["path"].startswith(snapshots_path("pg-broken") + "/")
            for e in entries
        ),
        "no snapshot may be read after the task failed",
    )


def scenario_poll_timeout(module, tmp):
    from mock_appliance import MockAppliance

    log = tmp / "timeout.jsonl"
    with MockAppliance(log) as appliance:
        client = module.SnapshotClient(
            appliance.base_url, SESSION_ID, poll_interval=0.02, poll_timeout=20.0
        )
        started = time.monotonic()
        try:
            client.take_snapshot(
                CLUSTER, "pg-hung", "never-finishes", interval=0.02, timeout=0.3
            )
        except module.TaskTimeoutError:
            elapsed = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001
            fail("a task stuck below a terminal status must raise TaskTimeoutError, got %r" % (exc,))
        else:
            fail("a task stuck below a terminal status must raise TaskTimeoutError")
        entries = appliance.requests()

    polls = task_polls(entries)
    check(len(polls) >= 3, "the client must keep polling until the timeout, got %d polls" % len(polls))
    check(
        elapsed >= 0.04,
        "polling must pace itself with the configured interval, finished in %.4fs" % elapsed,
    )
    # A 0.3s budget paced by a 0.02s interval is ~15 polls; a client that never
    # waits between polls issues hundreds.
    check(
        len(polls) <= 60,
        "polling must wait the configured interval between requests, got %d polls "
        "in %.3fs" % (len(polls), elapsed),
    )
    check(
        elapsed + 0.02 >= 0.02 * (len(polls) - 1),
        "successive polls were not spaced by the configured 0.02s interval: "
        "%d polls in %.3fs" % (len(polls), elapsed),
    )
    check(elapsed < 10.0, "the poll timeout was not honoured, took %.3fs" % elapsed)
    check(
        not any(
            e["method"] == "GET" and e["path"].startswith(snapshots_path("pg-hung") + "/")
            for e in entries
        ),
        "no snapshot may be read after a poll timeout",
    )


def scenario_query_omission(module, tmp):
    from mock_appliance import MockAppliance

    log = tmp / "list.jsonl"
    with MockAppliance(log) as appliance:
        client = module.SnapshotClient(appliance.base_url, SESSION_ID)
        unfiltered = client.list_protection_groups(CLUSTER)
        by_name = client.list_protection_groups(CLUSTER, names=["nightly-tier1"])
        by_ids = client.list_protection_groups(CLUSTER, pgs=["pg-nightly", "pg-hung"])
        all_filters = client.list_protection_groups(
            CLUSTER,
            names=["nightly-tier1"],
            states=["ACTIVE"],
            vms=["vm-4021"],
            cluster_pairs=["pair-a", "pair-b"],
        )
        entries = appliance.requests()

    check(len(unfiltered) == 3, "unfiltered list must return every protection group")
    check(len(by_name) == 1, "name filter must narrow the result")
    check(len(by_ids) == 2, "identifier filter must narrow the result")
    check(isinstance(all_filters, list), "list must return the response items")

    require_session_header(entries)
    check(len(entries) == 4, "expected 4 list requests, got %d" % len(entries))
    check(
        entries[0]["raw_query"] == "",
        "unset optional query parameters must be omitted entirely, got %r"
        % entries[0]["raw_query"],
    )
    check(
        entries[1]["query_pairs"] == [["names", "nightly-tier1"]],
        "a single name filter must serialise as names=<value> only, got %r"
        % entries[1]["raw_query"],
    )
    check(
        sorted(entries[2]["query_pairs"])
        == sorted([["pgs", "pg-nightly"], ["pgs", "pg-hung"]]),
        "a repeated filter must use form/explode serialisation, got %r"
        % entries[2]["raw_query"],
    )
    check(
        sorted(entries[3]["query_pairs"])
        == sorted([
            ["names", "nightly-tier1"],
            ["states", "ACTIVE"],
            ["vms", "vm-4021"],
            ["cluster_pairs", "pair-a"],
            ["cluster_pairs", "pair-b"],
        ]),
        "every set list filter must use form/explode serialisation, got %r"
        % entries[3]["raw_query"],
    )


def scenario_unauthenticated(module, tmp):
    from mock_appliance import MockAppliance

    log = tmp / "auth.jsonl"
    with MockAppliance(log) as appliance:
        client = module.SnapshotClient(appliance.base_url, "")
        try:
            client.list_protection_groups(CLUSTER)
        except module.ApiError as exc:
            check(exc.status == 401, "a missing session must surface HTTP 401, got %s" % exc.status)
        except Exception as exc:  # noqa: BLE001
            fail("an appliance error response must raise ApiError, got %r" % (exc,))
        else:
            fail("an appliance error response must raise ApiError")


# ---------------------------------------------------------------------- main


def main():
    check_protected_files()
    check_provenance()

    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(TESTS))
    import mock_appliance as appliance_fixture

    # Install before importing the package so both ``urllib.request.urlopen``
    # and a direct standard-library import use the deterministic adapter.
    appliance_fixture.install_transport()
    try:
        import vsan_dp as module
    except Exception as exc:  # noqa: BLE001
        fail("could not import the vsan_dp package from src/: %r" % (exc,))

    for name in (
        "SnapshotClient",
        "RetentionPeriod",
        "ApiError",
        "TaskFailedError",
        "TaskTimeoutError",
        "VsanDpError",
    ):
        check(hasattr(module, name), "vsan_dp does not export %s" % name)
    check_public_api(module)

    scenarios = (
        scenario_create_without_retention,
        scenario_create_with_retention,
        scenario_task_failure,
        scenario_poll_timeout,
        scenario_query_omission,
        scenario_unauthenticated,
    )
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        for scenario in scenarios:
            try:
                scenario(module, tmp)
            except SystemExit:
                raise
            except Exception as exc:  # noqa: BLE001
                fail("%s raised %s: %s" % (scenario.__name__, type(exc).__name__, exc))

    print("PASS: vcf91-0244")


if __name__ == "__main__":
    main()
