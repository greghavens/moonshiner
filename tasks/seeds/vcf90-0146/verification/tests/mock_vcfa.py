#!/usr/bin/env python3
"""Contract-pinned loopback server for the two VCF Automation operations."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def _tracker(status: str, progress: int, message: str) -> dict[str, object]:
    return {
        "progress": progress,
        "message": message,
        "status": status,
        "resources": [],
        "name": "Create integration",
        "id": "req-001",
        "selfLink": "/iaas/api/request-tracker/req-001",
    }


class State:
    def __init__(self, contract_path: Path, log_path: Path, scenario: str) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.routes = {
            (operation["method"], operation["path"]): operation["name"]
            for operation in contract["operations"]
        }
        expected = {
            ("POST", "/iaas/api/integrations"): "Create Integration Async",
            ("GET", "/iaas/api/request-tracker/{id}"): "Get Request Tracker",
        }
        if self.routes != expected:
            raise ValueError("mock contract does not contain exactly the supported operations")
        self.log_path = log_path
        self.scenario = scenario
        self.poll_count = 0
        self.sequence = 0
        self.lock = threading.Lock()

    def record(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        split = urlsplit(handler.path)
        selected_headers = {}
        for name in ("Authorization", "Accept", "Content-Type"):
            value = handler.headers.get(name)
            if value is not None:
                selected_headers[name.lower()] = value

        with self.lock:
            self.sequence += 1
            entry = {
                "sequence": self.sequence,
                "method": handler.command,
                "target": handler.path,
                "path": split.path,
                "query": parse_qs(split.query, keep_blank_values=True),
                "headers": selected_headers,
                "bodyText": body.decode("utf-8") if body else "",
                "body": json.loads(body) if body else None,
            }
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: "Server"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        split = urlsplit(self.path)
        if ("POST", split.path) not in self.server.state.routes:
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.state.record(self, body)
        self._json(202, _tracker("INPROGRESS", 0, "Queued"))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        split = urlsplit(self.path)
        prefix = "/iaas/api/request-tracker/"
        template = ("GET", "/iaas/api/request-tracker/{id}")
        request_id = split.path[len(prefix) :] if split.path.startswith(prefix) else ""
        if template not in self.server.state.routes or request_id != "req-001":
            self.send_error(404)
            return

        self.server.state.record(self, b"")
        with self.server.state.lock:
            self.server.state.poll_count += 1
            poll_count = self.server.state.poll_count

        if self.server.state.scenario == "unknown":
            payload = _tracker("CANCELLED", 10, "Unexpected state")
        elif poll_count == 1:
            payload = _tracker("INPROGRESS", 40, "Provisioning")
        elif self.server.state.scenario == "failure":
            payload = _tracker("FAILED", 40, "Provisioning failed")
        else:
            payload = _tracker("FINISHED", 100, "Created")
        self._json(200, payload)


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], state: State) -> None:
        super().__init__(address, Handler)
        self.state = state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=("success", "failure", "unknown"),
        default="success",
    )
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    state = State(args.contract, args.log, args.scenario)
    server = Server(("127.0.0.1", 0), state)
    print(json.dumps({"baseUrl": f"http://127.0.0.1:{server.server_port}"}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
