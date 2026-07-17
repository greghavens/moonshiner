"""Snapshot rotation for the nightly backup job.

A snapshot directory holds timestamped *.snap files. Rotation prunes the
oldest until only the retention count remains. A lockfile named
".rotate.lock" inside the directory serializes rotations: two jobs pruning
the same directory at once would delete each other's snapshots. The lock
is held only for the duration of a single rotate() call.
"""
import os


class LockHeldError(RuntimeError):
    """Another rotation currently owns this snapshot directory."""


class SnapshotStore:
    def __init__(self, root):
        self.root = root
        self._lock_path = os.path.join(root, ".rotate.lock")

    def _acquire(self):
        try:
            fd = os.open(self._lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockHeldError(f"rotation already running in {self.root}")
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)

    def _release(self):
        os.unlink(self._lock_path)

    def snapshots(self):
        """Snapshot filenames, oldest first (names embed a sortable stamp)."""
        return sorted(n for n in os.listdir(self.root) if n.endswith(".snap"))

    def rotate(self, keep):
        """Prune oldest snapshots until *keep* remain; returns names deleted."""
        self._acquire()
        if keep < 1:
            raise ValueError("keep must be >= 1")
        names = self.snapshots()
        if len(names) <= keep:
            return []
        doomed = names[: len(names) - keep]
        for name in doomed:
            os.unlink(os.path.join(self.root, name))
        self._release()
        return doomed
