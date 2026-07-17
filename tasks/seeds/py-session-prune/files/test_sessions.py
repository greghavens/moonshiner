"""Behavior checks for the session store. Run: python3 test_sessions.py"""
from sessions import SessionStore


def main():
    store = SessionStore(ttl=100)
    store.create("tok-a", "kim", now=1000)
    store.create("tok-b", "kim", now=1000)
    store.create("tok-c", "lee", now=1050)
    store.create("tok-d", "lee", now=1090)

    assert store.active_count(now=1095) == 4

    # At t=1160, tok-a and tok-b (expire 1100) and tok-c (expire 1150) are dead.
    removed = store.prune_expired(now=1160)
    assert removed == 3, f"three sessions had expired, got removed={removed!r}"
    assert store.is_active("tok-d", now=1160), "live session must survive the sweep"
    assert not store.is_active("tok-a", now=1160)
    assert store.active_count(now=1160) == 1, f"got {store.active_count(now=1160)}"

    # Sweeping again finds nothing new.
    assert store.prune_expired(now=1161) == 0

    # Log out everywhere: every one of the user's sessions ends.
    store2 = SessionStore(ttl=1000)
    store2.create("s1", "ana", now=0)
    store2.create("s2", "ana", now=0)
    store2.create("s3", "ana", now=0)
    store2.create("x1", "raj", now=0)

    ended = store2.logout_user("ana")
    assert sorted(ended) == ["s1", "s2", "s3"], (
        f"logout must end all of ana's sessions, got {sorted(ended)!r}")
    for tok in ("s1", "s2", "s3"):
        assert not store2.is_active(tok, now=1), f"{tok} still active after logout"
    assert store2.is_active("x1", now=1), "other users' sessions must survive"

    # A repeated logout is a clean no-op.
    assert store2.logout_user("ana") == []

    # New logins after a logout work normally.
    store2.create("s9", "ana", now=5)
    assert store2.is_active("s9", now=6)
    assert sorted(store2.logout_user("ana")) == ["s9"]

    print("all checks passed")


if __name__ == "__main__":
    main()
