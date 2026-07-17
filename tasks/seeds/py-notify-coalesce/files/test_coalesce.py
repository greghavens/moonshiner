"""Acceptance checks for coalesce.py. Run: python3 test_coalesce.py"""
from coalesce import Coalescer


def test_basic_coalescing_by_window():
    c = Coalescer(window=60, max_batch=10)
    assert c.add("thread:9", "comment 1", 0) == []
    assert c.add("thread:9", "comment 2", 10) == []
    assert c.add("thread:9", "comment 3", 59) == []
    assert c.pending() == {"thread:9": 3}
    flushed = c.drain(60)
    assert flushed == [{"key": "thread:9",
                        "payloads": ["comment 1", "comment 2", "comment 3"],
                        "opened_at": 0, "reason": "window"}]
    assert c.pending() == {}
    assert c.drain(120) == []


def test_boundary_payload_opens_a_new_batch():
    c = Coalescer(window=60, max_batch=10)
    c.add("k", "early", 0)
    out = c.add("k", "on the line", 60)      # exactly window later
    assert len(out) == 1
    assert out[0]["payloads"] == ["early"]
    assert out[0]["reason"] == "window"
    assert c.pending() == {"k": 1}
    (batch,) = c.flush_all(61)
    assert batch["payloads"] == ["on the line"]
    assert batch["opened_at"] == 60


def test_size_flush_is_immediate():
    c = Coalescer(window=1000, max_batch=3)
    assert c.add("k", 1, 0) == []
    assert c.add("k", 2, 1) == []
    out = c.add("k", 3, 2)
    assert out == [{"key": "k", "payloads": [1, 2, 3],
                    "opened_at": 0, "reason": "size"}]
    assert c.pending() == {}
    assert c.add("k", 4, 3) == []            # fresh window at t=3
    (batch,) = c.flush_all(4)
    assert batch["opened_at"] == 3
    assert batch["payloads"] == [4]


def test_expiry_fires_on_any_call_for_any_key():
    c = Coalescer(window=10, max_batch=99)
    c.add("alpha", "a1", 0)
    c.add("beta", "b1", 2)
    out = c.add("gamma", "g1", 20)           # both others are stale now
    assert [(b["key"], b["reason"]) for b in out] == \
        [("alpha", "window"), ("beta", "window")]
    assert c.pending() == {"gamma": 1}


def test_flush_ordering_opened_at_then_key():
    c = Coalescer(window=10, max_batch=99)
    c.add("zeta", "z", 0)
    c.add("alpha", "a", 0)
    c.add("mid", "m", 1)
    out = c.drain(50)
    assert [b["key"] for b in out] == ["alpha", "zeta", "mid"]


def test_keys_coalesce_independently():
    c = Coalescer(window=60, max_batch=10)
    c.add("u:1", "x", 0)
    c.add("u:2", "y", 30)
    out = c.drain(60)                        # only u:1 is stale
    assert [b["key"] for b in out] == ["u:1"]
    assert c.pending() == {"u:2": 1}
    out = c.drain(90)
    assert [b["key"] for b in out] == ["u:2"]


def test_flush_all_forces_everything():
    c = Coalescer(window=60, max_batch=10)
    c.add("b", 1, 0)
    c.add("a", 2, 5)
    c.add("a", 3, 6)
    out = c.flush_all(7)
    assert [(b["key"], b["reason"]) for b in out] == \
        [("b", "forced"), ("a", "forced")]
    assert out[1]["payloads"] == [2, 3]
    assert c.pending() == {}
    assert c.flush_all(8) == []


def test_time_never_goes_backwards():
    c = Coalescer(window=60, max_batch=10)
    c.add("k", "x", 100)
    for call in [lambda: c.add("k", "y", 99), lambda: c.drain(50),
                 lambda: c.flush_all(0)]:
        try:
            call()
            assert False, "clock rewound and nobody noticed"
        except ValueError:
            pass
    assert c.pending() == {"k": 1}, "failed call mutated state"
    assert c.add("k", "same-instant", 100) == []   # equal timestamp is fine
    assert c.pending() == {"k": 2}


def test_reopened_key_gets_fresh_window():
    c = Coalescer(window=10, max_batch=99)
    c.add("k", "one", 0)
    c.drain(10)
    c.add("k", "two", 25)
    assert c.drain(34) == []                 # 25+10=35 not reached yet
    (batch,) = c.drain(35)
    assert batch["opened_at"] == 25
    assert batch["payloads"] == ["two"]


def test_validation():
    for window, max_batch in [(0, 5), (-3, 5), ("60", 5),
                              (60, 0), (60, -2), (60, 2.5), (60, "3")]:
        try:
            Coalescer(window=window, max_batch=max_batch)
            assert False, "accepted (%r, %r)" % (window, max_batch)
        except ValueError:
            pass
    c = Coalescer(window=60, max_batch=5)
    for key in ["", None, 3]:
        try:
            c.add(key, "payload", 0)
            assert False, "accepted key %r" % (key,)
        except ValueError:
            pass
    assert c.pending() == {}


CHECKS = [
    test_basic_coalescing_by_window,
    test_boundary_payload_opens_a_new_batch,
    test_size_flush_is_immediate,
    test_expiry_fires_on_any_call_for_any_key,
    test_flush_ordering_opened_at_then_key,
    test_keys_coalesce_independently,
    test_flush_all_forces_everything,
    test_time_never_goes_backwards,
    test_reopened_key_gets_fresh_window,
    test_validation,
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
