"""Acceptance tests for the MVCC transaction layer. Run: python3 test_mvcc.py

Semantics under test (details in the ticket):
- begin() takes a snapshot: a transaction reads the committed state as of
  begin(), overlaid with its own writes. Later commits by others are
  invisible to it.
- First-committer-wins: commit() raises WriteConflict when any key in the
  transaction's write set (puts AND deletes) was committed by another
  transaction after this one began — even if the value is identical. The
  failed transaction is rolled back and closed. Reads never conflict
  (snapshot isolation, not serializable: write skew is allowed).
- Any operation on a committed/aborted/conflicted transaction raises
  TxnClosed.
- versions(key) counts stored version records for the key, tombstones
  included. vacuum() drops a record when (a) no active snapshot resolves the
  key to it AND (b) it is not the key's newest committed record — except
  that a newest-record tombstone is itself dropped once every older record
  of its key is gone (the key then vanishes entirely). Returns the number of
  records removed.
"""


def expect(exc, fn):
    try:
        fn()
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__}")


def main():
    from mvcc import MVCCStore, TxnClosed, WriteConflict

    # ---- basics: read-your-writes, commit visibility -----------------------
    db = MVCCStore()
    assert db.read("plan") is None
    t = db.begin()
    assert t.get("plan") is None
    t.put("plan", "starter")
    t.put("seats", 5)
    assert t.get("plan") == "starter"          # read-your-writes
    assert db.read("plan") is None             # not committed yet
    t2 = db.begin()
    assert t2.get("plan") is None              # invisible to a peer snapshot
    t2.abort()
    t.commit()
    assert db.read("plan") == "starter" and db.read("seats") == 5
    t3 = db.begin()
    assert t3.get("plan") == "starter"
    t3.commit()

    # overwrite own write, delete own write
    t = db.begin()
    t.put("plan", "pro")
    t.put("plan", "enterprise")
    assert t.get("plan") == "enterprise"
    t.delete("seats")
    assert t.get("seats") is None
    t.commit()
    assert db.read("plan") == "enterprise"
    assert db.read("seats") is None

    # ---- snapshot isolation -------------------------------------------------
    db = MVCCStore()
    w = db.begin(); w.put("color", "red"); w.commit()
    old = db.begin()
    assert old.get("color") == "red"
    w = db.begin(); w.put("color", "blue"); w.commit()
    assert old.get("color") == "red", "snapshot must not see later commits"
    fresh = db.begin()
    assert fresh.get("color") == "blue"
    fresh.commit()
    old.commit()                                # read-only commit always fine

    # a deletion is invisible to older snapshots
    old = db.begin()
    w = db.begin(); w.delete("color"); w.commit()
    assert old.get("color") == "blue"
    assert db.read("color") is None
    assert db.begin().get("color") is None
    old.abort()

    # ---- write-write conflicts ----------------------------------------------
    db = MVCCStore()
    w = db.begin(); w.put("stock", 10); w.commit()

    t1 = db.begin()
    t2 = db.begin()
    t2.put("stock", 9)
    t2.commit()
    t1.put("stock", 9)                          # same value: still a conflict
    t1.put("audit", "t1-was-here")
    e = expect(WriteConflict, t1.commit)
    assert "stock" in str(e), "conflict error should name a conflicting key"
    assert db.read("stock") == 9
    assert db.read("audit") is None, "a conflicted txn must apply nothing"
    expect(TxnClosed, lambda: t1.get("stock"))  # rolled back and closed

    # conflict against a committed delete
    t1 = db.begin()
    t2 = db.begin(); t2.delete("stock"); t2.commit()
    t1.put("stock", 42)
    expect(WriteConflict, t1.commit)

    # delete-delete conflicts too
    w = db.begin(); w.put("stock", 1); w.commit()
    t1 = db.begin()
    t2 = db.begin(); t2.delete("stock"); t2.commit()
    t1.delete("stock")
    expect(WriteConflict, t1.commit)

    # disjoint write sets never conflict; reads never conflict
    t1 = db.begin()
    t2 = db.begin(); t2.put("left", 1); t2.commit()
    assert t1.get("left") is None
    t1.put("right", 2)
    t1.commit()
    assert db.read("left") == 1 and db.read("right") == 2

    # sequential transactions on the same key are fine
    for v in ("a", "b", "c"):
        t = db.begin(); t.put("seq", v); t.commit()
    assert db.read("seq") == "c"

    # write skew is allowed (snapshot isolation, not serializable)
    db = MVCCStore()
    w = db.begin(); w.put("x", 0); w.put("y", 0); w.commit()
    t1 = db.begin()
    t2 = db.begin()
    assert t1.get("y") == 0
    t1.put("x", 1)
    assert t2.get("x") == 0
    t2.put("y", 1)
    t1.commit()
    t2.commit()
    assert db.read("x") == 1 and db.read("y") == 1

    # a retry after a conflict goes through
    t1 = db.begin()
    t2 = db.begin(); t2.put("x", 5); t2.commit()
    t1.put("x", 7)
    expect(WriteConflict, t1.commit)
    retry = db.begin()
    retry.put("x", 7)
    retry.commit()
    assert db.read("x") == 7

    # ---- closed-transaction guards ------------------------------------------
    t = db.begin()
    t.put("z", 1)
    t.commit()
    expect(TxnClosed, t.commit)
    expect(TxnClosed, t.abort)
    expect(TxnClosed, lambda: t.put("z", 2))
    expect(TxnClosed, lambda: t.get("z"))
    expect(TxnClosed, lambda: t.delete("z"))
    t = db.begin()
    t.put("gone", 1)
    t.abort()
    expect(TxnClosed, t.abort)
    expect(TxnClosed, lambda: t.put("gone", 2))
    assert db.read("gone") is None

    # ---- version chains and vacuum -------------------------------------------
    db = MVCCStore()
    assert db.versions("k") == 0
    t_old = db.begin()                           # snapshot from before v1
    reader3 = None
    for i in range(1, 6):
        t = db.begin()
        t.put("k", f"v{i}")
        t.commit()
        if i == 3:
            reader3 = db.begin()                 # snapshot pinned at v3
    assert db.versions("k") == 5
    assert t_old.get("k") is None
    assert reader3.get("k") == "v3"
    assert db.read("k") == "v5"

    # v1, v2, v4 are visible to nobody; v3 is pinned by reader3, v5 is newest
    assert db.vacuum() == 3
    assert db.versions("k") == 2
    assert reader3.get("k") == "v3", "vacuum must not steal a pinned version"
    assert t_old.get("k") is None, "vacuum must not resurrect newer versions"
    assert db.read("k") == "v5"

    reader3.commit()
    t_old.abort()
    assert db.vacuum() == 1                      # v3 released, only v5 stays
    assert db.versions("k") == 1
    assert db.read("k") == "v5"
    assert db.vacuum() == 0                      # nothing left to collect

    # tombstones vacuum away entirely once nothing can see the key
    db = MVCCStore()
    t = db.begin(); t.put("tmp", {"a": 1}); t.commit()
    t = db.begin()
    assert t.get("tmp") == {"a": 1}
    t.delete("tmp")
    t.commit()
    assert db.versions("tmp") == 2               # value + tombstone
    assert db.vacuum() == 2
    assert db.versions("tmp") == 0
    assert db.read("tmp") is None
    assert db.begin().get("tmp") is None

    # deleting a key that never existed still records (and conflicts on) it
    db = MVCCStore()
    t1 = db.begin()
    t2 = db.begin()
    t2.delete("phantom")
    t2.commit()
    assert db.versions("phantom") == 1
    t1.delete("phantom")
    expect(WriteConflict, t1.commit)
    assert db.vacuum() == 1
    assert db.versions("phantom") == 0

    # vacuum keeps versions needed by a snapshot that predates a tombstone
    db = MVCCStore()
    t = db.begin(); t.put("doc", "draft"); t.commit()
    pinned = db.begin()
    t = db.begin(); t.delete("doc"); t.commit()
    assert db.versions("doc") == 2
    removed = db.vacuum()
    assert removed == 0, "tombstone must stay while an older version is pinned"
    assert pinned.get("doc") == "draft"
    assert db.read("doc") is None
    pinned.commit()
    assert db.vacuum() == 2
    assert db.versions("doc") == 0

    print("all mvcc checks passed")


if __name__ == "__main__":
    main()
