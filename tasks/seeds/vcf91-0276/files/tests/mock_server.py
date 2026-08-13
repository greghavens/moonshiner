#!/usr/bin/env python3
"""Contract-pinned loopback VCF Operations fixture with an expiring access token.

The route allow-list is derived from docs/contract.json: only the four
operationIds named there are served, and every request is appended to a JSON
Lines log that the protected verifier reads.
"""

from __future__ import annotations

import argparse
import json
import secrets
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

EXPECTED_OPERATIONS = {
    "acquireToken": ("POST", "/api/auth/token/acquire"),
    "getResources": ("GET", "/api/resources"),
    "queryAlert": ("POST", "/api/alerts/query"),
    "releaseToken": ("POST", "/api/auth/token/release"),
}
EXPECTED_BASE_PATH = "/suite-api"
TOKEN_PREFIX = "OpsToken "

# Authorized-call budgets. The first token runs out part way through the alert
# pages, which is the whole point of the scenario.
FIRST_TOKEN_BUDGET = 3
LATER_TOKEN_BUDGET = 50


def load_contract(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    actual = {
        operation["operationId"]: (operation["method"], operation["path"])
        for operation in document["operations"]
    }
    if actual != EXPECTED_OPERATIONS:
        raise RuntimeError("focused contract operation set changed")
    if document["base_path"] != EXPECTED_BASE_PATH:
        raise RuntimeError("focused contract base path changed")
    if document["authentication"]["header"] != "Authorization":
        raise RuntimeError("focused contract authentication header changed")
    return document


def build_fixture() -> dict:
    def identifier(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(8)}"

    monitored = [
        {
            "identifier": identifier("vm"),
            "name": "app-tier-01",
            "resourceKindKey": "VirtualMachine",
        },
        {
            "identifier": identifier("vm"),
            "name": "app-tier-02",
            "resourceKindKey": "VirtualMachine",
        },
        {
            "identifier": identifier("vm"),
            "name": "db-tier-01",
            "resourceKindKey": "VirtualMachine",
        },
    ]
    unmonitored = [
        {
            "identifier": identifier("host"),
            "name": "esx-01.lab.local",
            "resourceKindKey": "HostSystem",
        },
        {
            "identifier": identifier("host"),
            "name": "esx-02.lab.local",
            "resourceKindKey": "HostSystem",
        },
    ]

    def alert(index: int, resource: str, level: str, status: str) -> dict:
        return {
            "alertId": identifier(f"alert{index}"),
            "resourceId": resource,
            "alertLevel": level,
            "status": status,
            "controlState": "OPEN",
            "alertDefinitionId": f"AlertDefinition-{index}",
            "alertDefinitionName": f"Sustained saturation {index}",
            "startTimeUTC": 1753368185 + index,
            "updateTimeUTC": 1753378185 + index,
            "cancelTimeUTC": 0,
            "suspendUntilTimeUTC": 0,
        }

    # Five alerts match the focused query; the decoys only appear when a filter
    # was dropped from the wire body.
    alerts = [
        alert(1, monitored[0]["identifier"], "CRITICAL", "ACTIVE"),
        alert(2, monitored[0]["identifier"], "WARNING", "ACTIVE"),
        alert(3, monitored[1]["identifier"], "IMMEDIATE", "NEW"),
        alert(4, unmonitored[0]["identifier"], "CRITICAL", "ACTIVE"),
        alert(5, monitored[1]["identifier"], "CRITICAL", "CANCELED"),
        alert(6, monitored[2]["identifier"], "CRITICAL", "UPDATED"),
        alert(7, monitored[2]["identifier"], "IMMEDIATE", "ACTIVE"),
        alert(8, monitored[2]["identifier"], "CRITICAL", "ACTIVE"),
    ]
    matching = [
        item["alertId"]
        for item in alerts
        if item["status"] != "CANCELED"
        and item["alertLevel"] in ("CRITICAL", "IMMEDIATE")
        and item["resourceId"] in {entry["identifier"] for entry in monitored}
    ]
    return {
        "username": f"svc-harvest-{secrets.token_hex(4)}",
        "password": f"pw-{secrets.token_hex(10)}",
        "resource_kind": "VirtualMachine",
        "resources": monitored + unmonitored,
        "monitored_ids": [entry["identifier"] for entry in monitored],
        "monitored_names": [entry["name"] for entry in monitored],
        "alerts": alerts,
        "matching_alert_ids": matching,
    }


class State:
    def __init__(self, log_path: Path, mode: str) -> None:
        self.log_path = log_path
        self.mode = mode
        self.lock = threading.Lock()
        self.sequence = 0
        self.tokens: dict[str, dict] = {}
        self.issued: list[str] = []
        self.fixture = build_fixture()
        if mode == "empty":
            resource_kind = self.fixture["resource_kind"]
            self.fixture["resources"] = [
                item
                for item in self.fixture["resources"]
                if item["resourceKindKey"] != resource_kind
            ]
            self.fixture["monitored_ids"] = []
            self.fixture["monitored_names"] = []
            self.fixture["matching_alert_ids"] = []

    def issue_token(self) -> tuple[str, int]:
        with self.lock:
            index = len(self.issued) + 1
            if self.mode == "always-expired":
                budget = 0
            else:
                budget = FIRST_TOKEN_BUDGET if index == 1 else LATER_TOKEN_BUDGET
            value = f"ops-{secrets.token_hex(16)}"
            self.tokens[value] = {"index": index, "remaining": budget}
            self.issued.append(value)
            return value, index

    def consume(self, header: str | None) -> tuple[int | None, str | None]:
        """Return (token index, failure reason) for a secured request."""
        if header is None or not header.startswith(TOKEN_PREFIX):
            return None, "missing"
        value = header[len(TOKEN_PREFIX) :]
        with self.lock:
            record = self.tokens.get(value)
            if record is None:
                return None, "unknown"
            if record["remaining"] <= 0:
                return record["index"], "expired"
            record["remaining"] -= 1
            return record["index"], None

    def record(self, entry: dict) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
                handle.flush()


def paginate(items: list, page: int, page_size: int) -> list:
    start = page * page_size
    return items[start : start + page_size]


def handler_type(state: State):
    fixture = state.fixture

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
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

        def do_HEAD(self) -> None:
            self._dispatch()

        def _dispatch(self) -> None:
            parsed = urlsplit(self.path)
            routes = {
                (method, EXPECTED_BASE_PATH + path): operation_id
                for operation_id, (method, path) in EXPECTED_OPERATIONS.items()
            }
            operation_id = routes.get((self.command, parsed.path))
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            headers = {name.lower(): value for name, value in self.headers.items()}
            header_values = {
                name.lower(): self.headers.get_all(name)
                for name in self.headers.keys()
            }
            self.entry = {
                "operationId": operation_id,
                "method": self.command,
                "target": self.path,
                "path": parsed.path,
                "query": parsed.query,
                "queryParams": [
                    list(pair)
                    for pair in parse_qsl(parsed.query, keep_blank_values=True)
                ],
                "headers": headers,
                "headerValues": header_values,
                "body": body.decode("utf-8", errors="replace"),
                "bodyLength": len(body),
                "tokenIndex": None,
                "issuedTokenIndex": None,
                "issuedToken": None,
            }

            if operation_id is None:
                self._error(404, "The requested resource is not available")
                return
            if headers.get("accept", "").split(";", 1)[0].strip() != "application/json":
                self._error(406, "Only application/json responses are produced")
                return

            token_index = None
            if operation_id != "acquireToken":
                token_index, failure = state.consume(headers.get("authorization"))
                self.entry["tokenIndex"] = token_index
                if failure is not None:
                    message = {
                        "missing": "Authorization header is missing or malformed",
                        "unknown": "The token is not recognized",
                        "expired": "The token has expired; acquire a new token",
                    }[failure]
                    self._error(401, message)
                    return

            if operation_id == "acquireToken":
                self._acquire(headers, body)
            elif operation_id == "getResources":
                self._resources()
            elif operation_id == "queryAlert":
                self._alerts(headers, body)
            else:
                self._release(body)

        def _acquire(self, headers: dict, body: bytes) -> None:
            if headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                self._error(415, "A JSON request body is required")
                return
            try:
                document = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "The request body is not valid JSON")
                return
            if not isinstance(document, dict):
                self._error(400, "The request body must be a username-password object")
                return
            unknown = set(document) - {"authSource", "password", "username"}
            if unknown:
                self._error(400, "The request body contains unsupported properties")
                return
            if "authSource" in document:
                # This deployment authenticates the fixture user against the
                # local source, so any auth source value is rejected.
                self._error(400, "The requested auth source does not exist")
                return
            if (
                document.get("username") != fixture["username"]
                or document.get("password") != fixture["password"]
            ):
                self._error(401, "Authentication failed")
                return
            value, index = state.issue_token()
            self.entry["issuedToken"] = value
            self.entry["issuedTokenIndex"] = index
            self._json(
                200,
                {
                    "token": value,
                    "validity": 1786800000000 + index,
                    "expiresAt": "2026-08-04T18:00:00.000",
                    "roles": ["ContentAdmin"],
                },
            )

        def _page_arguments(self) -> tuple[int, int] | None:
            page, page_size = 0, 1000
            for name, value in self.entry["queryParams"]:
                if name in ("page", "pageSize"):
                    try:
                        parsed = int(value)
                    except ValueError:
                        self._error(400, f"The {name} parameter is not an integer")
                        return None
                    if parsed < 0 or (name == "pageSize" and parsed < 1):
                        self._error(400, f"The {name} parameter is out of range")
                        return None
                    if name == "page":
                        page = parsed
                    else:
                        page_size = parsed
            return page, page_size

        def _resources(self) -> None:
            arguments = self._page_arguments()
            if arguments is None:
                return
            page, page_size = arguments
            kinds = [
                value for name, value in self.entry["queryParams"] if name == "resourceKind"
            ]
            names = [value for name, value in self.entry["queryParams"] if name == "name"]
            selected = [
                entry
                for entry in fixture["resources"]
                if (not kinds or entry["resourceKindKey"] in kinds)
                and (not names or entry["name"] in names)
            ]
            window = paginate(selected, page, page_size)
            self._json(
                200,
                {
                    "pageInfo": {
                        "totalCount": len(selected),
                        "page": page,
                        "pageSize": page_size,
                    },
                    "links": [{"href": "/suite-api/api/resources", "rel": "SELF"}],
                    "resourceList": [
                        {
                            "identifier": entry["identifier"],
                            "creationTime": 1750000000000,
                            "resourceKey": {
                                "name": entry["name"],
                                "adapterKindKey": "VMWARE",
                                "resourceKindKey": entry["resourceKindKey"],
                            },
                            "resourceHealth": "GREEN",
                            "resourceHealthValue": 100,
                            "resourceStatusStates": [
                                {
                                    "adapterInstanceId": "adapter-1",
                                    "resourceStatus": "DATA_RECEIVING",
                                    "resourceState": "STARTED",
                                }
                            ],
                        }
                        for entry in window
                    ],
                },
            )

        def _alerts(self, headers: dict, body: bytes) -> None:
            if headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                self._error(415, "A JSON request body is required")
                return
            arguments = self._page_arguments()
            if arguments is None:
                return
            page, page_size = arguments
            try:
                document = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "The request body is not valid JSON")
                return
            if not isinstance(document, dict):
                self._error(400, "The request body must be an alert-query object")
                return

            selected = list(fixture["alerts"])
            if document.get("activeOnly") is True:
                selected = [item for item in selected if item["status"] != "CANCELED"]
            criticality = document.get("alertCriticality")
            if isinstance(criticality, list) and criticality:
                selected = [
                    item for item in selected if item["alertLevel"] in criticality
                ]
            resource_query = document.get("resource-query")
            if isinstance(resource_query, dict):
                wanted = resource_query.get("resourceId")
                if isinstance(wanted, list) and wanted:
                    selected = [item for item in selected if item["resourceId"] in wanted]
            window = paginate(selected, page, page_size)
            self._json(
                200,
                {
                    "pageInfo": {
                        "totalCount": len(selected),
                        "page": page,
                        "pageSize": page_size,
                    },
                    "alerts": window,
                },
            )

        def _release(self, body: bytes) -> None:
            if body:
                self._error(400, "This operation does not accept a request body")
                return
            authorization = self.headers.get("Authorization", "")
            value = authorization[len(TOKEN_PREFIX) :]
            with state.lock:
                record = state.tokens.get(value)
                if record is not None:
                    record["remaining"] = 0
            self._json(200, {"message": "The sessionId is terminated successfully"})

        def _error(self, status: int, message: str) -> None:
            self._json(
                status,
                {
                    "message": message,
                    "httpStatusCode": status,
                    "apiErrorCode": status * 10,
                },
            )

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
            self.entry["status"] = status
            state.record(self.entry)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=("expire-once", "always-expired", "empty"),
        default="expire-once",
    )
    arguments = parser.parse_args()

    load_contract(arguments.contract)
    state = State(arguments.log, arguments.mode)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(state))

    document = {
        "port": server.server_address[1],
        "mode": arguments.mode,
        "username": state.fixture["username"],
        "password": state.fixture["password"],
        "resource_kind": state.fixture["resource_kind"],
        "monitored_ids": state.fixture["monitored_ids"],
        "monitored_names": state.fixture["monitored_names"],
        "matching_alert_ids": state.fixture["matching_alert_ids"],
    }
    pending = arguments.port_file.with_name(arguments.port_file.name + ".tmp")
    pending.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    pending.replace(arguments.port_file)

    signal.signal(
        signal.SIGTERM,
        lambda *_args: threading.Thread(target=server.shutdown, daemon=True).start(),
    )
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
