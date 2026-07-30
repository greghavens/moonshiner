#!/usr/bin/env python3
"""Contract-pinned loopback vCenter for the protected PowerShell verifier.

The server implements only the five operations named by docs/contract.json.
It writes one JSON object per request to the supplied JSONL log. The namespace
list made with OLD_SESSION is held until the verifier creates release_old, so
credential handoff and deferred logout can be observed deterministically.
"""

import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


OLD_USER = "svc-vks"
OLD_PASSWORD = "dummy-old-41f6"
NEW_USER = "svc-vks"
NEW_PASSWORD = "dummy-new-92ab"
OLD_SESSION = "session-old-1f6d4a"
NEW_SESSION = "session-new-8c0e27"
SUPERVISOR = "domain-c8:supervisor-7ca91"


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, log_file, gate_dir):
        super().__init__(address, handler)
        self.log_file = Path(log_file)
        self.gate_dir = Path(gate_dir)
        self.state_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.sequence = 0
        self.valid_sessions = set()
        self.settings = {
            "certificate_dns_names": ["api.platform.example.test"],
            "namespace_api_fairness_enabled": True,
        }

    def record(self, request):
        with self.log_lock:
            self.sequence += 1
            request["sequence"] = self.sequence
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(request, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Vcf91ContractMock/1"

    def log_message(self, *_args):
        pass

    def _read_and_record(self):
        parsed = urlparse(self.path)
        content_length = self.headers.get("Content-Length")
        length = int(content_length or "0")
        body = self.rfile.read(length) if length else b""
        request = {
            "method": self.command,
            "path": parsed.path,
            "query": parsed.query,
            "authorization": self.headers.get("Authorization"),
            "session": self.headers.get("vmware-api-session-id"),
            "accept": self.headers.get("Accept"),
            "content_type": self.headers.get("Content-Type"),
            "content_length": content_length,
            "body": body.decode("utf-8", "replace"),
        }
        self.server.record(request)
        return request, parsed

    def _send(self, status, payload=None):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status, message):
        self._send(
            status,
            {
                "error_type": "MOCK_CONTRACT_ERROR",
                "messages": [{"default_message": message}],
            },
        )

    def _session_is_valid(self, token):
        with self.server.state_lock:
            return token in self.server.valid_sessions

    def _require_session(self, request):
        token = request["session"]
        if not token or not self._session_is_valid(token):
            self._error(401, "A valid API session is required")
            return None
        return token

    @staticmethod
    def _decode_basic(value):
        if not value or not value.startswith("Basic "):
            return None, None
        try:
            raw = base64.b64decode(value[6:], validate=True).decode("utf-8")
            return raw.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return None, None

    def do_POST(self):
        request, parsed = self._read_and_record()
        if parsed.path != "/api/session" or parsed.query:
            self._error(404, "No such contract operation")
            return

        username, password = self._decode_basic(request["authorization"])
        if (username, password) == (OLD_USER, OLD_PASSWORD):
            token = OLD_SESSION
        elif (username, password) == (NEW_USER, NEW_PASSWORD):
            token = NEW_SESSION
        else:
            self._error(401, "Authentication failed")
            return
        with self.server.state_lock:
            self.server.valid_sessions.add(token)
        self._send(201, token)

    def do_DELETE(self):
        request, parsed = self._read_and_record()
        if parsed.path != "/api/session" or parsed.query:
            self._error(404, "No such contract operation")
            return
        token = self._require_session(request)
        if token is None:
            return
        with self.server.state_lock:
            self.server.valid_sessions.discard(token)
        self._send(204)

    def do_GET(self):
        request, parsed = self._read_and_record()
        token = self._require_session(request)
        if token is None:
            return
        if parsed.query:
            self._error(400, "The selected operation has no query parameters")
            return

        if parsed.path == "/api/vcenter/namespaces/instances/v2":
            if token == OLD_SESSION:
                received = self.server.gate_dir / "old_received"
                received.write_text("received\n", encoding="utf-8")
                deadline = time.monotonic() + 20
                release = self.server.gate_dir / "release_old"
                while not release.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                if not release.exists():
                    self._error(504, "Verifier did not release held request")
                    return
            self._send(
                200,
                [
                    {
                        "supervisor": SUPERVISOR,
                        "namespace": "payments-dev",
                        "description": "Payments VKS development namespace",
                        "config_status": "RUNNING",
                        "stats": {
                            "cpu_used": 480,
                            "memory_used": 2048,
                            "storage_used": 8192,
                        },
                    },
                    {
                        "supervisor": SUPERVISOR,
                        "namespace": "orders-prod",
                        "description": "Orders VKS production namespace",
                        "config_status": "RUNNING",
                        "stats": {
                            "cpu_used": 910,
                            "memory_used": 4096,
                            "storage_used": 16384,
                        },
                    },
                ],
            )
            return

        prefix = "/api/vcenter/namespace-management/supervisors/"
        suffix = "/workloads/kube-api-server-settings"
        if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
            supervisor = unquote(parsed.path[len(prefix) : -len(suffix)])
            if supervisor != SUPERVISOR:
                self._error(404, "Supervisor not found")
                return
            with self.server.state_lock:
                payload = dict(self.server.settings)
            self._send(200, payload)
            return

        self._error(404, "No such contract operation")

    def do_PATCH(self):
        request, parsed = self._read_and_record()
        token = self._require_session(request)
        if token is None:
            return
        if parsed.query:
            self._error(400, "The selected operation has no query parameters")
            return

        prefix = "/api/vcenter/namespace-management/supervisors/"
        suffix = "/workloads/kube-api-server-settings"
        if not (parsed.path.startswith(prefix) and parsed.path.endswith(suffix)):
            self._error(404, "No such contract operation")
            return
        supervisor = unquote(parsed.path[len(prefix) : -len(suffix)])
        if supervisor != SUPERVISOR:
            self._error(404, "Supervisor not found")
            return
        if not (request["content_type"] or "").lower().startswith("application/json"):
            self._error(415, "UpdateSpec must use application/json")
            return
        try:
            update = json.loads(request["body"])
        except json.JSONDecodeError:
            self._error(400, "UpdateSpec must be JSON")
            return
        allowed = {
            "certificate_dns_names_to_add_list",
            "certificate_dns_names_to_remove_list",
            "namespace_api_fairness_enabled",
        }
        if not isinstance(update, dict) or not update or not set(update).issubset(allowed):
            self._error(400, "Invalid UpdateSpec")
            return
        with self.server.state_lock:
            names = list(self.server.settings["certificate_dns_names"])
            for name in update.get("certificate_dns_names_to_add_list", []):
                if name not in names:
                    names.append(name)
            for name in update.get("certificate_dns_names_to_remove_list", []):
                if name in names:
                    names.remove(name)
            self.server.settings["certificate_dns_names"] = names
            if "namespace_api_fairness_enabled" in update:
                self.server.settings["namespace_api_fairness_enabled"] = update[
                    "namespace_api_fairness_enabled"
                ]
        self._send(204)


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: mock_vcenter.py PORT_FILE LOG_FILE GATE_DIR")
    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    gate_dir = Path(sys.argv[3])
    gate_dir.mkdir(parents=True, exist_ok=True)
    log_file.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, log_file, gate_dir)
    temporary = port_file.with_suffix(port_file.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="utf-8")
    os.replace(temporary, port_file)
    server.serve_forever()


if __name__ == "__main__":
    main()
