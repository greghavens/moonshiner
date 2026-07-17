"""Fan-out job relay: a fixed crew of worker threads drains a job queue.

One STOP marker travels the input queue: each worker that receives it puts
it back for the next worker and reports itself done. run() returns exactly
one outcome per job — ("ok", job_id, result) or ("error", job_id, message)
— once every worker has reported done. A job failure is an outcome, never a
lost job and never a wedged pool; workers keep draining after a bad job.
"""

import queue
import threading

_STOP = object()
_DONE = object()


class RelayPool:
    def __init__(self, handler, workers=2):
        if workers < 1:
            raise ValueError("workers must be >= 1")
        self.handler = handler
        self.workers = workers
        self.done_signals = 0
        self._inq = queue.Queue()
        self._outq = queue.Queue()
        self._threads = []

    def _worker(self):
        while True:
            item = self._inq.get()
            if item is _STOP:
                self._inq.put(_STOP)
                self._outq.put(_DONE)
                return
            job_id, payload = item
            result = self.handler(payload)
            self._outq.put(("ok", job_id, result))

    def run(self, jobs):
        for index in range(self.workers):
            thread = threading.Thread(
                target=self._worker, name=f"relay-{index}", daemon=True
            )
            self._threads.append(thread)
            thread.start()
        for job in jobs:
            self._inq.put(job)
        self._inq.put(_STOP)
        outcomes = []
        while self.done_signals < self.workers:
            message = self._outq.get()
            if message is _DONE:
                self.done_signals += 1
            else:
                outcomes.append(message)
        for thread in self._threads:
            thread.join(timeout=5)
        return outcomes

    def alive_workers(self):
        return [t.name for t in self._threads if t.is_alive()]

    def leftover_messages(self):
        return self._outq.qsize()
