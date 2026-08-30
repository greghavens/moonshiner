#!/usr/bin/env python3
"""Protected verification for the VCF 9.0 guest customization package.

Boots the contract-pinned loopback vCenter (mock_vcenter.py) on 127.0.0.1,
drives vcf_guest_customization through every scenario, then reads the mock's
request log and asserts the exact wire shape, including that the precheck gates
the mutating PUT and that unset optional fields never reach the wire.

No live VMware endpoint is contacted. The session id is a fixture dummy.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESSION_ID = "dummy-vcf90-session-id-0045"
PACKAGE = "vcf_guest_customization"

CHECKS = 0
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if condition:
        return True
    FAILURES.append(label + (f"\n  {detail}" if detail else ""))
    print(f"FAIL {label}")
    if detail:
        print(f"  {detail}")
    return False


def check_eq(label: str, expected, actual) -> bool:
    return check(
        label, expected == actual, f"expected: {expected!r}\n  actual:   {actual!r}"
    )


def fail(label: str, detail: str = "") -> None:
    check(label, False, detail)


# ---------------------------------------------------------------------------
# static checks on the delivered package
# ---------------------------------------------------------------------------


def check_stdlib_only(package_dir: Path) -> None:
    allowed = set(sys.stdlib_module_names)
    sources = sorted(package_dir.rglob("*.py"))
    check("the package ships at least one module", bool(sources))
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            fail(f"{source.name} parses", str(exc))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                check(
                    f"{source.name} imports only the standard library ({name})",
                    name in allowed or name == PACKAGE,
                )


# ---------------------------------------------------------------------------
# the loopback fixture
# ---------------------------------------------------------------------------


def start_mock(tmp: Path):
    port_file = tmp / "port"
    log_file = tmp / "requests.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "mock_vcenter.py"), str(port_file), str(log_file)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            err = process.stderr.read().decode("utf-8", "replace")
            raise SystemExit(f"mock_vcenter.py exited early:\n{err}")
        if port_file.exists():
            text = port_file.read_text(encoding="utf-8").strip()
            if text:
                return process, int(text), log_file
        time.sleep(0.05)
    process.kill()
    raise SystemExit("mock_vcenter.py did not report a port")


def read_log(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    return [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def attempt(label: str, call):
    """Run a call that is expected to succeed; a raise is recorded as a failure."""
    try:
        value = call()
    except Exception as exc:  # noqa: BLE001 - any raise here is a failure
        fail(label, f"raised {type(exc).__name__}: {exc}")
        return None
    check(label, True)
    return value


def expect_raises(label: str, exc_type, call):
    try:
        result = call()
    except exc_type as exc:
        check(label, True)
        return exc
    except Exception as exc:  # noqa: BLE001 - wrong exception type is a failure
        fail(label, f"raised {type(exc).__name__}: {exc}")
        return None
    fail(label, f"returned {result!r} instead of raising {exc_type.__name__}")
    return None


def summarise() -> int:
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} failure(s), {CHECKS} checks")
        return 1
    print(f"ALL TESTS PASSED ({CHECKS} checks)")
    return 0


def main() -> int:
    package_dir = ROOT / PACKAGE
    if not check(f"{PACKAGE}/__init__.py exists", (package_dir / "__init__.py").is_file()):
        return summarise()

    check_stdlib_only(package_dir)

    sys.path.insert(0, str(ROOT))
    try:
        package = __import__(PACKAGE)
    except Exception as exc:  # noqa: BLE001
        fail(f"importing {PACKAGE}", f"{type(exc).__name__}: {exc}")
        return summarise()
    check(f"{PACKAGE} imports", True)

    required = [
        "VcenterSession",
        "CustomizationResult",
        "CustomizationError",
        "VmNotFoundError",
        "AmbiguousVmError",
        "CustomizationPrecheckFailed",
        "CustomizationApiError",
        "apply_named_customization",
    ]
    missing = [name for name in required if not hasattr(package, name)]
    for name in required:
        check(f"{PACKAGE} exports {name}", name not in missing)
    if missing:
        return summarise()

    VcenterSession = package.VcenterSession
    CustomizationResult = package.CustomizationResult
    CustomizationError = package.CustomizationError
    VmNotFoundError = package.VmNotFoundError
    AmbiguousVmError = package.AmbiguousVmError
    CustomizationPrecheckFailed = package.CustomizationPrecheckFailed
    CustomizationApiError = package.CustomizationApiError
    apply_named_customization = package.apply_named_customization

    for name in (
        "VmNotFoundError",
        "AmbiguousVmError",
        "CustomizationPrecheckFailed",
        "CustomizationApiError",
    ):
        check(
            f"{name} derives from CustomizationError",
            issubclass(getattr(package, name), CustomizationError),
        )
    check(
        "CustomizationResult is a dataclass",
        dataclasses.is_dataclass(CustomizationResult),
    )
    if dataclasses.is_dataclass(CustomizationResult):
        check(
            "CustomizationResult is frozen",
            bool(getattr(CustomizationResult, "__dataclass_params__", None))
            and CustomizationResult.__dataclass_params__.frozen,
        )
        check_eq(
            "CustomizationResult field order",
            [
                "vm",
                "vm_name",
                "power_state",
                "spec_name",
                "check_status",
                "supported_guest_os",
                "supported_power_state",
            ],
            [f.name for f in dataclasses.fields(CustomizationResult)],
        )

    with tempfile.TemporaryDirectory(prefix="vcf90-0045-") as raw_tmp:
        tmp = Path(raw_tmp)
        process, port, log_file = start_mock(tmp)
        try:
            try:
                session = VcenterSession(f"http://127.0.0.1:{port}", SESSION_ID)
                run_scenarios(
                    session,
                    apply_named_customization,
                    VmNotFoundError,
                    AmbiguousVmError,
                    CustomizationPrecheckFailed,
                    CustomizationApiError,
                )
            except Exception as exc:  # noqa: BLE001 - report, then judge the wire
                fail("the scenarios run to completion", f"{type(exc).__name__}: {exc}")
            log = read_log(log_file)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    check_wire(log)
    return summarise()


# ---------------------------------------------------------------------------
# behaviour
# ---------------------------------------------------------------------------


def run_scenarios(
    session,
    apply_named_customization,
    VmNotFoundError,
    AmbiguousVmError,
    CustomizationPrecheckFailed,
    CustomizationApiError,
) -> None:
    raised = []

    # 1. the happy path: precheck says SUPPORTED, the PUT goes out.
    plain = attempt(
        "a customizable virtual machine is customized",
        lambda: apply_named_customization(session, "vcf90-web-01", "vcf90-linux-prep"),
    )
    check_eq("applied result vm", "vm-1201", getattr(plain, "vm", None))
    check_eq("applied result vm_name", "vcf90-web-01", getattr(plain, "vm_name", None))
    check_eq(
        "applied result power_state", "POWERED_OFF", getattr(plain, "power_state", None)
    )
    check_eq(
        "applied result spec_name", "vcf90-linux-prep", getattr(plain, "spec_name", None)
    )
    check_eq(
        "applied result check_status", "SUPPORTED", getattr(plain, "check_status", None)
    )
    check(
        "applied result supported_guest_os is True",
        getattr(plain, "supported_guest_os", None) is True,
    )
    check(
        "applied result supported_power_state is True",
        getattr(plain, "supported_power_state", None) is True,
    )

    # 2. the same apply, narrowed by two of the optional list filters.
    filtered = attempt(
        "supplied list filters are accepted",
        lambda: apply_named_customization(
            session,
            "vcf90-web-01",
            "vcf90-linux-prep",
            folders=("group-v22", "group-v23"),
            power_states=("POWERED_OFF",),
        ),
    )
    check_eq("filtered result vm", "vm-1201", getattr(filtered, "vm", None))

    # 3. clearing a pending customization sends no members at all.
    cleared = attempt(
        "a pending customization can be cleared",
        lambda: apply_named_customization(session, "vcf90-clear-04", None),
    )
    check_eq("cleared result vm", "vm-1204", getattr(cleared, "vm", None))
    check("cleared result spec_name is None", getattr(cleared, "spec_name", "x") is None)
    check_eq(
        "cleared result check_status", "SUPPORTED", getattr(cleared, "check_status", None)
    )

    # 4. the precheck refuses on power state, so nothing is changed.
    gated_power = expect_raises(
        "a powered on virtual machine raises CustomizationPrecheckFailed",
        CustomizationPrecheckFailed,
        lambda: apply_named_customization(session, "vcf90-db-02", "vcf90-linux-prep"),
    )
    if gated_power is not None:
        raised.append(gated_power)
        check_eq("gated power vm", "vm-1202", getattr(gated_power, "vm", None))
        check_eq(
            "gated power vm_name", "vcf90-db-02", getattr(gated_power, "vm_name", None)
        )
        check_eq(
            "gated power check_status",
            "NOT_SUPPORTED",
            getattr(gated_power, "check_status", None),
        )
        check(
            "gated power supported_guest_os is True",
            getattr(gated_power, "supported_guest_os", None) is True,
        )
        check(
            "gated power supported_power_state is False",
            getattr(gated_power, "supported_power_state", None) is False,
        )

    # 5. the precheck refuses on guest OS and never reached the power step, so
    #    the optional supported_power_state member is absent from the response.
    gated_os = expect_raises(
        "an unsupported guest OS raises CustomizationPrecheckFailed",
        CustomizationPrecheckFailed,
        lambda: apply_named_customization(
            session, "vcf90-legacy-03", "vcf90-linux-prep"
        ),
    )
    if gated_os is not None:
        raised.append(gated_os)
        check_eq("gated os vm", "vm-1203", getattr(gated_os, "vm", None))
        check(
            "gated os supported_guest_os is False",
            getattr(gated_os, "supported_guest_os", None) is False,
        )
        check(
            "gated os supported_power_state is None",
            getattr(gated_os, "supported_power_state", "x") is None,
        )

    # 6. the precheck itself fails, so the mutation is still never attempted.
    check_error = expect_raises(
        "a 503 from the precheck raises CustomizationApiError",
        CustomizationApiError,
        lambda: apply_named_customization(session, "vcf90-flaky-05", "vcf90-linux-prep"),
    )
    if check_error is not None:
        raised.append(check_error)
        check_eq("precheck error status", 503, getattr(check_error, "status_code", None))
        check_eq(
            "precheck error operation",
            "Vcenter.Vm.Guest.Customization_check",
            getattr(check_error, "operation_id", None),
        )
        check_eq(
            "precheck error type",
            "SERVICE_UNAVAILABLE",
            getattr(check_error, "error_type", None),
        )
        messages = list(getattr(check_error, "messages", ()) or ())
        check("precheck error carries a message", bool(messages))
        check(
            "precheck error messages are strings",
            all(isinstance(m, str) for m in messages),
        )
        check_eq(
            "precheck error carries default messages",
            ["The guest customization service is temporarily unavailable."],
            messages,
        )

    # 7. the PUT is reached and rejected: the error is surfaced, not swallowed.
    set_error = expect_raises(
        "an unknown specification name raises CustomizationApiError",
        CustomizationApiError,
        lambda: apply_named_customization(
            session, "vcf90-nospec-06", "vcf90-no-such-spec"
        ),
    )
    if set_error is not None:
        raised.append(set_error)
        check_eq("set error status", 404, getattr(set_error, "status_code", None))
        check_eq(
            "set error operation",
            "Vcenter.Vm.Guest.Customization_set",
            getattr(set_error, "operation_id", None),
        )
        check_eq("set error type", "NOT_FOUND", getattr(set_error, "error_type", None))
        check_eq(
            "set error carries default messages",
            ["No customization specification named vcf90-no-such-spec was found."],
            list(getattr(set_error, "messages", ()) or ()),
        )

    # 8. an unknown name is refused before anything else is issued.
    absent = expect_raises(
        "an unknown virtual machine name raises VmNotFoundError",
        VmNotFoundError,
        lambda: apply_named_customization(session, "vcf90-ghost-99", "vcf90-linux-prep"),
    )
    if absent is not None:
        raised.append(absent)
        check_eq("absent vm_name", "vcf90-ghost-99", getattr(absent, "vm_name", None))

    # 9. two matches is ambiguous, and is also refused before anything else.
    ambiguous = expect_raises(
        "a duplicated virtual machine name raises AmbiguousVmError",
        AmbiguousVmError,
        lambda: apply_named_customization(session, "vcf90-twin-07", "vcf90-linux-prep"),
    )
    if ambiguous is not None:
        raised.append(ambiguous)
        check_eq(
            "ambiguous vm_name", "vcf90-twin-07", getattr(ambiguous, "vm_name", None)
        )
        check_eq(
            "ambiguous matches",
            ("vm-1207", "vm-1208"),
            tuple(getattr(ambiguous, "matches", ()) or ()),
        )

    # 10. even a malformed 2xx response without check_status fails closed.
    indeterminate = expect_raises(
        "a missing check_status raises CustomizationPrecheckFailed",
        CustomizationPrecheckFailed,
        lambda: apply_named_customization(
            session, "vcf90-indeterminate-09", "vcf90-linux-prep"
        ),
    )
    if indeterminate is not None:
        raised.append(indeterminate)
        check_eq(
            "indeterminate vm", "vm-1209", getattr(indeterminate, "vm", None)
        )
        check(
            "indeterminate check_status is None",
            getattr(indeterminate, "check_status", "x") is None,
        )
        check(
            "indeterminate supported_guest_os is None",
            getattr(indeterminate, "supported_guest_os", "x") is None,
        )
        check(
            "indeterminate supported_power_state is None",
            getattr(indeterminate, "supported_power_state", "x") is None,
        )

    # 11. redirects are non-2xx responses and must not be followed.
    list_error = expect_raises(
        "a redirect from VM lookup raises CustomizationApiError",
        CustomizationApiError,
        lambda: apply_named_customization(
            session, "vcf90-redirect-10", "vcf90-linux-prep"
        ),
    )
    if list_error is not None:
        raised.append(list_error)
        check_eq("list error status", 302, getattr(list_error, "status_code", None))
        check_eq(
            "list error operation",
            "Vcenter.VM_list",
            getattr(list_error, "operation_id", None),
        )
        check_eq("list error type", "ERROR", getattr(list_error, "error_type", None))
        check_eq(
            "list error carries default messages",
            ["The inventory request was redirected."],
            list(getattr(list_error, "messages", ()) or ()),
        )

    for exc in raised:
        check(
            f"{type(exc).__name__} does not leak the session id",
            SESSION_ID not in str(exc) and SESSION_ID not in repr(exc.args),
        )


# ---------------------------------------------------------------------------
# wire shape
# ---------------------------------------------------------------------------

LIST_OP = "Vcenter.VM_list"
CHECK_OP = "Vcenter.Vm.Guest.Customization_check"
SET_OP = "Vcenter.Vm.Guest.Customization_set"


def check_wire(log: list[dict]) -> None:
    if not check("the mock recorded requests", bool(log)):
        return

    for entry in log:
        seq = entry["sequence"]
        check(
            f"request {seq} reached a contract operation",
            entry["operationId"] is not None,
            f"{entry['method']} {entry['target']}",
        )
        check_eq(f"request {seq} session header", SESSION_ID, entry["sessionHeader"])
        check(
            f"request {seq} accepts application/json",
            (entry["accept"] or "").split(";")[0].strip() == "application/json",
            f"Accept: {entry['accept']!r}",
        )

    lists = [e for e in log if e["operationId"] == LIST_OP]
    checks = [e for e in log if e["operationId"] == CHECK_OP]
    sets = [e for e in log if e["operationId"] == SET_OP]

    check_eq("Vcenter.VM_list call count", 11, len(lists))
    check_eq("Vcenter.Vm.Guest.Customization_check call count", 8, len(checks))
    check_eq("Vcenter.Vm.Guest.Customization_set call count", 4, len(sets))
    check_eq("total request count", 23, len(log))

    # --- Vcenter.VM_list --------------------------------------------------
    for entry in lists:
        seq = entry["sequence"]
        check_eq(f"list {seq} method", "GET", entry["method"])
        check_eq(f"list {seq} path", "/api/vcenter/vm", entry["path"])
        check_eq(f"list {seq} carries no body", "", entry["bodyRaw"])
        names = [value for name, value in entry["queryPairs"] if name == "names"]
        check_eq(f"list {seq} sends exactly one names filter", 1, len(names))
        for name, value in entry["queryPairs"]:
            check(
                f"list {seq} filter {name} is not sent empty",
                value != "",
                "an unset filter is omitted from the query string entirely",
            )

    if len(lists) >= 1:
        check_eq(
            "an unfiltered lookup sends only names",
            "names=vcf90-web-01",
            lists[0]["query"],
        )
    if len(lists) >= 2:
        check_eq(
            "supplied filters are sent in specification order and repeated per value",
            "names=vcf90-web-01&folders=group-v22&folders=group-v23"
            "&power_states=POWERED_OFF",
            lists[1]["query"],
        )

    # --- Vcenter.Vm.Guest.Customization_check -----------------------------
    for entry in checks:
        seq = entry["sequence"]
        check_eq(f"check {seq} method", "POST", entry["method"])
        check_eq(
            f"check {seq} query",
            "action=check",
            entry["query"],
        )
        check_eq(
            f"check {seq} path",
            "/api/vcenter/vm/{vm}/guest/customization".replace(
                "{vm}", entry["pathParameters"]["vm"]
            ),
            entry["path"],
        )
        check_eq(f"check {seq} carries no request body", "", entry["bodyRaw"])
        check(
            f"check {seq} declares no content beyond an empty body",
            entry["contentLength"] in (None, "0"),
            f"Content-Length: {entry['contentLength']!r}",
        )

    # --- Vcenter.Vm.Guest.Customization_set -------------------------------
    for entry in sets:
        seq = entry["sequence"]
        check_eq(f"set {seq} method", "PUT", entry["method"])
        check_eq(f"set {seq} query is empty", "", entry["query"])
        check_eq(
            f"set {seq} path",
            "/api/vcenter/vm/{vm}/guest/customization".replace(
                "{vm}", entry["pathParameters"]["vm"]
            ),
            entry["path"],
        )
        check_eq(
            f"set {seq} content type",
            "application/json",
            (entry["contentType"] or "").split(";")[0].strip(),
        )
        check(
            f"set {seq} never sends the inline spec member",
            "spec" not in (entry["bodyMembers"] or []),
        )
        body = entry["bodyJson"]
        check(f"set {seq} body is a JSON object", isinstance(body, dict))
        if isinstance(body, dict):
            check(
                f"set {seq} sends no member with a null value",
                all(value is not None for value in body.values()),
                "an unset SetSpec member is omitted, never sent as null",
            )

    applied = [e for e in sets if e["bodyMembers"] == ["name"]]
    cleared = [e for e in sets if e["bodyMembers"] == []]
    check_eq("sets that name a specification", 3, len(applied))
    check_eq("sets that clear a pending customization", 1, len(cleared))
    for entry in applied:
        check_eq(
            f"set {entry['sequence']} body members",
            ["name"],
            entry["bodyMembers"],
        )
        check_eq(
            f"set {entry['sequence']} named body is compact JSON",
            json.dumps(entry["bodyJson"], separators=(",", ":")),
            entry["bodyRaw"],
        )
    for entry in cleared:
        check_eq(
            f"set {entry['sequence']} clears with an empty object",
            {},
            entry["bodyJson"],
        )
        check_eq(f"set {entry['sequence']} clear body", "{}", entry["bodyRaw"])

    # --- the precheck gate ------------------------------------------------
    mutated = sorted({e["pathParameters"]["vm"] for e in sets})
    check_eq(
        "only virtual machines whose precheck reported SUPPORTED were mutated",
        ["vm-1201", "vm-1204", "vm-1206"],
        mutated,
    )
    for vm_id in ("vm-1202", "vm-1203", "vm-1205", "vm-1209"):
        check(
            f"{vm_id} was prechecked but never mutated",
            any(e["pathParameters"].get("vm") == vm_id for e in checks)
            and not any(e["pathParameters"].get("vm") == vm_id for e in sets),
        )
    for vm_id in ("vm-1207", "vm-1208"):
        check(
            f"{vm_id} was never prechecked and never mutated",
            not any(e["pathParameters"].get("vm") == vm_id for e in checks + sets),
        )
    check_eq(
        "the fixture ends with exactly one pending customization",
        {"vm-1201": "vcf90-linux-prep"},
        log[-1]["pendingCustomizations"],
    )

    # --- ordering ---------------------------------------------------------
    for vm_id, name in (
        ("vm-1201", "vcf90-web-01"),
        ("vm-1204", "vcf90-clear-04"),
        ("vm-1206", "vcf90-nospec-06"),
    ):
        first_check = min(
            (e["sequence"] for e in checks if e["pathParameters"]["vm"] == vm_id),
            default=None,
        )
        first_set = min(
            (e["sequence"] for e in sets if e["pathParameters"]["vm"] == vm_id),
            default=None,
        )
        first_list = min(
            (
                e["sequence"]
                for e in lists
                if any(v == name for k, v in e["queryPairs"] if k == "names")
            ),
            default=None,
        )
        check(
            f"{name} is resolved, then prechecked, then mutated",
            None not in (first_check, first_set, first_list)
            and first_list < first_check < first_set,
            f"list={first_list} check={first_check} set={first_set}",
        )

    for entry in sets:
        vm_id = entry["pathParameters"]["vm"]
        preceding = [
            e
            for e in checks
            if e["pathParameters"]["vm"] == vm_id and e["sequence"] < entry["sequence"]
        ]
        check(
            f"set {entry['sequence']} was preceded by a precheck of {vm_id}",
            bool(preceding),
        )


if __name__ == "__main__":
    raise SystemExit(main())
