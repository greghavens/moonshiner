"""Sequential batch runner for the nightly maintenance jobs.

Jobs are named callables registered in run order. The scheduler cron
script builds a JobRunner, registers everything, and calls run().
"""


class JobFailed(Exception):
    """Raised by run() when a job blows up; carries which one and why."""

    def __init__(self, job, cause):
        super().__init__("job %r failed: %r" % (job, cause))
        self.job = job
        self.cause = cause


class JobRunner:
    def __init__(self):
        self._jobs = []
        self._names = set()

    def add(self, name, fn):
        """Register a job; returns self so registration can be chained."""
        if not name or not name.strip():
            raise ValueError("job name required")
        if name in self._names:
            raise ValueError("duplicate job name: %r" % (name,))
        if not callable(fn):
            raise TypeError("job %r is not callable" % (name,))
        self._jobs.append((name, fn))
        self._names.add(name)
        return self

    def names(self):
        return [name for name, _ in self._jobs]

    def __len__(self):
        return len(self._jobs)

    def run(self):
        """Run every job in registration order.

        Returns [(name, result), ...]. The first exception aborts the
        whole batch: it is wrapped in JobFailed and later jobs never run.
        """
        results = []
        for name, fn in self._jobs:
            try:
                results.append((name, fn()))
            except Exception as exc:
                raise JobFailed(name, exc) from exc
        return results
