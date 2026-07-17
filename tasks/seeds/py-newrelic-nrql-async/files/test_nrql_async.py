"""Acceptance tests for the nrql_client package.

Runs a loopback fake NerdGraph endpoint (POST /graphql, API-Key auth,
async NRQL submit / nrqlQueryProgress polling / nrqlCancelQuery) and
drives nrql_client against it. No real New Relic, no real credentials,
no wall-clock sleeps: time comes from an injected clock and waiting goes
through an injected sleeper. The wire contract the fake enforces is
pinned in docs/contract.json. This file and everything under docs/ are
protected.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

API_KEY = CONTRACT["auth"]["fixture_api_key"]  # dummy; must never leak
ACCOUNT = CONTRACT["fixtures"]["account_id"]
QUERY_ID = CONTRACT["fixtures"]["query_id"]
TEMPLATE = CONTRACT["fixtures"]["template"]
PARAMS = CONTRACT["fixtures"]["params"]
RENDERED = CONTRACT["fixtures"]["rendered"]
RESULTS = CONTRACT["fixtures"]["results"]
METADATA = CONTRACT["fixtures"]["metadata"]


class FakeNerdGraph:
    """Loopback /graphql fake with request recording and scripted replies."""

    def __init__(self):
        self.requests = []  # {"method","path","headers","body","json"}
        self.script = []    # queued (status, doc) pairs

    def queue(self, status, doc):
        self.script.append((status, doc))

    def queue_submit_running(self):
        self.queue(200, {"data": {"actor": {"account": {"nrql": {
            "results": None,
            "metadata": None,
            "queryProgress": CONTRACT["fixtures"]["progress_running"],
        }}}}})

    def queue_poll_running(self):
        self.queue(200, {"data": {"actor": {"account": {"nrqlQueryProgress": {
            "results": None,
            "metadata": None,
            "queryProgress": CONTRACT["fixtures"]["progress_second"],
        }}}}})

    def queue_poll_done(self):
        self.queue(200, {"data": {"actor": {"account": {"nrqlQueryProgress": {
            "results": RESULTS,
            "metadata": METADATA,
            "queryProgress": CONTRACT["fixtures"]["progress_done"],
        }}}}})

    def next_step(self):
        if self.script:
            return self.script.pop(0)
        return (200, {"data": {"actor": {"account": {"nrql": {
            "results": [], "metadata": METADATA, "queryProgress": None}}}}})


def make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            try:
                doc = json.loads(body)
            except ValueError:
                doc = {}
            fake.requests.append({
                "method": "POST",
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
                "json": doc,
            })
            status, reply = fake.next_step()
            payload = json.dumps(reply).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    return Handler


class Harness:
    def __init__(self):
        self.fake = FakeNerdGraph()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d/graphql" % self.server.server_address[1]
        self.sleeps = []
        self.now = [CONTRACT["fixtures"]["clock_start"]]

    def client(self, module):
        return module.NerdGraphClient(
            endpoint_url=self.url,
            api_key=API_KEY,
            sleeper=self.sleeps.append,
            clock=lambda: self.now[0],
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run_test(name, fn, failures):
    h = Harness()
    try:
        fn(h)
        print("ok  - %s" % name)
    except AssertionError as exc:
        failures.append((name, exc))
        print("FAIL- %s: %s" % (name, exc))
    finally:
        h.close()


def test_render_nrql(h):
    import nrql_client as nc

    assert nc.render_nrql(TEMPLATE, PARAMS) == RENDERED, \
        "template rendering must produce the pinned NRQL"
    assert nc.render_nrql("SINCE {h} hours ago LIMIT {n}", {"h": 6, "n": 2000}) == \
        "SINCE 6 hours ago LIMIT 2000"
    assert nc.render_nrql("WHERE ok = {flag}", {"flag": True}) == "WHERE ok = true"
    assert nc.render_nrql("WHERE ok = {flag}", {"flag": False}) == "WHERE ok = false"
    assert nc.render_nrql("WHERE p = {p}", {"p": 99.9}) == "WHERE p = 99.9"

    for hostile in ["it's", "x\\y", "a\nb", CONTRACT["fixtures"]["injection_value"]]:
        try:
            nc.render_nrql("WHERE appName = {v}", {"v": hostile})
        except nc.UnsafeParameterError:
            pass
        else:
            raise AssertionError("hostile string %r must be rejected" % hostile)

    try:
        nc.render_nrql("WHERE p = {p}", {"p": float("nan")})
    except nc.UnsafeParameterError:
        pass
    else:
        raise AssertionError("NaN must be rejected")

    try:
        nc.render_nrql("WHERE p = {p}", {"p": {"nested": 1}})
    except nc.UnsafeParameterError:
        pass
    else:
        raise AssertionError("unsupported parameter types must be rejected")

    try:
        nc.render_nrql("WHERE a = {a}", {})
    except nc.UnsafeParameterError:
        pass
    else:
        raise AssertionError("a placeholder without a parameter must be rejected")

    try:
        nc.render_nrql("WHERE a = 1", {"a": 1})
    except nc.UnsafeParameterError:
        pass
    else:
        raise AssertionError("a parameter without a placeholder must be rejected")


def test_transport_and_fast_path(h):
    import nrql_client as nc

    h.fake.queue(200, {"data": {"actor": {"account": {"nrql": {
        "results": RESULTS, "metadata": METADATA, "queryProgress": None}}}}})
    client = h.client(nc)
    result = client.run(ACCOUNT, TEMPLATE, params=PARAMS, timeout=120)

    assert len(h.fake.requests) == 1, "a fast query needs exactly one request"
    req = h.fake.requests[0]
    assert req["path"] == "/graphql"
    assert req["headers"].get("api-key") == API_KEY, \
        "the user key travels in the API-Key header"
    assert "authorization" not in req["headers"], \
        "NerdGraph auth is API-Key, not a Bearer Authorization header"
    assert req["headers"].get("content-type", "").startswith("application/json")
    assert sorted(req["json"].keys()) == ["query", "variables"], \
        "the POST body is exactly {query, variables}"

    doc = req["json"]["query"]
    variables = req["json"]["variables"]
    assert variables == {
        "accountId": ACCOUNT,
        "query": RENDERED,
        "async": True,
        "timeout": 120,
    }, "submit variables must carry the rendered NRQL, async: true and the timeout"
    for decl in ["$accountId: Int!", "$query: Nrql!", "$timeout: Seconds", "$async: Boolean"]:
        assert decl in doc, "the document must declare %s" % decl
    assert RENDERED not in doc, "the NRQL string must not be spliced into the document"
    for field in ["results", "metadata", "queryProgress", "eventTypes", "timeWindow"]:
        assert field in doc, "the submit selection must include %s" % field

    assert isinstance(result, nc.QueryResult)
    assert result.results == RESULTS
    assert result.metadata == METADATA, "metadata must be preserved exactly"
    assert result.polls == 0
    assert result.query_id is None
    assert h.sleeps == [], "a fast query never sleeps"

    h.fake.queue(200, {"data": {"actor": {"account": {"nrql": {
        "results": [], "metadata": METADATA, "queryProgress": None}}}}})
    client.run(ACCOUNT, "SELECT count(*) FROM Transaction SINCE {h} hours ago", params={"h": 1})
    second = h.fake.requests[1]["json"]["variables"]
    assert "timeout" not in second, "the timeout variable is sent only when provided"
    assert second["async"] is True, "async is always true"
    assert h.fake.requests[1]["json"]["query"] == doc, \
        "one submit document is reused for every query"


def test_unsafe_interpolation_never_reaches_the_wire(h):
    import nrql_client as nc

    client = h.client(nc)
    try:
        client.run(ACCOUNT, "SELECT count(*) FROM Transaction WHERE appName = {app}",
                   params={"app": CONTRACT["fixtures"]["injection_value"]})
    except nc.UnsafeParameterError:
        pass
    else:
        raise AssertionError("an injection attempt must raise UnsafeParameterError")
    assert h.fake.requests == [], "rejected queries must never reach the wire"


def test_async_poll_workflow(h):
    import nrql_client as nc

    h.fake.queue_submit_running()
    h.fake.queue_poll_running()
    h.fake.queue_poll_done()
    client = h.client(nc)
    result = client.run(ACCOUNT, TEMPLATE, params=PARAMS)

    assert len(h.fake.requests) == 3, "submit plus two polls"
    assert h.sleeps == [5, 3], \
        "waits come from each response's retryAfter, via the injected sleeper"

    poll1, poll2 = h.fake.requests[1], h.fake.requests[2]
    assert poll1["body"] == poll2["body"], \
        "the documented poll is repeated verbatim - byte-identical bodies"
    pdoc = poll1["json"]["query"]
    assert 'nrqlQueryProgress(queryId: "%s")' % QUERY_ID in pdoc, \
        "polling uses nrqlQueryProgress with the returned queryId"
    assert "account(id: %d)" % ACCOUNT in pdoc, \
        "the poll must target the same account as the original query"
    assert "nrql(" not in pdoc, "polls must not resubmit the query"

    assert result.results == RESULTS
    assert result.metadata == METADATA, "metadata from the completed poll is preserved"
    assert result.metadata["timeWindow"]["begin"] == METADATA["timeWindow"]["begin"]
    assert result.polls == 2
    assert result.query_id == QUERY_ID


def test_retry_deadline_cancels_and_raises(h):
    import nrql_client as nc

    h.fake.queue_submit_running()
    h.fake.queue(200, {"data": {"nrqlCancelQuery": {
        "queryId": QUERY_ID, "requestStatus": "ACCEPTED", "rejectionReason": None}}})
    client = h.client(nc)
    deadline = CONTRACT["fixtures"]["progress_running"]["retryDeadline"]
    h.now[0] = deadline + 1.0

    try:
        client.run(ACCOUNT, TEMPLATE, params=PARAMS)
    except nc.QueryDeadlineError as exc:
        assert exc.query_id == QUERY_ID
    else:
        raise AssertionError("passing retryDeadline must raise QueryDeadlineError")

    assert len(h.fake.requests) == 2, "submit plus exactly one cancel"
    cancel = h.fake.requests[1]["json"]["query"]
    assert 'nrqlCancelQuery(queryId: "%s")' % QUERY_ID in cancel
    for field in ["requestStatus", "rejectionReason"]:
        assert field in cancel, "the cancel selection must include %s" % field


def test_cancel_api(h):
    import nrql_client as nc

    client = h.client(nc)
    h.fake.queue(200, {"data": {"nrqlCancelQuery": {
        "queryId": QUERY_ID, "requestStatus": "ACCEPTED", "rejectionReason": None}}})
    outcome = client.cancel(QUERY_ID)
    assert outcome["requestStatus"] == "ACCEPTED"

    h.fake.queue(200, {"data": {"nrqlCancelQuery": {
        "queryId": QUERY_ID, "requestStatus": "REJECTED",
        "rejectionReason": CONTRACT["fixtures"]["rejection_reason"]}}})
    try:
        client.cancel(QUERY_ID)
    except nc.CancelRejectedError as exc:
        assert exc.rejection_reason == CONTRACT["fixtures"]["rejection_reason"]
    else:
        raise AssertionError("a REJECTED cancellation must raise CancelRejectedError")


def test_graphql_and_http_errors(h):
    import nrql_client as nc

    client = h.client(nc)
    h.fake.queue(200, {"data": None, "errors": [CONTRACT["fixtures"]["graphql_error"]]})
    try:
        client.run(ACCOUNT, TEMPLATE, params=PARAMS)
    except nc.NerdGraphQueryError as exc:
        assert exc.messages == [CONTRACT["fixtures"]["graphql_error"]["message"]]
        assert exc.error_classes == ["INVALID_INPUT"], \
            "the errorClass extension must be surfaced"
        assert API_KEY not in str(exc)
    else:
        raise AssertionError("GraphQL errors with no data must raise NerdGraphQueryError")

    h.fake.queue(503, {"error": "service unavailable"})
    try:
        client.run(ACCOUNT, TEMPLATE, params=PARAMS)
    except nc.NerdGraphHttpError as exc:
        assert exc.status == 503
        assert API_KEY not in str(exc)
    else:
        raise AssertionError("non-2xx responses must raise NerdGraphHttpError")


def test_endpoints_and_hygiene(h):
    import nrql_client as nc

    assert nc.ENDPOINTS["US"] == CONTRACT["endpoints"]["US"]
    assert nc.ENDPOINTS["EU"] == CONTRACT["endpoints"]["EU"]

    h.fake.queue_submit_running()
    h.fake.queue_poll_done()
    client = h.client(nc)
    client.run(ACCOUNT, TEMPLATE, params=PARAMS)
    for req in h.fake.requests:
        assert API_KEY not in req["path"], "key leaked into the URL"
        assert API_KEY not in req["body"], "key leaked into the GraphQL body"


def test_provenance_fixtures(h):
    assert SOURCES["research"]["required"] is True
    assert len(SOURCES["research"]["official_sources"]) >= 2
    assert len(SOURCES["verified_facts"]) >= 4
    assert CONTRACT["auth"]["header"] == "API-Key"
    assert CONTRACT["submit"]["variable_types"]["query"] == "Nrql!"
    assert CONTRACT["submit"]["default_timeout_seconds"] == 5
    assert CONTRACT["submit"]["max_async_duration_minutes"] == 10
    assert CONTRACT["cancel"]["statuses"] == ["ACCEPTED", "REJECTED"]
    assert CONTRACT["query_progress_fields"] == [
        "queryId", "completed", "retryAfter", "retryDeadline", "resultExpiration"]


def main():
    failures = []
    tests = [
        ("render_nrql", test_render_nrql),
        ("transport_and_fast_path", test_transport_and_fast_path),
        ("unsafe_interpolation_never_reaches_the_wire",
         test_unsafe_interpolation_never_reaches_the_wire),
        ("async_poll_workflow", test_async_poll_workflow),
        ("retry_deadline_cancels_and_raises", test_retry_deadline_cancels_and_raises),
        ("cancel_api", test_cancel_api),
        ("graphql_and_http_errors", test_graphql_and_http_errors),
        ("endpoints_and_hygiene", test_endpoints_and_hygiene),
        ("provenance_fixtures", test_provenance_fixtures),
    ]
    for name, fn in tests:
        run_test(name, fn, failures)
    if failures:
        raise SystemExit("%d test(s) failed" % len(failures))
    print("all %d tests passed" % len(tests))


if __name__ == "__main__":
    main()
