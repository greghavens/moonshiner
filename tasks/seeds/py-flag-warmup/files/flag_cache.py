"""Feature-flag cache with background warmup.

The web app constructs a FlagCache at boot with a fetch callable that
pulls the full flag table from the flag service. start() runs the warmup
on a background thread so boot isn't blocked; request handlers call
wait_ready() before their first flag lookup. wait_ready() returns True
once the warmup attempt has finished — whether it succeeded or failed —
and False only if the attempt itself is still running when the timeout
expires. After a failed warmup, error() holds the exception and lookups
fall back to the compiled-in defaults.
"""
import threading


class FlagCache:
    def __init__(self, fetch, defaults=None):
        self._fetch = fetch
        self._defaults = dict(defaults or {})
        self._flags = None
        self._error = None
        self._done = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        """Kick off the warmup thread; returns immediately."""
        t = threading.Thread(target=self._warm, daemon=True)
        t.start()
        return t

    def _warm(self):
        try:
            flags = self._fetch()
            with self._lock:
                self._flags = dict(flags)
            self._done.set()
        except Exception as exc:
            with self._lock:
                self._error = exc

    def wait_ready(self, timeout=None):
        """Block until the warmup attempt has finished; False on timeout."""
        return self._done.wait(timeout)

    def error(self):
        """The exception the warmup died with, or None."""
        with self._lock:
            return self._error

    def is_enabled(self, name):
        """Current value of a flag, falling back to the defaults table."""
        with self._lock:
            if self._flags is not None and name in self._flags:
                return bool(self._flags[name])
        return bool(self._defaults.get(name, False))
