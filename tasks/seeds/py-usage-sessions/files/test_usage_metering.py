"""Acceptance tests for the feature-usage metering transforms.

Run: python3 test_usage_metering.py
"""
import copy


def ev(user, name, ts, key=None, **extra):
    e = {"user": user, "name": name, "ts": ts}
    if key is not None:
        e["key"] = key
    e.update(extra)
    return e


def main():
    from usage_metering import dedupe, sessionize, funnel, usage_report

    # ---------------- dedupe ----------------

    # first occurrence in INPUT order wins, even when the later duplicate
    # carries an earlier timestamp
    events = [
        ev("ana", "page_view", 100, key="k1", src="web"),
        ev("ana", "page_view", 90, key="k1", src="ios"),   # retry of k1, earlier ts
        ev("ana", "search", 110, key="k2"),
        ev("bob", "search", 111),                           # no key: always kept
        ev("bob", "search", 111),                           # no key: kept too
        ev("bob", "page_view", 120, key="k2"),              # k2 again, other user: dup
    ]
    snapshot = copy.deepcopy(events)
    out = dedupe(events)
    assert [e["ts"] for e in out] == [100, 110, 111, 111], [e["ts"] for e in out]
    assert out[0]["src"] == "web"          # the kept k1 is the first one seen
    assert events == snapshot              # input list and dicts untouched
    assert dedupe([]) == []

    # ---------------- sessionize ----------------

    try:
        sessionize([ev("ana", "x", 1)], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("gap of 0 must raise ValueError")
    assert sessionize([], 1800) == []

    # a delta of exactly `gap` stays in the SAME session; gap+1 splits
    events = [
        ev("ana", "open", 1000),
        ev("ana", "edit", 1000 + 1800),       # exactly at gap: same session
        ev("ana", "save", 1000 + 1800 + 1801),  # 1801 past previous: new session
    ]
    sessions = sessionize(events, 1800)
    assert len(sessions) == 2, sessions
    assert sessions[0]["start"] == 1000 and sessions[0]["end"] == 2800
    assert sessions[0]["duration"] == 1800
    assert [e["name"] for e in sessions[0]["events"]] == ["open", "edit"]
    assert sessions[1]["start"] == 4601 and sessions[1]["end"] == 4601
    assert sessions[1]["duration"] == 0
    assert [e["name"] for e in sessions[1]["events"]] == ["save"]

    # users are independent; events arrive interleaved and unsorted; output
    # is sorted by (user, start)
    events = [
        ev("bob", "open", 5000),
        ev("ana", "open", 5010),
        ev("bob", "edit", 5100),
        ev("ana", "edit", 9000),
        ev("bob", "save", 9500),
    ]
    sessions = sessionize(events, 600)
    got = [(s["user"], s["start"], s["end"], len(s["events"])) for s in sessions]
    assert got == [
        ("ana", 5010, 5010, 1),
        ("ana", 9000, 9000, 1),
        ("bob", 5000, 5100, 2),
        ("bob", 9500, 9500, 1),
    ], got

    # equal timestamps keep input order within the session (stable sort)
    events = [
        ev("ana", "first", 100),
        ev("ana", "second", 100),
        ev("ana", "third", 100),
    ]
    sessions = sessionize(events, 60)
    assert len(sessions) == 1
    assert [e["name"] for e in sessions[0]["events"]] == ["first", "second", "third"]

    # ---------------- funnel ----------------

    try:
        funnel([ev("ana", "x", 1)], [])
    except ValueError:
        pass
    else:
        raise AssertionError("empty steps must raise ValueError")

    steps = ["page_view", "add_to_cart", "purchase"]

    events = [
        # carol converts fully
        ev("carol", "page_view", 10),
        ev("carol", "add_to_cart", 20),
        ev("carol", "purchase", 30),
        # bob stops after the cart
        ev("bob", "page_view", 11),
        ev("bob", "add_to_cart", 25),
        # dave does things in the wrong order: his cart precedes his first
        # page_view, so nothing after the anchor matches add_to_cart and he
        # stalls at step 1 (the purchase can't count without the cart)
        ev("dave", "add_to_cart", 5),
        ev("dave", "page_view", 6),
        ev("dave", "purchase", 7),
        # erin never triggers the entry step at all
        ev("erin", "add_to_cart", 40),
        ev("erin", "purchase", 41),
    ]
    result = funnel(events, steps)
    assert [r["step"] for r in result] == steps
    assert result[0] == {"step": "page_view", "count": 3, "users": ["bob", "carol", "dave"]}
    assert result[1] == {"step": "add_to_cart", "count": 2, "users": ["bob", "carol"]}
    assert result[2] == {"step": "purchase", "count": 1, "users": ["carol"]}

    # first-touch anchoring: fay's first page_view is at ts 10; the cart at 15
    # sits after it, so she converts — an implementation that re-anchors at her
    # LAST page_view (ts 20) would miss the cart and get this wrong
    events = [
        ev("fay", "page_view", 10),
        ev("fay", "add_to_cart", 15),
        ev("fay", "page_view", 20),
    ]
    result = funnel(events, ["page_view", "add_to_cart"])
    assert result[1] == {"step": "add_to_cart", "count": 1, "users": ["fay"]}

    # equal timestamps resolve by stable input order, position-wise
    events = [
        ev("gil", "page_view", 50),
        ev("gil", "add_to_cart", 50),   # same ts, later in input: still counts
    ]
    result = funnel(events, ["page_view", "add_to_cart"])
    assert result[1]["count"] == 1, result

    events = [
        ev("hal", "add_to_cart", 50),   # same ts but BEFORE the page_view in input
        ev("hal", "page_view", 50),
    ]
    result = funnel(events, ["page_view", "add_to_cart"])
    assert result[0]["count"] == 1
    assert result[1] == {"step": "add_to_cart", "count": 0, "users": []}

    # a repeated step name needs a repeated occurrence
    events = [
        ev("ivy", "page_view", 1),
        ev("ivy", "page_view", 2),
        ev("jon", "page_view", 3),
    ]
    result = funnel(events, ["page_view", "page_view"])
    assert result[0]["count"] == 2
    assert result[1] == {"step": "page_view", "count": 1, "users": ["ivy"]}

    # a step nobody reaches still appears, zeroed
    result = funnel([ev("ivy", "page_view", 1)], ["page_view", "export_pdf"])
    assert result[1] == {"step": "export_pdf", "count": 0, "users": []}

    assert funnel([], ["page_view"]) == [{"step": "page_view", "count": 0, "users": []}]

    # ---------------- usage_report (the glued pipeline) ----------------

    events = [
        ev("ana", "page_view", 1000, key="a1"),
        ev("ana", "page_view", 1000, key="a1"),     # retry: must not double-count
        ev("ana", "add_to_cart", 1300, key="a2"),
        ev("ana", "page_view", 9000, key="a3"),     # second session (gap 1800)
        ev("bob", "page_view", 2000, key="b1"),
        ev("bob", "add_to_cart", 2100),             # keyless
        ev("bob", "purchase", 2200, key="b2"),
    ]
    report = usage_report(events, 1800, ["page_view", "add_to_cart", "purchase"])
    assert report == {
        "event_count": 6,
        "user_count": 2,
        "session_count": 3,
        "sessions_by_user": {"ana": 2, "bob": 1},
        "funnel": [
            {"step": "page_view", "count": 2, "users": ["ana", "bob"]},
            {"step": "add_to_cart", "count": 2, "users": ["ana", "bob"]},
            {"step": "purchase", "count": 1, "users": ["bob"]},
        ],
    }, report

    # dedupe happens FIRST: if the retry of a1 survived, ana would wrongly
    # reach add_to_cart twice... assert the deduped shape directly
    report = usage_report(
        [ev("ana", "page_view", 10, key="x"), ev("ana", "page_view", 11, key="x")],
        60, ["page_view", "page_view"])
    assert report["event_count"] == 1
    assert report["funnel"][1]["count"] == 0

    # empty pipeline
    report = usage_report([], 60, ["page_view"])
    assert report == {
        "event_count": 0,
        "user_count": 0,
        "session_count": 0,
        "sessions_by_user": {},
        "funnel": [{"step": "page_view", "count": 0, "users": []}],
    }, report

    print("ok")


if __name__ == "__main__":
    main()
