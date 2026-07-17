"""Acceptance harness: loopback fake Kubernetes API server for the
coordination.k8s.io/v1 Lease contract pinned in docs/contract.json.
Re-checks the existing ApiClient behavior and exercises the new
lease_renewer feature. Protected — do not modify. Run:

    python3 test_lease_renewer.py
"""

import http.server
import json
import re
import threading
from datetime import datetime, timedelta, timezone

from k8s_client import ApiClient, ApiError
from lease_renewer import LeadershipLost, LeaseRenewer, format_micro_time

TOKEN = "dummy-sa-token-4c19e2"  # dummy; must never leak
NAMESPACE = "sched"
LEASE_NAME = "ingest-leader"
IDENTITY = "scheduler-a"
RIVAL = "scheduler-b"
LEASE_PATH = f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases/{LEASE_NAME}"
COLLECTION_PATH = f"/apis/coordination.k8s.io/v1/namespaces/{NAMESPACE}/leases"
MICRO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


def check_eq(got, want, msg):
    check(got == want, f"{msg} — got {got!r}, want {want!r}")


def micro(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# ------------------------------------------------------------------ fakes


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def status_body(code, reason, message):
    return {"kind": "Status", "apiVersion": "v1", "status": "Failure",
            "message": message, "reason": reason, "code": code}


class FakeCluster:
    """Scripted lease store behind a loopback HTTP server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.spec = None          # current lease spec dict, or None
        self.rv = 0
        self.records = []         # (method, path, body-or-None, headers)
        self.put_faults = []      # list of (status, swap_holder_or_None)
        self.redirect_next = None  # absolute URL to 302 to, once

    def seed(self, holder, acquire_time, renew_time, duration, transitions, rv):
        self.spec = {
            "holderIdentity": holder,
            "leaseDurationSeconds": duration,
            "acquireTime": acquire_time,
            "renewTime": renew_time,
            "leaseTransitions": transitions,
        }
        self.rv = rv

    def lease_json(self):
        return {
            "apiVersion": "coordination.k8s.io/v1",
            "kind": "Lease",
            "metadata": {"name": LEASE_NAME, "namespace": NAMESPACE,
                         "resourceVersion": str(self.rv), "uid": "uid-lease-1"},
            "spec": dict(self.spec),
        }

    def start(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.app = self
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # ------------------------------------------------------------ logic

    def handle(self, h, method):
        length = int(h.headers.get("Content-Length") or 0)
        body = json.loads(h.rfile.read(length)) if length else None
        with self.lock:
            self.records.append((method, h.path,
                                 body, {k.lower(): v for k, v in h.headers.items()}))
            if h.headers.get("Authorization") != f"Bearer {TOKEN}":
                return h.send_json(401, status_body(401, "Unauthorized",
                                                    "credentials required"))
            if self.redirect_next:
                target, self.redirect_next = self.redirect_next, None
                return h.send_redirect(302, target)
            if method == "GET" and h.path.startswith(COLLECTION_PATH + "/"):
                name = h.path.rsplit("/", 1)[1]
                if name == LEASE_NAME and self.spec is not None:
                    return h.send_json(200, self.lease_json())
                return h.send_json(404, status_body(
                    404, "NotFound",
                    f'leases.coordination.k8s.io "{name}" not found'))
            if method == "POST" and h.path == COLLECTION_PATH:
                if self.spec is not None:
                    return h.send_json(409, status_body(
                        409, "AlreadyExists",
                        f'leases.coordination.k8s.io "{LEASE_NAME}" already exists'))
                self.spec = dict(body.get("spec") or {})
                self.rv = 1
                return h.send_json(201, self.lease_json())
            if method == "PUT" and h.path == LEASE_PATH:
                if self.put_faults:
                    code, swap = self.put_faults.pop(0)
                    if swap:
                        self.spec["holderIdentity"] = swap
                        self.rv += 1
                    return h.send_json(code, status_body(
                        code, "Conflict",
                        f'Operation cannot be fulfilled on leases.coordination.k8s.io '
                        f'"{LEASE_NAME}": the object has been modified; please apply '
                        f'your changes to the latest version and try again'))
                sent_rv = (body.get("metadata") or {}).get("resourceVersion")
                if sent_rv != str(self.rv):
                    return h.send_json(409, status_body(
                        409, "Conflict",
                        f'Operation cannot be fulfilled on leases.coordination.k8s.io '
                        f'"{LEASE_NAME}": the object has been modified; please apply '
                        f'your changes to the latest version and try again'))
                self.spec = dict(body.get("spec") or {})
                self.rv += 1
                return h.send_json(200, self.lease_json())
            return h.send_json(400, status_body(400, "BadRequest",
                                                f"unsupported {method} {h.path}"))

    def writes(self):
        with self.lock:
            return [(m, p, b) for m, p, b, _ in self.records if m in ("POST", "PUT")]

    def methods(self):
        with self.lock:
            return [m for m, _, _, _ in self.records]


class EvilHost:
    """A different origin that must never see our credentials."""

    def __init__(self):
        self.lock = threading.Lock()
        self.hits = []

    def handle(self, h, method):
        with self.lock:
            self.hits.append({k.lower(): v for k, v in h.headers.items()})
        h.send_json(200, {})

    def start(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.app = self
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, code, target):
        self.send_response(code)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self.server.app.handle(self, "GET")

    def do_POST(self):
        self.server.app.handle(self, "POST")

    def do_PUT(self):
        self.server.app.handle(self, "PUT")


T0 = datetime(2026, 7, 16, 9, 15, 0, 250000, tzinfo=timezone.utc)


def new_renewer(cluster, clock, duration=15):
    client = ApiClient(cluster.base_url, TOKEN)
    return LeaseRenewer(client, NAMESPACE, LEASE_NAME, IDENTITY,
                        lease_duration_seconds=duration, clock=clock)


def with_cluster(fn):
    cluster = FakeCluster().start()
    try:
        fn(cluster)
    finally:
        cluster.stop()


# ------------------------------------------------------------------ tests


def test_protected_docs_fixtures():
    with open("docs/contract.json", encoding="utf-8") as f:
        contract = json.load(f)
    with open("docs/official_sources.json", encoding="utf-8") as f:
        sources = json.load(f)
    research = sources["research"]
    check(research["required"] is True, "research provenance is mandatory")
    urls = research["official_sources"]
    check(len(urls) >= 2, "at least two official sources required")
    for src in urls:
        u = src["url"]
        first_party = ("kubernetes.io" in u
                       or "github.com/kubernetes/kubernetes" in u
                       or "githubusercontent.com/kubernetes/kubernetes" in u)
        check(u.startswith("https://") and first_party,
              f"official source must be first-party Kubernetes: {u}")
        check(src.get("used_for"), "each source records what it was used for")
    check(len(sources["verified_facts"]) >= 4, "verified facts must be summarized")
    ops = contract["operations"]
    check_eq(ops["replace"]["method"], "PUT", "contract pins replace as PUT")
    check_eq(ops["replace"]["conflict"]["status"], 409, "contract pins the 409 conflict")
    check("six fractional digits" in contract["micro_time"]["format"],
          "contract pins the MicroTime serialization")
    check("coordination.k8s.io/v1" in contract["interface"],
          "contract pins the stable coordination group version")


def test_existing_client_get_and_status_error(cluster):
    cluster.seed(RIVAL, micro(T0), micro(T0), 15, 3, rv=5)
    client = ApiClient(cluster.base_url, TOKEN)
    lease = client.get_lease(NAMESPACE, LEASE_NAME)
    check_eq(lease["kind"], "Lease", "get_lease returns the Lease object")
    check_eq(lease["spec"]["holderIdentity"], RIVAL, "spec decodes")
    check_eq(lease["metadata"]["resourceVersion"], "5", "resourceVersion decodes")
    method, path, _, headers = cluster.records[0]
    check_eq((method, path), ("GET", LEASE_PATH), "lease read path")
    check_eq(headers.get("authorization"), f"Bearer {TOKEN}",
             "bearer token on every request")
    check_eq(headers.get("accept"), "application/json", "Accept header set")
    try:
        client.get_lease(NAMESPACE, "ghost")
        raise AssertionError("missing lease must raise ApiError")
    except ApiError as err:
        check_eq(err.status_code, 404, "404 becomes ApiError.status_code")
        check_eq(err.reason, "NotFound", "reason from the Status body")
        check("ghost" in err.message, "message from the Status body")
        check(TOKEN not in str(err), "credentials never appear in errors")


def test_existing_client_refuses_cross_origin_redirect(cluster):
    evil = EvilHost().start()
    try:
        cluster.seed(RIVAL, micro(T0), micro(T0), 15, 0, rv=1)
        cluster.redirect_next = evil.base_url + LEASE_PATH
        client = ApiClient(cluster.base_url, TOKEN)
        try:
            client.get_lease(NAMESPACE, LEASE_NAME)
            raise AssertionError("a redirect must not be silently followed")
        except ApiError as err:
            check_eq(err.status_code, 302, "redirect surfaces with its status code")
            check_eq(err.reason, "Redirect", "redirect surfaces as reason Redirect")
            check(TOKEN not in str(err), "credentials never appear in errors")
        check_eq(len(evil.hits), 0,
                 "the bearer token must never reach a different origin")
    finally:
        evil.stop()


def test_acquire_creates_when_absent(cluster):
    clock = FakeClock(T0)
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is True, "acquire() returns True after creating")
    check(renewer.is_leader is True, "renewer reports leadership")
    check_eq(cluster.methods(), ["GET", "POST"],
             "create path is GET(404) then POST, nothing else")
    method, path, body = cluster.writes()[0]
    check_eq(path, COLLECTION_PATH, "POST goes to the lease collection")
    check_eq(body["apiVersion"], "coordination.k8s.io/v1",
             "create body pins the stable group version")
    check_eq(body["kind"], "Lease", "create body kind")
    check_eq(body["metadata"]["name"], LEASE_NAME, "create body names the lease")
    check_eq(body["metadata"].get("namespace"), NAMESPACE,
             "create body carries the namespace")
    spec = body["spec"]
    check_eq(spec["holderIdentity"], IDENTITY, "holderIdentity is our identity")
    check_eq(spec["leaseDurationSeconds"], 15, "leaseDurationSeconds carried")
    check_eq(spec["leaseTransitions"], 0, "a fresh lease starts at 0 transitions")
    check(MICRO_RE.match(spec["acquireTime"]),
          f"acquireTime must be RFC3339 with 6 fractional digits, got {spec['acquireTime']!r}")
    check_eq(spec["acquireTime"], "2026-07-16T09:15:00.250000Z",
             "acquireTime is the injected clock's now")
    check_eq(spec["renewTime"], spec["acquireTime"],
             "a fresh lease renews at its acquire time")
    _, _, _, headers = cluster.records[1]
    check_eq(headers.get("content-type"), "application/json",
             "JSON content type on writes")


def test_format_micro_time_pads_to_six_digits():
    check_eq(format_micro_time(datetime(2026, 7, 16, 9, 15, 0, 0, tzinfo=timezone.utc)),
             "2026-07-16T09:15:00.000000Z",
             "whole seconds still serialize six fractional digits")
    check_eq(format_micro_time(datetime(2026, 7, 16, 9, 15, 0, 1200, tzinfo=timezone.utc)),
             "2026-07-16T09:15:00.001200Z",
             "microseconds are zero-padded, not trimmed")


def test_acquire_respects_active_holder(cluster):
    clock = FakeClock(T0)
    cluster.seed(RIVAL, micro(T0 - timedelta(seconds=60)),
                 micro(T0 - timedelta(seconds=5)), 15, 3, rv=7)
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is False, "an unexpired foreign lease is respected")
    check(renewer.is_leader is False, "not leader after a failed acquire")
    check_eq(cluster.methods(), ["GET"], "no write may happen for an active holder")


def test_acquire_takes_over_expired(cluster):
    clock = FakeClock(T0)
    cluster.seed(RIVAL, micro(T0 - timedelta(seconds=300)),
                 micro(T0 - timedelta(seconds=40)), 15, 3, rv=7)
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is True, "an expired lease is taken over")
    check_eq(cluster.methods(), ["GET", "PUT"], "takeover is GET then PUT")
    _, path, body = cluster.writes()[0]
    check_eq(path, LEASE_PATH, "replace targets the named lease")
    check_eq(body["metadata"]["resourceVersion"], "7",
             "replace echoes the resourceVersion read by the GET")
    spec = body["spec"]
    check_eq(spec["holderIdentity"], IDENTITY, "takeover claims the lease")
    check_eq(spec["leaseTransitions"], 4, "takeover increments leaseTransitions")
    check_eq(spec["acquireTime"], "2026-07-16T09:15:00.250000Z",
             "takeover resets acquireTime to now")
    check_eq(spec["renewTime"], "2026-07-16T09:15:00.250000Z",
             "takeover resets renewTime to now")


def test_acquire_expiry_boundary(cluster):
    clock = FakeClock(T0)
    cluster.seed(RIVAL, micro(T0 - timedelta(seconds=300)),
                 micro(T0 - timedelta(seconds=15)), 15, 0, rv=2)
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is True,
          "a lease is expired exactly at renewTime + leaseDurationSeconds")


def test_acquire_reclaims_own_lease(cluster):
    clock = FakeClock(T0)
    acquire_str = micro(T0 - timedelta(seconds=120))
    cluster.seed(IDENTITY, acquire_str, micro(T0 - timedelta(seconds=90)), 15, 6, rv=9)
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is True, "our own lease is reclaimed after restart")
    _, _, body = cluster.writes()[0]
    spec = body["spec"]
    check_eq(spec["leaseTransitions"], 6,
             "reclaiming our own lease is not a transition")
    check_eq(spec["acquireTime"], acquire_str,
             "reclaiming preserves the original acquireTime")
    check_eq(spec["renewTime"], "2026-07-16T09:15:00.250000Z",
             "reclaiming advances renewTime to now")


def test_acquire_conflict_cedes(cluster):
    clock = FakeClock(T0)
    cluster.seed(RIVAL, micro(T0 - timedelta(seconds=300)),
                 micro(T0 - timedelta(seconds=40)), 15, 3, rv=7)
    cluster.put_faults = [(409, None)]
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is False,
          "losing the takeover race (409) means another candidate won")
    check(renewer.is_leader is False, "not leader after a lost race")
    check_eq(cluster.methods(), ["GET", "PUT"],
             "acquire must not retry a conflicted takeover")


def test_renew_happy_path(cluster):
    clock = FakeClock(T0)
    acquire_str = micro(T0 - timedelta(seconds=60))
    cluster.seed(IDENTITY, acquire_str, micro(T0 - timedelta(seconds=10)), 15, 2, rv=5)
    renewer = new_renewer(cluster, clock)
    check(renewer.acquire() is True, "precondition: we hold the lease")
    clock.advance(5)
    renewer.renew()
    check_eq(cluster.methods(), ["GET", "PUT", "GET", "PUT"],
             "renew is a fresh GET then a PUT")
    _, path, body = cluster.writes()[1]
    check_eq(path, LEASE_PATH, "renew replaces the named lease")
    check_eq(body["metadata"]["resourceVersion"], "6",
             "renew echoes the fresh GET's resourceVersion")
    spec = body["spec"]
    check_eq(spec["renewTime"], "2026-07-16T09:15:05.250000Z",
             "renewTime advances to the injected now")
    check_eq(spec["acquireTime"], acquire_str, "renewal preserves acquireTime")
    check_eq(spec["leaseTransitions"], 2, "renewal preserves leaseTransitions")
    check_eq(spec["holderIdentity"], IDENTITY, "renewal keeps our identity")
    check(renewer.is_leader is True, "still leader after renewal")


def test_renew_conflict_retries_through_fresh_get(cluster):
    clock = FakeClock(T0)
    cluster.seed(IDENTITY, micro(T0 - timedelta(seconds=60)),
                 micro(T0 - timedelta(seconds=10)), 15, 2, rv=5)
    cluster.put_faults = [(409, None)]
    renewer = new_renewer(cluster, clock)
    renewer.renew()
    check_eq(cluster.methods(), ["GET", "PUT", "GET", "PUT"],
             "a 409 renewal retries exactly once through a fresh GET")
    _, _, retry_body = cluster.writes()[1]
    check_eq(retry_body["metadata"]["resourceVersion"], "5",
             "the retry uses the resourceVersion of the fresh GET")
    check_eq(retry_body["spec"]["renewTime"], "2026-07-16T09:15:00.250000Z",
             "the retry still renews to the injected now")
    check(renewer.is_leader is True, "leadership survives a recovered conflict")


def test_renew_detects_lost_leadership(cluster):
    clock = FakeClock(T0)
    cluster.seed(RIVAL, micro(T0 - timedelta(seconds=5)),
                 micro(T0 - timedelta(seconds=5)), 15, 4, rv=8)
    renewer = new_renewer(cluster, clock)
    try:
        renewer.renew()
        raise AssertionError("renewing a lease held by someone else must raise")
    except LeadershipLost as err:
        check(RIVAL in str(err), "the new holder is named in the error")
        check(TOKEN not in str(err), "credentials never appear in errors")
    check(renewer.is_leader is False, "leadership flag cleared")
    check_eq(cluster.methods(), ["GET"],
             "no PUT may be attempted once the holder changed")


def test_renew_conflict_then_rival_holder(cluster):
    clock = FakeClock(T0)
    cluster.seed(IDENTITY, micro(T0 - timedelta(seconds=60)),
                 micro(T0 - timedelta(seconds=10)), 15, 2, rv=5)
    cluster.put_faults = [(409, RIVAL)]
    renewer = new_renewer(cluster, clock)
    try:
        renewer.renew()
        raise AssertionError("conflict revealing a new holder must raise")
    except LeadershipLost:
        pass
    check_eq(cluster.methods(), ["GET", "PUT", "GET"],
             "after the conflict the fresh GET reveals the rival; no second PUT")
    check(renewer.is_leader is False, "leadership flag cleared")


def test_renew_second_conflict_propagates(cluster):
    clock = FakeClock(T0)
    cluster.seed(IDENTITY, micro(T0 - timedelta(seconds=60)),
                 micro(T0 - timedelta(seconds=10)), 15, 2, rv=5)
    cluster.put_faults = [(409, None), (409, None)]
    renewer = new_renewer(cluster, clock)
    try:
        renewer.renew()
        raise AssertionError("a second conflict must propagate as ApiError")
    except ApiError as err:
        check_eq(err.status_code, 409, "the second 409 is surfaced")
        check_eq(err.reason, "Conflict", "reason comes from the Status body")
    check_eq(cluster.methods(), ["GET", "PUT", "GET", "PUT"],
             "exactly one retry: GET, PUT(409), GET, PUT(409)")


def main():
    test_protected_docs_fixtures()
    print("ok  test_protected_docs_fixtures")
    for fn in (
        test_existing_client_get_and_status_error,
        test_existing_client_refuses_cross_origin_redirect,
        test_acquire_creates_when_absent,
        test_acquire_respects_active_holder,
        test_acquire_takes_over_expired,
        test_acquire_expiry_boundary,
        test_acquire_reclaims_own_lease,
        test_acquire_conflict_cedes,
        test_renew_happy_path,
        test_renew_conflict_retries_through_fresh_get,
        test_renew_detects_lost_leadership,
        test_renew_conflict_then_rival_holder,
        test_renew_second_conflict_propagates,
    ):
        with_cluster(fn)
        print(f"ok  {fn.__name__}")
    test_format_micro_time_pads_to_six_digits()
    print("ok  test_format_micro_time_pads_to_six_digits")
    print(f"PASS  {CHECKS} checks")


if __name__ == "__main__":
    main()
