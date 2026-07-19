"""Database configuration and a deterministic transaction test double."""

from contextlib import contextmanager


def connection_policy(settings) -> dict[str, object]:
    configured = settings.DATABASES["default"]
    return {
        "engine": configured["ENGINE"],
        "name": configured["NAME"],
        "persistent_seconds": configured["CONN_MAX_AGE"],
        "atomic_requests": configured["ATOMIC_REQUESTS"],
    }


class IndexDatabase:
    def __init__(self):
        self.rows: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def atomic(self):
        before = list(self.rows)
        try:
            yield
        except Exception:
            self.rows = before
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def replace(self, label: str) -> None:
        if label == "reject-this-label":
            raise ValueError("index label rejected")
        self.rows.append(label)

