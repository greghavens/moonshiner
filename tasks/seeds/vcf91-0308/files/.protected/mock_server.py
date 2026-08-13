#!/usr/bin/env python3
"""Contract-routed loopback service used only by the protected verifier."""

from __future__ import annotations

import base64
import json
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


class Route:
    def __init__(self, operation_id: str, method: str, template: str) -> None:
        self.operation_id = operation_id
        self.method = method.upper()
        pattern = ["^"]
        for piece in re.split(r"(\{[^{}]+\})", template):
            if not piece:
                continue
            if piece.startswith("{"):
                name = piece[1:-1]
                pattern.append(r"(?P<%s>[^/]+)" % re.sub(r"\W", "_", name))
            else:
                pattern.append(re.escape(piece))
        pattern.append("$")
        self.regex = re.compile("".join(pattern))

    def match(self, path: str) -> dict[str, str] | None:
        found = self.regex.match(path)
        return found.groupdict() if found else None


class State:
    def __init__(self, config: dict, routes: list[Route], log_path: str) -> None:
        self.config = config
        self.routes = routes
        self.lock = threading.Lock()
        self.sequence = 0
        self.authorized_calls = 0
        self.tokens_issued = 0
        self.page_fault_sent = False
        self.rotated_fault_sent = False
        self.log = open(log_path, "w", encoding="utf-8")
        self.valid_tokens = {config["initialAccessToken"]}
        self.deployments = config["deployments"]
        self.by_id = {item["id"]: item for item in self.deployments}

    def record(self, entry: dict) -> None:
        self.log.write(json.dumps(entry, sort_keys=True) + "\n")
        self.log.flush()


def deployment_page(state: State, page: int, size: int) -> dict:
    total = len(state.deployments)
    start = page * size
    window = state.deployments[start : start + size]
    total_pages = (total + size - 1) // size if total else 1
    return {
        "content": [
            {
                "id": item["id"],
                "name": item["name"],
                "orgId": state.config["orgId"],
                "projectId": item["projectId"],
                "status": item["status"],
                "createdAt": item["createdAt"],
                "ownedBy": state.config["requestedBy"],
            }
            for item in window
        ],
        "empty": not window,
        "first": page == 0,
        "last": page >= total_pages - 1,
        "number": page,
        "numberOfElements": len(window),
        "size": size,
        "totalElements": total,
        "totalPages": total_pages,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: State = None  # type: ignore[assignment]

    def log_message(self, *args) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _body(self) -> bytes:
        value = self.headers.get("Content-Length")
        if not value or not value.isdigit() or int(value) <= 0:
            return b""
        return self.rfile.read(int(value))

    def _reply(self, status: int, payload) -> None:
        raw = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _route(self, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.state.routes:
            variables = route.match(path)
            if variables is not None:
                return route, variables
        return None, {}

    def _dispatch(self) -> None:
        state = self.state
        raw_body = self._body()
        split = urlsplit(self.path)
        with state.lock:
            state.sequence += 1
            entry = {
                "seq": state.sequence,
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": [[key, value] for key, value in self.headers.items()],
                "bodyB64": base64.b64encode(raw_body).decode("ascii"),
            }
            route, variables = self._route(split.path)
            if route is None:
                status, payload, operation = 404, {}, None
            elif route.method != self.command:
                status, payload, operation = 405, {}, None
            elif state.config["scenario"] == "transport_drop" and route.operation_id == "getDeployments":
                status, payload, operation = 0, None, route.operation_id
            else:
                operation = route.operation_id
                handler = {
                    "getAccessToken": self._access_token,
                    "getDeployments": self._get_deployments,
                    "submitDeploymentActionRequest": self._submit_action,
                }.get(operation)
                status, payload = handler(split, raw_body, variables) if handler else (501, {})
            entry["operationId"] = operation
            entry["status"] = status
            state.record(entry)
            if status == 0:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            self._reply(status, payload)

    def _token(self) -> str | None:
        headers = self.headers.get_all("Authorization") or []
        if len(headers) != 1 or not headers[0].startswith("Bearer "):
            return None
        return headers[0][len("Bearer ") :]

    def _authorize(self) -> tuple[int, dict] | None:
        state = self.state
        token = self._token()
        if token not in state.valid_tokens:
            return 401, {}
        if token == state.config["initialAccessToken"]:
            if state.authorized_calls >= state.config["expireAfterAuthorizedRequests"]:
                return 401, {}
        state.authorized_calls += 1
        return None

    def _access_token(self, split, raw_body: bytes, variables: dict[str, str]):
        state = self.state
        if variables.get("tenant") != state.config["tenant"]:
            return 404, {}
        media = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if media != "application/x-www-form-urlencoded":
            return 400, {}
        pieces = [piece.partition("=") for piece in raw_body.decode("utf-8", "replace").split("&") if piece]
        fields = {name: value for name, _, value in pieces}
        if fields.get("grant_type") != "refresh_token":
            return 400, {}
        if unquote(fields.get("refresh_token", "")) != state.config["apiToken"]:
            return 400, {}
        state.tokens_issued += 1
        issued = state.config["refreshedAccessToken"]
        if state.config["scenario"] == "malformed_token":
            return 200, {"access_token": "", "token_type": "Bearer", "expires_in": 3600}
        state.valid_tokens.add(issued)
        return 200, {
            "access_token": issued,
            "token_type": state.config["tokenType"],
            "expires_in": state.config["expiresIn"],
        }

    def _get_deployments(self, split, raw_body: bytes, variables: dict[str, str]):
        state = self.state
        if state.config["scenario"] == "page_401" and not state.page_fault_sent:
            state.page_fault_sent = True
            return 401, {}
        denied = self._authorize()
        if denied:
            return denied
        page, size = 0, 20
        for piece in split.query.split("&"):
            name, _, value = piece.partition("=")
            if name == "page" and value.isdigit():
                page = int(value)
            if name == "size" and value.isdigit() and int(value) > 0:
                size = int(value)
        payload = deployment_page(state, page, size)
        if state.config["scenario"] == "malformed_page" and page == 0:
            payload["last"] = "false"
        if state.config["scenario"] == "malformed_deployment" and page == 0:
            payload["content"][0]["id"] = ""
        return 200, payload

    def _submit_action(self, split, raw_body: bytes, variables: dict[str, str]):
        state = self.state
        deployment_id = variables.get("deploymentId")
        if (
            state.config["scenario"] == "second_401"
            and self._token() == state.config["refreshedAccessToken"]
            and not state.rotated_fault_sent
        ):
            state.rotated_fault_sent = True
            return 401, {}
        denied = self._authorize()
        if denied:
            return denied
        if state.config["scenario"] == "action_500":
            return 500, {}
        deployment = state.by_id.get(deployment_id)
        if deployment is None:
            return 404, {}
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return 409, {}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 409, {}
        if not isinstance(payload, dict):
            return 409, {}
        request_id = deployment["requestId"]
        request_status = state.config["requestStatus"]
        if state.config["scenario"] == "malformed_action_id":
            request_id = ""
        if state.config["scenario"] == "malformed_action_status":
            request_status = ""
        return 200, {
            "actionId": payload.get("actionId"),
            "completedTasks": 0,
            "createdAt": state.config["requestCreatedAt"],
            "deploymentId": deployment_id,
            "id": request_id,
            "name": state.config["requestName"],
            "requestedBy": state.config["requestedBy"],
            "status": request_status,
            "totalTasks": 2,
        }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: mock_server.py <contract.json> <config.json> <log.jsonl>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as handle:
        contract = json.load(handle)
    with open(argv[2], encoding="utf-8") as handle:
        config = json.load(handle)
    routes = [
        Route(item["operationId"], item["method"], item["pathTemplate"])
        for item in contract["operations"]
    ]
    Handler.state = State(config, routes, argv[3])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
