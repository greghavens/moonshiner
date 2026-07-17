# Loopback fake SDDC Manager for the VcfInventory acceptance harness.
# Binds 127.0.0.1 on an ephemeral port, writes the port to argv[1]
# (atomically), then serves until killed by the harness.
#
# Speaks the Tokens/Domains/Clusters/Datastores subset pinned in
# docs/contract.json, records every request, and offers control endpoints:
#
#   GET  /__log__              -> [{method, path, query, auth, ctype, body}, ...]
#   POST /__reset_log__        -> clears the request log
#   POST /__expire__           -> invalidates all issued access tokens
#   POST /__revoke_refresh__   -> makes the refresh token unknown (404 on PATCH)
#   POST /__restore_refresh__  -> makes the refresh token valid again
#   POST /__mode__             -> {"forbidden": bool, "fail": bool}
#
# Element order in every collection response flips on each call so a client
# that does not sort its output cannot produce stable JSON.
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

USERNAME = "svc-inventory"
PASSWORD = "dummy-pass-77c1e0"  # dummy; never a real credential
REFRESH_ID = "rt-55aa-4b1c-dummy"

DOMAINS = [
    {"id": "d0a2c9f4-1b2e-4c5d-9a01-3f6b8e7c5a10", "name": "sfo-m01",
     "type": "MANAGEMENT", "status": "ACTIVE"},
    {"id": "b7e41d80-6f2c-49a3-8d15-c09e2a4f7b22", "name": "sfo-w01",
     "type": "VI", "status": "ACTIVE"},
]

CLUSTERS = [
    {
        "id": "c66f2b8e-04d1-4a3b-9c77-e5a8f1d20b94",
        "name": "sfo-m01-cl01",
        "status": "ACTIVE",
        "isDefault": True,
        "isStretched": False,
        "primaryDatastoreName": "sfo-m01-cl01-ds-vsan01",
        "primaryDatastoreType": "VSAN",
        "domainId": DOMAINS[0]["id"],
        "hosts": [
            {"id": "3a7f0c92-51e8-4b6d-a294-80c1f5d7e638",
             "fqdn": "esx02.sfo.rainpole.io", "ipAddress": "10.0.10.102", "azName": "az1"},
            {"id": "1f4b8d20-93c6-47a1-b5e9-62d0a8c4f715",
             "fqdn": "esx01.sfo.rainpole.io", "ipAddress": "10.0.10.101", "azName": "az1"},
            {"id": "8e2d5a41-07b9-4c38-96f2-d41c0b7e9a53",
             "fqdn": "esx03.sfo.rainpole.io", "ipAddress": "10.0.10.103", "azName": "az1"},
        ],
    },
    {
        "id": "9a3d81f5-2c60-4e97-b48a-16f7d0c3e871",
        "name": "sfo-w01-cl01",
        "status": "ACTIVE",
        "isDefault": True,
        "isStretched": True,
        "primaryDatastoreName": "sfo-w01-cl01-ds-esa01",
        "primaryDatastoreType": "VSAN_ESA",
        "domainId": DOMAINS[1]["id"],
        "hosts": [
            {"id": "b90c4e73-68a2-4d15-8f3b-a75e1d09c246",
             "fqdn": "esx06.sfo.rainpole.io", "ipAddress": "10.0.20.106", "azName": "az1"},
            {"id": "5d18f6a4-c3b7-49e0-a821-96c4e0d2f533",
             "fqdn": "esx05.sfo.rainpole.io", "ipAddress": "10.0.20.105", "azName": "az1"},
        ],
    },
    {
        "id": "4be09c17-d582-4f36-a1c9-70e8b5d4f233",
        "name": "sfo-w01-cl02",
        "status": "EXPANDING",
        "isDefault": False,
        "isStretched": False,
        "primaryDatastoreName": "sfo-w01-cl02-ds-nfs01",
        "primaryDatastoreType": "NFS",
        "domainId": DOMAINS[1]["id"],
        "hosts": [
            {"id": "0f9e4d21-6c3a-48b5-8a17-d2e90c4b7f68",
             "fqdn": "esx07.sfo.rainpole.io", "ipAddress": "10.0.20.107", "azName": "az2"},
        ],
    },
]

DATASTORES = {
    "c66f2b8e-04d1-4a3b-9c77-e5a8f1d20b94": [
        {"id": "ds-71c0e8b5", "name": "sfo-m01-cl01-ds-nfs01", "datastoreType": "NFS",
         "totalCapacityGB": 4096.0, "freeCapacityGB": 2210.5, "vmCount": 4},
        {"id": "ds-29a4f6d1", "name": "sfo-m01-cl01-ds-vsan01", "datastoreType": "VSAN",
         "totalCapacityGB": 18432.0, "freeCapacityGB": 9016.25, "vmCount": 37},
    ],
    "9a3d81f5-2c60-4e97-b48a-16f7d0c3e871": [
        {"id": "ds-5b3e0c97", "name": "sfo-w01-cl01-ds-esa01", "datastoreType": "VSAN_ESA",
         "totalCapacityGB": 24576.0, "freeCapacityGB": 20112.75, "vmCount": 12},
    ],
    "4be09c17-d582-4f36-a1c9-70e8b5d4f233": [
        {"id": "ds-e84d2f60", "name": "sfo-w01-cl02-ds-nfs01", "datastoreType": "NFS",
         "totalCapacityGB": 8192.0, "freeCapacityGB": 7955.0, "vmCount": 1},
    ],
}

STATE = {
    "valid_tokens": set(),
    "issued": 0,
    "refresh_valid": True,
    "forbidden": False,
    "fail": False,
    "calls": {},   # path -> count, drives order flipping
}
LOG = []


def error_body(code, message, token):
    return {"errorCode": code, "message": message, "referenceToken": token}


def issue_token():
    STATE["issued"] += 1
    token = f"at-{STATE['issued']}-c40e5b1d77"
    STATE["valid_tokens"].add(token)
    return token


def flip(path, items):
    """Deterministically scramble collection order between calls."""
    n = STATE["calls"].get(path, 0)
    STATE["calls"][path] = n + 1
    return list(items) if n % 2 == 0 else list(reversed(items))


def flip_paged(path, params, items):
    """Scramble order per ENUMERATION, not per page: the orientation advances
    when page 0 is requested and stays fixed for that enumeration's later
    pages, so paging stays self-consistent while repeat enumerations see a
    different order."""
    number = int(params.get("pageNumber", ["0"])[0])
    if number == 0:
        STATE["calls"][path] = STATE["calls"].get(path, 0) + 1
    n = STATE["calls"].get(path, 1)
    return list(items) if (n - 1) % 2 == 0 else list(reversed(items))


def paginate(elements, params):
    size = int(params.get("pageSize", ["100"])[0])
    number = int(params.get("pageNumber", ["0"])[0])
    total = len(elements)
    pages = max(1, -(-total // size))
    return {
        "elements": elements[number * size:(number + 1) * size],
        "pageMetadata": {
            "pageNumber": number,
            "pageSize": size,
            "totalElements": total,
            "totalPages": pages,
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        if payload is None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        req = {
            "method": self.command,
            "path": parsed.path,
            "query": {k: v[0] for k, v in parse_qs(parsed.query).items()},
            "auth": self.headers.get("Authorization"),
            "ctype": self.headers.get("Content-Type"),
            "body": body.decode("utf-8", "replace"),
        }
        if not parsed.path.startswith("/__"):
            LOG.append(req)
        return req, parsed

    def _authorized(self, req):
        auth = req["auth"] or ""
        return auth.startswith("Bearer ") and auth[len("Bearer "):] in STATE["valid_tokens"]

    def _collection(self, req, parsed, path, elements):
        if not self._authorized(req):
            self._send(401, error_body("UNAUTHORIZED", "Authentication required", "AUTH01"))
            return
        if STATE["forbidden"]:
            self._send(403, error_body("FORBIDDEN", "The token role does not allow this operation", "F0RB1D"))
            return
        if STATE["fail"]:
            self._send(500, error_body("VCF_SYSTEM_ERROR", "Internal server error", "SRV5XX"))
            return
        params = parse_qs(parsed.query)
        self._send(200, paginate(flip_paged(path, params, elements), params))

    def do_GET(self):
        req, parsed = self._record()
        path = parsed.path
        if path == "/__log__":
            self._send(200, LOG)
        elif path == "/v1/domains":
            elements = DOMAINS
            params = parse_qs(parsed.query)
            if "type" in params:
                elements = [d for d in elements if d["type"] == params["type"][0]]
            self._collection(req, parsed, path, elements)
        elif path == "/v1/clusters":
            elements = CLUSTERS
            params = parse_qs(parsed.query)
            if "domainId" in params:
                elements = [c for c in elements if c["domainId"] == params["domainId"][0]]
            if "isStretched" in params:
                want = params["isStretched"][0] == "true"
                elements = [c for c in elements if c["isStretched"] == want]
            stripped = [{k: v for k, v in c.items() if k != "domainId"} for c in elements]
            self._collection(req, parsed, path, stripped)
        elif path.startswith("/v1/clusters/") and path.endswith("/datastores"):
            cluster_id = path[len("/v1/clusters/"):-len("/datastores")]
            if not self._authorized(req):
                self._send(401, error_body("UNAUTHORIZED", "Authentication required", "AUTH01"))
            elif cluster_id not in DATASTORES:
                self._send(404, error_body("CLUSTER_NOT_FOUND", "Cluster not found", "CLU404"))
            else:
                self._send(200, flip(path, DATASTORES[cluster_id]))
        else:
            self._send(404, error_body("NOT_FOUND", "No such operation", "N0R0UT"))

    def do_POST(self):
        req, parsed = self._record()
        path = parsed.path
        if path == "/__reset_log__":
            LOG.clear()
            self._send(200, {"ok": True})
        elif path == "/__expire__":
            STATE["valid_tokens"].clear()
            self._send(200, {"ok": True})
        elif path == "/__revoke_refresh__":
            STATE["refresh_valid"] = False
            self._send(200, {"ok": True})
        elif path == "/__restore_refresh__":
            STATE["refresh_valid"] = True
            self._send(200, {"ok": True})
        elif path == "/__mode__":
            mode = json.loads(req["body"] or "{}")
            STATE["forbidden"] = bool(mode.get("forbidden", False))
            STATE["fail"] = bool(mode.get("fail", False))
            self._send(200, {"ok": True})
        elif path == "/v1/tokens":
            try:
                spec = json.loads(req["body"])
            except ValueError:
                spec = {}
            if spec.get("username") != USERNAME or spec.get("password") != PASSWORD:
                self._send(400, error_body("INVALID_CREDENTIALS", "Invalid credentials provided", "L0G1N1"))
                return
            self._send(201, {"accessToken": issue_token(), "refreshToken": {"id": REFRESH_ID}})
        else:
            self._send(404, error_body("NOT_FOUND", "No such operation", "N0R0UT"))

    def do_PATCH(self):
        req, parsed = self._record()
        if parsed.path != "/v1/tokens/access-token/refresh":
            self._send(404, error_body("NOT_FOUND", "No such operation", "N0R0UT"))
            return
        if req["body"] != json.dumps(REFRESH_ID):
            self._send(400, error_body(
                "BAD_REQUEST",
                "Request body must be the refresh token id as a JSON string", "R3FR01"))
            return
        if not STATE["refresh_valid"]:
            self._send(404, error_body("REFRESH_TOKEN_NOT_FOUND", "The refresh token is unknown or revoked", "R3FR44"))
            return
        STATE["valid_tokens"].clear()
        self._send(200, issue_token())


def main():
    port_file = sys.argv[1]
    server = HTTPServer(("127.0.0.1", 0), Handler)
    tmp = port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(server.server_address[1]))
    os.replace(tmp, port_file)
    server.serve_forever()


if __name__ == "__main__":
    main()
