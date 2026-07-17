"""Tests for the gateway session store.

The EXISTING BEHAVIOR block passes against the shipped sessions.py and must
keep passing. The blocks below it cover the security-review features and
fail until they are implemented.

Run: python3 test_sessions.py
"""
from sessions import SessionStore


class FakeClock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------- existing behavior

def test_create_get_roundtrip():
    clock = FakeClock()
    store = SessionStore(100, clock)
    sid = store.create("u1", {"role": "admin"})
    assert isinstance(sid, str) and sid, sid
    record = store.get(sid)
    assert record == {"user_id": "u1", "data": {"role": "admin"}}, record
    other = store.create("u2")
    assert other != sid
    assert store.get(other) == {"user_id": "u2", "data": {}}


def test_unknown_and_destroyed_sessions_are_none():
    store = SessionStore(100, FakeClock())
    assert store.get("nope") is None
    sid = store.create("u1")
    assert store.destroy(sid) is True
    assert store.get(sid) is None
    assert store.destroy(sid) is False


def test_untouched_session_expires_after_ttl():
    clock = FakeClock()
    store = SessionStore(100, clock)
    sid = store.create("u1")
    clock.advance(100)  # expiry boundary is inclusive
    assert store.get(sid) is None
    # a fresh session on a fresh store is unaffected
    sid2 = store.create("u1")
    assert store.get(sid2) is not None


# ---------------------------------------------------------------- signed cookies

def test_cookie_value_roundtrip():
    clock = FakeClock()
    store = SessionStore(100, clock, secret=b"gateway-secret")
    sid = store.create("u1")
    value = store.cookie_value(sid)
    assert isinstance(value, str) and value.startswith(sid + "."), value
    signature = value[len(sid) + 1:]
    assert signature, "cookie value must carry a signature after the dot"
    assert store.session_from_cookie(value) == sid


def test_tampered_or_foreign_cookies_are_rejected():
    clock = FakeClock()
    store = SessionStore(100, clock, secret=b"gateway-secret")
    other = SessionStore(100, clock, secret=b"different-secret")
    sid = store.create("u1")
    value = store.cookie_value(sid)

    forged_sid = "f" * len(sid) + value[len(sid):]
    assert store.session_from_cookie(forged_sid) is None
    tampered_sig = value[:-1] + ("0" if value[-1] != "0" else "1")
    assert store.session_from_cookie(tampered_sig) is None

    other_sid = other.create("u1")
    assert store.session_from_cookie(other.cookie_value(other_sid)) is None

    for junk in ("", "no-dot", ".", sid, sid + ".", "." + value):
        assert store.session_from_cookie(junk) is None, junk


def test_valid_signature_for_dead_session_is_none():
    clock = FakeClock()
    store = SessionStore(100, clock, secret=b"gateway-secret")
    sid = store.create("u1")
    value = store.cookie_value(sid)
    clock.advance(100)
    assert store.session_from_cookie(value) is None


def test_cookie_helpers_require_a_secret():
    store = SessionStore(100, FakeClock())
    sid = store.create("u1")
    for call in (lambda: store.cookie_value(sid),
                 lambda: store.session_from_cookie(sid + ".deadbeef")):
        try:
            call()
        except RuntimeError:
            pass
        else:
            raise AssertionError("cookie helpers without a secret must raise RuntimeError")


# ---------------------------------------------------------------- rolling expiry

def test_activity_rolls_the_expiry_window():
    clock = FakeClock()
    store = SessionStore(100, clock)
    sid = store.create("u1")
    clock.advance(90)
    assert store.get(sid) is not None      # rolls expiry to t=190
    clock.advance(90)                      # t=180
    assert store.get(sid) is not None      # rolls expiry to t=280
    clock.advance(101)                     # t=281
    assert store.get(sid) is None, "idle past a full ttl must still expire"


def test_session_from_cookie_counts_as_activity():
    clock = FakeClock()
    store = SessionStore(100, clock, secret=b"gateway-secret")
    sid = store.create("u1")
    value = store.cookie_value(sid)
    clock.advance(90)
    assert store.session_from_cookie(value) == sid   # rolls to t=190
    clock.advance(90)                                # t=180
    assert store.get(sid) is not None


# ---------------------------------------------------------------- concurrent-login cap

def test_cap_evicts_least_recently_active_session():
    clock = FakeClock()
    store = SessionStore(1000, clock, max_sessions_per_user=2)
    s1 = store.create("u1")
    clock.advance(10)
    s2 = store.create("u1")
    clock.advance(10)
    s3 = store.create("u1")
    assert store.get(s1) is None, "oldest session should have been evicted"
    assert store.get(s2) is not None
    assert store.get(s3) is not None


def test_cap_honors_recent_activity_and_other_users():
    clock = FakeClock()
    store = SessionStore(1000, clock, max_sessions_per_user=2)
    s1 = store.create("u1")
    clock.advance(10)
    s2 = store.create("u1")
    bystander = store.create("u2")
    clock.advance(20)
    assert store.get(s1) is not None       # s1 is now the most recently active
    clock.advance(10)
    s3 = store.create("u1")                # must evict s2, not s1
    assert store.get(s2) is None
    assert store.get(s1) is not None
    assert store.get(s3) is not None
    assert store.get(bystander) is not None, "caps are per-user"


def test_destroyed_and_expired_sessions_free_cap_slots():
    clock = FakeClock()
    store = SessionStore(100, clock, max_sessions_per_user=2)
    s1 = store.create("u1")
    s2 = store.create("u1")
    store.destroy(s1)
    s3 = store.create("u1")               # slot freed by destroy: no eviction
    assert store.get(s2) is not None
    assert store.get(s3) is not None

    clock.advance(100)                    # s2 and s3 expire untouched
    s4 = store.create("u1")
    s5 = store.create("u1")
    assert store.get(s4) is not None
    assert store.get(s5) is not None


def main():
    tests = [
        test_create_get_roundtrip,
        test_unknown_and_destroyed_sessions_are_none,
        test_untouched_session_expires_after_ttl,
        test_cookie_value_roundtrip,
        test_tampered_or_foreign_cookies_are_rejected,
        test_valid_signature_for_dead_session_is_none,
        test_cookie_helpers_require_a_secret,
        test_activity_rolls_the_expiry_window,
        test_session_from_cookie_counts_as_activity,
        test_cap_evicts_least_recently_active_session,
        test_destroyed_and_expired_sessions_free_cap_slots,
        test_cap_honors_recent_activity_and_other_users,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
