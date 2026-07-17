"""In-process metrics registry for the request pipeline.

Request handlers run on worker threads and report timing samples through
record(); the exporter thread reads them back with samples()/summary().
Buckets are created lazily the first time a metric name is seen. Callers
can supply a bucket factory to choose the storage for new metrics (plain
list by default; high-volume metrics use bounded deques).
"""
import threading


class StatsRegistry:
    def __init__(self, bucket_factory=list):
        self._bucket_factory = bucket_factory
        self._buckets = {}
        self._lock = threading.Lock()

    def record(self, name, value):
        """Append one sample to the named metric, creating it on first use."""
        bucket = self._buckets.get(name)
        if bucket is None:
            bucket = self._bucket_factory()
            with self._lock:
                self._buckets[name] = bucket
        bucket.append(value)

    def samples(self, name):
        """Snapshot of all samples recorded for *name* (empty if unknown)."""
        with self._lock:
            bucket = self._buckets.get(name)
        return list(bucket) if bucket is not None else []

    def names(self):
        """All metric names seen so far, sorted."""
        with self._lock:
            return sorted(self._buckets)

    def summary(self, name):
        """Aggregate view the exporter ships: count / min / max / mean."""
        values = self.samples(name)
        if not values:
            return {"count": 0, "min": None, "max": None, "mean": None}
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "mean": sum(values) / len(values),
        }
