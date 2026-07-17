"""Acceptance tests for the atlas_clusters package (MongoDB Atlas
Administration API v2 cluster reconciler).

A loopback HTTP mock speaks the wire contract pinned in docs/contract.json:
versioned dated media types (GET clusters uses 2024-08-05, create/update use
2024-10-23 — the mock 406s anything else, exactly like Atlas), the
ClusterDescription20240805 payload shape, stateName polling, and the
application/json ApiError envelope. No real Atlas, no credentials, no
wall-clock sleeps — waiting is injected and recorded.

Run: python3 test_atlas_clusters.py
Protected — do not modify this file or anything under docs/.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from atlas_clusters import AtlasClient, ClusterReconciler, AtlasApiError, ClusterStateError

GROUP_ID = "64f00dfeedfacecafe123abc"
TOKEN = "eyJhbGciOiJFUzUxMiJ9.fixture.py-cluster-poller"
GET_MEDIA = "application/vnd.atlas.2024-08-05+json"
WRITE_MEDIA = "application/vnd.atlas.2024-10-23+json"
ERROR_MEDIA = "application/json"

CLUSTERS_PATH = f"/api/atlas/v2/groups/{GROUP_ID}/clusters"


def desired_spec():
    return {
        "name": "shipping-prod",
        "clusterType": "REPLICASET",
        "mongoDBMajorVersion": "8.0",
        "replicationSpecs": [
            {
                "zoneName": "Zone 1",
                "regionConfigs": [
                    {
                        "providerName": "AWS",
                        "regionName": "US_EAST_1",
                        "priority": 7,
                        "electableSpecs": {
                            "instanceSize": "M30",
                            "nodeCount": 3,
                            "diskSizeGB": 40,
                        },
                    }
                ],
            }
        ],
    }


def server_view(state_name, instance_size="M30", node_count=3, disk_gb=40):
    """What Atlas echoes back: the desired projection plus server fields."""
    return {
        "id": "65feedcafe0123456789abcd",
        "groupId": GROUP_ID,
        "name": "shipping-prod",
        "clusterType": "REPLICASET",
        "mongoDBMajorVersion": "8.0",
        "mongoDBVersion": "8.0.11",
        "stateName": state_name,
        "createDate": "2026-07-17T09:00:00Z",
        "replicationSpecs": [
            {
                "id": "65feedcafe0123456789ab00",
                "zoneId": "65feedcafe0123456789ab01",
                "zoneName": "Zone 1",
                "regionConfigs": [
                    {
                        "providerName": "AWS",
                        "regionName": "US_EAST_1",
                        "priority": 7,
                        "electableSpecs": {
                            "instanceSize": instance_size,
                            "nodeCount": node_count,
                            "diskSizeGB": disk_gb,
                        },
                    }
                ],
            }
        ],
        "connectionStrings": {"standardSrv": "mongodb+srv://shipping-prod.fixture.mongodb.net"},
        "links": [{"href": "ignored", "rel": "self"}],
    }


def api_error(status, code, detail, params=None, reason=None):
    reasons = {400: "Bad Request", 404: "Not Found", 409: "Conflict", 500: "Internal Server Error"}
    return (
        status,
        ERROR_MEDIA,
        {
            "detail": detail,
            "error": status,
            "errorCode": code,
            "parameters": params or [],
            "reason": reason or reasons.get(status, "Error"),
        },
    )


class MockAtlas:
    """Ordered script of (status, content_type, body) responses; records
    every request. Enforces the documented dated media types: a request
    whose Accept doesn't name the current version of the resource gets 406,
    like real Atlas."""

    def __init__(self):
        self.script = []
        self.requests = []
        self.lock = threading.Lock()

        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                with mock.lock:
                    mock.requests.append(
                        {
                            "method": self.command,
                            "path": self.path,
                            "accept": self.headers.get("Accept"),
                            "content_type": self.headers.get("Content-Type"),
                            "authorization": self.headers.get("Authorization"),
                            "body": json.loads(raw) if raw else None,
                        }
                    )
                    expected = GET_MEDIA if self.command == "GET" else WRITE_MEDIA
                    if self.headers.get("Accept") != expected:
                        status, ctype, body = api_error(
                            406,
                            "INVALID_VERSION_DATE",
                            f"Accept {self.headers.get('Accept')!r} does not name the current version of this resource.",
                            reason="Not Acceptable",
                        )
                    elif mock.script:
                        status, ctype, body = mock.script.pop(0)
                    else:
                        status, ctype, body = api_error(500, "UNEXPECTED_REQUEST", "mock script exhausted")
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PATCH = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base_url = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def reset(self, script):
        with self.lock:
            self.script = list(script)
            self.requests = []

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make(mock):
    sleeps = []
    client = AtlasClient(base_url=mock.base_url, token=TOKEN)
    rec = ClusterReconciler(client, sleep=sleeps.append, poll_interval=2.0)
    return rec, sleeps


def test_create_flow(mock):
    mock.reset(
        [
            api_error(404, "CLUSTER_NOT_FOUND", "No cluster named shipping-prod exists in group " + GROUP_ID + "."),
            (201, WRITE_MEDIA, server_view("CREATING")),
            (200, GET_MEDIA, server_view("CREATING")),
            (200, GET_MEDIA, server_view("IDLE")),
        ]
    )
    rec, sleeps = make(mock)
    result = rec.reconcile(GROUP_ID, desired_spec())

    reqs = mock.requests
    assert len(reqs) == 4, f"expected GET, POST, GET, GET — saw {[(r['method'], r['path']) for r in reqs]}"

    probe = reqs[0]
    assert probe["method"] == "GET", probe
    assert probe["path"] == CLUSTERS_PATH + "/shipping-prod", probe["path"]
    assert probe["accept"] == GET_MEDIA, f"cluster GET must send Accept {GET_MEDIA}, got {probe['accept']}"
    assert probe["authorization"] == "Bearer " + TOKEN, probe["authorization"]

    create = reqs[1]
    assert create["method"] == "POST", create
    assert create["path"] == CLUSTERS_PATH, create["path"]
    assert create["accept"] == WRITE_MEDIA, f"create must send Accept {WRITE_MEDIA}, got {create['accept']}"
    assert (create["content_type"] or "").split(";")[0] == WRITE_MEDIA, (
        f"create must send Content-Type {WRITE_MEDIA}, got {create['content_type']}"
    )
    assert create["body"] == desired_spec(), (
        "create body must be exactly the desired versioned payload:\n%s" % json.dumps(create["body"], indent=2)
    )

    for poll in reqs[2:]:
        assert poll["method"] == "GET" and poll["path"] == CLUSTERS_PATH + "/shipping-prod", poll
        assert poll["accept"] == GET_MEDIA, poll["accept"]

    assert sleeps == [2.0, 2.0], f"one injected sleep of poll_interval before each poll, got {sleeps}"
    assert result["action"] == "created", result
    assert result["final"]["stateName"] == "IDLE"
    assert result["final"]["mongoDBVersion"] == "8.0.11"


def test_unchanged_flow(mock):
    mock.reset([(200, GET_MEDIA, server_view("IDLE"))])
    rec, sleeps = make(mock)
    result = rec.reconcile(GROUP_ID, desired_spec())
    assert result["action"] == "unchanged", result
    assert len(mock.requests) == 1, (
        "a cluster already matching the desired spec must trigger no write and no polling: %s"
        % [(r["method"], r["path"]) for r in mock.requests]
    )
    assert sleeps == [], sleeps


def test_update_flow(mock):
    mock.reset(
        [
            (200, GET_MEDIA, server_view("IDLE", instance_size="M10", node_count=3, disk_gb=20)),
            (200, WRITE_MEDIA, server_view("UPDATING", instance_size="M10", node_count=3, disk_gb=20)),
            (200, GET_MEDIA, server_view("UPDATING")),
            (200, GET_MEDIA, server_view("IDLE")),
        ]
    )
    rec, sleeps = make(mock)
    result = rec.reconcile(GROUP_ID, desired_spec())

    reqs = mock.requests
    assert len(reqs) == 4, [(r["method"], r["path"]) for r in reqs]
    upd = reqs[1]
    assert upd["method"] == "PATCH", upd
    assert upd["path"] == CLUSTERS_PATH + "/shipping-prod", upd["path"]
    assert upd["accept"] == WRITE_MEDIA and (upd["content_type"] or "").split(";")[0] == WRITE_MEDIA, (
        upd["accept"],
        upd["content_type"],
    )
    assert upd["body"] == {"replicationSpecs": desired_spec()["replicationSpecs"]}, (
        "PATCH body must contain only the replicationSpecs being changed:\n%s" % json.dumps(upd["body"], indent=2)
    )
    assert result["action"] == "updated", result
    assert result["final"]["stateName"] == "IDLE"
    assert sleeps == [2.0, 2.0], sleeps


def test_duplicate_name_conflict(mock):
    mock.reset(
        [
            api_error(404, "CLUSTER_NOT_FOUND", "No cluster named shipping-prod exists."),
            api_error(
                409,
                "DUPLICATE_CLUSTER_NAME",
                "A cluster named shipping-prod is already present in group " + GROUP_ID + ".",
                params=["shipping-prod"],
            ),
        ]
    )
    rec, sleeps = make(mock)
    try:
        rec.reconcile(GROUP_ID, desired_spec())
        raise SystemExit("FAIL: 409 DUPLICATE_CLUSTER_NAME must raise AtlasApiError")
    except AtlasApiError as e:
        assert e.status == 409, e.status
        assert e.error_code == "DUPLICATE_CLUSTER_NAME", e.error_code
        assert e.parameters == ["shipping-prod"], e.parameters
        assert e.reason == "Conflict", e.reason
        assert "shipping-prod" in e.detail, e.detail
        assert TOKEN not in str(e), "error text must not leak the bearer token"
    assert len(mock.requests) == 2, (
        "a 409 conflict is terminal — no retry, no polling: %s" % [(r["method"], r["path"]) for r in mock.requests]
    )
    assert sleeps == [], sleeps


def test_error_envelope_decode(mock):
    mock.reset(
        [
            api_error(
                400,
                "INVALID_ENUM_VALUE",
                "An invalid enumeration value M31 was specified.",
                params=["M31", "instanceSize"],
                reason="Bad Request",
            )
        ]
    )
    rec, _ = make(mock)
    try:
        rec.reconcile(GROUP_ID, desired_spec())
        raise SystemExit("FAIL: ApiError responses must raise AtlasApiError")
    except AtlasApiError as e:
        assert (e.status, e.error_code) == (400, "INVALID_ENUM_VALUE"), (e.status, e.error_code)
        assert e.detail == "An invalid enumeration value M31 was specified.", e.detail
        assert e.parameters == ["M31", "instanceSize"], e.parameters
        assert "INVALID_ENUM_VALUE" in str(e), str(e)


def test_terminal_state_stops_polling(mock):
    mock.reset(
        [
            api_error(404, "CLUSTER_NOT_FOUND", "No cluster named shipping-prod exists."),
            (201, WRITE_MEDIA, server_view("CREATING")),
            (200, GET_MEDIA, server_view("CREATING")),
            (200, GET_MEDIA, server_view("DELETING")),
            (200, GET_MEDIA, server_view("IDLE")),  # must never be fetched
        ]
    )
    rec, _ = make(mock)
    try:
        rec.reconcile(GROUP_ID, desired_spec())
        raise SystemExit("FAIL: a cluster that starts DELETING mid-wait must raise ClusterStateError")
    except ClusterStateError as e:
        assert e.state_name == "DELETING", e.state_name
        assert "shipping-prod" in str(e), str(e)
    assert len(mock.requests) == 4, (
        "polling must stop at the terminal DELETING observation: %s"
        % [(r["method"], r["path"]) for r in mock.requests]
    )


def test_cluster_vanishing_mid_poll_is_terminal(mock):
    mock.reset(
        [
            (200, GET_MEDIA, server_view("IDLE", instance_size="M10")),
            (200, WRITE_MEDIA, server_view("UPDATING", instance_size="M10")),
            api_error(404, "CLUSTER_NOT_FOUND", "No cluster named shipping-prod exists."),
            (200, GET_MEDIA, server_view("IDLE")),  # must never be fetched
        ]
    )
    rec, _ = make(mock)
    try:
        rec.reconcile(GROUP_ID, desired_spec())
        raise SystemExit("FAIL: a cluster that vanishes mid-wait must raise ClusterStateError")
    except ClusterStateError as e:
        assert e.state_name is None, "vanished cluster has no stateName; got %r" % (e.state_name,)
    assert len(mock.requests) == 3, [(r["method"], r["path"]) for r in mock.requests]


def test_docs_fixtures_parse():
    for name in ("docs/contract.json", "docs/official_sources.json"):
        with open(name, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        assert doc, name
    with open("docs/contract.json", "r", encoding="utf-8") as fh:
        contract = json.load(fh)
    assert contract["media_types"]["get_cluster"] == GET_MEDIA
    assert contract["media_types"]["create_update_cluster"] == WRITE_MEDIA
    assert contract["state_names"] == ["IDLE", "CREATING", "UPDATING", "DELETING", "REPAIRING"]


def main():
    mock = MockAtlas()
    tests = [
        test_create_flow,
        test_unchanged_flow,
        test_update_flow,
        test_duplicate_name_conflict,
        test_error_envelope_decode,
        test_terminal_state_stops_polling,
        test_cluster_vanishing_mid_poll_is_terminal,
    ]
    try:
        for t in tests:
            t(mock)
            print("ok  ", t.__name__)
        test_docs_fixtures_parse()
        print("ok   test_docs_fixtures_parse")
    finally:
        mock.close()
    print("all %d tests passed" % (len(tests) + 1))


if __name__ == "__main__":
    main()
