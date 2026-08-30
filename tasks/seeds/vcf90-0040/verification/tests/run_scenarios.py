#!/usr/bin/env python3
"""Drives vcflcm against the loopback mock and asserts the exact wire shape.

Protected fixture for vcf90-0040 - do not modify.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from mock_vcenter import (  # noqa: E402
    APPLY,
    CANCEL,
    GET_INIT_SPEC,
    GET_STATUS,
    MockVCenter,
    Scenario,
    read_log,
    status_sample,
)

import vcflcm  # noqa: E402

CONTRACT = ROOT / "docs" / "contract.json"
SESSION = "f0a1c2d3-4e5f-6071-8293-a4b5c6d7e8f9"
BASE = "/api/vcenter/lcm/deployment/migration-upgrade"
STATUS_PATH = BASE + "/status"
POLL_INTERVAL = 2.5


class Failure(AssertionError):
    pass


def check(condition: Any, detail: str) -> None:
    if not condition:
        raise Failure(detail)


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def assert_common(entry: dict[str, Any], label: str) -> None:
    check(
        entry["headers"]["accept"] == "application/json",
        f"{label}: Accept header must be application/json, got "
        f"{entry['headers']['accept']!r}",
    )
    check(
        entry["session_header"] == SESSION,
        f"{label}: request must carry the vmware-api-session-id header",
    )


def assert_no_body(entry: dict[str, Any], label: str) -> None:
    check(
        entry["headers"]["content-type"] is None,
        f"{label}: a request with no body must not send a Content-Type header, got "
        f"{entry['headers']['content-type']!r}",
    )
    check(
        entry["body_bytes"] == 0 and entry["body_text"] == "",
        f"{label}: expected no request body, got {entry['body_text']!r}",
    )


def assert_entry(
    entry: dict[str, Any],
    operation_id: str,
    method: str,
    path: str,
    query: dict[str, str],
    body: dict[str, Any] | None,
    label: str,
) -> None:
    check(
        entry["operation_id"] == operation_id,
        f"{label}: expected {operation_id}, the mock matched "
        f"{entry['operation_id']!r} for {entry['method']} {entry['path']}",
    )
    check(entry["method"] == method, f"{label}: expected method {method}")
    check(entry["path"] == path, f"{label}: expected path {path}, got {entry['path']}")
    check(
        entry["query"] == query,
        f"{label}: expected query {query}, got {entry['query']}",
    )
    assert_common(entry, label)
    if body is None:
        assert_no_body(entry, label)
        return
    check(
        entry["headers"]["content-type"] == "application/json",
        f"{label}: a JSON body must be sent with Content-Type application/json, got "
        f"{entry['headers']['content-type']!r}",
    )
    check(
        entry["body_json"] == body,
        f"{label}: expected request body {json.dumps(body, sort_keys=True)}, got "
        f"{entry['body_text']!r}",
    )
    check(
        isinstance(entry["body_json"], dict)
        and sorted(entry["body_json"]) == sorted(body),
        f"{label}: unset optional properties must be omitted, got keys "
        f"{sorted(entry['body_json']) if isinstance(entry['body_json'], dict) else None}",
    )


def assert_kinds(entries: list[dict[str, Any]], expected: list[str], label: str) -> None:
    actual = [entry["operation_id"] for entry in entries]
    check(
        actual == expected,
        f"{label}: expected the request sequence {expected}, got {actual}",
    )


def build(scenario: Scenario, log: Path):
    mock = MockVCenter(CONTRACT, log, SESSION, scenario)
    base_url = mock.start()
    client = vcflcm.MigrationUpgradeClient(
        base_url, SESSION, vcflcm.load_contract(CONTRACT)
    )
    return mock, base_url, client


def driver(client, sleep: SleepRecorder, max_polls: int = 12):
    return vcflcm.MigrationUpgradeDriver(
        client, poll_interval=POLL_INTERVAL, max_polls=max_polls, sleep=sleep
    )


# -- scenarios -------------------------------------------------------------


def scenario_success(log: Path) -> None:
    scenario = Scenario(
        status_samples=[
            # No current_state: the appliance reports that no upgrade is running,
            # so this terminal-looking sample is not the outcome of our apply.
            status_sample(
                "SUCCEEDED",
                None,
                identifier=None,
                cancelable=False,
                end_time="2026-02-01T09:00:00.000Z",
            ),
            status_sample("RUNNING", "STAGING"),
            status_sample("RUNNING", "PREPARING", remaining_replication_data=4096),
            status_sample("BLOCKED", "PREPARED", remaining_replication_data=12),
            status_sample("RUNNING", "SWITCHOVER"),
            status_sample(
                "SUCCEEDED",
                "UPGRADED",
                cancelable=False,
                end_time="2026-03-14T02:41:07.000Z",
            ),
        ]
    )
    mock, _, client = build(scenario, log)
    sleep = SleepRecorder()
    try:
        outcome = driver(client, sleep).run()
    finally:
        mock.stop()

    check(
        isinstance(outcome, vcflcm.UpgradeOutcome),
        "run must return an UpgradeOutcome",
    )
    check(outcome.status == "SUCCEEDED", f"expected SUCCEEDED, got {outcome.status}")
    check(
        outcome.current_state == "UPGRADED",
        f"expected current_state UPGRADED, got {outcome.current_state}",
    )
    check(
        outcome.polls == 6,
        "the run must be polled through the non-running, RUNNING and BLOCKED "
        f"samples: expected 6 status polls, got {outcome.polls}",
    )
    check(not outcome.canceled, "a successful run must not report a cancellation")
    check(
        outcome.end_time == "2026-03-14T02:41:07.000Z",
        f"expected the terminal end_time, got {outcome.end_time}",
    )
    check(
        outcome.upgrade_identifier == "upg-8f21"
        and outcome.upgrade_to == "9.0.1.00000",
        "upgrade_info identifier and upgrade_to must be reported",
    )
    check(outcome.errors == (), f"expected no errors, got {outcome.errors}")
    check(
        sleep.calls == [POLL_INTERVAL] * 6,
        "the driver must wait poll_interval before each status request, including "
        f"the first: expected 6 waits of {POLL_INTERVAL}, got {sleep.calls}",
    )

    entries = read_log(log)
    assert_kinds(
        entries,
        [GET_INIT_SPEC, APPLY] + [GET_STATUS] * 6,
        "success",
    )
    assert_entry(entries[0], GET_INIT_SPEC, "GET", BASE, {}, None, "success/get")
    assert_entry(
        entries[1],
        APPLY,
        "POST",
        BASE,
        {"action": "apply"},
        None,
        "success/apply",
    )
    check(
        entries[1]["body_json"] is None,
        "with neither ApplySpec property set the optional request body must be "
        f"omitted entirely, got {entries[1]['body_text']!r}",
    )
    for index, entry in enumerate(entries[2:]):
        assert_entry(
            entry, GET_STATUS, "GET", STATUS_PATH, {}, None, f"success/status{index}"
        )


def scenario_pause_only(log: Path) -> None:
    scenario = Scenario(
        status_samples=[
            status_sample("RUNNING", "STAGING"),
            status_sample(
                "SUCCEEDED",
                "UPGRADED",
                cancelable=False,
                end_time="2026-03-14T02:41:07.000Z",
            ),
        ]
    )
    mock, _, client = build(scenario, log)
    sleep = SleepRecorder()
    try:
        outcome = driver(client, sleep).run(pause="BEFORE_SWITCHOVER")
    finally:
        mock.stop()

    check(outcome.status == "SUCCEEDED", "pause-only run must reach SUCCEEDED")
    entries = read_log(log)
    assert_kinds(entries, [GET_INIT_SPEC, APPLY, GET_STATUS, GET_STATUS], "pause")
    assert_entry(
        entries[1],
        APPLY,
        "POST",
        BASE,
        {"action": "apply"},
        {"pause": "BEFORE_SWITCHOVER"},
        "pause/apply",
    )
    check(
        scenario.applied_spec == {"pause": "BEFORE_SWITCHOVER"},
        f"the appliance received {scenario.applied_spec!r}",
    )


def scenario_switchover_only(log: Path) -> None:
    scenario = Scenario(
        status_samples=[
            status_sample("PENDING", "INITIALIZED"),
            status_sample("RUNNING", "STAGING"),
            status_sample(
                "SUCCEEDED",
                "UPGRADED",
                cancelable=False,
                end_time="2026-03-14T02:41:07.000Z",
            ),
        ]
    )
    mock, _, client = build(scenario, log)
    sleep = SleepRecorder()
    try:
        outcome = driver(client, sleep).run(
            start_switchover="2026-03-14T02:00:00.000Z"
        )
    finally:
        mock.stop()

    check(outcome.status == "SUCCEEDED", "switchover run must reach SUCCEEDED")
    check(
        outcome.polls == 3,
        f"PENDING is not terminal: expected 3 status polls, got {outcome.polls}",
    )
    entries = read_log(log)
    assert_entry(
        entries[1],
        APPLY,
        "POST",
        BASE,
        {"action": "apply"},
        {"start_switchover": "2026-03-14T02:00:00.000Z"},
        "switchover/apply",
    )


def scenario_rejected_specs(log: Path) -> None:
    mock, _, client = build(Scenario(status_samples=[status_sample("RUNNING", "STAGING")]), log)
    try:
        try:
            client.apply(
                pause="BEFORE_SWITCHOVER",
                start_switchover="2026-03-14T02:00:00.000Z",
            )
        except vcflcm.InvalidApplySpec:
            pass
        else:
            raise Failure(
                "apply must reject pause together with start_switchover before "
                "issuing a request"
            )
        try:
            client.apply(pause="AFTER_SWITCHOVER")
        except vcflcm.InvalidApplySpec:
            pass
        else:
            raise Failure("apply must reject a pause value outside the PausePolicy enum")
    finally:
        mock.stop()

    entries = read_log(log)
    check(
        entries == [],
        f"a locally rejected ApplySpec must not reach the appliance, got {entries}",
    )


def scenario_failure_then_cancel(log: Path) -> None:
    failure = [("com.vmware.vcenter.lcm.replication.failed", "Data replication failed.")]
    scenario = Scenario(
        status_samples=[
            status_sample("RUNNING", "STAGING"),
            status_sample("RUNNING", "PREPARING", remaining_replication_data=8192),
            status_sample("FAILED", "PREPARING", cancelable=True, errors=failure),
        ],
        post_cancel_samples=[
            status_sample("RUNNING", "CANCELING", cancelable=False),
            status_sample(
                "CANCELED",
                "CANCELED",
                cancelable=False,
                end_time="2026-03-14T03:02:44.000Z",
                errors=failure,
            ),
        ],
    )
    mock, _, client = build(scenario, log)
    sleep = SleepRecorder()
    try:
        outcome = driver(client, sleep).run(cancel_on_failure=True)
    finally:
        mock.stop()

    check(
        outcome.status == "CANCELED",
        f"cancel_on_failure must poll on to CANCELED, got {outcome.status}",
    )
    check(outcome.canceled, "outcome.canceled must record that cancel was issued")
    check(
        outcome.polls == 5,
        f"expected 3 polls before cancel and 2 after, got {outcome.polls}",
    )
    check(
        outcome.errors == ("Data replication failed.",),
        f"expected the notification error text, got {outcome.errors}",
    )
    check(
        sleep.calls == [POLL_INTERVAL] * 5,
        f"expected a wait before each of the 5 polls, got {sleep.calls}",
    )

    entries = read_log(log)
    assert_kinds(
        entries,
        [GET_INIT_SPEC, APPLY, GET_STATUS, GET_STATUS, GET_STATUS, CANCEL, GET_STATUS, GET_STATUS],
        "cancel",
    )
    assert_entry(
        entries[5], CANCEL, "POST", BASE, {"action": "cancel"}, None, "cancel/cancel"
    )


def scenario_failure_without_cancel(log: Path) -> None:
    failure = [("com.vmware.vcenter.lcm.precheck.failed", "Precheck failed on host esx-04.")]
    scenario = Scenario(
        status_samples=[
            status_sample("RUNNING", "STAGING"),
            status_sample("FAILED", "STAGING", errors=failure),
        ]
    )
    mock, _, client = build(scenario, log)
    sleep = SleepRecorder()
    try:
        outcome = driver(client, sleep).run(cancel_on_failure=False)
    finally:
        mock.stop()

    check(outcome.status == "FAILED", f"expected FAILED, got {outcome.status}")
    check(not outcome.canceled, "no cancellation may be reported")
    check(
        outcome.errors == ("Precheck failed on host esx-04.",),
        f"expected the notification error text, got {outcome.errors}",
    )
    entries = read_log(log)
    assert_kinds(entries, [GET_INIT_SPEC, APPLY, GET_STATUS, GET_STATUS], "no-cancel")
    check(
        all(entry["operation_id"] != CANCEL for entry in entries),
        "cancel must not be issued when cancel_on_failure is false",
    )


def scenario_not_configured(log: Path) -> None:
    mock, _, client = build(Scenario(configured=False), log)
    sleep = SleepRecorder()
    try:
        try:
            driver(client, sleep).run()
        except vcflcm.UpgradeNotConfigured:
            pass
        else:
            raise Failure("a 404 from the InitSpec read must raise UpgradeNotConfigured")
    finally:
        mock.stop()

    entries = read_log(log)
    assert_kinds(entries, [GET_INIT_SPEC], "not-configured")
    check(sleep.calls == [], f"no polling may happen, got {sleep.calls}")


def scenario_timeout(log: Path) -> None:
    scenario = Scenario(status_samples=[status_sample("RUNNING", "PREPARING")])
    mock, _, client = build(scenario, log)
    sleep = SleepRecorder()
    try:
        try:
            driver(client, sleep, max_polls=4).run()
        except vcflcm.UpgradePollTimeout as exc:
            check(
                exc.polls == 4,
                f"UpgradePollTimeout.polls must report the budget spent, got {exc.polls}",
            )
        else:
            raise Failure("a run that never reaches a terminal status must time out")
    finally:
        mock.stop()

    entries = read_log(log)
    assert_kinds(entries, [GET_INIT_SPEC, APPLY] + [GET_STATUS] * 4, "timeout")
    check(sleep.calls == [POLL_INTERVAL] * 4, f"expected 4 waits, got {sleep.calls}")


def scenario_mock_is_pinned(log: Path) -> None:
    """The mock serves only the operations the contract names."""

    mock, base_url, _ = build(
        Scenario(status_samples=[status_sample("RUNNING", "STAGING")]), log
    )
    try:
        for method, url in (
            ("PUT", base_url + BASE),
            ("POST", base_url + BASE + "?action=check"),
            ("GET", base_url + BASE + "?action=apply"),
            ("GET", base_url + "/api/vcenter/lcm/deployment/repository"),
            ("GET", base_url + "/api/session"),
        ):
            status, envelope = _probe(method, url, SESSION)
            check(
                status == 404 and envelope.get("error_type") == "NOT_FOUND",
                f"{method} {url} must not be served, got HTTP {status}",
            )
        status, envelope = _probe("GET", base_url + STATUS_PATH, "not-a-session")
        check(
            status == 401 and envelope.get("error_type") == "UNAUTHENTICATED",
            f"a request without a valid session must be rejected, got HTTP {status}",
        )
    finally:
        mock.stop()

    entries = read_log(log)
    check(
        [entry["operation_id"] for entry in entries[:5]] == [None] * 5,
        "unserved routes must be logged without an operation id",
    )


def _probe(method: str, url: str, session: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "vmware-api-session-id": session},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


SCENARIOS = (
    scenario_success,
    scenario_pause_only,
    scenario_switchover_only,
    scenario_rejected_specs,
    scenario_failure_then_cancel,
    scenario_failure_without_cancel,
    scenario_not_configured,
    scenario_timeout,
    scenario_mock_is_pinned,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vcf90-0040-") as workdir:
        for scenario in SCENARIOS:
            log = Path(workdir) / f"{scenario.__name__}.jsonl"
            try:
                scenario(log)
            except Failure as exc:
                print(f"FAIL [{scenario.__name__}] {exc}")
                return 1
            print(f"ok   {scenario.__name__}")
    print("PASS: contract wire shape and poll-to-terminal upgrade verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
