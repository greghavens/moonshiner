"""Protected verifier for the VCF Operations for Networks application-group reconciler.

Every case drives the shipped contract-pinned mock over real loopback HTTP on an
ephemeral port and then reads the mock's request log.  No live VMware endpoint is
contacted and nothing about the harness or its tools is simulated.

Run with ``python3 tests/verify.py``.
"""

import base64
import json
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.contract_mock import ApplianceState, ContractMock, ISSUED_TOKEN, fault  # noqa: E402

import vofn_appgroups  # noqa: E402
from vofn_appgroups import (  # noqa: E402
    APIError,
    ApplicationGroupClient,
    EnsureOutcome,
    ProtocolError,
    TransportError,
    render_outcome,
)

USERNAME = "svc-netops@corp.local"
PASSWORD = "N0t-Present-In-Any-Message"
TARGET = "Payments-Gateway"

LIST_OP = "getSavedApplicationsSummaries"
ADD_OP = "addApplication"
CREATE_OP = "create"
DELETE_OP = "delete"

ALLOWED_HEADERS = {
    "host",
    "accept",
    "accept-encoding",
    "authorization",
    "connection",
    "content-length",
    "content-type",
    "user-agent",
}

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append("%s%s" % (label, (": " + detail) if detail else ""))


def check_equal(label, actual, expected):
    check(label, actual == expected, "expected %r, got %r" % (expected, actual))


def baseline(include_target=False, target_at=25, duplicate=False):
    names = ["App-%03d" % index for index in range(1, 38)]
    if include_target:
        names.insert(target_at, TARGET)
    if duplicate:
        names.append(TARGET)
    return names


def auth_body(username=USERNAME, password=PASSWORD, domain=None):
    parts = ['{"username":%s,"password":%s' % (json.dumps(username), json.dumps(password))]
    if domain is not None:
        parts.append(',"domain":%s' % (domain,))
    parts.append("}")
    return "".join(parts)


def ops(log):
    return [entry["operation_id"] for entry in log]


def entries_for(log, operation_id):
    return [entry for entry in log if entry["operation_id"] == operation_id]


def header(entry, name):
    values = entry["headers"].get(name)
    if not values or len(values) != 1:
        return None
    return values[0]


def check_wire(label, log):
    """Assert the shape every request on the pinned contract must have."""
    for entry in log:
        tag = "%s[#%d %s %s]" % (label, entry["seq"], entry["method"], entry["path"])
        check(
            "%s reached a contract operation" % (tag,),
            entry["operation_id"] is not None,
            "the path is not named by docs/contract.json",
        )
        names = set(entry["headers"])
        check(
            "%s sent no header outside the contract" % (tag,),
            names <= ALLOWED_HEADERS,
            "unexpected headers %s" % (sorted(names - ALLOWED_HEADERS),),
        )
        for name, values in entry["headers"].items():
            check(
                "%s sent header %r once" % (tag, name),
                len(values) == 1,
                "got %r" % (values,),
            )
        check_equal("%s Accept header" % (tag,), header(entry, "accept"), "application/json")
        if entry["operation_id"] == CREATE_OP:
            check(
                "%s carries no Authorization" % (tag,),
                "authorization" not in entry["headers"],
                "the create operation overrides security with an empty list",
            )
        else:
            check_equal(
                "%s Authorization header" % (tag,),
                header(entry, "authorization"),
                "NetworkInsight " + ISSUED_TOKEN,
            )
        if entry["method"] == "POST":
            check_equal(
                "%s Content-Type header" % (tag,),
                header(entry, "content-type"),
                "application/json",
            )
            check("%s sent a body" % (tag,), entry["body"] != "", "body was empty")
        else:
            check(
                "%s sent no Content-Type" % (tag,),
                "content-type" not in entry["headers"],
                "bodyless requests must not declare a content type",
            )
            check_equal("%s sent no body" % (tag,), entry["body"], "")
        check(
            "%s answered a known status" % (tag,),
            entry["status"] != 404 and entry["status"] != 405,
            "the mock refused the route with %s" % (entry["status"],),
        )


def check_pagination(label, log, page_size, expected_pages, offset=0):
    """Assert one full summaries sweep: no cursor first, then the served cursor."""
    sweep = entries_for(log, LIST_OP)[offset : offset + expected_pages]
    check_equal("%s sweep length" % (label,), len(sweep), expected_pages)
    for index, entry in enumerate(sweep):
        if index == 0:
            check_equal(
                "%s page 1 query" % (label,),
                entry["query_pairs"],
                [("size", str(page_size))],
            )
        else:
            cursor = base64.b64encode(str(index * page_size).encode("ascii")).decode("ascii")
            check_equal(
                "%s page %d query" % (label, index + 1),
                entry["query_pairs"],
                [("size", str(page_size)), ("cursor", cursor)],
            )
        check_equal("%s page %d status" % (label, index + 1), entry["status"], 200)


def run(state, body, **client_kwargs):
    """Start the mock, hand ``body`` a live client, and return (result, log)."""
    with ContractMock(state) as mock:
        kwargs = {"domain_type": None, "domain_value": None, "timeout": 10.0}
        kwargs.update(client_kwargs)
        result = body(mock, kwargs)
    return result, state.log()


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


def case_creates_when_absent():
    state = ApplianceState(applications=baseline(), username=USERNAME, password=PASSWORD)

    def body(mock, kwargs):
        client = ApplicationGroupClient(mock.base_url + "/", USERNAME, PASSWORD, **kwargs)
        try:
            return client.ensure_application(TARGET, page_size=10)
        finally:
            client.close()

    outcome, log = run(state, body)
    check("absent: returns an EnsureOutcome", isinstance(outcome, EnsureOutcome), repr(outcome))
    check_equal("absent: created", outcome.created, True)
    check_equal("absent: create_attempts", outcome.create_attempts, 1)
    check_equal("absent: pages_read", outcome.pages_read, 4)
    check_equal("absent: name", outcome.name, TARGET)
    check_equal("absent: entity_id", outcome.entity_id, state.entity_id_for(TARGET))
    check_equal("absent: appliance holds one group", state.count_named(TARGET), 1)
    check_equal(
        "absent: operation sequence",
        ops(log),
        [CREATE_OP] + [LIST_OP] * 4 + [ADD_OP, DELETE_OP],
    )
    check_wire("absent", log)
    check_pagination("absent", log, 10, 4)
    check_equal("absent: auth body", entries_for(log, CREATE_OP)[0]["body"], auth_body())
    check_equal(
        "absent: create body",
        entries_for(log, ADD_OP)[0]["body"],
        '{"name":"Payments-Gateway"}',
    )
    check_equal("absent: create status", entries_for(log, ADD_OP)[0]["status"], 201)
    check_equal("absent: token release query", entries_for(log, DELETE_OP)[0]["query_pairs"], [])
    check_equal(
        "absent: rendered document",
        render_outcome(outcome),
        json.dumps(
            {
                "entity_id": outcome.entity_id,
                "name": TARGET,
                "created": True,
                "create_attempts": 1,
                "pages_read": 4,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )


def case_already_present():
    state = ApplianceState(
        applications=baseline(include_target=True), username=USERNAME, password=PASSWORD
    )

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            return client.ensure_application(TARGET, page_size=10)

    outcome, log = run(state, body)
    check_equal("present: created", outcome.created, False)
    check_equal("present: create_attempts", outcome.create_attempts, 0)
    check_equal("present: pages_read", outcome.pages_read, 4)
    check_equal("present: entity_id", outcome.entity_id, state.entity_id_for(TARGET))
    check_equal("present: issued no create", len(entries_for(log, ADD_OP)), 0)
    check_equal(
        "present: operation sequence", ops(log), [CREATE_OP] + [LIST_OP] * 4 + [DELETE_OP]
    )
    check_wire("present", log)
    check_pagination("present", log, 10, 4)
    check_equal("present: appliance unchanged", state.count_named(TARGET), 1)


def case_rerun_is_idempotent():
    state = ApplianceState(applications=baseline(), username=USERNAME, password=PASSWORD)

    def body(mock, kwargs):
        outcomes = []
        for _ in range(2):
            with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
                outcomes.append(client.ensure_application(TARGET, page_size=10))
        return outcomes

    outcomes, log = run(state, body)
    first, second = outcomes
    check_equal("rerun: first run created", first.created, True)
    check_equal("rerun: second run did not create", second.created, False)
    check_equal("rerun: same entity_id", second.entity_id, first.entity_id)
    check_equal("rerun: second run create_attempts", second.create_attempts, 0)
    check_equal("rerun: exactly one mutation on the wire", len(entries_for(log, ADD_OP)), 1)
    check_equal("rerun: appliance holds one group", state.count_named(TARGET), 1)
    check_equal("rerun: one token per run", len(entries_for(log, CREATE_OP)), 2)
    check_equal("rerun: one release per run", len(entries_for(log, DELETE_OP)), 2)
    check_wire("rerun", log)


def _lost_response(label, mode):
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(ADD_OP, 1, mode)],
    )

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            return client.ensure_application(TARGET, page_size=10, max_attempts=3)

    outcome, log = run(state, body)
    check_equal("%s: appliance holds one group" % (label,), state.count_named(TARGET), 1)
    check_equal("%s: no duplicate mutation" % (label,), len(entries_for(log, ADD_OP)), 1)
    check_equal("%s: create_attempts" % (label,), outcome.create_attempts, 1)
    check_equal("%s: created is false" % (label,), outcome.created, False)
    check_equal("%s: entity_id" % (label,), outcome.entity_id, state.entity_id_for(TARGET))
    check_equal("%s: re-read the whole collection" % (label,), outcome.pages_read, 8)
    check_equal("%s: sweeps on the wire" % (label,), len(entries_for(log, LIST_OP)), 8)
    check_pagination("%s first sweep" % (label,), log, 10, 4, offset=0)
    check_pagination("%s second sweep" % (label,), log, 10, 4, offset=4)
    check_wire(label, log)


def case_lost_response_status():
    _lost_response("lost-503", "commit_then_error")


def case_lost_response_reset():
    _lost_response("lost-reset", "commit_then_reset")


def case_read_after_write_conflict():
    state = ApplianceState(
        applications=baseline(include_target=True),
        username=USERNAME,
        password=PASSWORD,
        hidden_names=[TARGET],
    )

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            return client.ensure_application(TARGET, page_size=10, max_attempts=3)

    outcome, log = run(state, body)
    check_equal("conflict: created is false", outcome.created, False)
    check_equal("conflict: create_attempts", outcome.create_attempts, 1)
    check_equal("conflict: pages_read", outcome.pages_read, 8)
    check_equal("conflict: entity_id", outcome.entity_id, state.entity_id_for(TARGET))
    check_equal("conflict: appliance holds one group", state.count_named(TARGET), 1)
    creates = entries_for(log, ADD_OP)
    check_equal("conflict: exactly one mutation", len(creates), 1)
    check_equal("conflict: rejection observed", creates[0]["status"] if creates else None, 400)
    check_wire("conflict", log)


def case_rejection_is_not_retried():
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(ADD_OP, 1, "reject"), fault(ADD_OP, 2, "reject")],
    )
    box = {}

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            try:
                client.ensure_application(TARGET, page_size=10, max_attempts=3)
            except BaseException as exc:  # noqa: BLE001 - the type is the assertion
                box["error"] = exc
        return None

    _, log = run(state, body)
    error = box.get("error")
    check("reject: raised APIError", isinstance(error, APIError), repr(error))
    if isinstance(error, APIError):
        check_equal("reject: operation_id", error.operation_id, ADD_OP)
        check_equal("reject: status_code", error.status_code, 400)
        check_equal("reject: ApiError code", error.code, 400)
        check_equal("reject: ApiError message", error.message, "application name is not acceptable")
    check_equal("reject: exactly one mutation", len(entries_for(log, ADD_OP)), 1)
    check_equal("reject: one confirming sweep", len(entries_for(log, LIST_OP)), 8)
    check_equal("reject: nothing created", state.count_named(TARGET), 0)
    check_equal("reject: token still released", len(entries_for(log, DELETE_OP)), 1)
    check_wire("reject", log)


def case_retry_budget_is_honoured():
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(ADD_OP, index, "server_error") for index in (1, 2, 3, 4)],
    )
    box = {}

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            try:
                client.ensure_application(TARGET, page_size=10, max_attempts=2)
            except BaseException as exc:  # noqa: BLE001 - the type is the assertion
                box["error"] = exc
        return None

    _, log = run(state, body)
    error = box.get("error")
    check("budget: raised APIError", isinstance(error, APIError), repr(error))
    if isinstance(error, APIError):
        check_equal("budget: operation_id", error.operation_id, ADD_OP)
        check_equal("budget: status_code", error.status_code, 503)
    check_equal("budget: stopped at max_attempts", len(entries_for(log, ADD_OP)), 2)
    check_equal("budget: one sweep per attempt", len(entries_for(log, LIST_OP)), 8)
    check_equal("budget: nothing created", state.count_named(TARGET), 0)
    check_wire("budget", log)


def case_retry_after_failed_mutation():
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(ADD_OP, 1, "reset_before")],
    )

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            return client.ensure_application(TARGET, page_size=10, max_attempts=3)

    outcome, log = run(state, body)
    check_equal("recreate: created", outcome.created, True)
    check_equal("recreate: create_attempts", outcome.create_attempts, 2)
    check_equal("recreate: pages_read", outcome.pages_read, 8)
    check_equal("recreate: appliance holds one group", state.count_named(TARGET), 1)
    check_equal("recreate: entity_id", outcome.entity_id, state.entity_id_for(TARGET))
    check_equal("recreate: two mutations on the wire", len(entries_for(log, ADD_OP)), 2)
    check_wire("recreate", log)


def case_every_ambiguous_status_is_retried():
    for status in (500, 502, 504):
        state = ApplianceState(
            applications=baseline(),
            username=USERNAME,
            password=PASSWORD,
            faults=[fault(ADD_OP, 1, status)],
        )

        def body(mock, kwargs):
            with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
                return client.ensure_application(TARGET, page_size=10, max_attempts=2)

        outcome, log = run(state, body)
        label = "retry-%d" % (status,)
        check_equal("%s: created" % (label,), outcome.created, True)
        check_equal("%s: create_attempts" % (label,), outcome.create_attempts, 2)
        check_equal("%s: pages_read" % (label,), outcome.pages_read, 8)
        check_equal("%s: first status" % (label,), entries_for(log, ADD_OP)[0]["status"], status)
        check_equal("%s: appliance holds one group" % (label,), state.count_named(TARGET), 1)
        check_wire(label, log)


def case_duplicate_names_refused():
    state = ApplianceState(
        applications=baseline(include_target=True, duplicate=True),
        username=USERNAME,
        password=PASSWORD,
    )
    box = {}

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            try:
                client.ensure_application(TARGET, page_size=10)
            except BaseException as exc:  # noqa: BLE001 - the type is the assertion
                box["error"] = exc
        return None

    _, log = run(state, body)
    error = box.get("error")
    check("duplicate: raised ProtocolError", isinstance(error, ProtocolError), repr(error))
    if isinstance(error, ProtocolError):
        check_equal("duplicate: operation_id", error.operation_id, LIST_OP)
    check_equal("duplicate: issued no mutation", len(entries_for(log, ADD_OP)), 0)
    check_equal("duplicate: read every page", len(entries_for(log, LIST_OP)), 4)
    check_wire("duplicate", log)


def _protocol_fault(label, mode, occurrence, expected_pages):
    state = ApplianceState(
        applications=baseline(include_target=True),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(LIST_OP, occurrence, mode)],
    )
    box = {}

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            try:
                client.ensure_application(TARGET, page_size=10)
            except BaseException as exc:  # noqa: BLE001 - the type is the assertion
                box["error"] = exc
        return None

    _, log = run(state, body)
    error = box.get("error")
    check("%s: raised ProtocolError" % (label,), isinstance(error, ProtocolError), repr(error))
    if isinstance(error, ProtocolError):
        check_equal("%s: operation_id" % (label,), error.operation_id, LIST_OP)
    check_equal("%s: pages read" % (label,), len(entries_for(log, LIST_OP)), expected_pages)
    check_equal("%s: issued no mutation" % (label,), len(entries_for(log, ADD_OP)), 0)
    check_equal("%s: token released" % (label,), len(entries_for(log, DELETE_OP)), 1)
    check_wire(label, log)


def case_invalid_page_responses_refused():
    _protocol_fault("numeric-cursor", "numeric_cursor", 1, 1)
    _protocol_fault("null-cursor", "null_cursor", 1, 1)
    _protocol_fault("repeat-cursor", "repeat_cursor", 2, 2)
    _protocol_fault("malformed-results", "malformed_results", 1, 1)


def case_list_failure_is_api_error():
    state = ApplianceState(
        applications=baseline(include_target=True),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(LIST_OP, 1, "server_error")],
    )
    box = {}

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            try:
                client.ensure_application(TARGET, page_size=10)
            except BaseException as exc:  # noqa: BLE001 - the type is the assertion
                box["error"] = exc
        return None

    _, log = run(state, body)
    error = box.get("error")
    check("list-error: raised APIError", isinstance(error, APIError), repr(error))
    if isinstance(error, APIError):
        check_equal("list-error: operation_id", error.operation_id, LIST_OP)
        check_equal("list-error: status_code", error.status_code, 500)
        check_equal("list-error: ApiError code", error.code, 500)
        check_equal("list-error: ApiError message", error.message, "application summaries are unavailable")
    check_equal("list-error: issued no mutation", len(entries_for(log, ADD_OP)), 0)
    check_equal("list-error: token released", len(entries_for(log, DELETE_OP)), 1)
    check_wire("list-error", log)


def _termination_fault(label, mode):
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(LIST_OP, 1, mode)],
    )

    def body(mock, kwargs):
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            return client.ensure_application(TARGET, page_size=10)

    outcome, log = run(state, body)
    check_equal("%s: created" % (label,), outcome.created, True)
    check_equal("%s: stopped after one page" % (label,), outcome.pages_read, 1)
    check_equal("%s: created once" % (label,), len(entries_for(log, ADD_OP)), 1)
    check_equal("%s: appliance holds one group" % (label,), state.count_named(TARGET), 1)
    check_wire(label, log)


def case_page_termination_signals():
    _termination_fault("empty-cursor", "empty_cursor")
    _termination_fault("empty-results", "empty_results")


def case_authentication_failure():
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(CREATE_OP, 1, "unauthorized")],
    )
    box = {}

    def body(mock, kwargs):
        client = ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs)
        box["repr"] = repr(client)
        try:
            client.ensure_application(TARGET, page_size=10)
        except BaseException as exc:  # noqa: BLE001 - the type is the assertion
            box["error"] = exc
        finally:
            client.close()
        return None

    _, log = run(state, body)
    error = box.get("error")
    check("auth: raised APIError", isinstance(error, APIError), repr(error))
    if isinstance(error, APIError):
        check_equal("auth: operation_id", error.operation_id, CREATE_OP)
        check_equal("auth: status_code", error.status_code, 401)
    check_equal("auth: nothing else was attempted", ops(log), [CREATE_OP])
    check(
        "auth: password absent from the error",
        PASSWORD not in str(error) and PASSWORD not in repr(error),
        "the password leaked into the exception",
    )
    check(
        "auth: password absent from repr(client)",
        PASSWORD not in box.get("repr", ""),
        "the password leaked into repr()",
    )
    check_wire("auth", log)


def case_token_release_is_forgiving():
    state = ApplianceState(
        applications=baseline(include_target=True),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(DELETE_OP, 1, "server_error")],
    )
    box = {}

    def body(mock, kwargs):
        client = ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs)
        outcome = client.ensure_application(TARGET, page_size=10)
        try:
            client.close()
            client.close()
        except BaseException as exc:  # noqa: BLE001 - the type is the assertion
            box["error"] = exc
        return outcome

    outcome, log = run(state, body)
    check("release: close swallowed the failure", "error" not in box, repr(box.get("error")))
    check_equal("release: released once", len(entries_for(log, DELETE_OP)), 1)
    check_equal("release: outcome still usable", outcome.created, False)
    check_wire("release", log)


def _domain_case(label, domain_type, domain_value, expected_domain):
    state = ApplianceState(
        applications=baseline(include_target=True),
        username=USERNAME,
        password=PASSWORD,
        domain_type=domain_type,
        domain_value=domain_value,
    )

    def body(mock, kwargs):
        kwargs["domain_type"] = domain_type
        kwargs["domain_value"] = domain_value
        with ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs) as client:
            return client.ensure_application(TARGET, page_size=10)

    outcome, log = run(state, body)
    check_equal("%s: found the group" % (label,), outcome.entity_id, state.entity_id_for(TARGET))
    sent = entries_for(log, CREATE_OP)[0]["body"] if entries_for(log, CREATE_OP) else None
    check_equal("%s: auth body" % (label,), sent, auth_body(domain=expected_domain))
    check_wire(label, log)


def case_domain_local():
    _domain_case("domain-local", "LOCAL", None, '{"domain_type":"LOCAL"}')


def case_domain_ldap():
    _domain_case("domain-ldap", "LDAP", "corp.local", '{"domain_type":"LDAP","value":"corp.local"}')


def case_transport_failure_is_secret_safe():
    state = ApplianceState(
        applications=baseline(),
        username=USERNAME,
        password=PASSWORD,
        faults=[fault(CREATE_OP, 1, "reset_before")],
    )

    def body(mock, kwargs):
        client = ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs)
        try:
            client.ensure_application(TARGET, page_size=10)
        except BaseException as exc:  # noqa: BLE001 - the type is the assertion
            return exc
        finally:
            client.close()
        return None

    error, log = run(state, body)
    check("transport: raised TransportError", isinstance(error, TransportError), repr(error))
    if isinstance(error, TransportError):
        check_equal("transport: operation_id", error.operation_id, CREATE_OP)
    check(
        "transport: password absent from the error",
        PASSWORD not in str(error) and PASSWORD not in repr(error),
        "the password leaked into the exception",
    )
    check_equal("transport: only authentication was attempted", ops(log), [CREATE_OP])


def case_valid_boundaries_and_cached_authentication():
    state = ApplianceState(applications=[TARGET], username=USERNAME, password=PASSWORD)

    def body(mock, kwargs):
        base_url = mock.base_url.replace("http:", "HTTP:", 1)
        client = ApplicationGroupClient(base_url, USERNAME, PASSWORD, **kwargs)
        try:
            first = client.ensure_application(TARGET, page_size=1, max_attempts=1)
            second = client.ensure_application(TARGET, page_size=1000, max_attempts=5)
            return first, second
        finally:
            client.close()

    outcomes, log = run(state, body)
    check_equal("boundaries: both calls found the group", [item.created for item in outcomes], [False, False])
    check_equal("boundaries: authenticated once", len(entries_for(log, CREATE_OP)), 1)
    check_equal("boundaries: released once", len(entries_for(log, DELETE_OP)), 1)
    check_equal("boundaries: one page per call", len(entries_for(log, LIST_OP)), 2)
    check_equal("boundaries: lower page size", entries_for(log, LIST_OP)[0]["query_pairs"], [("size", "1")])
    check_equal("boundaries: upper page size", entries_for(log, LIST_OP)[1]["query_pairs"], [("size", "1000")])
    check_wire("boundaries", log)


def case_unicode_rendering():
    outcome = EnsureOutcome("18230:561:1", "Café-東京", False, 0, 1)
    rendered = render_outcome(outcome)
    check("render: non-ASCII is literal", "Café-東京" in rendered, rendered)
    check("render: non-ASCII is not escaped", "\\u00e9" not in rendered, rendered)
    check("render: one trailing newline", rendered.endswith("\n") and not rendered.endswith("\n\n"), repr(rendered))
    check_equal("render: key order", list(json.loads(rendered)), ["entity_id", "name", "created", "create_attempts", "pages_read"])


def case_validation():
    bad_urls = [
        "",
        "   ",
        "vofn.example.com",
        "ftp://vofn.example.com",
        "https://",
        "https://user:pass@vofn.example.com",
        "https://vofn.example.com:not-a-port",
        "https://vofn.example.com:70000",
        "https://vofn.example.com/api/ni",
        "https://vofn.example.com/?probe=1",
        "https://vofn.example.com/#fragment",
        None,
        123,
    ]
    for value in bad_urls:
        _rejects("base_url=%r" % (value,), lambda v=value: ApplicationGroupClient(v, USERNAME, PASSWORD))

    for value in ["", "   ", None, 7, b"admin"]:
        _rejects(
            "username=%r" % (value,),
            lambda v=value: ApplicationGroupClient("https://host", v, PASSWORD),
        )
        _rejects(
            "password=%r" % (value,),
            lambda v=value: ApplicationGroupClient("https://host", USERNAME, v),
        )

    bad_domains = [
        ("local", None),
        ("Local", None),
        ("LDAP", None),
        ("LDAP", ""),
        ("LOCAL", "corp.local"),
        (None, "corp.local"),
        ("AD", "corp.local"),
        (7, None),
    ]
    for domain_type, domain_value in bad_domains:
        _rejects(
            "domain=%r/%r" % (domain_type, domain_value),
            lambda t=domain_type, v=domain_value: ApplicationGroupClient(
                "https://host", USERNAME, PASSWORD, domain_type=t, domain_value=v
            ),
        )

    for value in [0, -1, "5", None, True, float("nan"), float("inf"), 10**10000]:
        _rejects(
            "timeout=%s" % (_printable(value),),
            lambda v=value: ApplicationGroupClient("https://host", USERNAME, PASSWORD, timeout=v),
        )

    state = ApplianceState(applications=baseline(), username=USERNAME, password=PASSWORD)

    def body(mock, kwargs):
        client = ApplicationGroupClient(mock.base_url, USERNAME, PASSWORD, **kwargs)
        for value in ["", "   ", None, 7, b"App"]:
            _rejects("name=%r" % (value,), lambda v=value: client.ensure_application(v))
        for value in [0, -1, 1001, True, 1.0, "10", None]:
            _rejects(
                "page_size=%r" % (value,),
                lambda v=value: client.ensure_application(TARGET, page_size=v),
            )
        for value in [0, -1, 6, True, 2.5, "3", None]:
            _rejects(
                "max_attempts=%r" % (value,),
                lambda v=value: client.ensure_application(TARGET, max_attempts=v),
            )
        client.close()
        return None

    _, log = run(state, body)
    check_equal("validation: rejected before any request", log, [])
    check_equal("validation: nothing created", state.count_named(TARGET), 0)


def _printable(value):
    """`repr`, for a value whose `repr` may not survive being taken.

    Python caps integer-to-string conversion at 4300 digits, and an integer too
    large to become a finite float -- which is exactly what makes it an
    interesting timeout to reject -- is well past that. Formatting the label
    raised before the case it labels ever ran.
    """
    try:
        return repr(value)
    except ValueError:
        return "<unprintable %s>" % (type(value).__name__,)


def _rejects(label, thunk):
    try:
        thunk()
    except ValueError as exc:
        check(
            "validation %s message is secret-safe" % (label,),
            PASSWORD not in str(exc),
            "the password leaked into the message",
        )
        return
    except BaseException as exc:  # noqa: BLE001 - wrong type is the failure
        check("validation %s raises ValueError" % (label,), False, "raised %r" % (exc,))
        return
    check("validation %s raises ValueError" % (label,), False, "no error was raised")


def case_contract_provenance():
    with open(os.path.join(ROOT, "docs", "contract.json"), "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    with open(os.path.join(ROOT, "docs", "official_sources.json"), "r", encoding="utf-8") as handle:
        sources = json.load(handle)
    expected_ids = [CREATE_OP, DELETE_OP, LIST_OP, ADD_OP]
    check_equal("provenance: contract operationIds", sorted(contract["operationIds"]), sorted(expected_ids))
    check_equal("provenance: source operationIds", sorted(sources["operationIds"]), sorted(expected_ids))
    check_equal(
        "provenance: pinned commit",
        contract["source"]["repositoryCommitSha"],
        sources["repositoryCommitSha"],
    )
    check_equal(
        "provenance: spec path",
        contract["source"]["specPath"],
        "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml",
    )
    check_equal(
        "provenance: module operation ids",
        [
            vofn_appgroups.CREATE_TOKEN_OPERATION_ID,
            vofn_appgroups.DELETE_TOKEN_OPERATION_ID,
            vofn_appgroups.LIST_SUMMARIES_OPERATION_ID,
            vofn_appgroups.ADD_APPLICATION_OPERATION_ID,
        ],
        expected_ids,
    )


CASES = [
    case_contract_provenance,
    case_creates_when_absent,
    case_already_present,
    case_rerun_is_idempotent,
    case_lost_response_status,
    case_lost_response_reset,
    case_read_after_write_conflict,
    case_rejection_is_not_retried,
    case_retry_budget_is_honoured,
    case_retry_after_failed_mutation,
    case_every_ambiguous_status_is_retried,
    case_duplicate_names_refused,
    case_invalid_page_responses_refused,
    case_list_failure_is_api_error,
    case_page_termination_signals,
    case_authentication_failure,
    case_token_release_is_forgiving,
    case_domain_local,
    case_domain_ldap,
    case_transport_failure_is_secret_safe,
    case_valid_boundaries_and_cached_authentication,
    case_unicode_rendering,
    case_validation,
]


def main():
    for case in CASES:
        try:
            case()
        except Exception:  # noqa: BLE001 - an unexpected error is a failure
            FAILURES.append(
                "%s raised unexpectedly:\n%s" % (case.__name__, traceback.format_exc())
            )
    if FAILURES:
        print("FAILED: %d problem(s) across %d checks" % (len(FAILURES), CHECKS[0]))
        for failure in FAILURES:
            print("  - %s" % (failure,))
        return 1
    print("PASSED %d checks across %d cases" % (CHECKS[0], len(CASES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
