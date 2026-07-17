"""PII scrubbing stage of the support-ticket export.

The export driver enqueues raw ticket records with submit(); worker
threads scrub them with the pluggable redact function (rule sets differ
per data region) and collect the clean records. drain() blocks until
every record handed to submit() so far has been fully scrubbed and
returns a snapshot — the exporter calls it right before writing the
archive, so anything not scrubbed by then never makes it to the archive.
"""
import queue
import threading

_STOP = object()


class ScrubPipeline:
    def __init__(self, redact, workers=1):
        self._redact = redact
        self._queue = queue.Queue()
        self._results = []
        self._results_lock = threading.Lock()
        self._threads = []
        for _ in range(workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._threads.append(t)

    def submit(self, record):
        """Hand one raw record to the scrubbers."""
        self._queue.put(record)

    def _worker(self):
        while True:
            record = self._queue.get()
            if record is _STOP:
                self._queue.task_done()
                return
            self._queue.task_done()
            clean = self._redact(record)
            with self._results_lock:
                self._results.append(clean)

    def drain(self):
        """Wait for all submitted records, then snapshot the clean ones."""
        self._queue.join()
        with self._results_lock:
            return list(self._results)

    def close(self):
        """Stop the workers. The pipeline must be drained first."""
        for _ in self._threads:
            self._queue.put(_STOP)
        for t in self._threads:
            t.join(timeout=5)
