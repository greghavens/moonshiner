#!/usr/bin/env python3
"""Loopback-only VCF Installer mock constrained by docs/contract.json."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_OPERATIONS = {
    ("POST", "/v1/tokens"): "createToken",
    ("GET", "/v1/system/appliance-info"): "getApplianceInfo",
    ("PUT", "/v1/system/settings/depot"): "updateDepotSettings",
}

# `Connect-VcfInstallerServer` posts for a token and then probes this route to
# identify the appliance, all before any candidate code runs. That is the client
# library talking to the appliance, not the module under test talking to the API
# this fixture pins, so it is served here rather than added to the contract
# excerpt -- which is a verbatim subset of the published 9.0 specification and
# has to stay one.
HANDSHAKE_ROUTES = {("GET", "/v1/sddc-manager"): "sdkConnectionHandshake"}


def load_routes(contract_path: Path) -> dict[tuple[str, str], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes: dict[tuple[str, str], str] = {}
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                routes[(method.upper(), path)] = operation["operationId"]
    if routes != EXPECTED_OPERATIONS:
        raise RuntimeError(f"contract routes do not match the pinned fixture: {routes!r}")
    return routes


class VcfInstallerMock(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, routes, log_path, first_put_status):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.first_put_status = first_put_status
        self.log_lock = threading.Lock()
        self.depot_body = None
        self.effect_count = 0
        self.put_count = 0

    def append_log(self, record: dict) -> None:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfInstallerContractMock/9.0"
    sys_version = ""

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def _read_body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b""

    def _dispatch(self):
        split = urlsplit(self.path)
        method = self.command.upper()
        route = (method, split.path)
        raw_body = self._read_body()
        headers = {name.lower(): value for name, value in self.headers.items()}

        if route in HANDSHAKE_ROUTES:
            self._respond_and_log(
                status=200,
                payload={"id": "fixture-installer", "version": "9.0.0.0.24703748"},
                operation_id=HANDSHAKE_ROUTES[route],
                query=split.query,
                headers=headers,
                raw_body=raw_body,
                effect_applied=False,
            )
            return

        if route not in self.server.routes:
            self._respond_and_log(
                status=404,
                payload={"errorCode": "NOT_IN_CONTRACT", "message": "route not served"},
                operation_id=None,
                query=split.query,
                headers=headers,
                raw_body=raw_body,
                effect_applied=False,
            )
            return

        operation_id = self.server.routes[route]
        if operation_id == "createToken":
            self._respond_and_log(
                status=201,
                payload={
                    "accessToken": "fixture-access-token",
                    "refreshToken": {"id": "fixture-refresh-token"},
                },
                operation_id=operation_id,
                query=split.query,
                headers=headers,
                raw_body=raw_body,
                effect_applied=False,
            )
            return

        if operation_id == "getApplianceInfo":
            self._respond_and_log(
                status=200,
                payload={"role": "VcfInstaller", "version": "9.0.0.0.24703748"},
                operation_id=operation_id,
                query=split.query,
                headers=headers,
                raw_body=raw_body,
                effect_applied=False,
            )
            return

        try:
            parsed_body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond_and_log(
                status=400,
                payload={"errorCode": "INVALID_JSON", "message": "invalid JSON body"},
                operation_id=operation_id,
                query=split.query,
                headers=headers,
                raw_body=raw_body,
                effect_applied=False,
            )
            return

        self.server.put_count += 1
        status = self.server.first_put_status if self.server.put_count == 1 else 202

        # A 500 is deliberately ambiguous: the idempotent PUT has taken effect,
        # but the client sees a retryable failure. Other failures do not take
        # effect.
        effect_applied = False
        if status in (202, 500) and parsed_body != self.server.depot_body:
            effect_applied = True
            self.server.depot_body = parsed_body
            self.server.effect_count += 1

        if status == 500:
            payload = {
                "errorCode": "INTERNAL_SERVER_ERROR",
                "message": "retry the idempotent request",
            }
        elif status == 400:
            payload = {
                "errorCode": "BAD_REQUEST",
                "message": "do not retry this request",
            }
        elif status == 503:
            payload = {
                "errorCode": "SERVICE_UNAVAILABLE",
                "message": "do not retry this non-500 failure",
            }
        else:
            payload = parsed_body

        self._respond_and_log(
            status=status,
            payload=payload,
            operation_id=operation_id,
            query=split.query,
            headers=headers,
            raw_body=raw_body,
            effect_applied=effect_applied,
        )

    def _respond_and_log(
        self,
        *,
        status: int,
        payload: dict,
        operation_id: str | None,
        query: str,
        headers: dict[str, str],
        raw_body: bytes,
        effect_applied: bool,
    ) -> None:
        response = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.server.append_log(
            {
                "method": self.command.upper(),
                "path": urlsplit(self.path).path,
                "query": query,
                "operationId": operation_id,
                "headers": headers,
                "bodyUtf8": raw_body.decode("utf-8", errors="replace"),
                "bodyHex": raw_body.hex(),
                "status": status,
                "effectApplied": effect_applied,
                "effectCount": self.server.effect_count,
            }
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()
        self.close_connection = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument(
        "--first-put-status", type=int, choices=(400, 500, 503), default=500
    )
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = VcfInstallerMock(
        ("127.0.0.1", 0),
        Handler,
        routes=routes,
        log_path=args.log,
        first_put_status=args.first_put_status,
    )
    host, port = server.server_address
    args.ready.write_text(
        json.dumps({"baseUri": f"http://{host}:{port}", "port": port}),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
