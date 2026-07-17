"""Parallel export step of the docs publisher.

Renders every source document with the injected convert callable
(markdown -> HTML in production) on a small thread pool. The report the
publisher acts on must be complete: every input document ends up either
in converted (name -> rendered output) or in failed (name -> error
message). A document that lands in neither silently vanishes from the
published site with no trace in the publish log — that must never
happen.
"""
import threading
from concurrent.futures import ThreadPoolExecutor


class BatchReport:
    def __init__(self):
        self.converted = {}
        self.failed = {}

    def accounted(self):
        """Every document name the batch has a verdict for."""
        return set(self.converted) | set(self.failed)

    def summary(self):
        return f"{len(self.converted)} converted, {len(self.failed)} failed"


class Exporter:
    def __init__(self, convert, workers=4):
        self._convert = convert
        self._workers = workers

    def run(self, docs):
        """Convert *docs* (name -> source) in parallel; returns the report."""
        report = BatchReport()
        lock = threading.Lock()

        def work(name, source):
            html = self._convert(name, source)
            with lock:
                report.converted[name] = html

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for name, source in sorted(docs.items()):
                pool.submit(work, name, source)
        return report
