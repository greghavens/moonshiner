#!/usr/bin/env python3
"""Contract-pinned loopback VCF Automation service used by protected verification.

It serves only the operations named in docs/contract.json and refuses everything
else. Region pages are returned in the fixture's own order, never sorted, so a
client that wants a stable order has to impose one itself.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
EXPECTED_OPERATION_IDS = {"queryRegions"}
EXPECTED_SOURCE_KIND = "reference-documentation"
EXPECTED_ACCEPT = "application/json;version=9.1.0"

ACCESS_TOKEN = "dummy-vcfa-provider-token-91"

KNOWN_QUERY_KEYS = ("filter", "metadata", "sortAsc", "sortDesc", "page", "pageSize")
OPTIONAL_QUERY_KEYS = ("filter", "metadata", "sortAsc", "sortDesc")
FILTER_PATTERN = re.compile(r"^status==(?P<status>[A-Z_]+)$")


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path_template: str
    pattern: re.Pattern[str]

    @staticmethod
    def from_contract(operation: dict[str, Any]) -> "Route":
        path_template = operation["path"]
        pieces: list[str] = []
        cursor = 0
        for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9]*)\}", path_template):
            pieces.append(re.escape(path_template[cursor : match.start()]))
            pieces.append(f"(?P<{match.group(1)}>[^/]+)")
            cursor = match.end()
        pieces.append(re.escape(path_template[cursor:]))
        return Route(
            operation_id=operation["operationId"],
            method=operation["method"].upper(),
            path_template=path_template,
            pattern=re.compile("^" + "".join(pieces) + "$"),
        )


def load_contract() -> tuple[list[Route], dict[str, Any]]:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("kind") != EXPECTED_SOURCE_KIND:
        raise RuntimeError("contract is not the reference-derived projection")
    if source.get("acceptVersion") != "9.1.0":
        raise RuntimeError("contract does not pin the 9.1.0 Accept version")
    operations = contract.get("operations", [])
    if {item.get("operationId") for item in operations} != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the loopback service")
    query = contract["operations"][0]["queryParameters"]
    if tuple(item["name"] for item in query) != KNOWN_QUERY_KEYS:
        raise RuntimeError("contract query parameter set does not match the service")
    return [Route.from_contract(item) for item in operations], contract


def load_regions() -> list[dict[str, Any]]:
    return json.loads(
        (ROOT / "fixtures" / "vcfa_regions.json").read_text(encoding="utf-8")
    )


class MockState:
    def __init__(
        self, routes: list[Route], regions: list[dict[str, Any]], request_log: Path
    ) -> None:
        self.routes = routes
        self.regions = regions
        self.request_log = request_log
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method:
                continue
            match = route.pattern.fullmatch(path)
            if match:
                return route, {
                    key: unquote(value) for key, value in match.groupdict().items()
                }
        return None, {}

    def append_log(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, ContractHandler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def _dispatch(self) -> None:
        split_target = urlsplit(self.path)
        route, _parameters = self.server.state.match(self.command, split_target.path)
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(body_length)
        pairs = parse_qsl(split_target.query, keep_blank_values=True)

        if route is None:
            status, payload = 404, self._error(
                "NOT_FOUND",
                "The contract-pinned loopback service does not serve this operation.",
            )
        else:
            status, payload = self._query_regions(pairs, body)

        self.server.state.append_log(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": split_target.path,
                "rawQuery": split_target.query,
                "queryKeys": [key for key, _ in pairs],
                "query": {key: value for key, value in pairs},
                "headers": {
                    name.lower(): value.strip() for name, value in self.headers.items()
                },
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "bodyLength": len(body),
                "responseStatus": status,
            }
        )
        self._send_json(status, payload)

    def _query_regions(
        self, pairs: list[tuple[str, str]], body: bytes
    ) -> tuple[int, Any]:
        if self.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return 401, self._error(
                "UNAUTHORIZED", "A bearer access token is required."
            )
        if (self.headers.get("Accept") or "").replace(" ", "") != EXPECTED_ACCEPT:
            return 406, self._error(
                "UNSUPPORTED_VERSION",
                "The Accept header must request application/json;version=9.1.0.",
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED", "A collection query must not carry a request body."
            )

        keys = [key for key, _ in pairs]
        unknown = sorted({key for key in keys if key not in KNOWN_QUERY_KEYS})
        if unknown:
            return 400, self._error(
                "UNKNOWN_QUERY_PARAMETER",
                f"Unsupported query parameter(s): {', '.join(unknown)}.",
            )
        duplicated = sorted({key for key in keys if keys.count(key) > 1})
        if duplicated:
            return 400, self._error(
                "DUPLICATE_QUERY_PARAMETER",
                f"Repeated query parameter(s): {', '.join(duplicated)}.",
            )

        values = dict(pairs)
        # An optional parameter the caller did not set is omitted, never blank.
        blank = sorted(
            key
            for key in OPTIONAL_QUERY_KEYS
            if key in values and values[key].strip() == ""
        )
        if blank:
            return 400, self._error(
                "EMPTY_QUERY_PARAMETER",
                "Unset optional parameter(s) must be omitted from the request "
                f"target, not sent empty: {', '.join(blank)}.",
            )

        for key in ("page", "pageSize"):
            if key not in values:
                return 400, self._error(
                    "MISSING_QUERY_PARAMETER", f"The {key} parameter is required."
                )
        try:
            page = int(values["page"])
            page_size = int(values["pageSize"])
        except ValueError:
            return 400, self._error(
                "INVALID_QUERY_PARAMETER", "page and pageSize must be integers."
            )
        if page < 1:
            return 400, self._error(
                "INVALID_QUERY_PARAMETER", "page has a minimum of 1."
            )
        if page_size < 1 or page_size > 128:
            return 400, self._error(
                "INVALID_QUERY_PARAMETER", "pageSize has a maximum of 128."
            )

        matched = self.server.state.regions
        if "filter" in values:
            match = FILTER_PATTERN.fullmatch(values["filter"])
            if match is None:
                return 400, self._error(
                    "UNSUPPORTED_FILTER",
                    "This loopback fixture only implements the FIQL expression "
                    "status==<VALUE>.",
                )
            wanted = match.group("status")
            matched = [item for item in matched if item["status"] == wanted]

        result_total = len(matched)
        page_count = (result_total + page_size - 1) // page_size
        start = (page - 1) * page_size
        # sortAsc, sortDesc and metadata are accepted and deliberately ignored.
        return 200, {
            "resultTotal": result_total,
            "pageCount": page_count,
            "page": page,
            "pageSize": page_size,
            "associations": [],
            "values": matched[start : start + page_size],
        }

    @staticmethod
    def _error(minor_error_code: str, message: str) -> dict[str, str]:
        return {"minorErrorCode": minor_error_code, "message": message}

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;version=9.1.0")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(encoded)


def write_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: mock_vcfa.py PORT_FILE REQUEST_LOG")
    port_file = Path(sys.argv[1]).resolve()
    request_log = Path(sys.argv[2]).resolve()
    routes, _contract = load_contract()
    state = MockState(routes, load_regions(), request_log)
    server = ContractServer(("127.0.0.1", 0), state)
    write_atomic(port_file, str(server.server_port))
    server.serve_forever()


if __name__ == "__main__":
    main()
