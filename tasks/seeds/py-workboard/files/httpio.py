"""Request/Response objects and the WSGI glue.

Handlers never see raw WSGI: they take a Request and return a Response.
"""
import json
from http.client import responses as _STATUS_TEXT
from urllib.parse import parse_qsl


class Request:
    def __init__(self, method, path, query=None, headers=None, body=b""):
        self.method = method
        self.path = path
        self.query = dict(query or {})
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body

    def json(self):
        """Parsed JSON body, or None if the body is empty or malformed."""
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


class Response:
    def __init__(self, status, body, content_type):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.content_type = content_type
        self.headers = []

    def status_line(self):
        return "%d %s" % (self.status, _STATUS_TEXT.get(self.status, "Unknown"))


def json_response(data, status=200):
    payload = json.dumps(data, sort_keys=True)
    return Response(status, payload, "application/json; charset=utf-8")


def html_response(markup, status=200):
    return Response(status, markup, "text/html; charset=utf-8")


def bad_request(message):
    return json_response({"error": message}, status=400)


def not_found(message="not found"):
    return json_response({"error": message}, status=404)


def method_not_allowed(allowed):
    resp = json_response({"error": "method not allowed"}, status=405)
    resp.headers.append(("Allow", ", ".join(allowed)))
    return resp


def int_param(params, key):
    """Path parameters arrive as strings; ids must be integers."""
    try:
        return int(params[key])
    except (KeyError, ValueError):
        raise ValueError("expected an integer for %r in the path" % key)


def request_from_environ(environ):
    length = int(environ.get("CONTENT_LENGTH") or 0)
    body = environ["wsgi.input"].read(length) if length else b""
    headers = {
        key[5:].replace("_", "-"): value
        for key, value in environ.items()
        if key.startswith("HTTP_")
    }
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    return Request(
        method=environ.get("REQUEST_METHOD", "GET").upper(),
        path=environ.get("PATH_INFO") or "/",
        query=parse_qsl(environ.get("QUERY_STRING", "")),
        headers=headers,
        body=body,
    )


def to_wsgi(response, start_response):
    headers = [
        ("Content-Type", response.content_type),
        ("Content-Length", str(len(response.body))),
    ] + response.headers
    start_response(response.status_line(), headers)
    return [response.body]
