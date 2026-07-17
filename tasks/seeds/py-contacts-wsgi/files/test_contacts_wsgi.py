"""Acceptance tests for contacts_api.create_app() -- WSGI JSON REST resource.

Boots the app over loopback HTTP (wsgiref on 127.0.0.1, ephemeral port) and
drives it with http.client. Stdlib only, no external network.

Run: python3 test_contacts_wsgi.py
"""
import json
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from wsgiref.simple_server import WSGIRequestHandler, make_server

from contacts_api import create_app


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):  # keep test output clean
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


def request(port, method, path, body=None):
    """Returns (status, lowercase-header dict, parsed JSON body or None)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {}
        if body is not None:
            if not isinstance(body, (str, bytes)):
                body = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        header_map = {k.lower(): v for k, v in resp.getheaders()}
        parsed = None
        if raw:
            ctype = header_map.get("content-type", "")
            parsed = json.loads(raw) if ctype.startswith("application/json") else raw
        return resp.status, header_map, parsed
    finally:
        conn.close()


def assert_json(headers, context):
    ctype = headers.get("content-type", "")
    assert ctype.startswith("application/json"), f"{context}: content-type {ctype!r}"


def test_create_and_fetch():
    with serve(create_app()) as port:
        status, headers, body = request(
            port, "POST", "/contacts",
            {"name": "Ada Lovelace", "email": "ada@example.com", "phone": "+44 20 7946 0501"},
        )
        assert status == 201, f"create should 201, got {status}: {body}"
        assert_json(headers, "create")
        assert isinstance(body["id"], int), body
        assert headers.get("location") == f"/contacts/{body['id']}", headers.get("location")
        assert set(body) == {"id", "name", "email", "phone"}, body
        assert body["name"] == "Ada Lovelace"
        assert body["email"] == "ada@example.com"
        assert body["phone"] == "+44 20 7946 0501"

        status, headers, fetched = request(port, "GET", f"/contacts/{body['id']}")
        assert status == 200, status
        assert_json(headers, "fetch")
        assert fetched == body, (fetched, body)

        # phone is optional and comes back as null when omitted
        status, _, minimal = request(
            port, "POST", "/contacts", {"name": "Grace Hopper", "email": "grace@example.com"}
        )
        assert status == 201, status
        assert minimal["phone"] is None, minimal

        # non-ASCII names survive the round trip
        status, _, unicode_body = request(
            port, "POST", "/contacts", {"name": "Zoë Åberg", "email": "zoe@example.com"}
        )
        assert status == 201, status
        _, _, unicode_fetched = request(port, "GET", f"/contacts/{unicode_body['id']}")
        assert unicode_fetched["name"] == "Zoë Åberg", unicode_fetched


def test_ids_increase_and_are_never_reused():
    with serve(create_app()) as port:
        _, _, first = request(port, "POST", "/contacts", {"name": "First", "email": "f@example.com"})
        _, _, second = request(port, "POST", "/contacts", {"name": "Second", "email": "s@example.com"})
        assert first["id"] == 1, first
        assert second["id"] > first["id"], (first, second)
        status, _, _ = request(port, "DELETE", f"/contacts/{second['id']}")
        assert status == 204, status
        _, _, third = request(port, "POST", "/contacts", {"name": "Third", "email": "t@example.com"})
        assert third["id"] > second["id"], "deleted ids must never be reused"


def test_create_validation():
    with serve(create_app()) as port:
        cases = [
            ({"email": "no-name@example.com"}, "name"),
            ({"name": "", "email": "x@example.com"}, "name"),
            ({"name": "   ", "email": "x@example.com"}, "name"),
            ({"name": 7, "email": "x@example.com"}, "name"),
            ({"name": "No Email"}, "email"),
            ({"name": "Bad Email", "email": "not-an-email"}, "email"),
            ({"name": "Bad Email", "email": 9}, "email"),
            ({"name": "Bad Phone", "email": "p@example.com", "phone": 5}, "phone"),
            ({"name": "X", "email": "x@example.com", "nickname": "ex"}, "nickname"),
            ({"id": 99, "name": "X", "email": "x@example.com"}, "id"),
        ]
        for payload, field in cases:
            status, headers, body = request(port, "POST", "/contacts", payload)
            assert status == 400, f"{payload!r} should 400, got {status}"
            assert_json(headers, f"validation {payload!r}")
            assert field in body.get("errors", {}), f"expected error for {field!r} in {body}"

        # every bad field is reported at once
        status, _, body = request(port, "POST", "/contacts", {"name": "", "email": "nope"})
        assert status == 400, status
        assert "name" in body["errors"] and "email" in body["errors"], body

        # malformed JSON and non-object payloads
        status, headers, body = request(port, "POST", "/contacts", "{not json")
        assert status == 400, status
        assert_json(headers, "malformed json")
        assert "error" in body, body
        status, _, body = request(port, "POST", "/contacts", [1, 2, 3])
        assert status == 400, status
        assert "error" in body, body

        # none of the rejected payloads was stored
        _, _, listing = request(port, "GET", "/contacts")
        assert listing["total"] == 0, listing


def test_list_pagination():
    with serve(create_app()) as port:
        for i in range(1, 26):
            status, _, _ = request(
                port, "POST", "/contacts",
                {"name": f"Contact {i:02d}", "email": f"c{i:02d}@example.com"},
            )
            assert status == 201, status

        status, headers, page = request(port, "GET", "/contacts")
        assert status == 200, status
        assert_json(headers, "list")
        assert set(page) == {"items", "total", "limit", "offset"}, page.keys()
        assert page["total"] == 25 and page["limit"] == 10 and page["offset"] == 0, page
        assert [c["name"] for c in page["items"]] == [f"Contact {i:02d}" for i in range(1, 11)]

        _, _, page = request(port, "GET", "/contacts?limit=7&offset=21")
        assert page["limit"] == 7 and page["offset"] == 21 and page["total"] == 25, page
        assert [c["name"] for c in page["items"]] == [f"Contact {i:02d}" for i in range(22, 26)]

        _, _, page = request(port, "GET", "/contacts?offset=100")
        assert page["items"] == [] and page["total"] == 25, page

        status, _, page = request(port, "GET", "/contacts?limit=50")
        assert status == 200 and page["limit"] == 50, page

        for bad in ("limit=0", "limit=51", "limit=ten", "limit=-3", "limit=2.5", "offset=-1", "offset=x"):
            status, headers, body = request(port, "GET", f"/contacts?{bad}")
            assert status == 400, f"?{bad} should 400, got {status}"
            assert_json(headers, f"bad paging {bad}")
            assert "error" in body or "errors" in body, body


def test_put_replaces_whole_contact():
    with serve(create_app()) as port:
        _, _, created = request(
            port, "POST", "/contacts",
            {"name": "Old Name", "email": "old@example.com", "phone": "555-0100"},
        )
        cid = created["id"]
        status, _, updated = request(
            port, "PUT", f"/contacts/{cid}", {"name": "New Name", "email": "new@example.com"}
        )
        assert status == 200, status
        assert updated == {"id": cid, "name": "New Name", "email": "new@example.com", "phone": None}, updated
        _, _, fetched = request(port, "GET", f"/contacts/{cid}")
        assert fetched == updated, fetched

        # PUT validates like POST and must not partially apply
        status, _, body = request(port, "PUT", f"/contacts/{cid}", {"name": "Half"})
        assert status == 400 and "email" in body["errors"], body
        _, _, fetched = request(port, "GET", f"/contacts/{cid}")
        assert fetched == updated, "failed PUT must leave the record untouched"

        status, _, body = request(
            port, "PUT", "/contacts/424242", {"name": "Ghost", "email": "g@example.com"}
        )
        assert status == 404 and "error" in body, (status, body)


def test_delete():
    with serve(create_app()) as port:
        _, _, created = request(port, "POST", "/contacts", {"name": "Temp", "email": "t@example.com"})
        cid = created["id"]
        status, _, body = request(port, "DELETE", f"/contacts/{cid}")
        assert status == 204, status
        assert body is None, f"204 must have an empty body, got {body!r}"
        status, _, _ = request(port, "GET", f"/contacts/{cid}")
        assert status == 404, status
        status, _, body = request(port, "DELETE", f"/contacts/{cid}")
        assert status == 404 and "error" in body, (status, body)


def test_405_with_allow_header():
    with serve(create_app()) as port:
        _, _, created = request(port, "POST", "/contacts", {"name": "Keep", "email": "k@example.com"})
        cid = created["id"]
        payload = {"name": "X", "email": "x@example.com"}
        cases = [
            ("PUT", "/contacts", payload, {"GET", "POST"}),
            ("DELETE", "/contacts", None, {"GET", "POST"}),
            ("PATCH", "/contacts", payload, {"GET", "POST"}),
            ("POST", f"/contacts/{cid}", payload, {"GET", "PUT", "DELETE"}),
            ("PATCH", f"/contacts/{cid}", payload, {"GET", "PUT", "DELETE"}),
        ]
        for method, path, body_payload, allowed in cases:
            status, headers, body = request(port, method, path, body_payload)
            assert status == 405, f"{method} {path} should 405, got {status}"
            allow = {m.strip() for m in headers.get("allow", "").split(",") if m.strip()}
            assert allow == allowed, f"{method} {path}: Allow={allow!r}, want {allowed}"
            assert_json(headers, f"405 {method} {path}")
            assert "error" in body, body
        # the 405s must not have touched the store
        _, _, listing = request(port, "GET", "/contacts")
        assert listing["total"] == 1, listing


def test_unknown_routes_404():
    with serve(create_app()) as port:
        for path in ("/", "/nope", "/contacts/abc", "/contacts/12/extra", "/contacts/", "/CONTACTS"):
            status, headers, body = request(port, "GET", path)
            assert status == 404, f"GET {path} should 404, got {status}"
            assert_json(headers, f"404 {path}")
            assert "error" in body, body
        status, _, body = request(port, "GET", "/contacts/999")
        assert status == 404 and "error" in body, (status, body)


def test_each_app_instance_has_its_own_store():
    with serve(create_app()) as port_a, serve(create_app()) as port_b:
        status, _, _ = request(port_a, "POST", "/contacts", {"name": "Only A", "email": "a@example.com"})
        assert status == 201, status
        _, _, listing_b = request(port_b, "GET", "/contacts")
        assert listing_b["total"] == 0, "create_app() instances must not share state"
        _, _, listing_a = request(port_a, "GET", "/contacts")
        assert listing_a["total"] == 1, listing_a


def main():
    tests = [
        test_create_and_fetch,
        test_ids_increase_and_are_never_reused,
        test_create_validation,
        test_list_pagination,
        test_put_replaces_whole_contact,
        test_delete,
        test_405_with_allow_header,
        test_unknown_routes_404,
        test_each_app_instance_has_its_own_store,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
