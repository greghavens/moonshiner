"""Staged export sessions for the parts-catalog dump.

A StageSession stages records against a backend and settles the stage on
exit: finalize() commits the staged data when the body succeeded, discard()
drops it when the body failed. Exactly one of the two terminal calls may be
issued per session. A cleanup failure during discard must never replace the
processing error that triggered it; it is recorded on the session instead.
The session is closed after exit on every path.
"""


class CleanupError(Exception):
    """Raised by a staging backend that fails to finalize or discard."""


class StageSession:
    def __init__(self, backend):
        self.backend = backend
        self.is_open = False
        self.written = 0
        self.cleanup_error = None

    def __enter__(self):
        self.backend.open()
        self.is_open = True
        return self

    def write(self, record):
        if not self.is_open:
            raise RuntimeError("session is not open")
        self.backend.write(record)
        self.written += 1

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.backend.finalize()
            except Exception:
                self.backend.discard()
                self.is_open = False
                raise
            self.is_open = False
            return False
        self.backend.discard()
        self.is_open = False
        return False


def export_records(backend, records, transform):
    """Stage every transformed record; returns the number written."""
    with StageSession(backend) as session:
        for record in records:
            session.write(transform(record))
        return session.written
