"""Acceptance gate for the quarry shift-log archiver.

Run with warnings escalated:  python3 -W error test_quarrylog.py

Every library call must complete without leaking an open file or
database handle, and outputs must be byte-identical to the pinned ones.
"""

import gc
import os
import tempfile
import warnings

import quarrylog


def clean_call(fn, *args):
    """Call fn(*args); fail if the call leaves an unclosed resource behind."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = fn(*args)
        gc.collect()
    leaks = [w for w in caught if issubclass(w.category, ResourceWarning)]
    assert not leaks, "%s() leaked a resource: %s" % (fn.__name__, leaks[0].message)
    return out


def main():
    with tempfile.TemporaryDirectory() as td:
        shift = os.path.join(td, "shift.log")
        with open(shift, "w") as f:
            f.write("# morning shift\nT-12,14.5\nT-07,9.5\n\nT-12,11.0\n")

        rows = clean_call(quarrylog.read_shift, shift)
        assert rows == [("T-12", 14.5), ("T-07", 9.5), ("T-12", 11.0)], rows

        tail = clean_call(quarrylog.last_entries, shift, 2)
        assert tail == ["T-07,9.5", "T-12,11.0"], tail

        recap = os.path.join(td, "recap.txt")
        clean_call(quarrylog.write_recap, recap, "2026-03-02", rows)
        with open(recap) as f:
            body = f.read()
        want = "recap 2026-03-02\nT-12 14.5\nT-07 9.5\nT-12 11.0\ntotal 35.0\n"
        assert body == want, "recap body drifted: %r" % body

        db = os.path.join(td, "site.db")
        clean_call(quarrylog.archive_shift, db, "2026-03-02", rows)
        assert clean_call(quarrylog.day_total, db, "2026-03-02") == 35.0

        clean_call(quarrylog.archive_shift, db, "2026-03-03", [("T-07", 4.0)])
        assert clean_call(quarrylog.day_total, db, "2026-03-03") == 4.0
        assert clean_call(quarrylog.day_total, db, "2026-03-02") == 35.0
        assert clean_call(quarrylog.day_total, db, "2026-01-01") == 0

    print("ok")


if __name__ == "__main__":
    main()
