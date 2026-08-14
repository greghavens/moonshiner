#!/usr/bin/env python3
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CREATE_ID = "Snapservice.Clusters.ProtectionGroups_create$Task"
GET_ID = "Snapservice.Clusters.ProtectionGroups_get"
CREATE_TARGET = "/api/snapservice/clusters/domain%20c8%2Fblue/protection-groups?vmw-task=true"
GET_TARGET = "/api/snapservice/clusters/domain%20c8%2Fblue/protection-groups/pg%2042%2Fblue"
PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def canonical_target(target):
    return PERCENT_ESCAPE.sub(lambda match: match.group(0).upper(), target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--scenario", choices=("create-401", "read-401"), required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    contract = json.loads((root / "docs" / "contract.json").read_text(encoding="utf-8"))
    allowed = {(item["method"], item["operationId"]) for item in contract["operations"]}
    if allowed != {("POST", CREATE_ID), ("GET", GET_ID)}:
        raise RuntimeError("mock and contract operation sets differ")

    state = {"create_count": 0, "expired_once": False}
    lock = threading.Lock()
    log_path = Path(args.log_file)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self._handle()

        def do_GET(self):
            self._handle()

        def _handle(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            target = self.path
            operation_id = None
            if (self.command == "POST"
                    and canonical_target(target) == canonical_target(CREATE_TARGET)):
                operation_id = CREATE_ID
            elif (self.command == "GET"
                    and canonical_target(target) == canonical_target(GET_TARGET)):
                operation_id = GET_ID

            entry = {
                "method": self.command,
                "target": target,
                "httpVersion": self.request_version,
                "operationId": operation_id,
                "headers": [[name, value] for name, value in self.headers.items()],
                "body": body.decode("utf-8"),
            }
            with lock:
                with log_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
                    stream.flush()

                token = self.headers.get("vmware-api-session-id")
                if operation_id == CREATE_ID:
                    if (args.scenario == "create-401"
                            and token == "access-old"
                            and not state["expired_once"]):
                        state["expired_once"] = True
                        self._unauthenticated()
                    elif state["create_count"] != 0:
                        self._json(409, {"error_type": "ALREADY_EXISTS", "messages": []})
                    elif ((args.scenario == "create-401" and token != "access-new")
                            or (args.scenario == "read-401" and token != "access-old")):
                        self._unauthenticated()
                    else:
                        state["create_count"] += 1
                        self._json(202, "pg 42/blue")
                elif operation_id == GET_ID:
                    if state["create_count"] != 1:
                        self._json(409, {"error_type": "NOT_CREATED", "messages": []})
                    elif (args.scenario == "read-401"
                            and token == "access-old"
                            and not state["expired_once"]):
                        state["expired_once"] = True
                        self._unauthenticated()
                    elif token == "access-new" and state["expired_once"]:
                        self._json(200, {
                            "snapshot_policies": [{
                                "name": "nested policy name",
                                "schedule": {"unit": "HOUR", "interval": 1},
                                "retention": {"unit": "DAY", "duration": 1},
                            }],
                            "name": "Nightly \"critical\"\nset",
                            "status": "ACTIVE",
                            "target_entities": {"vms": ["vm-101", "vm\\202"]},
                            "vms": ["vm-101", "vm\\202"],
                            "snapshots": [],
                            "locked": False,
                        })
                    else:
                        self._unauthenticated()
                else:
                    self._json(404, {"error_type": "NOT_FOUND", "messages": []})

        def _unauthenticated(self):
            self._json(401, {
                "error_type": "UNAUTHENTICATED",
                "messages": [{
                    "id": "snapservice.authentication.expired",
                    "default_message": "The session access token has expired.",
                    "args": [],
                }],
            })

        def _json(self, status, value):
            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Path(args.port_file).write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
