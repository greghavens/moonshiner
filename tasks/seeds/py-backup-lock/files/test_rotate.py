"""Behavior checks for snapshot rotation. Run: python3 test_rotate.py"""
import os
import tempfile

from rotate import LockHeldError, SnapshotStore


def make_snaps(root, stamps):
    for stamp in stamps:
        with open(os.path.join(root, f"{stamp}.snap"), "w") as f:
            f.write("data")


def main():
    with tempfile.TemporaryDirectory() as root:
        store = SnapshotStore(root)
        make_snaps(root, ["20260601", "20260608", "20260615", "20260622"])

        deleted = store.rotate(keep=2)
        assert deleted == ["20260601.snap", "20260608.snap"], f"got {deleted!r}"
        assert store.snapshots() == ["20260615.snap", "20260622.snap"]

        # Nothing to prune: a no-op rotation...
        assert store.rotate(keep=5) == []
        # ...must leave the store usable for the next scheduled run.
        deleted = store.rotate(keep=1)
        assert deleted == ["20260615.snap"], (
            f"rotation after a no-op run should work, got {deleted!r}")

    with tempfile.TemporaryDirectory() as root:
        store = SnapshotStore(root)
        make_snaps(root, ["20260701", "20260708"])

        # A bad retention value is rejected...
        try:
            store.rotate(keep=0)
            raise AssertionError("keep=0 must be rejected")
        except ValueError:
            pass
        # ...and must not wedge the store for the next run.
        deleted = store.rotate(keep=1)
        assert deleted == ["20260701.snap"], (
            f"rotation after a rejected call should work, got {deleted!r}")
        assert store.snapshots() == ["20260708.snap"]

    with tempfile.TemporaryDirectory() as root:
        # A lock left by a genuinely concurrent rotation is still honored.
        store = SnapshotStore(root)
        make_snaps(root, ["20260801", "20260808"])
        with open(os.path.join(root, ".rotate.lock"), "w") as f:
            f.write("4242")
        try:
            store.rotate(keep=1)
            raise AssertionError("a held lock must block rotation")
        except LockHeldError:
            pass
        os.unlink(os.path.join(root, ".rotate.lock"))
        assert store.rotate(keep=1) == ["20260801.snap"]

    print("all checks passed")


if __name__ == "__main__":
    main()
