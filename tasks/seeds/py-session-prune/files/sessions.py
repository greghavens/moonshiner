"""In-memory session store for the web tier.

Sessions live under an opaque token with an absolute expiry time. A
periodic sweep prunes dead sessions; "log out everywhere" ends every
session a user owns. All time inputs are epoch seconds; callers may pass
`now` explicitly (the schedulers do) or let it default to the clock.
"""
import time


class SessionStore:
    def __init__(self, ttl=3600):
        self.ttl = ttl
        self._sessions = {}   # token -> {"user": str, "expires_at": float}
        self._by_user = {}    # user  -> [token, ...]

    def create(self, token, user, now=None):
        now = time.time() if now is None else now
        self._sessions[token] = {"user": user, "expires_at": now + self.ttl}
        self._by_user.setdefault(user, []).append(token)

    def is_active(self, token, now=None):
        now = time.time() if now is None else now
        sess = self._sessions.get(token)
        return bool(sess and sess["expires_at"] > now)

    def active_count(self, now=None):
        now = time.time() if now is None else now
        return sum(1 for s in self._sessions.values() if s["expires_at"] > now)

    def prune_expired(self, now=None):
        """Drop every expired session; returns how many were removed."""
        now = time.time() if now is None else now
        removed = 0
        for token in self._sessions:
            if self._sessions[token]["expires_at"] <= now:
                self._drop(token)
                removed += 1
        return removed

    def logout_user(self, user):
        """End every session belonging to *user*; returns the tokens ended."""
        ended = []
        tokens = self._by_user.get(user, [])
        for token in tokens:
            tokens.remove(token)
            self._sessions.pop(token, None)
            ended.append(token)
        return ended

    def _drop(self, token):
        sess = self._sessions.pop(token)
        owners = self._by_user.get(sess["user"], [])
        if token in owners:
            owners.remove(token)
