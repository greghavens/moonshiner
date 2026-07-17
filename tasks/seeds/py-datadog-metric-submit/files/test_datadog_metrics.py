"""Acceptance tests for the ddmetrics package.

Runs a loopback fake Datadog intake (Metrics API v2 subset: POST
/api/v2/series) and drives ddmetrics against it. No real Datadog, no real
credentials, no wall-clock sleeps — waiting is injected and recorded. The
wire contract the fake enforces is pinned in docs/contract.json. This file
and everything under docs/ are protected.
"""

import gzip
import json
import os
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

API_KEY = CONTRACT["auth"]["fixture_api_key"]  # dummy; must never leak
SERIES_PATH = "/api/v2/series"
NOW = 1770000000  # fixed injected epoch for every test


def compact(obj):
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


class FakeIntake:
    """Loopback /api/v2/series endpoint with request recording."""

    def __init__(self):
        self.requests = []   # {"method","path","query","headers","body"}
        self.responses = []  # queued (status, body_dict, headers) tuples

    def queue(self, status, body, headers=None):
        self.responses.append((status, body, headers or {}))

    def next_response(self):
        if self.responses:
            return self.responses.pop(0)
        return (202, {"errors": []}, {})


def make_handler(intake):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            path, _, query = self.path.partition("?")
            intake.requests.append({
                "method": "POST",
                "path": path,
                "query": query,
                # HTTP header names are case-insensitive; record them folded
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            })
            status, doc, extra = intake.next_response()
            payload = json.dumps(doc).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    return Handler


def start_server(intake):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(intake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def decode_body(req):
    body = req["body"]
    enc = req["headers"].get("content-encoding")
    if enc == "deflate":
        body = zlib.decompress(body)
    elif enc == "gzip":
        body = gzip.decompress(body)
    return json.loads(body.decode("utf-8"))


def make_client(base_url, **kw):
    from ddmetrics.client import MetricsClient
    sleeps = []
    kw.setdefault("compression", None)
    client = MetricsClient(
        API_KEY,
        base_url=base_url,
        now=lambda: NOW,
        sleep=sleeps.append,
        max_retries=kw.pop("max_retries", 2),
        **kw,
    )
    return client, sleeps


def gauge(name, value=1.0, tag_pad=None):
    from ddmetrics import series as s
    tags = ["pad:" + tag_pad] if tag_pad is not None else ["env:staging"]
    return s.make_series(name, s.GAUGE, [(NOW, value)], tags=tags)


def test_site_resolution():
    from ddmetrics.sites import resolve_api_base, KNOWN_SITES
    expect = CONTRACT["sites"]["api_base_by_site"]
    for site, base in expect.items():
        assert resolve_api_base(site) == base, (site, resolve_api_base(site))
        assert site in KNOWN_SITES, site
    for bogus in ("datadog.com", "api.datadoghq.com", "https://datadoghq.com", ""):
        try:
            resolve_api_base(bogus)
        except ValueError:
            pass
        else:
            raise AssertionError("unknown site accepted: %r" % bogus)


def test_series_schema():
    from ddmetrics import series as s
    # documented v2 integer type enum
    assert s.UNSPECIFIED == 0
    assert s.COUNT == 1
    assert s.RATE == 2
    assert s.GAUGE == 3

    doc = s.make_series(
        "checkout.latency",
        s.GAUGE,
        [(NOW - 10, 0.25), {"timestamp": NOW, "value": 0.5}],
        tags=["env:staging", "service:checkout"],
        resources=[{"name": "web-01", "type": "host"}],
        unit="second",
        source_type_name="ddmetrics-tests",
    )
    assert doc["metric"] == "checkout.latency"
    assert doc["type"] == 3
    assert doc["points"] == [
        {"timestamp": NOW - 10, "value": 0.25},
        {"timestamp": NOW, "value": 0.5},
    ]
    assert isinstance(doc["points"][0]["timestamp"], int)
    assert isinstance(doc["points"][0]["value"], float)
    assert doc["tags"] == ["env:staging", "service:checkout"]
    assert doc["resources"] == [{"name": "web-01", "type": "host"}]
    assert doc["unit"] == "second"
    assert doc["source_type_name"] == "ddmetrics-tests"

    # optional fields must be absent, not null
    bare = s.make_series("checkout.count", s.COUNT, [(NOW, 2.0)], interval=10)
    assert bare["interval"] == 10
    for absent in ("tags", "resources", "unit", "source_type_name"):
        assert absent not in bare, absent

    def rejects(fn):
        try:
            fn()
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    rejects(lambda: s.make_series("", s.GAUGE, [(NOW, 1.0)]))
    rejects(lambda: s.make_series("m", "gauge", [(NOW, 1.0)]))  # v2 types are ints
    rejects(lambda: s.make_series("m", 7, [(NOW, 1.0)]))
    rejects(lambda: s.make_series("m", s.GAUGE, []))
    # rate/count require the corresponding interval (docs)
    rejects(lambda: s.make_series("m", s.COUNT, [(NOW, 1.0)]))
    rejects(lambda: s.make_series("m", s.RATE, [(NOW, 1.0)]))
    # resources need both name and type
    rejects(lambda: s.make_series("m", s.GAUGE, [(NOW, 1.0)],
                                  resources=[{"name": "web-01"}]))


def test_batch_limits_and_split():
    from ddmetrics import batch
    from ddmetrics import series as s
    # documented payload limits, pinned
    assert batch.MAX_PAYLOAD_BYTES == CONTRACT["limits"]["max_payload_bytes"]
    assert batch.MAX_DECOMPRESSED_BYTES == CONTRACT["limits"]["max_decompressed_bytes"]
    assert batch.MAX_PAYLOAD_BYTES == 512000
    assert batch.MAX_DECOMPRESSED_BYTES == 5242880

    items = [s.make_series("m%d" % i, s.GAUGE, [(NOW, float(i))]) for i in range(5)]
    size2 = len(compact({"series": items[:2]}))
    batches = batch.split_series(items, max_bytes=size2)
    assert [len(b["series"]) for b in batches] == [2, 2, 1], batches
    flat = [d["metric"] for b in batches for d in b["series"]]
    assert flat == ["m0", "m1", "m2", "m3", "m4"]  # order preserved
    for b in batches:
        assert len(compact(b)) <= size2

    one = len(compact({"series": items[:1]}))
    try:
        batch.split_series(items[:1], max_bytes=one - 1)
    except ValueError:
        pass
    else:
        raise AssertionError("oversize single series must be rejected")


def test_wire_contract():
    intake = FakeIntake()
    server, base = start_server(intake)
    try:
        client, _ = make_client(base)
        report = client.submit([gauge("checkout.latency", 0.25)])
    finally:
        server.shutdown()
    assert len(intake.requests) == 1
    req = intake.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == SERIES_PATH
    assert req["query"] == ""  # the API key travels as a header, never a query param
    assert req["headers"].get("dd-api-key") == API_KEY
    assert "dd-application-key" not in req["headers"]  # submit needs API key only
    assert req["headers"].get("content-type") == "application/json"
    assert req["headers"].get("accept") == "application/json"
    assert "content-encoding" not in req["headers"]
    body = decode_body(req)
    assert set(body.keys()) == {"series"}
    assert body["series"][0]["metric"] == "checkout.latency"
    assert body["series"][0]["points"] == [{"timestamp": NOW, "value": 0.25}]
    assert report["ok"] is True
    assert report["submitted"] == 1
    assert report["accepted"] == 1
    assert report["batches"] == [
        {"status": 202, "series_count": 1, "errors": [], "attempts": 1}
    ]


def test_client_splits_at_documented_limit():
    intake = FakeIntake()
    server, base = start_server(intake)
    try:
        client, _ = make_client(base)
        items = [gauge("bulk.%d" % i, 1.0, tag_pad="x" * 119000) for i in range(5)]
        report = client.submit(items)
    finally:
        server.shutdown()
    assert len(intake.requests) == 2, len(intake.requests)
    for req in intake.requests:
        assert len(req["body"]) <= 512000
    first, second = (decode_body(r) for r in intake.requests)
    assert [d["metric"] for d in first["series"]] == ["bulk.0", "bulk.1", "bulk.2", "bulk.3"]
    assert [d["metric"] for d in second["series"]] == ["bulk.4"]
    assert report["accepted"] == 5
    assert [b["series_count"] for b in report["batches"]] == [4, 1]


def test_deflate_compression():
    intake = FakeIntake()
    server, base = start_server(intake)
    try:
        client, _ = make_client(base, compression="deflate")
        client.submit([gauge("compressed.metric", 2.5)])
    finally:
        server.shutdown()
    req = intake.requests[0]
    assert req["headers"].get("content-encoding") == "deflate"
    raw = zlib.decompress(req["body"])
    assert len(req["body"]) < len(raw)
    doc = json.loads(raw.decode("utf-8"))
    assert doc["series"][0]["metric"] == "compressed.metric"


def test_gzip_compression_and_unsupported_encoding():
    intake = FakeIntake()
    server, base = start_server(intake)
    try:
        client, _ = make_client(base, compression="gzip")
        client.submit([gauge("gz.metric")])
    finally:
        server.shutdown()
    req = intake.requests[0]
    assert req["headers"].get("content-encoding") == "gzip"
    doc = json.loads(gzip.decompress(req["body"]).decode("utf-8"))
    assert doc["series"][0]["metric"] == "gz.metric"

    from ddmetrics.client import MetricsClient
    for bad in ("zstd1", "br", "snappy"):
        try:
            MetricsClient(API_KEY, base_url="http://127.0.0.1:1",
                          compression=bad, now=lambda: NOW, sleep=lambda s: None)
        except ValueError as exc:
            assert bad in str(exc)
        else:
            raise AssertionError("unsupported compression accepted: %r" % bad)


def test_compressed_batches_split_by_decompressed_limit():
    intake = FakeIntake()
    server, base = start_server(intake)
    try:
        client, _ = make_client(base, compression="deflate")
        items = [gauge("big.%d" % i, 1.0, tag_pad="y" * 1000000) for i in range(6)]
        report = client.submit(items)
    finally:
        server.shutdown()
    assert len(intake.requests) == 2, len(intake.requests)
    sizes = []
    for req in intake.requests:
        assert req["headers"].get("content-encoding") == "deflate"
        assert len(req["body"]) <= 512000  # wire payload obeys the 500KB cap
        raw = zlib.decompress(req["body"])
        assert len(raw) < 5242880  # decompressed size stays under the 5MB cap
        sizes.append(len(json.loads(raw.decode("utf-8"))["series"]))
    assert sizes == [5, 1], sizes
    assert report["accepted"] == 6


def test_point_freshness_window():
    intake = FakeIntake()
    server, base = start_server(intake)
    from ddmetrics import series as s
    try:
        client, _ = make_client(base)
        # documented window: at most 1h old, at most 10min into the future
        ok = [
            s.make_series("edge.old", s.GAUGE, [(NOW - 3600, 1.0)]),
            s.make_series("edge.future", s.GAUGE, [(NOW + 600, 1.0)]),
        ]
        report = client.submit(ok)
        assert report["ok"] is True

        for name, ts in (("stale.metric", NOW - 3601), ("future.metric", NOW + 601)):
            bad = s.make_series(name, s.GAUGE, [(ts, 1.0)])
            before = len(intake.requests)
            try:
                client.submit([bad])
            except ValueError as exc:
                assert name in str(exc)
            else:
                raise AssertionError("out-of-window point accepted: %s" % name)
            assert len(intake.requests) == before  # rejected before any POST
    finally:
        server.shutdown()


def test_partial_batch_failure_and_intake_errors():
    intake = FakeIntake()
    server, base = start_server(intake)
    intake.queue(202, {"errors": []})
    intake.queue(400, {"errors": ["Invalid metric type"]})
    intake.queue(202, {"errors": ["deprecation warning: interval ignored"]})
    try:
        client, _ = make_client(base)
        items = [gauge("part.%d" % i, 1.0, tag_pad="z" * 119000) for i in range(9)]
        report = client.submit(items)
    finally:
        server.shutdown()
    assert len(intake.requests) == 3  # a failed payload must not stop the rest
    assert [b["status"] for b in report["batches"]] == [202, 400, 202]
    assert [b["series_count"] for b in report["batches"]] == [4, 4, 1]
    assert report["batches"][0]["errors"] == []
    assert report["batches"][1]["errors"] == ["Invalid metric type"]
    # a 202 whose body still carries errors is not a clean accept
    assert report["batches"][2]["errors"] == ["deprecation warning: interval ignored"]
    assert report["submitted"] == 9
    assert report["accepted"] == 4
    assert report["ok"] is False


def test_rate_limit_retry_and_exhaustion():
    intake = FakeIntake()
    server, base = start_server(intake)
    intake.queue(429, {"errors": ["rate limited"]}, {"Retry-After": "7"})
    try:
        client, sleeps = make_client(base)
        report = client.submit([gauge("retry.metric")])
    finally:
        server.shutdown()
    assert len(intake.requests) == 2
    assert sleeps == [7.0], sleeps
    assert report["batches"][0]["status"] == 202
    assert report["batches"][0]["attempts"] == 2
    assert report["ok"] is True

    intake = FakeIntake()
    server, base = start_server(intake)
    for _ in range(3):
        intake.queue(429, {"errors": ["rate limited"]}, {"Retry-After": "3"})
    try:
        client, sleeps = make_client(base, max_retries=2)
        report = client.submit([gauge("retry.metric")])
    finally:
        server.shutdown()
    assert len(intake.requests) == 3  # 1 try + max_retries retries
    assert sleeps == [3.0, 3.0], sleeps
    assert report["batches"][0]["status"] == 429
    assert report["batches"][0]["attempts"] == 3
    assert report["batches"][0]["errors"] == ["rate limited"]
    assert report["ok"] is False
    assert report["accepted"] == 0


def test_api_key_never_leaks():
    intake = FakeIntake()
    server, base = start_server(intake)
    intake.queue(403, {"errors": ["Forbidden"]})
    try:
        client, _ = make_client(base)
        report = client.submit([gauge("secret.metric")])
    finally:
        server.shutdown()
    assert report["batches"][0]["status"] == 403
    assert report["batches"][0]["errors"] == ["Forbidden"]
    assert API_KEY not in json.dumps(report)
    assert API_KEY not in repr(report)


def main():
    tests = [
        test_site_resolution,
        test_series_schema,
        test_batch_limits_and_split,
        test_wire_contract,
        test_client_splits_at_documented_limit,
        test_deflate_compression,
        test_gzip_compression_and_unsupported_encoding,
        test_compressed_batches_split_by_decompressed_limit,
        test_point_freshness_window,
        test_partial_batch_failure_and_intake_errors,
        test_rate_limit_retry_and_exhaustion,
        test_api_key_never_leaks,
    ]
    assert SOURCES["research"]["required"] is True
    assert len(SOURCES["research"]["official_sources"]) >= 2
    for t in tests:
        t()
        print("ok  %s" % t.__name__)
    print("all %d test groups passed" % len(tests))


if __name__ == "__main__":
    main()
