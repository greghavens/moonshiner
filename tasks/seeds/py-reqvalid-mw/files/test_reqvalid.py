"""Acceptance tests for reqvalid.wrap(app, rules) -- WSGI request validation.

Boots wrapped apps over loopback HTTP (wsgiref on 127.0.0.1, ephemeral port)
and drives them with http.client. Stdlib only, no external network.

Run: python3 test_reqvalid.py
"""
import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from wsgiref.simple_server import WSGIRequestHandler, make_server

from reqvalid import wrap

RULES = {
    ("GET", "/search"): [
        {"name": "q", "in": "query", "type": "str", "required": True},
        {"name": "limit", "in": "query", "type": "int", "default": 10, "min": 1, "max": 100},
        {"name": "sort", "in": "query", "type": "str", "choices": ["asc", "desc"]},
        {"name": "debug", "in": "query", "type": "bool"},
        {"name": "X-Api-Key", "in": "header", "type": "str", "required": True},
    ],
    ("POST", "/orders"): [
        {"name": "sku", "in": "body", "type": "str", "required": True},
        {"name": "qty", "in": "body", "type": "int", "required": True, "min": 1, "max": 999},
        {"name": "gift", "in": "body", "type": "bool", "default": False},
        {"name": "X-Request-Id", "in": "header", "type": "str"},
    ],
}


def params_echo_app(environ, start_response):
    """Inner app reporting what the middleware attached (or null if nothing)."""
    payload = json.dumps({"params": environ.get("reqvalid.params")}).encode("utf-8")
    start_response("200 OK", [("Content-Type", "application/json"),
                              ("Content-Length", str(len(payload)))])
    return [payload]


def body_echo_app(environ, start_response):
    """Inner app that reads its own request body -- proves pass-through."""
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    raw = environ["wsgi.input"].read(length) if length > 0 else b""
    payload = json.dumps({
        "got": raw.decode("utf-8"),
        "params": environ.get("reqvalid.params"),
    }).encode("utf-8")
    start_response("418 I'm a teapot", [("Content-Type", "application/json"),
                                        ("X-Inner", "yes"),
                                        ("Content-Length", str(len(payload)))])
    return [payload]


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def serve(app):
    server = make_server("127.0.0.1", 0, app, handler_class=_QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def request(port, method, path, body=None, headers=None):
    """Returns (status, lowercase-header dict, parsed JSON body or None)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        send_headers = dict(headers or {})
        if body is not None:
            if not isinstance(body, (str, bytes)):
                body = json.dumps(body)
            send_headers.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=body, headers=send_headers)
        resp = conn.getresponse()
        raw = resp.read()
        header_map = {k.lower(): v for k, v in resp.getheaders()}
        parsed = json.loads(raw) if raw else None
        return resp.status, header_map, parsed
    finally:
        conn.close()


def problem_triples(body):
    return [(p["name"], p["in"], p["code"]) for p in body["invalid_params"]]


def test_valid_request_coerces_and_passes_through():
    with serve(wrap(params_echo_app, RULES)) as port:
        status, _, body = request(port, "GET", "/search?q=widgets&limit=25&debug=TRUE",
                                  headers={"X-Api-Key": "k123"})
        assert status == 200, (status, body)
        assert body["params"] == {"q": "widgets", "limit": 25, "debug": True,
                                  "X-Api-Key": "k123"}, body
        # sort was optional with no default: absent, not None

        # header lookup is case-insensitive
        status, _, body = request(port, "GET", "/search?q=x",
                                  headers={"x-api-key": "lower"})
        assert status == 200, (status, body)
        assert body["params"]["X-Api-Key"] == "lower", body


def test_defaults_injected_for_missing_optionals():
    with serve(wrap(params_echo_app, RULES)) as port:
        status, _, body = request(port, "GET", "/search?q=x",
                                  headers={"X-Api-Key": "k"})
        assert status == 200, (status, body)
        assert body["params"] == {"q": "x", "limit": 10, "X-Api-Key": "k"}, body


def test_all_problems_collected_and_sorted():
    with serve(wrap(params_echo_app, RULES)) as port:
        status, headers, body = request(port, "GET", "/search?limit=abc&sort=up&foo=1")
        assert status == 400, (status, body)
        ctype = headers.get("content-type", "")
        assert ctype.startswith("application/problem+json"), ctype
        assert body["type"] == "urn:problem-type:request-validation", body
        assert body["title"] == "Request validation failed", body
        assert body["status"] == 400, body
        assert problem_triples(body) == [
            ("X-Api-Key", "header", "required"),
            ("foo", "query", "unexpected"),
            ("limit", "query", "type"),
            ("q", "query", "required"),
            ("sort", "query", "choice"),
        ], problem_triples(body)


def test_range_and_duplicate_codes():
    with serve(wrap(params_echo_app, RULES)) as port:
        status, _, body = request(port, "GET", "/search?q=a&q=b&limit=1000",
                                  headers={"X-Api-Key": "k"})
        assert status == 400, (status, body)
        assert problem_triples(body) == [
            ("limit", "query", "range"),
            ("q", "query", "duplicate"),
        ], problem_triples(body)

        status, _, body = request(port, "GET", "/search?q=x&limit=0",
                                  headers={"X-Api-Key": "k"})
        assert status == 400, (status, body)
        assert problem_triples(body) == [("limit", "query", "range")], problem_triples(body)


def test_body_happy_path_with_defaults():
    with serve(wrap(params_echo_app, RULES)) as port:
        status, _, body = request(port, "POST", "/orders", {"sku": "WID-1", "qty": 3})
        assert status == 200, (status, body)
        assert body["params"] == {"sku": "WID-1", "qty": 3, "gift": False}, body

        status, _, body = request(port, "POST", "/orders",
                                  {"sku": "WID-2", "qty": 999, "gift": True},
                                  headers={"X-Request-Id": "r-77"})
        assert status == 200, (status, body)
        assert body["params"] == {"sku": "WID-2", "qty": 999, "gift": True,
                                  "X-Request-Id": "r-77"}, body


def test_body_typed_errors():
    with serve(wrap(params_echo_app, RULES)) as port:
        # JSON body values are NOT string-coerced
        status, _, body = request(port, "POST", "/orders", {"sku": "WID-1", "qty": "3"})
        assert status == 400, (status, body)
        assert problem_triples(body) == [("qty", "body", "type")], problem_triples(body)

        # a JSON boolean is not an int
        status, _, body = request(port, "POST", "/orders", {"sku": "WID-1", "qty": True})
        assert status == 400, (status, body)
        assert problem_triples(body) == [("qty", "body", "type")], problem_triples(body)

        status, _, body = request(port, "POST", "/orders",
                                  {"sku": 5, "qty": 0, "extra": 1})
        assert status == 400, (status, body)
        assert problem_triples(body) == [
            ("extra", "body", "unexpected"),
            ("qty", "body", "range"),
            ("sku", "body", "type"),
        ], problem_triples(body)

        status, _, body = request(port, "POST", "/orders", "{nope")
        assert status == 400, (status, body)
        assert problem_triples(body) == [("body", "body", "json")], problem_triples(body)

        status, _, body = request(port, "POST", "/orders", [1, 2])
        assert status == 400, (status, body)
        assert problem_triples(body) == [("body", "body", "json")], problem_triples(body)

        # empty body counts as {} -> required fields missing
        status, _, body = request(port, "POST", "/orders")
        assert status == 400, (status, body)
        assert problem_triples(body) == [
            ("qty", "body", "required"),
            ("sku", "body", "required"),
        ], problem_triples(body)


def test_bool_coercion_matrix():
    with serve(wrap(params_echo_app, RULES)) as port:
        for raw, expected in (("1", True), ("0", False), ("true", True),
                              ("False", False), ("TRUE", True)):
            status, _, body = request(port, "GET", f"/search?q=x&debug={raw}",
                                      headers={"X-Api-Key": "k"})
            assert status == 200, (raw, status, body)
            assert body["params"]["debug"] is expected, (raw, body)

        status, _, body = request(port, "GET", "/search?q=x&debug=yes",
                                  headers={"X-Api-Key": "k"})
        assert status == 400, (status, body)
        assert problem_triples(body) == [("debug", "query", "type")], problem_triples(body)


def test_undeclared_routes_pass_through_untouched():
    with serve(wrap(body_echo_app, RULES)) as port:
        # different method on a declared path counts as undeclared
        status, headers, body = request(port, "POST", "/search?limit=zzz&foo=1",
                                        body="hello world")
        assert status == 418, (status, body)
        assert headers.get("x-inner") == "yes", headers
        assert body["got"] == "hello world", body
        assert body["params"] is None, "undeclared route must not get reqvalid.params"

        # never-declared path, inner app reads its own body
        status, _, body = request(port, "POST", "/webhooks/github",
                                  body='{"raw": "payload"}')
        assert status == 418, (status, body)
        assert body["got"] == '{"raw": "payload"}', body
        assert body["params"] is None, body


def test_inner_app_never_runs_on_validation_failure():
    calls = []

    def counting_app(environ, start_response):
        calls.append(environ["PATH_INFO"])
        return params_echo_app(environ, start_response)

    with serve(wrap(counting_app, RULES)) as port:
        status, _, _ = request(port, "GET", "/search")
        assert status == 400, status
        assert calls == [], f"inner app ran on invalid request: {calls}"
        status, _, _ = request(port, "GET", "/search?q=ok", headers={"X-Api-Key": "k"})
        assert status == 200, status
        assert calls == ["/search"], calls


def main():
    tests = [
        test_valid_request_coerces_and_passes_through,
        test_defaults_injected_for_missing_optionals,
        test_all_problems_collected_and_sorted,
        test_range_and_duplicate_codes,
        test_body_happy_path_with_defaults,
        test_body_typed_errors,
        test_bool_coercion_matrix,
        test_undeclared_routes_pass_through_untouched,
        test_inner_app_never_runs_on_validation_failure,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
