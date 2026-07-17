"""Acceptance tests for the pagedb B-tree page store. Run: python3 test_pagedb.py

Contract summary (details in the ticket):
- PageStore.open(path, page_size=4096); page_size is fixed at creation and
  stored in the file; reopening honors the stored value.
- Page 0 is the header (magic, geometry, root, free-list head, entry count,
  checksum). flush() is the commit point: data pages first, header last.
- open() truncates a file that is longer than the header's committed page
  count (leftover bytes from an interrupted append are discarded), and raises
  CorruptError when the header checksum fails or the file is shorter than the
  committed page count claims.
- Deletions merge/borrow on underflow; freed pages go on a persistent free
  list and are reused before the file grows.
All test data lives under ./pagedb_test_data (created and removed here).
"""
import os
import shutil

DATA_DIR = os.path.join(".", "pagedb_test_data")


def fsize(path):
    return os.path.getsize(path)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def main():
    from pagedb import PageStore, CorruptError

    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR)
    os.makedirs(DATA_DIR)
    path = os.path.join(DATA_DIR, "data.db")
    try:
        # ---- create + basics ----------------------------------------------
        st = PageStore.open(path, page_size=512)
        assert os.path.exists(path), "open must create the file"
        assert len(st) == 0
        assert st.get("missing") is None
        assert list(st.scan()) == []

        st.put("alpha", b"one")
        st.put("beta", b"two")
        assert st.get("alpha") == b"one"
        assert st.get("beta") == b"two"
        assert len(st) == 2

        # overwrite replaces, len unchanged
        st.put("alpha", b"uno")
        assert st.get("alpha") == b"uno"
        assert len(st) == 2

        # zero-length value is a real value, distinct from missing
        st.put("empty", b"")
        assert st.get("empty") == b""
        assert len(st) == 3

        # delete reports whether the key existed
        assert st.delete("beta") is True
        assert st.delete("beta") is False
        assert st.get("beta") is None
        assert len(st) == 2

        # ---- validation ---------------------------------------------------
        for bad in [lambda: st.put("", b"x"),
                    lambda: st.put("k" * 65, b"x"),        # key cap: page_size//8 bytes
                    lambda: st.put("é" * 33, b"x"),   # 66 utf-8 bytes, same cap
                    lambda: st.put("big", b"x" * 129)]:    # value cap: page_size//4 bytes
            try:
                bad()
                assert False, "expected ValueError"
            except ValueError:
                pass
        st.put("k" * 64, b"x" * 128)  # both caps inclusive
        assert st.get("k" * 64) == b"x" * 128
        assert st.delete("k" * 64) is True

        st.flush()
        assert fsize(path) % 512 == 0, "file must be whole 512-byte pages"
        stats = st.stats()
        assert sorted(stats.keys()) == ["entries", "free_pages", "height",
                                        "nodes", "page_size", "pages"], stats
        assert stats["page_size"] == 512
        assert stats["entries"] == 2
        assert stats["pages"] == fsize(path) // 512

        # ---- bulk load: splits must happen --------------------------------
        keys = [f"key-{i:04d}" for i in range(300)]
        for i in range(300):
            j = (i * 7) % 300  # deterministic non-sorted insertion order
            st.put(keys[j], f"val-{j:03d}-{j:03d}-{j:03d}".encode())
        # the three earlier keys are still around
        assert st.get("alpha") == b"uno" and st.get("empty") == b""
        assert len(st) == 302
        for j in (0, 1, 149, 298, 299):
            assert st.get(keys[j]) == f"val-{j:03d}-{j:03d}-{j:03d}".encode(), j

        stats = st.stats()
        assert 2 <= stats["height"] <= 4, stats
        assert stats["nodes"] >= 15, stats
        assert stats["entries"] == 302

        # overwrites that grow the payload must split correctly too
        for j in range(0, 300, 6):
            st.put(keys[j], bytes([65 + j % 26]) * 100)
        assert len(st) == 302
        for j in (0, 6, 294):
            assert st.get(keys[j]) == bytes([65 + j % 26]) * 100, j

        # ---- range scans: lo inclusive, hi exclusive ----------------------
        got = [k for k, _ in st.scan("key-0010", "key-0015")]
        assert got == [f"key-{i:04d}" for i in range(10, 15)], got
        got = [k for k, _ in st.scan("key-0297")]
        assert got == ["key-0297", "key-0298", "key-0299"], got
        got = [k for k, _ in st.scan(None, "key-0002")]
        assert got == ["alpha", "empty", "key-0000", "key-0001"], got
        assert list(st.scan("key-0100", "key-0100")) == []
        assert list(st.scan("key-0200", "key-0100")) == []
        full = list(st.scan())
        assert [k for k, _ in full] == sorted(["alpha", "empty"] + keys)
        assert full[0][1] == b"uno"  # values ride along

        # scan bounds need not be present keys
        got = [k for k, _ in st.scan("key-0000x", "key-0002x")]
        assert got == ["key-0001", "key-0002"], got

        # ---- commit point + crash rewind ----------------------------------
        st.flush()
        assert fsize(path) % 512 == 0
        snap = read_bytes(path)  # committed image
        for i in range(20):
            st.put(f"extra-{i:02d}", b"uncommitted-batch")
        st.flush()
        st.close()
        st.close()  # close is idempotent

        st = PageStore.open(path)  # stored page size wins over the default
        assert st.stats()["page_size"] == 512
        assert len(st) == 322
        assert st.get("extra-07") == b"uncommitted-batch"
        st.close()

        # a crash right after the first flush: disk holds the older image
        write_bytes(path, snap)
        st = PageStore.open(path, page_size=4096)  # arg ignored for existing file
        assert st.stats()["page_size"] == 512
        assert len(st) == 302, "rewound image must show exactly the first commit"
        assert st.get("extra-07") is None
        assert st.get("key-0123") is not None

        # ---- mass delete: merges shrink the tree, pages hit the free list --
        nodes_before = st.stats()["nodes"]
        for j in range(300):
            if j % 30 != 0:
                assert st.delete(keys[j]) is True, j
        assert st.delete("alpha") is True and st.delete("empty") is True
        kept = [f"key-{i:04d}" for i in range(0, 300, 30)]
        assert len(st) == 10
        assert [k for k, _ in st.scan()] == kept
        for k in kept:
            assert st.get(k) is not None, k
        stats = st.stats()
        assert stats["height"] <= 2, stats
        assert stats["nodes"] <= 6, ("underflowing nodes must merge", stats)
        assert stats["nodes"] < nodes_before
        assert stats["free_pages"] >= 15, stats

        # free list survives close/reopen
        st.flush()
        free_before = st.stats()["free_pages"]
        st.close()
        st = PageStore.open(path)
        assert st.stats()["free_pages"] == free_before
        assert len(st) == 10

        # ---- free pages are reused before the file grows -------------------
        size_before = fsize(path)
        for i in range(40):
            st.put(f"new-{i:03d}", b"reuse-me-please-12345678")
        st.flush()
        assert fsize(path) == size_before, "must allocate from the free list first"
        assert st.stats()["free_pages"] < free_before

        # steady-state churn: same workload twice lands on the same file size
        for k, _ in list(st.scan()):
            assert st.delete(k) is True
        assert len(st) == 0 and list(st.scan()) == []
        for cycle in range(2):
            for i in range(150):
                st.put(f"x-{i:03d}", b"churn-value-abcdefgh")
            st.flush()
            size_now = fsize(path)
            if cycle == 0:
                size_a = size_now
            else:
                assert size_now == size_a, (size_a, size_now)
            for i in range(150):
                assert st.delete(f"x-{i:03d}") is True
            st.flush()
        st.close()

        # ---- crash: garbage appended past the committed length -------------
        committed = fsize(path)
        with open(path, "ab") as f:
            f.write(b"\xcc" * 700)
        st = PageStore.open(path)
        assert fsize(path) == committed, "open must truncate past the committed length"
        assert len(st) == 0
        st.put("after-repair", b"ok")
        st.flush()
        assert fsize(path) % 512 == 0
        st.close()

        # ---- crash: torn header write --------------------------------------
        img = read_bytes(path)
        write_bytes(path, bytes(b ^ 0xFF for b in img[:32]) + img[32:])
        try:
            PageStore.open(path)
            assert False, "corrupt header must not open"
        except CorruptError:
            pass
        write_bytes(path, img)  # restore -> opens again
        st = PageStore.open(path)
        assert st.get("after-repair") == b"ok"
        st.close()

        # ---- unicode keys round-trip, ordered by codepoint ------------------
        upath = os.path.join(DATA_DIR, "uni.db")
        st = PageStore.open(upath, page_size=512)
        for k in ["zèbre", "épée", "apple"]:
            st.put(k, k.encode("utf-8"))
        assert [k for k, _ in st.scan()] == ["apple", "zèbre", "épée"]
        st.close()
        st = PageStore.open(upath)
        assert st.get("épée") == "épée".encode("utf-8")
        st.close()

        # ---- default page size + closed-store guards ------------------------
        opath = os.path.join(DATA_DIR, "other.db")
        st = PageStore.open(opath)
        assert st.stats()["page_size"] == 4096
        st.put("a", b"1")
        st.flush()
        assert fsize(opath) % 4096 == 0
        st.close()
        for op in [lambda: st.put("b", b"2"), lambda: st.get("a"),
                   lambda: list(st.scan()), lambda: st.delete("a")]:
            try:
                op()
                assert False, "closed store must raise ValueError"
            except ValueError:
                pass

        # ---- crash: file shorter than the header claims ---------------------
        st = PageStore.open(path)
        st.put("padder-1", b"p" * 100)
        st.put("padder-2", b"q" * 100)
        st.flush()
        pages = fsize(path) // 512
        assert pages >= 3
        st.close()
        os.truncate(path, (pages - 1) * 512)
        try:
            PageStore.open(path)
            assert False, "short file must not open"
        except CorruptError:
            pass
    finally:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    print("all pagedb checks passed")


if __name__ == "__main__":
    main()
