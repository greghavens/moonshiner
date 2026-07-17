"""Acceptance tests for the dt_entities package.

Runs a loopback fake Dynatrace Environment API v2 (/api/v2/entities with
entitySelector/from/pageSize/nextPageKey semantics, Api-Token auth and the
documented error envelope) and drives dt_entities against it. No real
Dynatrace, no real credentials, no network beyond 127.0.0.1. The wire
contract the fake enforces is pinned in docs/contract.json. This file and
everything under docs/ are protected.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

TOKEN = CONTRACT["auth"]["fixture_token"]  # dummy; must never leak
ENTITIES_PATH = CONTRACT["endpoint"]["path"]


class FakeDynatrace:
    """Loopback /api/v2/entities fake with request recording."""

    def __init__(self):
        self.requests = []  # {"method","path","query","headers","raw_query"}
        self.script = []    # queued (status, doc) pairs

    def queue(self, status, doc):
        self.script.append((status, doc))

    def next_step(self):
        if self.script:
            return self.script.pop(0)
        return (200, {"totalCount": 0, "pageSize": 50, "entities": []})


def make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            parsed = urlparse(self.path)
            fake.requests.append({
                "method": "GET",
                "path": parsed.path,
                "raw_query": parsed.query,
                "query": parse_qs(parsed.query, keep_blank_values=True),
                "headers": {k.lower(): v for k, v in self.headers.items()},
            })
            status, doc = fake.next_step()
            payload = json.dumps(doc).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json;charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    return Handler


class Fixture:
    def __init__(self):
        self.fake = FakeDynatrace()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.fake))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def client(self, **kwargs):
        from dt_entities import EntitiesClient
        return EntitiesClient(self.base_url, TOKEN, **kwargs)


def entity(entity_id, name, etype="HOST", tags=(), zones=(), **extra):
    doc = {
        "entityId": entity_id,
        "displayName": name,
        "type": etype,
        "tags": [dict(t) for t in tags],
        "managementZones": [dict(z) for z in zones],
    }
    doc.update(extra)
    return doc


PAGE1 = CONTRACT["fixtures"]["page1"]
PAGE2 = CONTRACT["fixtures"]["page2"]
SELECTOR = CONTRACT["fixtures"]["entity_selector"]


def run(fn):
    fx = Fixture()
    try:
        fn(fx)
    finally:
        fx.close()


# --- request shaping -------------------------------------------------------

def test_first_page_request(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 1, "pageSize": 200,
                        "entities": [entity("HOST-1", "web-01")]})
    export_entities(fx.client(), SELECTOR, page_size=200, time_from="now-2h",
                    fields=("+lastSeenTms", "+tags", "+managementZones"))
    assert len(fx.fake.requests) == 1
    req = fx.fake.requests[0]
    assert req["path"] == ENTITIES_PATH, "must call the v2 entities endpoint"
    q = req["query"]
    assert q["entitySelector"] == [SELECTOR]
    assert q["pageSize"] == ["200"]
    assert q["from"] == ["now-2h"]
    assert q["fields"] == ["+lastSeenTms,+tags,+managementZones"]
    assert set(q) == {"entitySelector", "pageSize", "from", "fields"}, \
        "no undocumented query parameters on the first page"
    auth = req["headers"].get("authorization", "")
    assert auth == CONTRACT["auth"]["header_value_prefix"] + TOKEN
    assert "api-token" not in req["raw_query"].lower(), \
        "token must never travel in the URL"


def test_default_page_size_still_explicit(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 0, "pageSize": 50, "entities": []})
    export_entities(fx.client(), SELECTOR)
    q = fx.fake.requests[0]["query"]
    assert q["pageSize"] == ["50"], "exporter pins the page size instead of relying on server defaults"
    assert q["from"] == ["now-3d"], "documented default timeframe is pinned explicitly"


def test_next_page_request_carries_only_the_cursor(fx):
    from dt_entities import export_entities
    key = CONTRACT["fixtures"]["next_page_key"]
    fx.fake.queue(200, {"totalCount": 3, "pageSize": 2, "nextPageKey": key,
                        "entities": [entity("HOST-1", "a"), entity("HOST-2", "b")]})
    fx.fake.queue(200, {"totalCount": 3, "pageSize": 2, "nextPageKey": None,
                        "entities": [entity("HOST-3", "c")]})
    export_entities(fx.client(), SELECTOR, page_size=2)
    assert len(fx.fake.requests) == 2
    req = fx.fake.requests[1]
    assert req["path"] == ENTITIES_PATH
    assert set(req["query"]) == {"nextPageKey"}, \
        "subsequent pages must omit every parameter except nextPageKey"
    assert req["query"]["nextPageKey"] == [key], \
        "cursor must round-trip byte-exactly (watch the URL encoding)"
    auth = req["headers"].get("authorization", "")
    assert auth == CONTRACT["auth"]["header_value_prefix"] + TOKEN


def test_pagination_accumulates_in_order(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 5, "pageSize": 2, "nextPageKey": "k1",
                        "entities": [entity("HOST-2", "b"), entity("HOST-1", "a")]})
    fx.fake.queue(200, {"totalCount": 5, "pageSize": 2, "nextPageKey": "k2",
                        "entities": [entity("HOST-4", "d"), entity("HOST-3", "c")]})
    fx.fake.queue(200, {"totalCount": 5, "pageSize": 2,
                        "entities": [entity("HOST-5", "e")]})
    result = export_entities(fx.client(), SELECTOR, page_size=2)
    assert len(fx.fake.requests) == 3, "loop must stop once nextPageKey is absent"
    assert result["pages"] == 3
    assert result["totalCount"] == 5
    ids = [e["entityId"] for e in result["entities"]]
    assert ids == ["HOST-1", "HOST-2", "HOST-3", "HOST-4", "HOST-5"], \
        "export order must be deterministic regardless of page order"


# --- totalCount handling ---------------------------------------------------

def test_total_count_drift_raises(fx):
    from dt_entities import export_entities
    from dt_entities.errors import EntityDriftError
    fx.fake.queue(200, {"totalCount": 3, "pageSize": 2, "nextPageKey": "k1",
                        "entities": [entity("HOST-1", "a"), entity("HOST-2", "b")]})
    fx.fake.queue(200, {"totalCount": 4, "pageSize": 2,
                        "entities": []})
    try:
        export_entities(fx.client(), SELECTOR, page_size=2)
    except EntityDriftError as err:
        assert err.collected == 2
        assert err.total_count == 3, "drift compares against the first page's totalCount"
        assert [e["entityId"] for e in err.entities] == ["HOST-1", "HOST-2"], \
            "partial results must survive on the error"
    else:
        raise AssertionError("collected != totalCount must raise EntityDriftError")


def test_boundary_duplicates_are_deduplicated(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 3, "pageSize": 2, "nextPageKey": "k1",
                        "entities": [entity("HOST-1", "first-seen-name"),
                                     entity("HOST-2", "b")]})
    fx.fake.queue(200, {"totalCount": 3, "pageSize": 2,
                        "entities": [entity("HOST-1", "renamed-mid-scan"),
                                     entity("HOST-3", "c")]})
    result = export_entities(fx.client(), SELECTOR, page_size=2)
    ids = [e["entityId"] for e in result["entities"]]
    assert ids == ["HOST-1", "HOST-2", "HOST-3"]
    kept = [e for e in result["entities"] if e["entityId"] == "HOST-1"][0]
    assert kept["displayName"] == "first-seen-name", \
        "page-boundary duplicates keep the first occurrence"


# --- selector validation ---------------------------------------------------

def test_selector_requires_a_type(fx):
    from dt_entities import export_entities
    for bad in ["", "   ", 'healthState("HEALTHY")']:
        try:
            export_entities(fx.client(), bad)
        except ValueError:
            pass
        else:
            raise AssertionError("selector without type(...) must be rejected: %r" % bad)
    assert fx.fake.requests == [], "invalid selectors must be rejected before any request"


def test_selector_rejects_two_types(fx):
    from dt_entities import export_entities
    try:
        export_entities(fx.client(), 'type("HOST"),type("SERVICE")')
    except ValueError:
        pass
    else:
        raise AssertionError("only one entity type per selector is allowed")
    assert fx.fake.requests == []


def test_selector_length_limit(fx):
    from dt_entities import export_entities
    long_tag = "x" * 2000
    sel = 'type("HOST"),tag("%s")' % long_tag
    assert len(sel) > CONTRACT["endpoint"]["entity_selector_max_chars"]
    try:
        export_entities(fx.client(), sel)
    except ValueError:
        pass
    else:
        raise AssertionError("selector longer than the documented limit must be rejected")
    assert fx.fake.requests == []


def test_build_selector(fx):
    from dt_entities import build_selector
    sel = build_selector("HOST", tags=("env:prod",), health_state="HEALTHY")
    assert sel == 'type("HOST"),tag("env:prod"),healthState("HEALTHY")'
    sel2 = build_selector("SERVICE")
    assert sel2 == 'type("SERVICE")'
    try:
        build_selector("HOST", health_state="DEGRADED")
    except ValueError:
        pass
    else:
        raise AssertionError("healthState only accepts the two documented values")


def test_fields_must_use_plus_prefix(fx):
    from dt_entities import export_entities
    try:
        export_entities(fx.client(), SELECTOR, fields=("lastSeenTms",))
    except ValueError:
        pass
    else:
        raise AssertionError("fields entries must carry the documented '+' prefix")
    assert fx.fake.requests == []


# --- client argument rules -------------------------------------------------

def test_fetch_page_takes_selector_xor_cursor(fx):
    client = fx.client()
    try:
        client.fetch_page()
    except ValueError:
        pass
    else:
        raise AssertionError("one of entity_selector / next_page_key is required")
    try:
        client.fetch_page(entity_selector=SELECTOR, next_page_key="k")
    except ValueError:
        pass
    else:
        raise AssertionError("entity_selector and next_page_key are mutually exclusive")
    assert fx.fake.requests == []


# --- error envelope --------------------------------------------------------

def test_constraint_violation_envelope(fx):
    from dt_entities.errors import DtApiError
    fx.fake.queue(400, CONTRACT["fixtures"]["error_400"])
    try:
        fx.client().fetch_page(entity_selector=SELECTOR)
    except DtApiError as err:
        assert err.status == 400
        assert err.code == 400
        assert err.message == CONTRACT["fixtures"]["error_400"]["error"]["message"]
        assert isinstance(err.violations, list) and len(err.violations) == 1
        assert err.violations[0]["message"].startswith("The entity selector")
        assert TOKEN not in str(err), "token must never appear in error text"
    else:
        raise AssertionError("error envelope must raise DtApiError")


def test_unauthorized_envelope(fx):
    from dt_entities.errors import DtApiError
    fx.fake.queue(401, {"error": {"code": 401, "message": "Token is missing required scope."}})
    try:
        fx.client().fetch_page(entity_selector=SELECTOR)
    except DtApiError as err:
        assert err.status == 401
        assert err.violations == []
        assert TOKEN not in str(err)
    else:
        raise AssertionError("401 must raise DtApiError")


# --- normalization ---------------------------------------------------------

def test_normalization_shape(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 2, "pageSize": 50, "entities": [PAGE1, PAGE2]})
    result = export_entities(fx.client(), SELECTOR)
    ent = result["entities"][0]
    assert ent["entityId"] == "HOST-A0B1C2D3E4F5A6B7"
    assert list(ent.keys()) == CONTRACT["normalization"]["keys"], \
        "normalized entities expose exactly the documented keys, in order"
    assert ent["tags"] == [
        "AWS:owner=platform",
        "CONTEXTLESS:env=prod",
        "CONTEXTLESS:swap",
    ], "tags flatten to context:key=value (key only when no value) and sort"
    assert ent["managementZones"] == ["mainframe", "web-farm"], \
        "management zones normalize to sorted names"
    assert ent["firstSeenTms"] == 1723041455000
    assert ent["lastSeenTms"] == 1752741455000


def test_normalization_defaults_and_sorting(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 2, "pageSize": 50, "entities": [
        {"entityId": "HOST-FFFF000011112222", "type": "HOST"},
        entity("HOST-0000111122223333", "alpha"),
    ]})
    result = export_entities(fx.client(), SELECTOR)
    ids = [e["entityId"] for e in result["entities"]]
    assert ids == ["HOST-0000111122223333", "HOST-FFFF000011112222"], \
        "entities sort by entityId"
    bare = result["entities"][1]
    assert bare["displayName"] == "", "missing displayName normalizes to empty string"
    assert bare["tags"] == [] and bare["managementZones"] == []
    assert bare["firstSeenTms"] is None and bare["lastSeenTms"] is None


def test_export_is_json_serializable_and_stable(fx):
    from dt_entities import export_entities
    fx.fake.queue(200, {"totalCount": 2, "pageSize": 50, "entities": [PAGE2, PAGE1]})
    first = export_entities(fx.client(), SELECTOR)
    fx.fake.queue(200, {"totalCount": 2, "pageSize": 50, "entities": [PAGE1, PAGE2]})
    second = export_entities(fx.client(), SELECTOR)
    assert json.dumps(first["entities"], sort_keys=False) == \
        json.dumps(second["entities"], sort_keys=False), \
        "the same environment state must serialize identically"


# --- protected provenance fixtures ----------------------------------------

def test_provenance_fixtures():
    assert SOURCES["research"]["required"] is True
    assert len(SOURCES["research"]["official_sources"]) >= 2
    assert SOURCES["research"]["product"].startswith("Dynatrace")
    assert isinstance(SOURCES["verified_facts"], list) and SOURCES["verified_facts"]
    assert CONTRACT["endpoint"]["path"] == "/api/v2/entities"
    assert CONTRACT["auth"]["header_value_prefix"] == "Api-Token "
    assert CONTRACT["endpoint"]["token_scope"] == "entities.read"
    assert CONTRACT["pagination"]["subsequent_pages_only_param"] == "nextPageKey"


def main():
    tests = [
        test_first_page_request,
        test_default_page_size_still_explicit,
        test_next_page_request_carries_only_the_cursor,
        test_pagination_accumulates_in_order,
        test_total_count_drift_raises,
        test_boundary_duplicates_are_deduplicated,
        test_selector_requires_a_type,
        test_selector_rejects_two_types,
        test_selector_length_limit,
        test_build_selector,
        test_fields_must_use_plus_prefix,
        test_fetch_page_takes_selector_xor_cursor,
        test_constraint_violation_envelope,
        test_unauthorized_envelope,
        test_normalization_shape,
        test_normalization_defaults_and_sorting,
        test_export_is_json_serializable_and_stable,
    ]
    test_provenance_fixtures()
    for fn in tests:
        run(fn)
        print("ok  %s" % fn.__name__)
    print("all %d tests passed" % (len(tests) + 1))


if __name__ == "__main__":
    main()
