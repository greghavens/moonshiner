#!/usr/bin/env python3
"""Contract-pinned loopback NSX Policy collection server for the verifier."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


OPERATIONS = {
    "/policy/api/v1/infra/tier-1s": {
        "operationId": "ListTier1",
        "pages": {
            "": (
                [
                    {
                        "id": "t1-z",
                        "display_name": "core",
                        "path": "/infra/tier-1s/t1-z",
                        "tier0_path": "/infra/tier-0s/provider-b",
                        "ha_mode": "ACTIVE_STANDBY",
                    },
                    {
                        "id": "t1-b",
                        "display_name": "Core",
                        "path": "/infra/tier-1s/t1-b",
                        "tier0_path": "/infra/tier-0s/provider-a",
                        "ha_mode": "ACTIVE_ACTIVE",
                    },
                ],
                "tier1:2",
            ),
            "tier1:2": (
                [
                    {
                        "id": "t1-a",
                        "display_name": "Core",
                        "path": "/infra/tier-1s/t1-a",
                        "tier0_path": None,
                        "ha_mode": None,
                    },
                    {
                        "id": "t1-e",
                        "display_name": "edge",
                        "path": "/infra/tier-1s/t1-e",
                        "tier0_path": "/infra/tier-0s/provider-a",
                        "ha_mode": "ACTIVE_STANDBY",
                    },
                ],
                None,
            ),
        },
    },
    "/policy/api/v1/infra/segments": {
        "operationId": "ListAllInfraSegments",
        "pages": {
            "": (
                [
                    {
                        "id": "seg-z",
                        "display_name": "app",
                        "path": "/infra/segments/seg-z",
                        "connectivity_path": "/infra/tier-1s/t1-z",
                        "transport_zone_path": "/infra/sites/default/enforcement-points/default/transport-zones/overlay",
                        "admin_state": "UP",
                    },
                    {
                        "id": "seg-b",
                        "display_name": "App",
                        "path": "/infra/segments/seg-b",
                        "connectivity_path": "/infra/tier-1s/t1-b",
                        "transport_zone_path": None,
                        "admin_state": "DOWN",
                    },
                ],
                "segments:2",
            ),
            "segments:2": (
                [
                    {
                        "id": "seg-a",
                        "display_name": "App",
                        "path": "/infra/segments/seg-a",
                        "connectivity_path": None,
                        "transport_zone_path": "/infra/sites/default/enforcement-points/default/transport-zones/vlan",
                        "admin_state": "UP",
                    },
                    {
                        "id": "seg-d",
                        "display_name": "db",
                        "path": "/infra/segments/seg-d",
                        "connectivity_path": "/infra/tier-1s/t1-e",
                        "transport_zone_path": "/infra/sites/default/enforcement-points/default/transport-zones/overlay",
                        "admin_state": "UP",
                    },
                ],
                None,
            ),
        },
    },
}


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], log_path: Path):
        super().__init__(address, Handler)
        self.log_path = log_path
        self.lock = threading.Lock()
        self.expiring_token_uses: dict[str, int] = {}
        self.reverse_next = True

    def append_log(self, record: dict[str, object]) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
                stream.write("\n")

    def should_expire(self, token: str) -> bool:
        if not token.endswith("-old"):
            return False
        with self.lock:
            uses = self.expiring_token_uses.get(token, 0)
            self.expiring_token_uses[token] = uses + 1
            return uses >= 1

    def ordered_results(self, values: list[dict[str, object]]) -> list[dict[str, object]]:
        with self.lock:
            reverse = self.reverse_next
            self.reverse_next = not self.reverse_next
        copied = list(values)
        if reverse:
            copied.reverse()
        return copied


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_json(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        operation = OPERATIONS.get(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        authorization = self.headers.get("Authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""

        status = 200
        response: dict[str, object]
        cursor = query.get("cursor", [""])[0]

        if operation is None:
            status = 404
            response = {"error_code": 40400, "error_message": "operation is outside the pinned contract"}
        elif not token or (not token.endswith("-old") and not token.endswith("-fresh")):
            status = 401
            response = {"error_code": 40100, "error_message": "missing or unknown access token"}
        elif self.server.should_expire(token):
            status = 401
            response = {"error_code": 40101, "error_message": "access token expired"}
        elif set(query) - {"cursor", "page_size"}:
            status = 400
            response = {"error_code": 40001, "error_message": "unsupported query parameter"}
        elif query.get("page_size", [None])[0] != "2":
            status = 400
            response = {"error_code": 40002, "error_message": "verifier requires page_size=2"}
        elif cursor not in operation["pages"]:
            status = 400
            response = {"error_code": 40003, "error_message": "unknown opaque cursor"}
        else:
            values, next_cursor = operation["pages"][cursor]
            response = {
                "result_count": 4 if cursor == "" else None,
                "results": self.server.ordered_results(values),
            }
            if next_cursor is not None:
                response["cursor"] = next_cursor
            if response["result_count"] is None:
                del response["result_count"]

        self.server.append_log(
            {
                "method": "GET",
                "path": parsed.path,
                "query": {key: values for key, values in sorted(query.items())},
                "authorization": authorization,
                "operationId": operation["operationId"] if operation else None,
                "status": status,
            }
        )
        self.send_json(status, response)

    def do_POST(self) -> None:  # noqa: N802
        self.send_json(405, {"error_code": 40500, "error_message": "only named GET operations are served"})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path(__file__).resolve().parent / "docs" / "contract.json",
    )
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    contract_operations = {
        contract["source"]["basePath"] + operation["path"]: (
            operation["method"],
            operation["operationId"],
        )
        for operation in contract["operations"]
    }
    implemented_operations = {
        path: ("GET", details["operationId"]) for path, details in OPERATIONS.items()
    }
    if contract_operations != implemented_operations:
        raise SystemExit("mock routes do not exactly match the pinned contract")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")

    server = ContractServer(("127.0.0.1", 0), args.log)
    host, port = server.server_address
    print(
        json.dumps(
            {
                "baseUrl": f"http://{host}:{port}",
                "logPath": str(args.log),
                "operationIds": ["ListTier1", "ListAllInfraSegments"],
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
