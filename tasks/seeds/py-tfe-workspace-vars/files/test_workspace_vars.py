"""Acceptance harness for the workspace-variable reconciler.

Runs a loopback fake Terraform Enterprise API pinning the wire contract in
docs/contract.json. No real TFE, no real credentials, no network.
Protected — do not modify. Run: python3 test_workspace_vars.py
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from tfe_client import TFEClient
import vars_reconciler as vr

TOKEN = "test-token-xyz789"  # dummy credential
WS = "ws-kVji3BF9ZNWpqGXp"
VARS_PATH = "/api/v2/workspaces/%s/vars" % WS

NOT_FOUND_DOC = {
    "errors": [
        {
            "status": "404",
            "title": "not found",
            "detail": "workspace not found, or user unauthorized to perform action",
        }
    ]
}


class FakeTFE:
    """Scripted loopback TFE: routes[(method, path)] -> [(status, body)]."""

    def __init__(self):
        self.requests = []
        self.routes = {}

    def route(self, method, path, status, body=None):
        self.routes.setdefault((method, path), []).append((status, body))

    def take(self, method, path):
        queue = self.routes.get((method, path))
        if not queue:
            return 599, {"errors": [{"status": "599", "title": "unscripted",
                                     "detail": "%s %s" % (method, path)}]}
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]


def make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            split = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            fake.requests.append({
                "method": self.command,
                "path": split.path,
                "query": split.query,
                "auth": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(raw.decode("utf-8")) if raw else None,
            })
            status, body = fake.take(self.command, split.path)
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response(status)
            if payload:
                self.send_header("Content-Type", "application/vnd.api+json")
                self.send_header("Content-Length", str(len(payload)))
            else:
                self.send_header("Content-Length", "0")
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        do_GET = do_POST = do_PATCH = do_DELETE = _serve

        def log_message(self, *args):
            pass

    return Handler


def with_server(test):
    fake = FakeTFE()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = TFEClient("http://127.0.0.1:%d" % server.server_address[1], TOKEN)
    try:
        test(fake, client)
    finally:
        server.shutdown()
        server.server_close()


def var_obj(var_id, key, value, description, category, hcl, sensitive):
    return {
        "id": var_id,
        "type": "vars",
        "attributes": {
            "key": key,
            "value": value,
            "description": description,
            "category": category,
            "hcl": hcl,
            "sensitive": sensitive,
        },
    }


def fixture_doc():
    return {"data": [
        var_obj("var-Region11", "region", "us-east-1", "primary region", "terraform", False, False),
        var_obj("var-DbPass22", "db_password", None, "", "terraform", False, True),
        var_obj("var-TfLog33", "TF_LOG", "info", "", "env", False, False),
        var_obj("var-Stale44", "RETIRED_FLAG", "1", "", "env", False, False),
    ]}


def desired(key, category, value="", description="", hcl=False, sensitive=False):
    return {"key": key, "category": category, "value": value,
            "description": description, "hcl": hcl, "sensitive": sensitive}


def identities(entries):
    return [(e["category"], e["key"]) for e in entries]


# ---------------------------------------------------------------- transport

def test_transport_headers_and_error_passthrough(fake, client):
    fake.route("GET", "/api/v2/ping", 200, {"data": []})
    status, body = client.request("GET", "/api/v2/ping")
    assert status == 200 and body == {"data": []}

    fake.route("POST", "/api/v2/ping", 201, {"data": {"id": "x"}})
    status, _ = client.request("POST", "/api/v2/ping", body={"data": {}})
    assert status == 201

    fake.route("GET", "/api/v2/missing", 404, NOT_FOUND_DOC)
    status, body = client.request("GET", "/api/v2/missing")
    assert status == 404, "transport must hand back non-2xx, not raise"
    assert body == NOT_FOUND_DOC

    get_req, post_req = fake.requests[0], fake.requests[1]
    assert get_req["auth"] == "Bearer " + TOKEN
    assert post_req["auth"] == "Bearer " + TOKEN
    assert post_req["content_type"] == "application/vnd.api+json"
    assert post_req["body"] == {"data": {}}


# -------------------------------------------------------------------- fetch

def test_fetch_sends_documented_request(fake, client):
    fake.route("GET", VARS_PATH, 200, fixture_doc())
    current = vr.fetch_workspace_vars(client, WS)

    assert len(fake.requests) == 1, "the list endpoint is not paginated: one GET"
    req = fake.requests[0]
    assert req["method"] == "GET"
    assert req["path"] == VARS_PATH
    assert req["query"] == "", "no query parameters are documented for this endpoint"
    assert req["auth"] == "Bearer " + TOKEN

    assert [v["key"] for v in current] == ["region", "db_password", "TF_LOG", "RETIRED_FLAG"]
    region = current[0]
    assert region["id"] == "var-Region11"
    assert region["category"] == "terraform"
    assert region["value"] == "us-east-1"
    assert region["hcl"] is False and region["sensitive"] is False
    assert region["description"] == "primary region"


def test_fetch_keeps_sensitive_value_unreadable(fake, client):
    fake.route("GET", VARS_PATH, 200, fixture_doc())
    current = vr.fetch_workspace_vars(client, WS)
    secret = current[1]
    assert secret["key"] == "db_password"
    assert secret["sensitive"] is True
    assert secret["value"] is None, "sensitive values are never exposed by the API"


def test_fetch_decodes_404_masking(fake, client):
    fake.route("GET", VARS_PATH, 404, NOT_FOUND_DOC)
    try:
        vr.fetch_workspace_vars(client, WS)
    except vr.TFEAPIError as err:
        assert err.status == 404
        assert err.errors[0]["title"] == "not found"
        assert "user unauthorized to perform action" in str(err), (
            "the 404-masks-authorization wording must be preserved"
        )
    else:
        raise AssertionError("a 404 error document must raise TFEAPIError")


# --------------------------------------------------------------------- plan

def current_from(doc):
    out = []
    for item in doc["data"]:
        entry = dict(item["attributes"])
        entry["id"] = item["id"]
        out.append(entry)
    return out


def test_plan_minimum_change_set(fake, client):
    current = current_from(fixture_doc())
    want = [
        desired("region", "terraform", "us-east-1", "primary region"),
        desired("db_password", "terraform", "hunter2", sensitive=True),
        desired("TF_LOG", "env", "debug"),
        {"key": "tags", "category": "terraform", "value": '["edge"]', "hcl": True},
    ]
    plan = vr.plan_changes(current, want)

    assert identities(plan["create"]) == [("terraform", "tags")]
    assert identities(plan["update"]) == [("env", "TF_LOG")]
    assert identities(plan["delete"]) == [("env", "RETIRED_FLAG")]

    tags = plan["create"][0]
    assert tags["hcl"] is True
    assert tags["sensitive"] is False, "omitted attributes take documented defaults"
    assert tags["description"] == ""

    log = plan["update"][0]
    assert log["id"] == "var-TfLog33"
    assert log["value"] == "debug"

    gone = plan["delete"][0]
    assert gone["id"] == "var-Stale44"


def test_plan_sensitive_metadata_rules(fake, client):
    current = [dict(id="var-DbPass22", key="db_password", value=None,
                    description="", category="terraform", hcl=False, sensitive=True)]

    in_sync = vr.plan_changes(current, [desired("db_password", "terraform", "hunter2", sensitive=True)])
    assert in_sync["create"] == [] and in_sync["update"] == [] and in_sync["delete"] == [], (
        "matching metadata means in sync; the secret value must never be compared"
    )

    rotated = vr.plan_changes(current, [desired("db_password", "terraform", "hunter2",
                                                description="rotated 2026-07", sensitive=True)])
    assert identities(rotated["update"]) == [("terraform", "db_password")]
    assert rotated["update"][0]["id"] == "var-DbPass22"
    assert rotated["update"][0]["value"] == "hunter2", (
        "a sensitive rewrite must carry the full desired attributes"
    )

    downgraded = vr.plan_changes(current, [desired("db_password", "terraform", "plain")])
    assert identities(downgraded["update"]) == [("terraform", "db_password")]
    assert downgraded["update"][0]["sensitive"] is False

    plain = [dict(id="var-Plain66", key="api_host", value="internal",
                  description="", category="env", hcl=False, sensitive=False)]
    upgraded = vr.plan_changes(plain, [desired("api_host", "env", "internal", sensitive=True)])
    assert identities(upgraded["update"]) == [("env", "api_host")]


def test_plan_category_is_identity(fake, client):
    current = [dict(id="var-EnvReg55", key="REGION", value="us-east-1",
                    description="", category="env", hcl=False, sensitive=False)]
    plan = vr.plan_changes(current, [desired("REGION", "terraform", "us-east-1")])
    assert identities(plan["create"]) == [("terraform", "REGION")]
    assert identities(plan["delete"]) == [("env", "REGION")]
    assert plan["update"] == [], "same key in another category is a different variable"


# -------------------------------------------------------------------- apply

def two_create_plan():
    current = [
        dict(id="var-Region11", key="region", value="us-east-1",
             description="primary region", category="terraform", hcl=False, sensitive=False),
        dict(id="var-Stale44", key="RETIRED_FLAG", value="1",
             description="", category="env", hcl=False, sensitive=False),
    ]
    want = [
        desired("region", "terraform", "eu-west-1", "primary region"),
        desired("DATADOG_SITE", "env", "datadoghq.eu"),
        {"key": "tags", "category": "terraform", "value": '["edge"]', "hcl": True},
    ]
    return vr.plan_changes(current, want)


def test_apply_request_shapes_and_order(fake, client):
    fake.route("POST", VARS_PATH, 201, {"data": {"id": "var-New01", "type": "vars"}})
    fake.route("POST", VARS_PATH, 201, {"data": {"id": "var-New02", "type": "vars"}})
    fake.route("PATCH", VARS_PATH + "/var-Region11", 200,
               {"data": {"id": "var-Region11", "type": "vars"}})
    fake.route("DELETE", VARS_PATH + "/var-Stale44", 204)

    report = vr.apply_plan(client, WS, two_create_plan())
    assert report == [
        "create env/DATADOG_SITE",
        "create terraform/tags",
        "update terraform/region",
        "delete env/RETIRED_FLAG",
    ]

    ops = [(r["method"], r["path"]) for r in fake.requests]
    assert ops == [
        ("POST", VARS_PATH),
        ("POST", VARS_PATH),
        ("PATCH", VARS_PATH + "/var-Region11"),
        ("DELETE", VARS_PATH + "/var-Stale44"),
    ], "creates, then updates, then deletes, each sorted by (category, key)"

    first_create = fake.requests[0]
    assert first_create["content_type"] == "application/vnd.api+json"
    assert first_create["body"] == {"data": {"type": "vars", "attributes": {
        "key": "DATADOG_SITE", "value": "datadoghq.eu", "description": "",
        "category": "env", "hcl": False, "sensitive": False,
    }}}

    second_create = fake.requests[1]
    assert second_create["body"]["data"]["attributes"]["key"] == "tags"
    assert second_create["body"]["data"]["attributes"]["hcl"] is True

    patch = fake.requests[2]
    assert patch["content_type"] == "application/vnd.api+json"
    assert patch["body"]["data"]["type"] == "vars"
    assert patch["body"]["data"]["id"] == "var-Region11", (
        "PATCH bodies repeat the variable id in data.id"
    )
    assert patch["body"]["data"]["attributes"]["value"] == "eu-west-1"

    delete = fake.requests[3]
    assert delete["body"] is None, "DELETE sends no body"


def test_apply_partial_failure_decodes_errors(fake, client):
    fake.route("POST", VARS_PATH, 201, {"data": {"id": "var-New01", "type": "vars"}})
    fake.route("POST", VARS_PATH, 422, {"errors": [{
        "status": "422", "title": "invalid attribute",
        "detail": "Key has already been taken",
    }]})

    try:
        vr.apply_plan(client, WS, two_create_plan())
    except vr.TFEAPIError as err:
        assert err.status == 422
        assert err.errors == [{"status": "422", "title": "invalid attribute",
                               "detail": "Key has already been taken"}]
        assert "Key has already been taken" in str(err)
        assert err.completed == ["create env/DATADOG_SITE"], (
            "the error must report which operations already completed"
        )
    else:
        raise AssertionError("a 422 error document must raise TFEAPIError")

    methods = [r["method"] for r in fake.requests]
    assert methods == ["POST", "POST"], "apply must stop at the first failure"


# ---------------------------------------------------------------- reconcile

def test_reconcile_dry_run_makes_no_writes(fake, client):
    fake.route("GET", VARS_PATH, 200, fixture_doc())
    want = [
        desired("region", "terraform", "us-east-1", "primary region"),
        desired("db_password", "terraform", "hunter2", sensitive=True),
        desired("TF_LOG", "env", "info"),
    ]
    out = vr.reconcile(client, WS, want, dry_run=True)
    assert identities(out["plan"]["delete"]) == [("env", "RETIRED_FLAG")]
    assert out["plan"]["create"] == [] and out["plan"]["update"] == []
    assert out["report"] == []
    assert [r["method"] for r in fake.requests] == ["GET"], (
        "a dry run must not issue any write request"
    )


def test_reconcile_end_to_end(fake, client):
    fake.route("GET", VARS_PATH, 200, fixture_doc())
    fake.route("POST", VARS_PATH, 201, {"data": {"id": "var-New01", "type": "vars"}})
    fake.route("PATCH", VARS_PATH + "/var-TfLog33", 200,
               {"data": {"id": "var-TfLog33", "type": "vars"}})
    fake.route("DELETE", VARS_PATH + "/var-Stale44", 204)
    want = [
        desired("region", "terraform", "us-east-1", "primary region"),
        desired("db_password", "terraform", "hunter2", sensitive=True),
        desired("TF_LOG", "env", "debug"),
        {"key": "tags", "category": "terraform", "value": '["edge"]', "hcl": True},
    ]
    out = vr.reconcile(client, WS, want)
    assert out["report"] == [
        "create terraform/tags",
        "update env/TF_LOG",
        "delete env/RETIRED_FLAG",
    ]
    ops = [(r["method"], r["path"]) for r in fake.requests]
    assert ops == [
        ("GET", VARS_PATH),
        ("POST", VARS_PATH),
        ("PATCH", VARS_PATH + "/var-TfLog33"),
        ("DELETE", VARS_PATH + "/var-Stale44"),
    ]


TESTS = [
    test_transport_headers_and_error_passthrough,
    test_fetch_sends_documented_request,
    test_fetch_keeps_sensitive_value_unreadable,
    test_fetch_decodes_404_masking,
    test_plan_minimum_change_set,
    test_plan_sensitive_metadata_rules,
    test_plan_category_is_identity,
    test_apply_request_shapes_and_order,
    test_apply_partial_failure_decodes_errors,
    test_reconcile_dry_run_makes_no_writes,
    test_reconcile_end_to_end,
]


def main():
    for test in TESTS:
        with_server(test)
        print("ok  %s" % test.__name__)
    print("all %d tests passed" % len(TESTS))


if __name__ == "__main__":
    main()
