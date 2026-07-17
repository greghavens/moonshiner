"""Acceptance tests for the REST Proxy v2 consumer lifecycle layer.

Runs loopback fake REST Proxy instances implementing the v2 consumer
subset pinned in docs/contract.json. No vendor network, no real
credentials. Also re-checks the existing restproxy.Transport behavior,
which must keep working unchanged.
"""

import http.server
import json
import threading

from restproxy import Transport, RestProxyError, V2, V2_JSON_EMBEDDED

import consumer_lifecycle

USERNAME = "audit.reader"
PASSWORD = "dummy-cred-91f4c2"  # dummy; must never reach an untrusted host
import base64 as _b64
EXPECTED_AUTH = "Basic " + _b64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()).decode()

CHECKS = [0]


def check(cond, label):
    CHECKS[0] += 1
    assert cond, f"FAILED: {label}"


class FakeProxy:
    """One loopback REST Proxy v2 instance recording every request."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = []          # dicts and ("handled", n) markers
        self.records_plan = []    # per GET records: ("batch", [...]) |
                                  # ("error", status, code, msg) |
                                  # ("redirect", url)
        self.create_fault = None  # (status, error_code, message)
        self.base_uri_override = None
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _record(self, kind, body):
                q = {}
                path = self.path
                if "?" in path:
                    path, _, qs = path.partition("?")
                    for pair in qs.split("&"):
                        k, _, v = pair.partition("=")
                        q[k] = v
                with proxy.lock:
                    proxy.events.append({
                        "kind": kind,
                        "method": self.command,
                        "path": path,
                        "query": q,
                        "auth": self.headers.get("Authorization"),
                        "content_type": self.headers.get("Content-Type"),
                        "accept": self.headers.get("Accept"),
                        "body": body,
                    })
                return path, q

            def _read_body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                return json.loads(raw.decode()) if raw else None

            def _reply(self, status, payload=None, headers=None):
                data = b""
                if payload is not None:
                    data = json.dumps(payload).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if data:
                    self.send_header("Content-Type", V2)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def do_POST(self):
                body = self._read_body()
                parts = self.path.split("/")
                if len(parts) == 3 and parts[1] == "consumers":
                    self._record("create", body)
                    if proxy.create_fault is not None:
                        status, code, msg = proxy.create_fault
                        self._reply(status,
                                    {"error_code": code, "message": msg})
                        return
                    group = parts[2]
                    name = body["name"]
                    base = proxy.base_uri_override or (
                        f"{proxy.url}/consumers/{group}/instances/{name}")
                    self._reply(200, {"instance_id": name, "base_uri": base})
                elif self.path.endswith("/subscription"):
                    self._record("subscribe", body)
                    self._reply(204)
                elif self.path.endswith("/offsets"):
                    self._record("commit", body)
                    self._reply(200, {})
                else:
                    self._record("other", body)
                    self._reply(404, {"error_code": 404,
                                      "message": "HTTP 404 Not Found"})

            def do_GET(self):
                path, _ = self._record("records", None)
                if not path.endswith("/records"):
                    self._reply(404, {"error_code": 404,
                                      "message": "HTTP 404 Not Found"})
                    return
                with proxy.lock:
                    step = (proxy.records_plan.pop(0)
                            if proxy.records_plan else ("batch", []))
                if step[0] == "batch":
                    self._reply(200, step[1])
                elif step[0] == "redirect":
                    self._reply(302, None, {"Location": step[1]})
                else:
                    _, status, code, msg = step
                    self._reply(status, {"error_code": code, "message": msg})

            def do_DELETE(self):
                self._record("delete", None)
                self._reply(204)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                      Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def mark(self, label):
        with self.lock:
            self.events.append(("handled", label))

    def of_kind(self, kind):
        with self.lock:
            return [e for e in self.events
                    if isinstance(e, dict) and e["kind"] == kind]

    def order(self):
        with self.lock:
            out = []
            for e in self.events:
                if isinstance(e, tuple):
                    out.append(e)
                else:
                    out.append(e["kind"])
            return out

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def batch(topic, pairs):
    return [{"topic": topic, "key": None, "value": {"n": o},
             "partition": p, "offset": o} for p, o in pairs]


def test_transport_still_works(evil):
    proxy = FakeProxy()
    try:
        t = Transport(proxy.url + "/", USERNAME, PASSWORD)
        check(t.origin == proxy.url, "transport origin from base url")
        resp = t.request("POST", "/consumers/g0",
                         body={"name": "i0", "format": "json"})
        check(resp.status == 200, "transport create status")
        check(resp.body["instance_id"] == "i0", "transport parses body")
        check(resp.body["base_uri"].startswith(proxy.url),
              "base_uri points at the serving instance")
        ev = proxy.of_kind("create")[0]
        check(ev["auth"] == EXPECTED_AUTH, "transport sends Basic auth")
        check(ev["content_type"] == V2,
              "transport Content-Type is application/vnd.kafka.v2+json")
        check(ev["accept"] == V2, "transport default Accept is v2")

        proxy.create_fault = (409, 40902,
                              "Consumer with specified name already exists.")
        try:
            t.request("POST", "/consumers/g0", body={"name": "i0"})
            check(False, "error envelope must raise RestProxyError")
        except RestProxyError as err:
            check(err.http_status == 409, "RestProxyError.http_status")
            check(err.error_code == 40902, "RestProxyError.error_code")
            check("already exists" in err.message, "RestProxyError.message")
        proxy.create_fault = None

        proxy.records_plan = [("redirect", evil.url + "/loot")]
        resp = t.request("GET", "/consumers/g0/instances/i0/records",
                         accept=V2_JSON_EMBEDDED)
        check(resp.status == 302, "transport surfaces redirects, no follow")
        check(len(evil.events) == 0, "redirect target never contacted")

        resp = t.request("DELETE", "/consumers/g0/instances/i0")
        check(resp.status == 204, "transport 204 status")
        check(resp.body is None, "transport 204 has no body")
    finally:
        proxy.close()


def run(proxy, handler=None, cancelled=None, **kw):
    t = Transport(proxy.url, USERNAME, PASSWORD)
    calls = [0]

    def default_handler(records):
        calls[0] += 1
        proxy.mark(calls[0])

    kw.setdefault("max_batches", 10)
    return consumer_lifecycle.run_session(
        t, "audit-loaders", "loader-1", ["orders.v1", "payments.v1"],
        handler or default_handler, cancelled=cancelled, **kw)


def test_happy_path():
    proxy = FakeProxy()
    try:
        b1 = batch("orders.v1", [(0, 4), (0, 5), (1, 3)])
        b2 = batch("payments.v1", [(2, 9)])
        proxy.records_plan = [("batch", b1), ("batch", b2), ("batch", [])]
        seen = []

        def handler(records):
            seen.append(records)
            proxy.mark(len(seen))

        result = run(proxy, handler, timeout_ms=1500, max_bytes=100000)
        check(result.outcome == "COMPLETED", "happy outcome COMPLETED")
        check(result.failure is None, "happy failure is None")
        check(result.deleted is True, "consumer instance deleted")
        check(seen == [b1, b2], "handler saw both batches in order")
        check(result.batches == [b1, b2], "result.batches preserved")

        create = proxy.of_kind("create")[0]
        check(create["path"] == "/consumers/audit-loaders",
              "create posted to /consumers/{group}")
        check(create["body"] == {"name": "loader-1", "format": "json",
                                 "auto.offset.reset": "earliest",
                                 "auto.commit.enable": "false"},
              "create body pins name/format/offset-reset/no-autocommit")
        check(create["content_type"] == V2, "create Content-Type v2")

        sub = proxy.of_kind("subscribe")[0]
        check(sub["path"] ==
              "/consumers/audit-loaders/instances/loader-1/subscription",
              "subscription path uses returned base_uri")
        check(sub["body"] == {"topics": ["orders.v1", "payments.v1"]},
              "subscription body lists topics")
        check(sub["content_type"] == V2, "subscription Content-Type v2")

        fetches = proxy.of_kind("records")
        check(len(fetches) == 3, "three fetches: two batches then empty")
        check(all(f["accept"] == V2_JSON_EMBEDDED for f in fetches),
              "records Accept is the json embedded-format media type")
        check(fetches[0]["query"] == {"timeout": "1500",
                                      "max_bytes": "100000"},
              "records query pins timeout and max_bytes")

        commits = proxy.of_kind("commit")
        check(len(commits) == 2, "one commit per processed batch")
        check(commits[0]["body"] == {"offsets": [
            {"topic": "orders.v1", "partition": 0, "offset": 5},
            {"topic": "orders.v1", "partition": 1, "offset": 3}]},
            "commit 1 holds max offset per partition, sorted")
        check(commits[1]["body"] == {"offsets": [
            {"topic": "payments.v1", "partition": 2, "offset": 9}]},
            "commit 2 body")
        check(commits[0]["content_type"] == V2, "commit Content-Type v2")
        check(result.committed == {("orders.v1", 0): 5, ("orders.v1", 1): 3,
                                   ("payments.v1", 2): 9},
              "result.committed maps (topic, partition) to offset")

        order = proxy.order()
        check(order.index(("handled", 1)) <
              order.index("commit"), "commit only after processing")
        deletes = proxy.of_kind("delete")
        check(len(deletes) == 1, "exactly one delete")
        check(deletes[0]["path"] ==
              "/consumers/audit-loaders/instances/loader-1",
              "delete targets the instance URI")
        check(order[-1] == "delete", "delete is the final request")
        check(all(e["auth"] == EXPECTED_AUTH for e in proxy.of_kind("create")
                  + proxy.of_kind("subscribe") + fetches + commits + deletes),
              "every request authenticated")
    finally:
        proxy.close()


def test_create_conflict():
    proxy = FakeProxy()
    try:
        proxy.create_fault = (409, 40902,
                              "Consumer with specified name already exists.")
        result = run(proxy)
        check(result.outcome == "PROXY_ERROR", "conflict outcome")
        check("40902" in result.failure, "conflict failure names 40902")
        check(result.deleted is False, "nothing to delete after failed create")
        check(len(proxy.of_kind("delete")) == 0, "no delete request sent")
        check(len(proxy.of_kind("subscribe")) == 0, "no subscribe attempted")
        check(result.batches == [], "no batches on failed create")
    finally:
        proxy.close()


def test_handler_failure_commits_nothing_new():
    proxy = FakeProxy()
    try:
        b1 = batch("orders.v1", [(0, 7)])
        b2 = batch("orders.v1", [(0, 8)])
        proxy.records_plan = [("batch", b1), ("batch", b2)]
        calls = [0]

        def handler(records):
            calls[0] += 1
            if calls[0] == 2:
                raise ValueError("boom at batch 2")
            proxy.mark(calls[0])

        result = run(proxy, handler)
        check(result.outcome == "HANDLER_ERROR", "handler error outcome")
        check("boom at batch 2" in result.failure,
              "failure carries the handler error")
        check(len(proxy.of_kind("commit")) == 1,
              "failed batch is never committed")
        check(result.committed == {("orders.v1", 0): 7},
              "only the processed batch is committed")
        check(len(proxy.of_kind("delete")) == 1,
              "instance deleted after handler failure")
        check(result.deleted is True, "deleted flag after handler failure")
    finally:
        proxy.close()


def test_cancellation():
    proxy = FakeProxy()
    try:
        proxy.records_plan = [("batch", batch("orders.v1", [(0, 1)])),
                              ("batch", batch("orders.v1", [(0, 2)]))]
        done = [0]

        def handler(records):
            done[0] += 1
            proxy.mark(done[0])

        result = run(proxy, handler, cancelled=lambda: done[0] >= 1)
        check(result.outcome == "CANCELLED", "cancelled outcome")
        check(len(result.batches) == 1, "stopped before the second fetch")
        check(len(proxy.of_kind("commit")) == 1,
              "work done before cancellation stays committed")
        check(len(proxy.of_kind("delete")) == 1,
              "instance deleted on cancellation")
        check(result.deleted is True, "deleted flag on cancellation")
    finally:
        proxy.close()


def test_fetch_error_keeps_prior_commits():
    proxy = FakeProxy()
    try:
        proxy.records_plan = [("batch", batch("orders.v1", [(1, 6)])),
                              ("error", 500, 50002, "Kafka error")]
        result = run(proxy)
        check(result.outcome == "PROXY_ERROR", "fetch error outcome")
        check("50002" in result.failure, "failure names the proxy error code")
        check(result.committed == {("orders.v1", 1): 6},
              "commits before the failure survive")
        check(len(proxy.of_kind("delete")) == 1, "delete after fetch error")
        check(result.deleted is True, "deleted flag after fetch error")
    finally:
        proxy.close()


def test_untrusted_base_uri(evil):
    proxy = FakeProxy()
    try:
        proxy.base_uri_override = (evil.url +
                                   "/consumers/audit-loaders/instances/loader-1")
        result = run(proxy)
        check(result.outcome == "PROXY_ERROR", "untrusted base_uri outcome")
        check(result.failure == "UNTRUSTED_BASE_URI",
              "failure is UNTRUSTED_BASE_URI")
        check(len(evil.events) == 0,
              "credentials never sent to the foreign origin")
        check(len(proxy.of_kind("subscribe")) == 0,
              "no subscribe through an untrusted base_uri")
        deletes = proxy.of_kind("delete")
        check(len(deletes) == 1 and deletes[0]["path"] ==
              "/consumers/audit-loaders/instances/loader-1",
              "cleanup goes through the configured origin")
    finally:
        proxy.close()


def test_redirect_blocked(evil):
    proxy = FakeProxy()
    try:
        proxy.records_plan = [("redirect", evil.url + "/records")]
        result = run(proxy)
        check(result.outcome == "PROXY_ERROR", "redirect outcome")
        check(result.failure == "REDIRECT_BLOCKED",
              "failure is REDIRECT_BLOCKED")
        check(len(evil.events) == 0, "redirect target never contacted")
        check(len(proxy.of_kind("delete")) == 1, "delete after redirect")
    finally:
        proxy.close()


def test_format_mismatch():
    proxy = FakeProxy()
    try:
        proxy.records_plan = [("error", 406, 40601,
                               "Consumer format does not match "
                               "the embedded format requested by the "
                               "Accept header.")]
        result = run(proxy)
        check(result.outcome == "PROXY_ERROR", "format mismatch outcome")
        check("40601" in result.failure, "failure names 40601")
        check(len(proxy.of_kind("delete")) == 1, "delete after 406")
    finally:
        proxy.close()


def main():
    evil = FakeProxy()  # the untrusted host: must stay silent throughout
    try:
        test_transport_still_works(evil)
        test_happy_path()
        test_create_conflict()
        test_handler_failure_commits_nothing_new()
        test_cancellation()
        test_fetch_error_keeps_prior_commits()
        test_untrusted_base_uri(evil)
        test_redirect_blocked(evil)
        test_format_mismatch()
        check(len(evil.events) == 0, "untrusted host saw zero requests total")
    finally:
        evil.close()
    print(f"OK - {CHECKS[0]} checks passed")


if __name__ == "__main__":
    main()
