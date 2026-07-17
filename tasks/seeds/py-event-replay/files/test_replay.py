"""Acceptance checks for replay.py. Run: python3 test_replay.py"""
import copy

from replay import Replayer


def apply_opened(state, data):
    return {"owner": data["owner"], "tier": data["tier"],
            "points": 0, "sources": []}


def apply_earned(state, data):
    out = dict(state)
    out["points"] = state["points"] + data["points"]
    out["sources"] = state["sources"] + [data["source"]]
    return out


def apply_redeemed(state, data):
    out = dict(state)
    out["points"] = state["points"] - data["points"]
    return out


def make_replayer():
    r = Replayer(dict)
    r.on("opened", 2, apply_opened)
    r.on("earned", 3, apply_earned)
    r.on("redeemed", 1, apply_redeemed)
    r.upcast("opened", 1, lambda d: dict(d, tier="basic"))
    r.upcast("earned", 1, lambda d: {"points": d["pts"]})
    r.upcast("earned", 2, lambda d: dict(d, source="legacy"))
    return r


def make_events():
    return [
        {"seq": 4, "type": "redeemed", "v": 1, "data": {"points": 30}},
        {"seq": 1, "type": "opened", "v": 1, "data": {"owner": "ada"}},
        {"seq": 3, "type": "earned", "v": 3,
         "data": {"points": 40, "source": "promo"}},
        {"seq": 2, "type": "earned", "v": 1, "data": {"pts": 100}},
    ]


FINAL = {"owner": "ada", "tier": "basic", "points": 110,
         "sources": ["legacy", "promo"]}


def test_rebuild_orders_by_seq_and_upcasts():
    state = make_replayer().rebuild(make_events())
    assert state == FINAL, state


def test_input_events_are_not_mutated():
    events = make_events()
    pristine = copy.deepcopy(events)
    make_replayer().rebuild(events)
    assert events == pristine, "rebuild scribbled on the caller's events"


def test_gaps_are_fine_duplicates_are_not():
    r = make_replayer()
    sparse = [
        {"seq": 10, "type": "opened", "v": 2,
         "data": {"owner": "bo", "tier": "gold"}},
        {"seq": 700, "type": "earned", "v": 3,
         "data": {"points": 5, "source": "signup"}},
    ]
    assert r.rebuild(sparse)["points"] == 5
    dup = sparse + [{"seq": 10, "type": "redeemed", "v": 1,
                     "data": {"points": 1}}]
    try:
        r.rebuild(dup)
        assert False, "duplicate seq accepted"
    except ValueError:
        pass


def test_snapshot_skips_and_stays_pristine():
    snapshot = {"seq": 2, "state": {"owner": "ada", "tier": "basic",
                                    "points": 100, "sources": ["legacy"]}}
    frozen = copy.deepcopy(snapshot)
    r = make_replayer()
    state = r.rebuild(make_events(), snapshot=snapshot)
    assert state == FINAL, state
    state["sources"].append("tampered")
    state["points"] = -1
    assert snapshot == frozen, "snapshot shares structure with the result"
    assert r.rebuild(make_events(), snapshot=snapshot) == FINAL
    # even with zero events to apply, the caller gets a private copy
    untouched = r.rebuild([], snapshot=snapshot)
    untouched["points"] = 0
    untouched["sources"].append("x")
    assert snapshot == frozen, "empty replay handed back the snapshot itself"


def test_event_newer_than_handler_fails_loud():
    r = make_replayer()
    events = [{"seq": 1, "type": "redeemed", "v": 2, "data": {"points": 1}}]
    try:
        r.rebuild(events)
        assert False, "applied an event from the future"
    except ValueError:
        pass


def test_missing_upcaster_link():
    r = Replayer(dict)
    r.on("adjusted", 3, lambda s, d: d)
    r.upcast("adjusted", 1, lambda d: d)      # 1->2 exists, 2->3 missing
    events = [{"seq": 1, "type": "adjusted", "v": 1, "data": {}}]
    try:
        r.rebuild(events)
        assert False, "hole in the upcast chain went unnoticed"
    except ValueError:
        pass


def test_strict_mode_for_unknown_types():
    r = make_replayer()
    events = make_events() + [{"seq": 9, "type": "audited", "v": 1,
                               "data": {}}]
    try:
        r.rebuild(events)
        assert False, "unknown event type applied silently"
    except KeyError:
        pass
    assert r.rebuild(events, strict=False) == FINAL


def test_duplicate_registration_rejected():
    r = make_replayer()
    try:
        r.on("earned", 4, apply_earned)
        assert False, "second handler for a type accepted"
    except ValueError:
        pass
    try:
        r.upcast("earned", 1, lambda d: d)
        assert False, "second upcaster for the same step accepted"
    except ValueError:
        pass


def test_rebuilds_are_independent():
    r = Replayer(lambda: {"points": 0, "log": []})
    first = r.rebuild([])
    second = r.rebuild([])
    assert first == {"points": 0, "log": []}
    first["log"].append("x")
    first["points"] = 99
    assert second == {"points": 0, "log": []}, "rebuilds share state"


CHECKS = [
    test_rebuild_orders_by_seq_and_upcasts,
    test_input_events_are_not_mutated,
    test_gaps_are_fine_duplicates_are_not,
    test_snapshot_skips_and_stays_pristine,
    test_event_newer_than_handler_fails_loud,
    test_missing_upcaster_link,
    test_strict_mode_for_unknown_types,
    test_duplicate_registration_rejected,
    test_rebuilds_are_independent,
]


def main():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
