"""URL routing.

Handlers register themselves by name with the @endpoint decorator; url
tables (each routes module exports one) map "METHOD /pattern" onto those
names. Keeping the two apart lets the url layout live in one flat list per
module while handlers stay plain functions.
"""
from httpio import method_not_allowed, not_found

_ENDPOINTS = {}


def endpoint(fn):
    """Register fn under its function name so url tables can refer to it."""
    _ENDPOINTS[fn.__name__] = fn
    return fn


def _match(pattern, path):
    """Return the dict of path params if pattern matches path, else None."""
    pattern_parts = pattern.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(pattern_parts) != len(path_parts):
        return None
    params = {}
    for expected, got in zip(pattern_parts, path_parts):
        if expected.startswith("<") and expected.endswith(">"):
            if not got:
                return None
            params[expected[1:-1]] = got
        elif expected != got:
            return None
    return params


class Router:
    def __init__(self):
        self._table = []  # (method, pattern, endpoint name)

    def add(self, method, pattern, name):
        self._table.append((method.upper(), pattern, name))

    def table(self):
        """Human-readable route dump, one line per registered url."""
        return ["%s %s -> %s" % row for row in self._table]

    def dispatch(self, ctx, request):
        allowed = []
        for method, pattern, name in self._table:
            params = _match(pattern, request.path)
            if params is None:
                continue
            if method != request.method:
                allowed.append(method)
                continue
            handler = _ENDPOINTS.get(name)
            if handler is None:
                return not_found()
            return handler(ctx, request, params)
        if allowed:
            return method_not_allowed(sorted(set(allowed)))
        return not_found()
