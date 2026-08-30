#!/usr/bin/env python3
"""Protected acceptance harness for the vcfops_alerts package.

It stands up the contract-pinned loopback VCF Operations appliance from
mock_vcfops.py, drives the candidate package against it, and then reads the
mock's JSON Lines request log to assert the exact wire shape of every request.

No live VMware endpoint is contacted: the only host involved is 127.0.0.1.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "_verification" / "requests.jsonl"

sys.path.insert(0, str(ROOT))

import mock_vcfops  # noqa: E402
from mock_vcfops import (  # noqa: E402
    A1,
    A2,
    A3,
    A4,
    A5,
    A6,
    A7,
    ALERTS,
    AUTH_SOURCE,
    ERROR_USERNAME,
    MISSING_SORT_ALERTS,
    NON_JSON_USERNAME,
    PASSWORD,
    R1,
    R3,
    R_BAD_PAGE_INFO,
    R_EMPTY,
    R_HTTP_ERROR,
    R_MISSING_SORT,
    R_NON_JSON,
    R_RUNAWAY,
    TOKEN,
    USERNAME,
    MockAppliance,
)

ACQUIRE_TARGET = "/suite-api/api/auth/token/acquire"
ALERTS_TARGET = "/suite-api/api/alerts"

PACKAGE_NAME = "vcfops_alerts"
PACKAGE_DIR = ROOT / PACKAGE_NAME


# ---------------------------------------------------------------------------
# Tiny assertion harness
# ---------------------------------------------------------------------------

class CheckFailed(Exception):
    pass


FAILURES: list[str] = []
CHECKS = 0


def check(condition: bool, message: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise CheckFailed(message)


def check_equal(actual: Any, expected: Any, message: str) -> None:
    check(actual == expected, f"{message}\n    expected: {expected!r}\n    actual:   {actual!r}")


def scenario(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def decorate(fn: Callable[[], None]) -> Callable[[], None]:
        fn._scenario_name = name  # type: ignore[attr-defined]
        return fn

    return decorate


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def acquires(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e["operationId"] == "acquireToken"]


def alert_reads(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e["operationId"] == "getAlerts"]


def assert_no_stray_traffic(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        check(
            entry["operationId"] in ("acquireToken", "getAlerts"),
            f"request {entry['seq']} hit {entry['method']} {entry['path']}, which is not one "
            "of the two operations the contract names",
        )
        check_equal(
            entry["path"],
            ACQUIRE_TARGET if entry["operationId"] == "acquireToken" else ALERTS_TARGET,
            f"request {entry['seq']} used the wrong target; the contract's serverBasePath is "
            "/suite-api",
        )


def assert_acquire_shape(entry: dict[str, Any], auth_source: str | None) -> None:
    check_equal(entry["method"], "POST", "acquireToken must be a POST")
    check_equal(entry["path"], ACQUIRE_TARGET, "acquireToken target")
    check_equal(entry["rawQuery"], "", "acquireToken takes no query parameters")
    check(
        entry["headers"].get("content-type", "").split(";")[0].strip().lower()
        == "application/json",
        "acquireToken must declare Content-Type: application/json",
    )
    check(
        "application/json"
        in [p.split(";")[0].strip().lower() for p in entry["headers"].get("accept", "").split(",")],
        "acquireToken must ask for application/json via Accept",
    )
    check(
        entry["hasAuthorizationHeader"] is False,
        "acquireToken declares an empty security array in the pinned spec, so it must not "
        "carry an Authorization header",
    )

    body = entry["bodyJson"]
    check(isinstance(body, dict), "acquireToken body must be a JSON object")
    expected_members = ["password", "username"]
    if auth_source is not None:
        expected_members = ["authSource", "password", "username"]
    check_equal(
        sorted(body),
        expected_members,
        "username-password member set on the wire; an unset optional member must be omitted "
        "entirely, not sent as null or an empty string",
    )
    check_equal(body["username"], USERNAME, "username on the wire")
    check_equal(body["password"], PASSWORD, "password on the wire")
    if auth_source is not None:
        check_equal(body["authSource"], auth_source, "authSource on the wire")


def assert_read_shape(
    entry: dict[str, Any],
    page: int,
    page_size: int,
    alert_ids: list[str],
    resource_ids: list[str],
) -> None:
    check_equal(entry["method"], "GET", "getAlerts must be a GET")
    check_equal(entry["path"], ALERTS_TARGET, "getAlerts target")
    check(entry["bodyRaw"] in (None, ""), "getAlerts must not carry a request body")
    check(
        "application/json"
        in [p.split(";")[0].strip().lower() for p in entry["headers"].get("accept", "").split(",")],
        "getAlerts must ask for application/json via Accept",
    )
    check_equal(
        entry["headers"].get("authorization"),
        TOKEN,
        "getAlerts must carry the acquired auth-token verbatim in the Authorization header "
        "declared by the Token-based-authorization apiKey scheme",
    )

    query = entry["query"]
    expected_names = {"page", "pageSize"}
    if alert_ids:
        expected_names.add("id")
    if resource_ids:
        expected_names.add("resourceId")
    check_equal(
        sorted(query),
        sorted(expected_names),
        "getAlerts query parameter set; an unset optional parameter must be omitted entirely, "
        "not sent with an empty value",
    )
    check_equal(query["page"], [str(page)], "page on the wire")
    check_equal(query["pageSize"], [str(page_size)], "pageSize on the wire")
    if alert_ids:
        check_equal(
            query["id"],
            list(alert_ids),
            "id is a form/explode array: one repeated key per value, never comma-joined",
        )
    if resource_ids:
        check_equal(
            query["resourceId"],
            list(resource_ids),
            "resourceId is a form/explode array: one repeated key per value, never comma-joined",
        )


def assert_page_sequence(
    entries: list[dict[str, Any]],
    pages: list[int],
    page_size: int,
    alert_ids: list[str] | None = None,
    resource_ids: list[str] | None = None,
) -> None:
    reads = alert_reads(entries)
    check_equal(
        [int(e["query"]["page"][0]) for e in reads],
        pages,
        "page sequence on the wire; pages are 0-based and consecutive, and no extra page is "
        "fetched once page-info says the collection is complete",
    )
    for page, entry in zip(pages, reads):
        assert_read_shape(entry, page, page_size, alert_ids or [], resource_ids or [])


def ids_of(alerts: Any) -> list[str]:
    check(isinstance(alerts, list), f"collect_alerts must return a list, got {type(alerts)!r}")
    out = []
    for item in alerts:
        check(isinstance(item, dict), "each returned alert must be a dict")
        out.append(item.get("alertId"))
    return out


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
# Fixture storage order is A5, A2, A7, A1, A6, A3, A4 and is deliberately not
# the emission order, so a per-page sort produces the wrong overall result.
# Emission order is startTimeUTC descending, then alertId ascending.

FULL_ORDER = [A4, A6, A2, A1, A3, A5, A7]


def build_client(mod: Any, **kwargs: Any) -> Any:
    params = {
        "base_url": BASE_URL,
        "username": USERNAME,
        "password": PASSWORD,
    }
    params.update(kwargs)
    return mod.VcfOperationsClient(**params)


@scenario("whole collection is paged out and emitted in the contracted order")
def scenario_full_collection() -> None:
    client = build_client(CANDIDATE, page_size=2)
    alerts = client.collect_alerts()
    check_equal(ids_of(alerts), FULL_ORDER, "emission order for the unfiltered collection")
    by_id = {item["alertId"]: item for item in ALERTS}
    check_equal(
        alerts,
        [by_id[alert_id] for alert_id in FULL_ORDER],
        "collect_alerts must return the complete raw alert objects, not a projection",
    )

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    check_equal(len(acquires(entries)), 1, "acquireToken call count")
    assert_acquire_shape(acquires(entries)[0], auth_source=None)
    assert_page_sequence(entries, pages=[0, 1, 2, 3], page_size=2)
    check_equal(entries[0]["operationId"], "acquireToken", "the token is acquired before any read")


@scenario("a collection that fills its last page exactly does not trigger a trailing read")
def scenario_exact_multiple() -> None:
    client = build_client(CANDIDATE, page_size=1)
    alerts = client.collect_alerts(resource_ids=[R3])
    check_equal(ids_of(alerts), [A4, A7], "emission order for the R3 collection")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    assert_page_sequence(entries, pages=[0, 1], page_size=1, resource_ids=[R3])


@scenario("a repeated array parameter is serialized form/explode, never comma-joined")
def scenario_repeated_array_param() -> None:
    client = build_client(CANDIDATE, page_size=2)
    alerts = client.collect_alerts(resource_ids=[R1, R3])
    check_equal(ids_of(alerts), [A4, A6, A1, A3, A7], "emission order for the R1+R3 collection")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    assert_page_sequence(entries, pages=[0, 1, 2], page_size=2, resource_ids=[R1, R3])
    for entry in alert_reads(entries):
        check(
            entry["rawQuery"].count("resourceId=") == 2,
            f"resourceId must appear twice in the query string, saw {entry['rawQuery']!r}",
        )


@scenario("filtering by alert id omits resourceId entirely")
def scenario_alert_ids_only() -> None:
    client = build_client(CANDIDATE, page_size=5)
    alerts = client.collect_alerts(alert_ids=[A2, A4])
    check_equal(ids_of(alerts), [A4, A2], "emission order for the id-filtered collection")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    assert_page_sequence(entries, pages=[0], page_size=5, alert_ids=[A2, A4])


@scenario("an empty collection costs exactly one read and returns an empty list")
def scenario_empty_collection() -> None:
    client = build_client(CANDIDATE, page_size=2)
    alerts = client.collect_alerts(resource_ids=[R_EMPTY])
    check_equal(ids_of(alerts), [], "an empty collection emits nothing")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    assert_page_sequence(entries, pages=[0], page_size=2, resource_ids=[R_EMPTY])


@scenario("a supplied auth source is sent, and only then")
def scenario_auth_source() -> None:
    client = build_client(CANDIDATE, auth_source=AUTH_SOURCE, page_size=7)
    alerts = client.collect_alerts()
    check_equal(ids_of(alerts), FULL_ORDER, "emission order with an auth source configured")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    check_equal(len(acquires(entries)), 1, "acquireToken call count")
    assert_acquire_shape(acquires(entries)[0], auth_source=AUTH_SOURCE)
    assert_page_sequence(entries, pages=[0], page_size=7)


@scenario("the token is acquired once and reused across collections")
def scenario_token_reuse() -> None:
    client = build_client(CANDIDATE, page_size=7)
    first = client.collect_alerts()
    second = client.collect_alerts()
    check_equal(ids_of(first), FULL_ORDER, "first collection")
    check_equal(ids_of(second), FULL_ORDER, "second collection")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    check_equal(
        len(acquires(entries)),
        1,
        "acquireToken must run once per client and the token reused for every read",
    )
    check_equal(len(alert_reads(entries)), 2, "getAlerts call count")


@scenario("the documented constructor defaults are exposed and pageSize is explicit")
def scenario_constructor_defaults() -> None:
    parameters = inspect.signature(CANDIDATE.VcfOperationsClient).parameters
    for name, expected in [
        ("auth_source", None),
        ("page_size", 1000),
        ("max_pages", 1000),
        ("timeout", 30.0),
    ]:
        check(name in parameters, f"VcfOperationsClient must accept {name}")
        check_equal(parameters[name].default, expected, f"documented default for {name}")

    client = CANDIDATE.VcfOperationsClient(BASE_URL, USERNAME, PASSWORD)
    check_equal(
        APPLIANCE.entries(),
        [],
        "constructing a client must not acquire a token before the first read",
    )
    alerts = client.collect_alerts()
    check_equal(ids_of(alerts), FULL_ORDER, "collection read with constructor defaults")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    assert_page_sequence(entries, pages=[0], page_size=1000)


@scenario("a rejected login raises before any read is attempted")
def scenario_bad_credentials() -> None:
    client = build_client(CANDIDATE, password="wrong-dummy-pass", page_size=2)
    try:
        client.collect_alerts()
    except CANDIDATE.VcfOperationsAuthError as exc:
        message = str(exc)
        check(
            "wrong-dummy-pass" not in message and USERNAME not in message,
            "neither credential member may appear in the raised message",
        )
    else:
        raise CheckFailed("a 401 from acquireToken must raise VcfOperationsAuthError")

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    check_equal(len(acquires(entries)), 1, "acquireToken call count")
    check_equal(acquires(entries)[0]["status"], 401, "the mock rejected the login")
    check_equal(len(alert_reads(entries)), 0, "no read may be attempted without a token")


@scenario("non-authentication HTTP failures raise the protocol error")
def scenario_http_failures() -> None:
    acquire_client = build_client(CANDIDATE, username=ERROR_USERNAME)
    try:
        acquire_client.collect_alerts()
    except CANDIDATE.VcfOperationsProtocolError:
        pass
    else:
        raise CheckFailed("a non-2xx acquireToken response must raise VcfOperationsProtocolError")

    entries = APPLIANCE.entries()
    check_equal(len(acquires(entries)), 1, "failed acquireToken call count")
    check_equal(acquires(entries)[0]["status"], 503, "the mock's acquireToken failure status")
    check_equal(len(alert_reads(entries)), 0, "no read may follow a failed acquireToken")

    APPLIANCE.reset()
    read_client = build_client(CANDIDATE, page_size=2)
    try:
        read_client.collect_alerts(resource_ids=[R_HTTP_ERROR])
    except CANDIDATE.VcfOperationsProtocolError:
        pass
    else:
        raise CheckFailed("a non-2xx getAlerts response must raise VcfOperationsProtocolError")

    entries = APPLIANCE.entries()
    check_equal(len(alert_reads(entries)), 1, "failed getAlerts call count")
    check_equal(alert_reads(entries)[0]["status"], 503, "the mock's getAlerts failure status")


@scenario("non-JSON and unusable page-info responses raise the protocol error")
def scenario_malformed_responses() -> None:
    acquire_client = build_client(CANDIDATE, username=NON_JSON_USERNAME)
    try:
        acquire_client.collect_alerts()
    except CANDIDATE.VcfOperationsProtocolError:
        pass
    else:
        raise CheckFailed("a non-JSON acquireToken response must raise VcfOperationsProtocolError")
    check_equal(len(acquires(APPLIANCE.entries())), 1, "non-JSON acquireToken call count")
    check_equal(len(alert_reads(APPLIANCE.entries())), 0, "no read may follow a malformed token")
    APPLIANCE.reset()

    for resource_id, label in [
        (R_NON_JSON, "a response that is not JSON"),
        (R_BAD_PAGE_INFO, "a page with no usable pageInfo.totalCount"),
    ]:
        client = build_client(CANDIDATE, page_size=2)
        try:
            client.collect_alerts(resource_ids=[resource_id])
        except CANDIDATE.VcfOperationsProtocolError:
            pass
        else:
            raise CheckFailed(f"{label} must raise VcfOperationsProtocolError")
        check_equal(len(alert_reads(APPLIANCE.entries())), 1, f"read count for {label}")
        APPLIANCE.reset()


@scenario("alerts with absent sort members are preserved and handled deterministically")
def scenario_missing_sort_members() -> None:
    first_client = build_client(CANDIDATE, page_size=2)
    first = first_client.collect_alerts(resource_ids=[R_MISSING_SORT])

    APPLIANCE.reset()
    second_client = build_client(CANDIDATE, page_size=3)
    second = second_client.collect_alerts(resource_ids=[R_MISSING_SORT])

    check_equal(second, first, "missing sort members must produce the same whole-collection order")
    check_equal(
        sorted(item["fixture"] for item in first),
        sorted(item["fixture"] for item in MISSING_SORT_ALERTS),
        "alerts missing startTimeUTC or alertId must not be dropped",
    )
    check_equal(
        sorted(first, key=lambda item: item["fixture"]),
        sorted(MISSING_SORT_ALERTS, key=lambda item: item["fixture"]),
        "alerts with optional sort members absent must be returned as raw objects",
    )
    positions = {item["fixture"]: index for index, item in enumerate(first)}
    check(
        positions["same-time-a"] < positions["same-time-z"],
        "present alertId values must break equal startTimeUTC ties in ascending order",
    )


@scenario("a collection that never runs out is bounded by max_pages")
def scenario_runaway_guard() -> None:
    client = build_client(CANDIDATE, page_size=2, max_pages=4)
    try:
        client.collect_alerts(resource_ids=[R_RUNAWAY])
    except CANDIDATE.VcfOperationsProtocolError:
        pass
    else:
        raise CheckFailed(
            "a server that keeps returning pages must stop at max_pages and raise "
            "VcfOperationsProtocolError, not page forever"
        )

    entries = APPLIANCE.entries()
    assert_no_stray_traffic(entries)
    assert_page_sequence(entries, pages=[0, 1, 2, 3], page_size=2, resource_ids=[R_RUNAWAY])


@scenario("constructor arguments are validated")
def scenario_argument_validation() -> None:
    bad = [
        ({"page_size": 0}, "page_size below 1"),
        ({"page_size": -1}, "a negative page_size"),
        ({"max_pages": 0}, "max_pages below 1"),
        ({"max_pages": -1}, "a negative max_pages"),
        ({"username": ""}, "an empty username"),
        ({"password": ""}, "an empty password"),
        ({"base_url": ""}, "an empty base_url"),
        ({"auth_source": ""}, "an empty auth_source"),
    ]
    for kwargs, label in bad:
        try:
            build_client(CANDIDATE, **kwargs)
        except ValueError:
            continue
        raise CheckFailed(f"{label} must raise ValueError")
    check_equal(APPLIANCE.entries(), [], "argument validation must not touch the appliance")


@scenario("the documented exception hierarchy is exported")
def scenario_exception_hierarchy() -> None:
    for name in (
        "VcfOperationsError",
        "VcfOperationsAuthError",
        "VcfOperationsProtocolError",
    ):
        check(hasattr(CANDIDATE, name), f"{PACKAGE_NAME} must export {name}")
    check(
        issubclass(CANDIDATE.VcfOperationsAuthError, CANDIDATE.VcfOperationsError),
        "VcfOperationsAuthError must derive from VcfOperationsError",
    )
    check(
        issubclass(CANDIDATE.VcfOperationsProtocolError, CANDIDATE.VcfOperationsError),
        "VcfOperationsProtocolError must derive from VcfOperationsError",
    )


@scenario("the package is stdlib only")
def scenario_stdlib_only() -> None:
    check(PACKAGE_DIR.is_dir(), f"{PACKAGE_NAME}/ must be a package directory at the repo root")
    check((PACKAGE_DIR / "__init__.py").is_file(), f"{PACKAGE_NAME}/__init__.py must exist")

    allowed = set(sys.stdlib_module_names) | {PACKAGE_NAME}
    sources = sorted(PACKAGE_DIR.rglob("*.py"))
    check(bool(sources), "the package must contain Python sources")
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                check(
                    root in allowed,
                    f"{source.relative_to(ROOT)} imports {root!r}, which is not in the standard "
                    "library; the package must be stdlib only",
                )


SCENARIOS = [
    scenario_full_collection,
    scenario_exact_multiple,
    scenario_repeated_array_param,
    scenario_alert_ids_only,
    scenario_empty_collection,
    scenario_auth_source,
    scenario_token_reuse,
    scenario_constructor_defaults,
    scenario_bad_credentials,
    scenario_http_failures,
    scenario_malformed_responses,
    scenario_missing_sort_members,
    scenario_runaway_guard,
    scenario_argument_validation,
    scenario_exception_hierarchy,
    scenario_stdlib_only,
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def import_candidate() -> Any:
    if not PACKAGE_DIR.is_dir():
        print(f"FAIL  {PACKAGE_NAME}/ package directory is missing at the repo root")
        raise SystemExit(1)
    try:
        module = __import__(PACKAGE_NAME)
    except Exception:
        print(f"FAIL  importing {PACKAGE_NAME} raised:")
        traceback.print_exc()
        raise SystemExit(1)
    for name in ("VcfOperationsClient", "VcfOperationsAuthError", "VcfOperationsProtocolError"):
        if not hasattr(module, name):
            print(f"FAIL  {PACKAGE_NAME} does not export {name}")
            raise SystemExit(1)
    return module


def main() -> int:
    global APPLIANCE, BASE_URL, CANDIDATE

    CANDIDATE = import_candidate()

    APPLIANCE = MockAppliance(LOG_PATH)
    BASE_URL = APPLIANCE.start()
    print(f"loopback VCF Operations appliance on {BASE_URL}{mock_vcfops.BASE_PATH}")
    print(f"contract {mock_vcfops.PINNED_SPEC_PATH} @ {mock_vcfops.PINNED_TAG} "
          f"({mock_vcfops.PINNED_COMMIT[:12]})")
    print()

    passed = 0
    try:
        for fn in SCENARIOS:
            name = fn._scenario_name  # type: ignore[attr-defined]
            APPLIANCE.reset()
            try:
                fn()
            except CheckFailed as exc:
                FAILURES.append(f"{name}\n  {exc}")
                print(f"FAIL  {name}")
                for line in str(exc).splitlines():
                    print(f"      {line}")
            except Exception as exc:  # noqa: BLE001 - report anything the candidate raises
                FAILURES.append(f"{name}\n  unexpected {type(exc).__name__}: {exc}")
                print(f"FAIL  {name}")
                for line in traceback.format_exc().splitlines():
                    print(f"      {line}")
            else:
                passed += 1
                print(f"ok    {name}")
    finally:
        APPLIANCE.stop()

    print()
    print(f"{passed}/{len(SCENARIOS)} scenarios passed, {CHECKS} assertions evaluated")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} scenario(s)")
        return 1
    print("PASS")
    return 0


APPLIANCE: MockAppliance
BASE_URL: str
CANDIDATE: Any

if __name__ == "__main__":
    raise SystemExit(main())
