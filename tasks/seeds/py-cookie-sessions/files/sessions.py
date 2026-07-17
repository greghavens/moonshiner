"""In-memory session store for the admin gateway.

The gateway owns time: callers inject a clock (a zero-arg callable returning
seconds as a float) so expiry is testable and deterministic.
"""
import secrets


class SessionStore:
    def __init__(self, ttl_seconds, clock):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl = ttl_seconds
        self.clock = clock
        self._sessions = {}

    def create(self, user_id, data=None):
        """Open a session for user_id, returning the opaque session id."""
        session_id = secrets.token_hex(16)
        now = self.clock()
        self._sessions[session_id] = {
            "user_id": user_id,
            "data": dict(data or {}),
            "created_at": now,
            "expires_at": now + self.ttl,
        }
        return session_id

    def get(self, session_id):
        """Return {'user_id', 'data'} for a live session, else None."""
        record = self._sessions.get(session_id)
        if record is None:
            return None
        if self.clock() >= record["expires_at"]:
            del self._sessions[session_id]
            return None
        return {"user_id": record["user_id"], "data": record["data"]}

    def destroy(self, session_id):
        """Drop a session. Returns True if it existed (live or not)."""
        return self._sessions.pop(session_id, None) is not None
