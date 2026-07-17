"""Acceptance tests for the splunk_hec package.

Runs a loopback fake Splunk HTTP Event Collector (event, raw, ack and
health endpoints with indexer acknowledgment) and drives splunk_hec
against it. No real Splunk, no real credentials, no wall-clock sleeps —
waiting is injected and recorded. The wire contract the fake enforces is
pinned in docs/contract.json. This file and everything under docs/ are
protected.
"""

import gzip
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

TOKEN = CONTRACT["auth"]["fixture_token"]      # dummy; must never leak
CHANNEL = CONTRACT["channel"]["fixture_channel"]


class FakeHec:
    """Loopback /services/collector fake with request recording.

    Responses are scripted per test as a queue of ("json", status, doc)
    or ("drop",) entries; "drop" closes the connection without sending
    any response bytes (an ambiguous transport failure).
    """

    def __init__(self):
        self.requests = []  # {"method","path","query","headers","body"}
        self.script = []

    def queue(self, status, doc):
        self.script.append(("json", status, doc))

    def queue_drop(self):
        self.script.append(("drop",))

    def next_step(self):
        if self.script:
            return self.script.pop(0)
        return ("json", 200, {"text": "Success", "code": 0, "ackId": 0})


def make_handler(hec):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _handle(self, method):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            path, _, query = self.path.partition("?")
            hec.requests.append({
                "method": method,
                "path": path,
                "query": query,
                # header names are case-insensitive; record them folded
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            })
            step = hec.next_step()
            if step[0] == "drop":
                # ambiguous failure: request consumed, no response sent
                self.close_connection = True
                try:
                    self.connection.close()
                except OSError:
                    pass
                return
            _, status, doc = step
            payload = json.dumps(doc).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            self._handle("POST")

        def do_GET(self):
            self._handle("GET")

        def log_message(self, *args):
            pass

    return Handler


class Fixture:
    def __init__(self):
        self.hec = FakeHec()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hec))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.sleeps = []

    def sender(self, **kwargs):
        from splunk_hec import HecSender
        kwargs.setdefault("token", TOKEN)
        kwargs.setdefault("channel", CHANNEL)
        kwargs.setdefault("sleeper", self.sleeps.append)
        return HecSender(self.base_url, **kwargs)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def with_fixture(fn):
    fx = Fixture()
    try:
        fn(fx)
    finally:
        fx.close()


def body_lines(req):
    raw = req["body"]
    if req["headers"].get("content-encoding") == "gzip":
        raw = gzip.decompress(raw)
    return [json.loads(line) for line in raw.decode("utf-8").split("\n") if line]


SAMPLE_EVENTS = [
    {
        "event": {"action": "belt_start", "line": 3},
        "time": 1770000000.25,
        "host": "press-07",
        "source": "conveyor",
        "sourcetype": "plant:belt",
        "fields": {"site": "osl", "bays": ["a", "b"]},
    },
    {"event": "belt jam cleared", "time": 1770000001.5},
    {"event": {"action": "belt_stop", "line": 3}},
]


def test_channel_is_validated(fx):
    from splunk_hec import HecSender
    try:
        HecSender(fx.base_url, token=TOKEN, channel="not-a-guid",
                  sleeper=fx.sleeps.append)
        raise AssertionError("non-GUID channel must be rejected")
    except ValueError:
        pass
    assert fx.hec.requests == [], "constructor must not talk to the network"
    fx.sender()  # the fixture GUID is accepted


def test_send_events_builds_documented_envelopes(fx):
    fx.hec.queue(200, {"text": "Success", "code": 0, "ackId": 7})
    ack_id = fx.sender().send_events(SAMPLE_EVENTS)
    assert ack_id == 7, "send_events must return the ackId from the response"
    assert len(fx.hec.requests) == 1
    req = fx.hec.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == "/services/collector/event", (
        "JSON envelopes go to the event endpoint, got %s" % req["path"])
    assert req["headers"].get("authorization") == "Splunk " + TOKEN
    assert req["headers"].get("x-splunk-request-channel") == CHANNEL, (
        "event sends must carry the request channel header")
    assert TOKEN not in req["path"] + req["query"], "token must never be in the URL"
    lines = body_lines(req)
    assert len(lines) == 3, "batch must serialize one envelope per event"
    assert lines[0] == {
        "event": {"action": "belt_start", "line": 3},
        "time": 1770000000.25,
        "host": "press-07",
        "source": "conveyor",
        "sourcetype": "plant:belt",
        "fields": {"site": "osl", "bays": ["a", "b"]},
    }
    assert lines[1] == {"event": "belt jam cleared", "time": 1770000001.5}
    assert lines[2] == {"event": {"action": "belt_stop", "line": 3}}


def test_envelope_validation_happens_before_any_request(fx):
    sender = fx.sender()
    bad = [
        [{"time": 1770000000}],                                # no event key
        [{"event": "x", "severity": "high"}],                  # unknown key
        [{"event": "x", "fields": {"nest": {"a": 1}}}],        # nested object
        [{"event": "x", "fields": {"mv": [{"a": 1}]}}],        # object in array
        [{"event": "x", "time": "yesterday"}],                 # non-numeric time
    ]
    for events in bad:
        try:
            sender.send_events(events)
            raise AssertionError("invalid envelope %r must be rejected" % events)
        except ValueError:
            pass
    assert fx.hec.requests == [], "validation failures must not reach the wire"
    fx.hec.queue(200, {"text": "Success", "code": 0, "ackId": 1})
    sender.send_events([{"event": "x", "fields": {"mv": ["a", 2, True]}}])
    assert len(fx.hec.requests) == 1, "flat multivalue fields are legal"


def test_send_raw_uses_channel_query_parameter(fx):
    fx.hec.queue(200, {"text": "Success", "code": 0, "ackId": 3})
    ack_id = fx.sender().send_raw("jam at bay 4\njam cleared\n")
    assert ack_id == 3
    req = fx.hec.requests[0]
    assert req["path"] == "/services/collector/raw"
    assert req["query"] == "channel=" + CHANNEL, (
        "raw sends must pass the channel as a query parameter")
    assert req["body"] == b"jam at bay 4\njam cleared\n", (
        "raw bodies must be passed through byte-exact")
    assert req["headers"].get("authorization") == "Splunk " + TOKEN


def test_gzip_option_compresses_event_bodies(fx):
    fx.hec.queue(200, {"text": "Success", "code": 0, "ackId": 9})
    ack_id = fx.sender(gzip=True).send_events(SAMPLE_EVENTS)
    assert ack_id == 9
    req = fx.hec.requests[0]
    assert req["headers"].get("content-encoding") == "gzip", (
        "gzip senders must declare Content-Encoding: gzip")
    decompressed = gzip.decompress(req["body"])
    assert len(decompressed) > len(req["body"]), "body must actually be compressed"
    assert body_lines(req)[0]["host"] == "press-07"
    assert len(body_lines(req)) == 3


def test_poll_acks_speaks_the_ack_protocol(fx):
    fx.hec.queue(200, {"acks": {"0": True, "1": False, "2": True}})
    statuses = fx.sender().poll_acks([0, 1, 2])
    assert statuses == {0: True, 1: False, 2: True}, (
        "ack statuses must be keyed by integer ackId, got %r" % statuses)
    req = fx.hec.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == "/services/collector/ack"
    assert req["headers"].get("x-splunk-request-channel") == CHANNEL, (
        "ack queries must use the same channel that sent the data")
    assert json.loads(req["body"].decode("utf-8")) == {"acks": [0, 1, 2]}


def test_wait_until_acked_never_requeries_a_confirmed_ack(fx):
    fx.hec.queue(200, {"acks": {"4": True, "5": False}})
    fx.hec.queue(200, {"acks": {"5": True}})
    fx.sender().wait_until_acked([4, 5], poll_interval=10.0, max_polls=5)
    assert len(fx.hec.requests) == 2
    first = json.loads(fx.hec.requests[0]["body"].decode("utf-8"))
    second = json.loads(fx.hec.requests[1]["body"].decode("utf-8"))
    assert first == {"acks": [4, 5]}
    assert second == {"acks": [5]}, (
        "HEC deletes a delivered ackId once it reports true; asking again "
        "always returns false, so acked ids must never be re-queried")
    assert fx.sleeps == [10.0], "exactly one injected sleep between the two polls"


def test_wait_until_acked_times_out_with_unacked_ids(fx):
    from splunk_hec import HecAckTimeout
    for _ in range(3):
        fx.hec.queue(200, {"acks": {"7": False}})
    try:
        fx.sender().wait_until_acked([7], poll_interval=5.0, max_polls=3)
        raise AssertionError("unacked ids after max_polls must raise HecAckTimeout")
    except HecAckTimeout as err:
        assert set(err.unacked) == {7}, err.unacked
    assert len(fx.hec.requests) == 3, "must poll exactly max_polls times"
    assert fx.sleeps == [5.0, 5.0], "sleeps happen between polls, not after the last"


def test_ambiguous_transport_failure_resends_the_same_batch(fx):
    fx.hec.queue_drop()
    fx.hec.queue(200, {"text": "Success", "code": 0, "ackId": 4})
    ack_id = fx.sender().send_events_with_retry(
        SAMPLE_EVENTS, max_attempts=3, backoff=0.5)
    assert ack_id == 4
    assert len(fx.hec.requests) == 2, "one drop, one successful resend"
    assert fx.hec.requests[0]["body"] == fx.hec.requests[1]["body"], (
        "the resent request must be byte-identical; indexer acknowledgment "
        "is what makes this at-least-once retry safe")
    assert fx.sleeps == [0.5], "one injected backoff sleep before the resend"


def test_busy_and_throttled_responses_are_retried(fx):
    fx.hec.queue(503, {"text": "Server is busy", "code": 9})
    fx.hec.queue(429, {"text": "HEC queue is at capacity and cannot process "
                               "any more requests", "code": 26})
    fx.hec.queue(200, {"text": "Success", "code": 0, "ackId": 11})
    ack_id = fx.sender().send_events_with_retry(
        [{"event": "hi"}], max_attempts=4, backoff=0.25)
    assert ack_id == 11
    assert len(fx.hec.requests) == 3
    assert fx.sleeps == [0.25, 0.25]


def test_client_errors_are_terminal_and_redacted(fx):
    from splunk_hec import HecServerError
    fx.hec.queue(400, {"text": "Invalid data format", "code": 6})
    try:
        fx.sender().send_events_with_retry(
            [{"event": "hi"}], max_attempts=4, backoff=0.25)
        raise AssertionError("HEC code 6 must raise HecServerError")
    except HecServerError as err:
        assert err.status == 400
        assert err.code == 6
        assert err.text == "Invalid data format"
        assert TOKEN not in str(err), "the token must never appear in errors"
    assert len(fx.hec.requests) == 1, "4xx (other than 429) must not be retried"
    assert fx.sleeps == []


def test_transport_errors_exhaust_into_oserror(fx):
    fx.hec.queue_drop()
    fx.hec.queue_drop()
    try:
        fx.sender().send_events_with_retry(
            [{"event": "hi"}], max_attempts=2, backoff=0.1)
        raise AssertionError("exhausted transport retries must raise")
    except OSError:
        pass
    assert len(fx.hec.requests) == 2, "exactly max_attempts requests"


def test_success_without_ackid_is_an_error(fx):
    from splunk_hec import HecServerError
    fx.hec.queue(200, {"text": "Success", "code": 0})
    try:
        fx.sender().send_events([{"event": "hi"}])
        raise AssertionError("a 200 without ackId means the token has no "
                             "indexer acknowledgment; the sender must refuse")
    except HecServerError as err:
        assert "ack" in str(err).lower()


def test_health_endpoint(fx):
    fx.hec.queue(200, {"text": "HEC is healthy", "code": 17})
    assert fx.sender().health() is True
    fx.hec.queue(503, {"text": "HEC is unhealthy, queues are full", "code": 18})
    assert fx.sender().health() is False
    assert [r["method"] for r in fx.hec.requests] == ["GET", "GET"]
    assert fx.hec.requests[0]["path"] == "/services/collector/health"


def main():
    tests = [
        test_channel_is_validated,
        test_send_events_builds_documented_envelopes,
        test_envelope_validation_happens_before_any_request,
        test_send_raw_uses_channel_query_parameter,
        test_gzip_option_compresses_event_bodies,
        test_poll_acks_speaks_the_ack_protocol,
        test_wait_until_acked_never_requeries_a_confirmed_ack,
        test_wait_until_acked_times_out_with_unacked_ids,
        test_ambiguous_transport_failure_resends_the_same_batch,
        test_busy_and_throttled_responses_are_retried,
        test_client_errors_are_terminal_and_redacted,
        test_transport_errors_exhaust_into_oserror,
        test_success_without_ackid_is_an_error,
        test_health_endpoint,
    ]
    assert SOURCES["research"]["required"] is True
    for test in tests:
        with_fixture(test)
        print("ok  %s" % test.__name__)
    print("%d tests passed" % len(tests))


if __name__ == "__main__":
    main()
