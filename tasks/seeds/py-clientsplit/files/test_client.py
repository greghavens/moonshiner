"""Tests for the Deskline client refactor.

The LEGACY BEHAVIOR block below runs against the shipped apiclient.py and
passes today; it must stay green -- those are the semantics every existing
call site depends on. The TARGET LAYOUT block imports the post-refactor
package (client/transport.py, client/auth.py, client/resources.py) and
re-pins the same behavior through the new seams; it fails until the split
exists.

Every HTTP scenario runs against a scripted local mock of Deskline bound to
127.0.0.1 on an ephemeral port. Responses are consumed in script order and
every request is recorded, so the assertions are exact. No real network.

Run: python3 test_client.py
"""
import http.server
import json
import socket
import threading


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _handle(self):
        api = self.server.api
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        decoded = json.loads(raw.decode("utf-8")) if raw else None
        with api.lock:
            api.requests.append({
                "method": self.command,
                "path": self.path,
                "api_key": self.headers.get("X-Api-Key"),
                "content_type": self.headers.get("Content-Type"),
                "json": decoded,
            })
            if api.script:
                status, body = api.script.pop(0)
            else:
                status, body = 599, {"error": {"code": "script_exhausted",
                                               "message": "unexpected extra request"}}
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle


class MockDeskline:
    """A scripted stand-in for the Deskline API on a local ephemeral port."""

    def __init__(self, *script):
        self.lock = threading.Lock()
        self.script = list(script)
        self.requests = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.api = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def unused_port_url():
    """A loopback URL nothing is listening on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


TICKET = {"id": "T-100", "subject": "Printer on 4 is haunted",
          "state": "open", "priority": "high"}


# ================================================================ LEGACY BEHAVIOR
# These pass against the shipped apiclient.py and MUST keep passing after
# the refactor: every existing call site constructs DesklineClient exactly
# like this.

from apiclient import ApiError, AuthError, DesklineClient


def test_legacy_get_ticket():
    api = MockDeskline((200, TICKET))
    try:
        c = DesklineClient(api.base_url, "key-legacy")
        got = c.get_ticket("T-100")
        assert got == TICKET, got
        req = api.requests[0]
        assert (req["method"], req["path"]) == ("GET", "/tickets/T-100"), req
        assert req["api_key"] == "key-legacy", req
    finally:
        api.close()


def test_legacy_missing_ticket_is_none():
    api = MockDeskline((404, {"error": {"code": "not_found", "message": "no such ticket"}}))
    try:
        c = DesklineClient(api.base_url, "key-legacy")
        assert c.get_ticket("T-404") is None
    finally:
        api.close()


def test_legacy_create_wire_shape_and_validation():
    api = MockDeskline((201, dict(TICKET, id="T-101")))
    try:
        c = DesklineClient(api.base_url, "key-legacy")
        created = c.create_ticket("VPN drops hourly", "since Tuesday", priority="high")
        assert created["id"] == "T-101", created
        req = api.requests[0]
        assert (req["method"], req["path"]) == ("POST", "/tickets"), req
        assert req["json"] == {"subject": "VPN drops hourly", "body": "since Tuesday",
                               "priority": "high"}, req["json"]
        assert (req["content_type"] or "").startswith("application/json"), req
        for bad in (lambda: c.create_ticket("", "x"),
                    lambda: c.create_ticket("   ", "x"),
                    lambda: c.create_ticket("ok", "x", priority="urgent")):
            try:
                bad()
            except ValueError:
                pass
            else:
                raise AssertionError("client-side validation must raise ValueError")
        assert len(api.requests) == 1, "invalid input must never reach the wire"
    finally:
        api.close()


def test_legacy_auth_error_is_typed():
    api = MockDeskline((401, {"error": {"code": "key_revoked", "message": "rotate it"}}))
    try:
        c = DesklineClient(api.base_url, "key-old")
        try:
            c.get_ticket("T-1")
        except AuthError as e:
            assert isinstance(e, ApiError)
            assert (e.code, e.status) == ("key_revoked", 401), (e.code, e.status)
        else:
            raise AssertionError("a 401 must raise AuthError")
    finally:
        api.close()


def test_legacy_error_envelope_is_decoded():
    api = MockDeskline((422, {"error": {"code": "state_conflict",
                                        "message": "ticket already closed"}}))
    try:
        c = DesklineClient(api.base_url, "key-legacy")
        try:
            c.close_ticket("T-9", "done")
        except AuthError:
            raise AssertionError("a 422 is not an auth problem")
        except ApiError as e:
            assert (e.code, e.status) == ("state_conflict", 422), (e.code, e.status)
            assert "ticket already closed" in e.message, e.message
        else:
            raise AssertionError("a 422 must raise ApiError")
    finally:
        api.close()


def test_legacy_close_and_list():
    api = MockDeskline(
        (200, dict(TICKET, state="closed")),
        (200, {"tickets": [TICKET, dict(TICKET, id="T-102")]}),
    )
    try:
        c = DesklineClient(api.base_url, "key-legacy")
        closed = c.close_ticket("T-100", "rebooted the printer")
        assert closed["state"] == "closed", closed
        assert api.requests[0]["path"] == "/tickets/T-100/close"
        assert api.requests[0]["json"] == {"resolution": "rebooted the printer"}
        tickets = c.list_open_tickets()
        assert [t["id"] for t in tickets] == ["T-100", "T-102"], tickets
        assert api.requests[1]["path"] == "/tickets?state=open"
    finally:
        api.close()


# ================================================================ TARGET LAYOUT
# Everything below imports the post-refactor package and fails until it
# exists. The old behavior must hold when re-pinned through the new seams.

class FakeTransport:
    """A scripted transport: records every call, answers from a queue.

    This is the whole point of the refactor -- resource code testable
    without any HTTP server at all.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def request(self, method, path, headers=None, json_body=None):
        self.calls.append((method, path, dict(headers or {}), json_body))
        return self.script.pop(0)


def test_transport_is_plain_http_no_auth_no_status_opinions():
    from client.transport import UrllibTransport
    api = MockDeskline(
        (200, {"pong": True}),
        (404, {"error": {"code": "not_found", "message": "nope"}}),
    )
    try:
        t = UrllibTransport(api.base_url)
        status, body = t.request("GET", "/ping")
        assert (status, body) == (200, {"pong": True}), (status, body)
        # a 404 is DATA to the transport, not an exception
        status, body = t.request("GET", "/tickets/T-404")
        assert status == 404, status
        assert body["error"]["code"] == "not_found", body
        # and it sends no credentials unless told to
        assert api.requests[0]["api_key"] is None, api.requests[0]
    finally:
        api.close()


def test_transport_posts_json_and_headers_verbatim():
    from client.transport import UrllibTransport
    api = MockDeskline((201, {"id": "T-1"}))
    try:
        t = UrllibTransport(api.base_url)
        status, body = t.request("POST", "/tickets",
                                 headers={"X-Api-Key": "key-t"},
                                 json_body={"subject": "s", "body": "b"})
        assert (status, body["id"]) == (201, "T-1")
        req = api.requests[0]
        assert req["api_key"] == "key-t", req
        assert req["json"] == {"subject": "s", "body": "b"}, req
        assert (req["content_type"] or "").startswith("application/json"), req
    finally:
        api.close()


def test_transport_socket_trouble_is_typed():
    from client.transport import TransportError, UrllibTransport
    t = UrllibTransport(unused_port_url(), timeout=2)
    try:
        t.request("GET", "/ping")
    except TransportError:
        pass
    else:
        raise AssertionError("connection trouble must raise TransportError")


def test_auth_headers_are_fresh_dicts():
    from client.auth import ApiKeyAuth
    auth = ApiKeyAuth("key-a")
    h = auth.headers()
    assert h == {"X-Api-Key": "key-a"}, h
    h["X-Api-Key"] = "clobbered"
    h["Extra"] = "junk"
    assert auth.headers() == {"X-Api-Key": "key-a"}, "headers() must not share state"


def test_resources_run_on_a_fake_transport_no_server_anywhere():
    from client.auth import ApiKeyAuth
    from client.resources import TicketsApi
    fake = FakeTransport((201, dict(TICKET, id="T-201")))
    tickets = TicketsApi(fake, ApiKeyAuth("key-f"))
    created = tickets.create("Badge reader beeps twice", "east door", priority="low")
    assert created["id"] == "T-201", created
    assert fake.calls == [(
        "POST", "/tickets", {"X-Api-Key": "key-f"},
        {"subject": "Badge reader beeps twice", "body": "east door", "priority": "low"},
    )], fake.calls


def test_resources_error_mapping_through_the_fake():
    from client.auth import ApiKeyAuth
    from client.resources import ApiError, AuthError, TicketsApi
    fake = FakeTransport(
        (401, {"error": {"code": "key_revoked", "message": "rotate it"}}),
        (422, {"error": {"code": "state_conflict", "message": "already closed"}}),
        (404, {"error": {"code": "not_found", "message": "gone"}}),
    )
    tickets = TicketsApi(fake, ApiKeyAuth("key-f"))
    try:
        tickets.get("T-1")
    except AuthError as e:
        assert isinstance(e, ApiError) and e.status == 401, e
    else:
        raise AssertionError("401 through the new stack must raise AuthError")
    try:
        tickets.close("T-1", "done")
    except ApiError as e:
        assert (e.code, e.status) == ("state_conflict", 422), (e.code, e.status)
    else:
        raise AssertionError("422 through the new stack must raise ApiError")
    assert tickets.get("T-404") is None, "404 on get stays None through the new stack"


def test_resources_validation_needs_no_transport_call():
    from client.auth import ApiKeyAuth
    from client.resources import TicketsApi
    fake = FakeTransport()  # empty script: any request would blow up
    tickets = TicketsApi(fake, ApiKeyAuth("key-f"))
    for bad in (lambda: tickets.create("", "x"),
                lambda: tickets.create("ok", "x", priority="urgent")):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("validation must raise ValueError")
    assert fake.calls == [], "invalid input must never reach the transport"


def test_full_stack_over_real_http():
    from client.auth import ApiKeyAuth
    from client.resources import TicketsApi
    from client.transport import UrllibTransport
    api = MockDeskline((200, {"tickets": [TICKET]}))
    try:
        tickets = TicketsApi(UrllibTransport(api.base_url), ApiKeyAuth("key-r"))
        got = tickets.list_open()
        assert [t["id"] for t in got] == ["T-100"], got
        req = api.requests[0]
        assert (req["method"], req["path"]) == ("GET", "/tickets?state=open"), req
        assert req["api_key"] == "key-r", req
    finally:
        api.close()


def test_facade_shares_the_exception_types_with_the_layers():
    import apiclient
    from client import resources
    assert apiclient.ApiError is resources.ApiError, (
        "except apiclient.ApiError in old call sites must catch errors "
        "raised by the new layers -- same class object, not a copy")
    assert apiclient.AuthError is resources.AuthError


def main():
    test_legacy_get_ticket()
    test_legacy_missing_ticket_is_none()
    test_legacy_create_wire_shape_and_validation()
    test_legacy_auth_error_is_typed()
    test_legacy_error_envelope_is_decoded()
    test_legacy_close_and_list()
    print("legacy behavior: green")

    test_transport_is_plain_http_no_auth_no_status_opinions()
    test_transport_posts_json_and_headers_verbatim()
    test_transport_socket_trouble_is_typed()
    test_auth_headers_are_fresh_dicts()
    test_resources_run_on_a_fake_transport_no_server_anywhere()
    test_resources_error_mapping_through_the_fake()
    test_resources_validation_needs_no_transport_call()
    test_full_stack_over_real_http()
    test_facade_shares_the_exception_types_with_the_layers()
    print("ok")


if __name__ == "__main__":
    main()
