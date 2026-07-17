"""Maintenance-job dispatcher for the reporting database.

Nightly jobs (vacuum, reindex, stats refresh, ...) run on their own
threads, each borrowing a session from a small bounded pool — the
reporting replica only allows a handful of concurrent sessions, so the
pool is the hard ceiling. Failed jobs are recorded in errors and must
leave the pool as they found it: the same pool serves every batch that
runs after them.
"""
import queue
import threading


class PoolExhausted(RuntimeError):
    """No idle session became available within the acquire timeout."""


class Connection:
    """Stand-in for a replica session (production wraps psycopg here)."""

    def __init__(self, conn_id):
        self.conn_id = conn_id
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        return f"{self.conn_id}#{len(self.queries)}"


class ConnectionPool:
    def __init__(self, size):
        self.size = size
        self._idle = queue.Queue()
        for i in range(size):
            self._idle.put(Connection(f"conn-{i + 1}"))

    def acquire(self, timeout=1.0):
        try:
            return self._idle.get(timeout=timeout)
        except queue.Empty:
            raise PoolExhausted(f"no idle session (pool size {self.size})")

    def release(self, conn):
        self._idle.put(conn)

    def idle_count(self):
        return self._idle.qsize()


class Dispatcher:
    def __init__(self, pool):
        self._pool = pool
        self._lock = threading.Lock()
        self.results = {}
        self.errors = {}

    def _run_one(self, name, job):
        conn = self._pool.acquire()
        try:
            result = job(conn)
        except Exception as exc:
            with self._lock:
                self.errors[name] = str(exc)
            return
        self._pool.release(conn)
        with self._lock:
            self.results[name] = result

    def run_batch(self, jobs):
        """Run all (name, job) pairs concurrently; blocks until done."""
        threads = [
            threading.Thread(target=self._run_one, args=(name, job), daemon=True)
            for name, job in jobs
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
