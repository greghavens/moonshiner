#!/usr/bin/env python3
"""Acceptance harness for the merakiinv package.

Runs a loopback fake of the Cisco Meraki Dashboard API v1 whose wire shapes
are pinned in docs/contract.json (provenance in docs/official_sources.json).
Hermetic: no real Meraki dashboard, no real API key, no sleeping.

Protected file -- do not modify. Run: python3 test_meraki_inventory.py
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from merakiinv import (
    MerakiAPIError,
    MerakiSession,
    base_url_for_region,
    collect_inventory,
)

API_KEY = "f00dfeedfacef00dfeedfacef00dfeedfacef00d-fake"

CHECKS = 0


def check(cond, msg):
    global CHECKS
    CHECKS += 1
    assert cond, "check %d failed: %s" % (CHECKS, msg)


class FakeDashboard:
    """Scripted loopback Meraki dashboard: per-path FIFO response queues."""

    def __init__(self):
        self.requests = []
        self.routes = {}
        self.server = None

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/api/v1" % self.server.server_address[1]

    def enqueue(self, path, status, body, headers=None):
        self.routes.setdefault(path, []).append((status, headers or {}, body))

    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urlsplit(self.path)
                fake.requests.append(
                    {
                        "method": "GET",
                        "path": parts.path,
                        "query": parse_qs(parts.query),
                        "auth": self.headers.get("Authorization"),
                    }
                )
                queue = fake.routes.get(parts.path, [])
                if queue:
                    status, hdrs, body = queue.pop(0)
                else:
                    status, hdrs, body = (
                        500,
                        {},
                        {"errors": ["unexpected request %s" % parts.path]},
                    )
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in hdrs.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def org(oid, name, enabled, region, model):
    return {
        "id": oid,
        "name": name,
        "url": "https://n7.meraki.com/o/%s" % oid,
        "api": {"enabled": enabled},
        "licensing": {"model": model},
        "cloud": {"region": {"name": region}},
    }


def avail(serial, name, mac, ptype, status, net):
    return {
        "mac": mac,
        "name": name,
        "network": {"id": net},
        "productType": ptype,
        "serial": serial,
        "status": status,
        "tags": [],
    }


def test_regional_base_urls():
    check(
        base_url_for_region("global") == "https://api.meraki.com/api/v1",
        "global region must map to the documented default base URL",
    )
    check(
        base_url_for_region("canada") == "https://api.meraki.ca/api/v1",
        "canada region base URL wrong",
    )
    check(
        base_url_for_region("china") == "https://api.meraki.cn/api/v1",
        "china region base URL wrong",
    )
    check(
        base_url_for_region("india") == "https://api.meraki.in/api/v1",
        "india region base URL wrong",
    )
    check(
        base_url_for_region("us-fedramp") == "https://api.gov-meraki.com/api/v1",
        "us-fedramp region base URL wrong",
    )
    try:
        base_url_for_region("mars")
    except ValueError:
        pass
    else:
        check(False, "unknown region must raise ValueError")
    check(True, "unknown region raised ValueError")
    check(
        MerakiSession(API_KEY).base_url == "https://api.meraki.com/api/v1",
        "session default base_url must be the documented global base URL",
    )


def test_inventory_traversal():
    fake = FakeDashboard()
    fake.start()
    base = fake.base_url

    orgs_next = (
        "%s/organizations?perPage=2&startingAfter=810001&cursorcheck=orgs2" % base
    )
    fake.enqueue(
        "/api/v1/organizations",
        200,
        [
            org("810002", "Basil Foods", True, "North America", "co-term"),
            org("810001", "Aster Labs", True, "Canada", "per-device"),
        ],
        headers={
            "Link": "<%s/organizations?perPage=2>; rel=first, <%s>; rel=next"
            % (base, orgs_next)
        },
    )
    fake.enqueue(
        "/api/v1/organizations",
        200,
        [org("810003", "Cinder Ops", False, "North America", "co-term")],
    )
    fake.enqueue(
        "/api/v1/organizations/810001/networks",
        200,
        [
            {
                "id": "N_81011",
                "organizationId": "810001",
                "name": "Depot Wifi",
                "productTypes": ["wireless", "switch"],
                "timeZone": "America/Los_Angeles",
                "tags": [],
            },
            {
                "id": "N_81005",
                "organizationId": "810001",
                "name": "HQ Appliance",
                "productTypes": ["appliance"],
                "timeZone": "America/Los_Angeles",
                "tags": [],
            },
        ],
    )
    avail_next = (
        "%s/organizations/810001/devices/availabilities"
        "?perPage=2&startingAfter=Q2KD-0003-SW03&cursorcheck=av2" % base
    )
    fake.enqueue(
        "/api/v1/organizations/810001/devices/availabilities",
        200,
        [
            avail(
                "Q2KD-0003-SW03", "sw-depot-3", "98:18:88:aa:00:03",
                "switch", "online", "N_81011",
            ),
            avail(
                "Q2KD-0001-AP01", "ap-hq-1", "98:18:88:aa:00:01",
                "wireless", "alerting", "N_81011",
            ),
        ],
        headers={"Link": '<%s>; rel="next", <%s/organizations/810001/devices/availabilities?perPage=2>; rel="first"' % (avail_next, base)},
    )
    fake.enqueue(
        "/api/v1/organizations/810001/devices/availabilities",
        200,
        [
            avail(
                "Q2KD-0004-SW04", "sw-depot-4", "98:18:88:aa:00:04",
                "switch", "alerting", "N_81005",
            ),
            avail(
                "Q2KD-0002-SW02", "sw-depot-2", "98:18:88:aa:00:02",
                "switch", "online", "N_81011",
            ),
        ],
    )
    fake.enqueue(
        "/api/v1/organizations/810002/networks",
        200,
        [
            {
                "id": "N_82001",
                "organizationId": "810002",
                "name": "Dockside",
                "productTypes": ["wireless", "camera"],
                "timeZone": "America/New_York",
                "tags": [],
            }
        ],
    )
    fake.enqueue(
        "/api/v1/organizations/810002/devices/availabilities",
        200,
        [
            avail(
                "Q2BF-0001-AP01", "ap-basil-1", "98:18:88:bb:00:01",
                "wireless", "online", "N_82001",
            )
        ],
    )

    sleeps = []
    session = MerakiSession(API_KEY, base_url=base, sleep=sleeps.append)
    inventory = collect_inventory(
        session,
        product_types=["switch", "wireless"],
        statuses=["online", "alerting"],
        per_page=2,
    )

    expected = {
        "organizations": [
            {
                "id": "810001",
                "name": "Aster Labs",
                "region": "Canada",
                "networks": [
                    {
                        "id": "N_81005",
                        "name": "HQ Appliance",
                        "productTypes": ["appliance"],
                    },
                    {
                        "id": "N_81011",
                        "name": "Depot Wifi",
                        "productTypes": ["switch", "wireless"],
                    },
                ],
                "devices": [
                    {
                        "serial": "Q2KD-0001-AP01",
                        "name": "ap-hq-1",
                        "mac": "98:18:88:aa:00:01",
                        "productType": "wireless",
                        "status": "alerting",
                        "networkId": "N_81011",
                    },
                    {
                        "serial": "Q2KD-0002-SW02",
                        "name": "sw-depot-2",
                        "mac": "98:18:88:aa:00:02",
                        "productType": "switch",
                        "status": "online",
                        "networkId": "N_81011",
                    },
                    {
                        "serial": "Q2KD-0003-SW03",
                        "name": "sw-depot-3",
                        "mac": "98:18:88:aa:00:03",
                        "productType": "switch",
                        "status": "online",
                        "networkId": "N_81011",
                    },
                    {
                        "serial": "Q2KD-0004-SW04",
                        "name": "sw-depot-4",
                        "mac": "98:18:88:aa:00:04",
                        "productType": "switch",
                        "status": "alerting",
                        "networkId": "N_81005",
                    },
                ],
            },
            {
                "id": "810002",
                "name": "Basil Foods",
                "region": "North America",
                "networks": [
                    {
                        "id": "N_82001",
                        "name": "Dockside",
                        "productTypes": ["camera", "wireless"],
                    }
                ],
                "devices": [
                    {
                        "serial": "Q2BF-0001-AP01",
                        "name": "ap-basil-1",
                        "mac": "98:18:88:bb:00:01",
                        "productType": "wireless",
                        "status": "online",
                        "networkId": "N_82001",
                    }
                ],
            },
        ]
    }

    check(len(inventory["organizations"]) == 2, "API-disabled org must be skipped")
    first, second = inventory["organizations"]
    check(first["id"] == "810001", "organizations must be sorted ascending by id")
    check(first["region"] == "Canada", "region must come from cloud.region.name")
    check(second["region"] == "North America", "region wrong for second org")
    check(
        [n["id"] for n in first["networks"]] == ["N_81005", "N_81011"],
        "networks must be sorted ascending by id",
    )
    check(
        first["networks"][1]["productTypes"] == ["switch", "wireless"],
        "network productTypes must be sorted",
    )
    check(
        [d["serial"] for d in first["devices"]]
        == ["Q2KD-0001-AP01", "Q2KD-0002-SW02", "Q2KD-0003-SW03", "Q2KD-0004-SW04"],
        "devices must be sorted ascending by serial",
    )
    check(
        {d["status"] for d in first["devices"]} == {"online", "alerting"},
        "device status values must be preserved verbatim",
    )
    check(inventory == expected, "full inventory snapshot mismatch: %r" % (inventory,))
    check(sleeps == [], "no sleeping on the happy path")

    reqs = fake.requests
    check(
        [r["path"] for r in reqs]
        == [
            "/api/v1/organizations",
            "/api/v1/organizations",
            "/api/v1/organizations/810001/networks",
            "/api/v1/organizations/810001/devices/availabilities",
            "/api/v1/organizations/810001/devices/availabilities",
            "/api/v1/organizations/810002/networks",
            "/api/v1/organizations/810002/devices/availabilities",
        ],
        "request order must be: all org pages, then per-org networks then "
        "availabilities in ascending org id order; got %r" % [r["path"] for r in reqs],
    )
    check(
        all(r["auth"] == "Bearer %s" % API_KEY for r in reqs),
        "every request must carry the documented Authorization bearer header",
    )
    check(
        all(r["path"].startswith("/api/v1/") for r in reqs),
        "every request must use the documented /api/v1 base path",
    )
    check(
        not any("810003" in r["path"] for r in reqs),
        "no requests may be issued for an org whose API access is disabled",
    )
    check(reqs[0]["query"].get("perPage") == ["2"], "first orgs page must send perPage")
    check(
        reqs[1]["query"]
        == {"perPage": ["2"], "startingAfter": ["810001"], "cursorcheck": ["orgs2"]},
        "second orgs page must use the Link rel=next URL verbatim "
        "(no re-added params); got %r" % (reqs[1]["query"],),
    )
    av1 = reqs[3]["query"]
    check(av1.get("perPage") == ["2"], "availabilities must send perPage")
    check(
        av1.get("productTypes[]") == ["switch", "wireless"],
        "productTypes filter must use the documented array form "
        "productTypes[] repeated; got %r" % (av1,),
    )
    check(
        av1.get("statuses[]") == ["online", "alerting"],
        "statuses filter must use the documented array form statuses[]",
    )
    check(
        reqs[4]["query"]
        == {
            "perPage": ["2"],
            "startingAfter": ["Q2KD-0003-SW03"],
            "cursorcheck": ["av2"],
        },
        "availabilities page 2 must follow the quoted rel=\"next\" Link URL "
        "verbatim; got %r" % (reqs[4]["query"],),
    )
    check(
        reqs[5]["query"].get("productTypes[]") is None,
        "networks listing must not receive the device productTypes filter",
    )
    fake.stop()


def test_rate_limit_retry():
    fake = FakeDashboard()
    fake.start()
    path = "/api/v1/organizations/990001/devices/availabilities"
    fake.enqueue(
        path,
        429,
        {"errors": ["API rate limit exceeded for organization"]},
        headers={"Retry-After": "2"},
    )
    fake.enqueue(
        path,
        200,
        [avail("Q2RL-0001-AP01", "ap-rl-1", "98:18:88:cc:00:01", "wireless", "online", "N_99001")],
    )
    sleeps = []
    session = MerakiSession(API_KEY, base_url=fake.base_url, sleep=sleeps.append)
    items = session.get_paginated("/organizations/990001/devices/availabilities")
    check(len(items) == 1, "retry after 429 must return the successful page")
    check(items[0]["serial"] == "Q2RL-0001-AP01", "retried page body mismatch")
    check(len(sleeps) == 1, "exactly one wait for one 429")
    check(float(sleeps[0]) == 2.0, "must wait exactly the Retry-After seconds")
    check(
        len([r for r in fake.requests if r["path"] == path]) == 2,
        "one retry request after the 429",
    )
    fake.stop()


def test_rate_limit_bounded():
    fake = FakeDashboard()
    fake.start()
    path = "/api/v1/organizations/990002/networks"
    for _ in range(3):
        fake.enqueue(
            path,
            429,
            {"errors": ["API rate limit exceeded for organization"]},
            headers={"Retry-After": "1"},
        )
    sleeps = []
    session = MerakiSession(
        API_KEY, base_url=fake.base_url, sleep=sleeps.append, max_retries=2
    )
    try:
        session.get_paginated("/organizations/990002/networks")
    except MerakiAPIError as exc:
        check(exc.status == 429, "exhausted retries must surface status 429")
        check(
            any("rate limit" in e.lower() for e in exc.errors),
            "server error strings must be preserved",
        )
        check(API_KEY not in str(exc), "the API key must never leak into errors")
    else:
        check(False, "persistent 429 must raise MerakiAPIError")
    check([float(s) for s in sleeps] == [1.0, 1.0], "max_retries=2 means two waits")
    check(
        len([r for r in fake.requests if r["path"] == path]) == 3,
        "max_retries=2 means three requests total",
    )
    fake.stop()


def test_error_decoding():
    fake = FakeDashboard()
    fake.start()
    fake.enqueue(
        "/api/v1/organizations/990404/networks",
        404,
        {"errors": ["Organization not found"]},
    )
    session = MerakiSession(API_KEY, base_url=fake.base_url, sleep=lambda s: None)
    try:
        session.get_json("/organizations/990404/networks")
    except MerakiAPIError as exc:
        check(exc.status == 404, "error must carry the HTTP status")
        check(
            "Organization not found" in exc.errors,
            "documented errors array must be decoded",
        )
        check("404" in str(exc), "stringified error must name the status")
        check(API_KEY not in str(exc), "the API key must never leak into errors")
    else:
        check(False, "404 must raise MerakiAPIError")
    fake.stop()


def main():
    test_regional_base_urls()
    test_inventory_traversal()
    test_rate_limit_retry()
    test_rate_limit_bounded()
    test_error_decoding()
    print("OK (%d checks)" % CHECKS)


if __name__ == "__main__":
    main()
