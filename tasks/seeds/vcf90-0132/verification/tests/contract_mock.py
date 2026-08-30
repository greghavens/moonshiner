"""Loopback-only HTTP fixture pinned to docs/contract.json.

The request log is intentionally in process: verification reads ``state.log``
directly, so the server exposes no non-contract log endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MockState:
    scenario: str
    log: list[dict[str, Any]] = field(default_factory=list)
    issued_tokens: int = 0
    expired_once: bool = False


def _contract_routes() -> dict[tuple[str, str], str]:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    base = contract["server_base_path"]
    routes = {
        (operation["method"], base + operation["path"]): operation["operationId"]
        for operation in contract["operations"]
    }
    expected = {
        ("POST", "/api/ni/auth/token"): "create",
        ("GET", "/api/ni/entities/vms"): "listVms",
    }
    if routes != expected:
        raise RuntimeError("mock and pinned operation contract differ")
    return routes


class ContractMockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, scenario: str) -> None:
        if scenario not in {"expiry", "optionals", "omission"}:
            raise ValueError(f"unknown mock scenario: {scenario}")
        self.state = MockState(scenario=scenario)
        self.routes = _contract_routes()
        super().__init__(("127.0.0.1", 0), ContractHandler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}/api/ni"


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractMockServer

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        operation_id = self.server.routes.get((self.command, split.path))
        self.server.state.log.append(
            {
                "method": self.command,
                "target": self.path,
                "operationId": operation_id,
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "body": body,
            }
        )

        if operation_id == "create":
            self._create_token()
        elif operation_id == "listVms":
            self._list_vms(split.query)
        else:
            self._json_response(404, {"message": "operation is not in the contract"})

    def _create_token(self) -> None:
        self.server.state.issued_tokens += 1
        token = f"token-{self.server.state.issued_tokens}"
        self._json_response(200, {"token": token, "expiry": 2000000000})

    def _list_vms(self, query: str) -> None:
        pairs = parse_qsl(query, keep_blank_values=True)
        parameters = dict(pairs)
        if len(parameters) != len(pairs):
            self._json_response(400, {"message": "duplicate query parameter"})
            return

        if self.server.state.scenario == "expiry":
            self._list_vms_with_expiry(parameters)
        elif self.server.state.scenario == "optionals":
            self._list_vms_with_optionals(parameters)
        else:
            self._list_vms_with_omission(parameters)

    def _list_vms_with_expiry(self, parameters: dict[str, str]) -> None:
        authorization = self.headers.get("Authorization")
        if parameters == {"size": "2"}:
            if authorization != "NetworkInsight token-1":
                self._json_response(401, {"message": "unauthorized"})
                return
            self._json_response(
                200,
                {
                    "results": [
                        {
                            "entity_id": "18230:1:alpha",
                            "entity_type": "VirtualMachine",
                            "time": 1700000001,
                        },
                        {
                            "entity_id": "18230:1:beta",
                            "entity_type": "VirtualMachine",
                            "time": 1700000002,
                        },
                    ],
                    "cursor": "cursor-page-2",
                    "total_count": 3,
                },
            )
            return

        if parameters == {"size": "2", "cursor": "cursor-page-2"}:
            if authorization == "NetworkInsight token-1" and not self.server.state.expired_once:
                self.server.state.expired_once = True
                self._json_response(401, {"message": "token expired"})
                return
            if authorization != "NetworkInsight token-2":
                self._json_response(401, {"message": "unauthorized"})
                return
            self._json_response(
                200,
                {
                    "results": [
                        {
                            "entity_id": "18230:1:gamma",
                            "entity_type": "VirtualMachine",
                            "time": 1700000003,
                        }
                    ],
                    "total_count": 3,
                },
            )
            return

        self._json_response(400, {"message": "unexpected query"})

    def _list_vms_with_optionals(self, parameters: dict[str, str]) -> None:
        if self.headers.get("Authorization") != "NetworkInsight token-1":
            self._json_response(401, {"message": "unauthorized"})
            return

        common = {
            "size": "1.5",
            "start_time": "1700000000",
            "end_time": "1700000999.25",
        }
        if parameters == common:
            self._json_response(
                200,
                {
                    "results": [
                        {
                            "entity_id": "18230:1:delta",
                            "entity_type": "VirtualMachine",
                            "time": 1700000100,
                        }
                    ],
                    "cursor": "next/+?=& segment",
                    "total_count": 2,
                },
            )
            return

        if parameters == {**common, "cursor": "next/+?=& segment"}:
            self._json_response(
                200,
                {
                    "results": [
                        {
                            "entity_id": "18230:1:epsilon",
                            "entity_type": "VirtualMachine",
                            "time": 1700000200,
                        }
                    ],
                    "total_count": 2,
                },
            )
            return

        self._json_response(400, {"message": "unexpected query"})

    def _list_vms_with_omission(self, parameters: dict[str, str]) -> None:
        if self.headers.get("Authorization") != "NetworkInsight token-1":
            self._json_response(401, {"message": "unauthorized"})
            return
        if parameters:
            self._json_response(400, {"message": "optional parameter was not omitted"})
            return
        self._json_response(200, {"results": [], "total_count": 0})

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class RunningContractMock:
    def __init__(self, scenario: str = "expiry") -> None:
        self.server = ContractMockServer(scenario)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> ContractMockServer:
        self.thread.start()
        return self.server

    def __exit__(self, *exc_info: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
