"""Acceptance tests for the table-driven state machine engine (fsmcore.py).

The engine under test is generic: machines are declared as plain data
(a states table plus a flat list of transition rows) and the tests drive it
with two small sample machines — a helpdesk ticket workflow and a parcel
locker — plus a few inline single-purpose machines.

Run: python3 test_fsmcore.py
"""

import fsmcore


# ------------------------------------------------------------ sample machines


def build_ticket():
    """Helpdesk ticket workflow. Context tracks assignee, reopen count and a
    log written by the in_progress enter/exit hooks."""
    ctx = {"assignee": None, "reopens": 0, "resolutions": 0, "log": []}

    def note(tag):
        def hook(context, step):
            context["log"].append("%s:%s->%s" % (tag, step["src"], step["dst"]))
        return hook

    def assign(context, step):
        context["assignee"] = step["payload"]

    def count_resolution(context, step):
        context["resolutions"] += 1

    def may_reopen(context, step):
        return context["reopens"] < 2

    def count_reopen(context, step):
        context["reopens"] += 1

    definition = {
        "initial": "new",
        "states": {
            "new": {},
            "triaged": {},
            "in_progress": {"enter": note("enter"), "exit": note("exit")},
            "waiting": {},
            "resolved": {},
            "closed": {},
        },
        "transitions": [
            {"event": "triage", "src": "new", "dst": "triaged"},
            {"event": "assign", "src": "triaged", "dst": "in_progress", "action": assign},
            {"event": "block", "src": "in_progress", "dst": "waiting"},
            {"event": "unblock", "src": "waiting", "dst": "in_progress"},
            {"event": "resolve", "src": "in_progress", "dst": "resolved", "action": count_resolution},
            {"event": "reopen", "src": "resolved", "dst": "in_progress",
             "guard": may_reopen, "action": count_reopen},
            {"event": "close", "src": "resolved", "dst": "closed"},
        ],
    }
    return fsmcore.Machine(definition, ctx)


def build_locker(trace=None):
    """Parcel locker. deposit stores an access code, open is guarded on the
    code in the payload, scan is a self-transition that counts courier scans.
    If trace is a list, occupied's hooks append to it."""
    ctx = {"code": None, "scans": 0}
    hooks = {}
    if trace is not None:
        hooks = {
            "enter": lambda c, s: trace.append("enter-occupied"),
            "exit": lambda c, s: trace.append("exit-occupied"),
        }

    def store_code(context, step):
        context["code"] = step["payload"]

    def code_matches(context, step):
        return step["payload"] == context["code"]

    def clear_code(context, step):
        context["code"] = None

    def count_scan(context, step):
        context["scans"] += 1

    definition = {
        "initial": "empty",
        "states": {"empty": {}, "occupied": hooks},
        "transitions": [
            {"event": "deposit", "src": "empty", "dst": "occupied", "action": store_code},
            {"event": "open", "src": "occupied", "dst": "empty",
             "guard": code_matches, "action": clear_code},
            {"event": "scan", "src": "occupied", "dst": "occupied", "action": count_scan},
        ],
    }
    return fsmcore.Machine(definition, ctx)


# ------------------------------------------------------------------- helpers


def expect_definition_error(definition):
    try:
        fsmcore.Machine(definition, {})
    except fsmcore.DefinitionError:
        return
    raise AssertionError("DefinitionError expected for %r" % (definition,))


def expect_rejection(machine, event, payload=None):
    before_state = machine.state
    before_len = len(machine.history)
    try:
        machine.send(event, payload)
    except fsmcore.TransitionError as exc:
        assert machine.state == before_state, "state must not change on rejection"
        assert len(machine.history) == before_len, "history must not grow on rejection"
        return exc
    raise AssertionError("TransitionError expected for event %r" % event)


# --------------------------------------------------------------------- tests


def test_initial_state_and_construction():
    trace = []
    m = build_locker(trace)
    assert m.state == "empty"
    assert m.history == []
    assert trace == [], "constructing a machine must fire no hooks"
    ctx = {"tag": 7}
    m2 = fsmcore.Machine(
        {"initial": "a", "states": {"a": {}}, "transitions": []}, ctx)
    assert m2.context is ctx, "context object is attached as-is"


def test_definition_validation():
    # initial state not declared
    expect_definition_error(
        {"initial": "ghost", "states": {"a": {}}, "transitions": []})
    # transition src not declared
    expect_definition_error(
        {"initial": "a", "states": {"a": {}},
         "transitions": [{"event": "go", "src": "b", "dst": "a"}]})
    # transition dst not declared
    expect_definition_error(
        {"initial": "a", "states": {"a": {}},
         "transitions": [{"event": "go", "src": "a", "dst": "b"}]})
    # transition row missing a required key
    expect_definition_error(
        {"initial": "a", "states": {"a": {}},
         "transitions": [{"src": "a", "dst": "a"}]})
    # transition row with an unknown key (typo'd "from")
    expect_definition_error(
        {"initial": "a", "states": {"a": {}},
         "transitions": [{"event": "go", "from": "a", "src": "a", "dst": "a"}]})
    # state hooks with an unknown key
    expect_definition_error(
        {"initial": "a", "states": {"a": {"entry": lambda c, s: None}},
         "transitions": []})
    # unknown top-level key
    expect_definition_error(
        {"initial": "a", "states": {"a": {}}, "transitions": [], "meta": 1})


def test_basic_send_and_action():
    m = build_locker()
    got = m.send("deposit", "4711")
    assert got == "occupied", "send returns the new state name"
    assert m.state == "occupied"
    assert m.context["code"] == "4711", "action ran with the payload"
    assert len(m.history) == 1
    rec = m.history[0]
    assert rec == {"seq": 1, "event": "deposit", "src": "empty",
                   "dst": "occupied", "payload": "4711"}


def test_exit_action_enter_order_and_state_visibility():
    order = []
    seen = {}

    def on_exit(context, step):
        order.append("exit")
        seen["during_exit"] = machine.state

    def on_enter(context, step):
        order.append("enter")
        seen["during_enter"] = machine.state
        seen["history_len_during_enter"] = len(machine.history)

    def action(context, step):
        order.append("action")
        seen["during_action"] = machine.state

    definition = {
        "initial": "idle",
        "states": {"idle": {"exit": on_exit}, "busy": {"enter": on_enter}},
        "transitions": [
            {"event": "start", "src": "idle", "dst": "busy", "action": action},
        ],
    }
    machine = fsmcore.Machine(definition, {})
    machine.send("start")
    assert order == ["exit", "action", "enter"], "pinned order: exit, action, enter"
    assert seen["during_exit"] == "idle", "exit hook still sees the old state"
    assert seen["during_action"] == "busy", "action already sees the new state"
    assert seen["during_enter"] == "busy"
    assert seen["history_len_during_enter"] == 0, \
        "the audit record is appended only after the enter hook finished"
    assert len(machine.history) == 1


def test_unknown_event_is_rejected_with_allowed_list():
    m = build_locker()
    exc = expect_rejection(m, "open", "0000")
    assert exc.state == "empty"
    assert exc.event == "open"
    assert exc.allowed == ["deposit"]
    assert "open" in str(exc) and "empty" in str(exc)


def test_guard_refusal_is_rejected_with_allowed_list():
    trace = []
    m = build_locker(trace)
    m.send("deposit", "9182")
    del trace[:]
    exc = expect_rejection(m, "open", "1111")  # wrong code
    assert exc.state == "occupied"
    assert exc.event == "open"
    assert exc.allowed == ["open", "scan"], "allowed list is static and sorted"
    assert m.context["code"] == "9182", "action must not run when the guard refuses"
    assert trace == [], "no hooks fire on a refused transition"
    # the right code still works afterwards
    assert m.send("open", "9182") == "empty"


def test_first_matching_row_wins_in_table_order():
    calls = []

    def big(context, step):
        calls.append("big")
        return step["payload"] >= 1000

    def medium(context, step):
        calls.append("medium")
        return step["payload"] >= 100

    definition = {
        "initial": "received",
        "states": {"received": {}, "escalated": {}, "review": {}, "auto_ok": {}},
        "transitions": [
            {"event": "route", "src": "received", "dst": "escalated", "guard": big},
            {"event": "route", "src": "received", "dst": "review", "guard": medium},
            {"event": "route", "src": "received", "dst": "auto_ok"},
        ],
    }

    m = fsmcore.Machine(definition, {})
    assert m.send("route", 5000) == "escalated"
    assert calls == ["big"], "later guards are not evaluated once a row matched"

    del calls[:]
    m = fsmcore.Machine(definition, {})
    assert m.send("route", 250) == "review"
    assert calls == ["big", "medium"]

    del calls[:]
    m = fsmcore.Machine(definition, {})
    assert m.send("route", 5) == "auto_ok", "unguarded row fires when guards refuse"
    assert calls == ["big", "medium"]


def test_self_transition_refires_hooks():
    trace = []
    m = build_locker(trace)
    m.send("deposit", "22")
    del trace[:]
    assert m.send("scan") == "occupied"
    assert trace == ["exit-occupied", "enter-occupied"], \
        "a self-transition runs exit then enter again"
    assert m.context["scans"] == 1
    rec = m.history[-1]
    assert rec["src"] == "occupied" and rec["dst"] == "occupied"


def test_audit_trail_records_every_transition_in_order():
    m = build_ticket()
    m.send("triage")
    m.send("assign", "dana")
    m.send("block")
    m.send("unblock")
    m.send("resolve")
    # a rejected event must not leave a trace
    expect_rejection(m, "assign", "kim")
    h = m.history
    assert [r["seq"] for r in h] == [1, 2, 3, 4, 5]
    assert [r["event"] for r in h] == ["triage", "assign", "block", "unblock", "resolve"]
    assert h[0]["src"] == "new"
    for earlier, later in zip(h, h[1:]):
        assert earlier["dst"] == later["src"], "audit records chain src->dst"
    assert h[1]["payload"] == "dana"
    assert m.context["assignee"] == "dana"


def test_allowed_events_listing():
    m = build_ticket()
    m.send("triage")
    m.send("assign", "lee")
    assert m.allowed_events() == ["block", "resolve"], "sorted, from the table"
    m.send("resolve")
    m.send("close")
    assert m.allowed_events() == [], "terminal state allows nothing"
    exc = expect_rejection(m, "reopen")
    assert exc.allowed == []


def test_context_mutation_visible_to_later_guards():
    m = build_ticket()
    m.send("triage")
    m.send("assign", "ana")
    m.send("resolve")
    assert m.send("reopen") == "in_progress"      # reopens: 0 -> 1
    m.send("resolve")
    assert m.send("reopen") == "in_progress"      # reopens: 1 -> 2
    m.send("resolve")
    exc = expect_rejection(m, "reopen")           # guard now refuses
    assert exc.event == "reopen"
    assert m.state == "resolved"
    assert m.context["reopens"] == 2


def test_machines_from_one_definition_are_independent():
    definition = {
        "initial": "off",
        "states": {"off": {}, "on": {}},
        "transitions": [{"event": "flip", "src": "off", "dst": "on"}],
    }
    a = fsmcore.Machine(definition, {"n": 1})
    b = fsmcore.Machine(definition, {"n": 2})
    a.send("flip")
    assert a.state == "on"
    assert b.state == "off", "instances must not share current state"
    assert b.history == [], "instances must not share the audit trail"
    assert b.context == {"n": 2}


def test_history_returns_a_copy():
    m = build_locker()
    m.send("deposit", "5")
    h = m.history
    h.append({"seq": 99, "event": "fake", "src": "x", "dst": "y", "payload": None})
    assert len(m.history) == 1, "mutating the returned list must not affect the machine"


def test_payload_defaults_to_none():
    got = {}

    def action(context, step):
        got.update(step)

    definition = {
        "initial": "a",
        "states": {"a": {}, "b": {}},
        "transitions": [{"event": "go", "src": "a", "dst": "b", "action": action}],
    }
    m = fsmcore.Machine(definition, {})
    m.send("go")
    assert got == {"event": "go", "payload": None, "src": "a", "dst": "b"}
    assert m.history[0]["payload"] is None


def main():
    tests = [
        test_initial_state_and_construction,
        test_definition_validation,
        test_basic_send_and_action,
        test_exit_action_enter_order_and_state_visibility,
        test_unknown_event_is_rejected_with_allowed_list,
        test_guard_refusal_is_rejected_with_allowed_list,
        test_first_matching_row_wins_in_table_order,
        test_self_transition_refires_hooks,
        test_audit_trail_records_every_transition_in_order,
        test_allowed_events_listing,
        test_context_mutation_visible_to_later_guards,
        test_machines_from_one_definition_are_independent,
        test_history_returns_a_copy,
        test_payload_defaults_to_none,
    ]
    for t in tests:
        t()
    print("all %d test groups passed" % len(tests))


if __name__ == "__main__":
    main()
