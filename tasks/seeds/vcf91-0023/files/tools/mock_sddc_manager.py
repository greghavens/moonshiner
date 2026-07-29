#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the VCF cluster inventory task."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


EXPECTED_ROUTES = [("getClusters", "GET", "/v1/clusters")]
EXPECTED_QUERY_PARAMETERS = [
    "isStretched",
    "isImageBased",
    "domainId",
    "managedObjectReferenceId",
    "name",
    "isDefault",
    "isHciMeshEnabled",
    "pageSize",
    "pageNumber",
    "useCache",
]


class FixtureState:
    def __init__(
        self,
        contract_path: Path,
        scenario_path: Path,
        log_path: Path,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        routes = [
            (item.get("operationId"), item.get("method"), item.get("path"))
            for item in contract.get("operations", [])
        ]
        if routes != EXPECTED_ROUTES:
            raise RuntimeError("loopback fixture contract routes do not match")
        operation = contract["operations"][0]
        parameter_names = [
            item.get("name")
            for item in operation.get("query_parameters", [])
        ]
        if parameter_names != EXPECTED_QUERY_PARAMETERS:
            raise RuntimeError("loopback fixture contract parameters do not match")
        if (
            operation.get("responses", {}).get("200", {}).get("schema_ref")
            != "#/components/schemas/PageOfCluster"
        ):
            raise RuntimeError("loopback fixture success schema does not match")
        self.routes = {
            (method, path): operation_id
            for operation_id, method, path in routes
        }

        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        if set(scenario) != {
            "access_token",
            "page_size",
            "clusters",
            "fault",
        }:
            raise RuntimeError("loopback scenario has an unexpected shape")
        if (
            not isinstance(scenario["access_token"], str)
            or not scenario["access_token"]
            or isinstance(scenario["page_size"], bool)
            or not isinstance(scenario["page_size"], int)
            or scenario["page_size"] <= 0
            or not isinstance(scenario["clusters"], list)
            or any(not isinstance(item, dict) for item in scenario["clusters"])
            or scenario["fault"]
            not in {None, "http_500", "unexpected_206", "bad_totals"}
        ):
            raise RuntimeError("loopback scenario values are invalid")
        self.scenario = scenario
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.reverse_next_response = True

    def append_log(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")


class FixtureServer(ThreadingHTTPServer):
    state: FixtureState


class Handler(BaseHTTPRequestHandler):
    server: FixtureServer

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

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        operation_id = self.server.state.routes.get(
            (self.command, parsed.path)
        )
        body = self._read_body()
        entry: dict[str, Any] = {
            "operationId": operation_id,
            "method": self.command,
            "rawTarget": self.path,
            "queryPairs": [
                [name, value]
                for name, value in parse_qsl(
                    parsed.query,
                    keep_blank_values=True,
                )
            ],
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
            "body": body.decode("utf-8", errors="replace"),
        }
        if operation_id == "getClusters":
            self._get_clusters(entry, body)
        else:
            self._send_json(
                entry,
                404,
                {
                    "errorCode": "ROUTE_NOT_IN_CONTRACT",
                    "message": "Route is not served by this fixture",
                },
            )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(max(length, 0))

    def _get_clusters(
        self,
        entry: dict[str, Any],
        body: bytes,
    ) -> None:
        state = self.server.state
        scenario = state.scenario
        pairs = entry["queryPairs"]
        valid_query = (
            isinstance(pairs, list)
            and len(pairs) == 2
            and pairs[0][0] == "pageSize"
            and pairs[1][0] == "pageNumber"
        )
        try:
            page_size = int(pairs[0][1]) if valid_query else -1
            page_number = int(pairs[1][1]) if valid_query else -1
        except (TypeError, ValueError):
            page_size = -1
            page_number = -1
        valid = (
            valid_query
            and page_size == scenario["page_size"]
            and page_number >= 0
            and not body
            and entry["contentType"] is None
            and entry["accept"] == "application/json"
            and entry["authorization"]
            == "Bearer " + scenario["access_token"]
        )
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "BAD_CLUSTER_PAGE_REQUEST",
                    "message": "Collection request did not match the contract",
                },
            )
            return

        clusters = scenario["clusters"]
        if scenario["fault"] == "http_500":
            self._send_json(
                entry,
                500,
                {
                    "errorCode": "FIXTURE_INTERNAL_ERROR",
                    "message": "The fixture rejected this inventory request",
                },
            )
            return
        total_elements = len(clusters)
        total_pages = (
            (total_elements + page_size - 1) // page_size
            if total_elements
            else 0
        )
        if total_pages and page_number >= total_pages:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "PAGE_OUT_OF_RANGE",
                    "message": "Requested page is outside the collection",
                },
            )
            return
        start = page_number * page_size
        elements = [dict(item) for item in clusters[start : start + page_size]]
        with state.state_lock:
            reverse = state.reverse_next_response
            state.reverse_next_response = not state.reverse_next_response
        if reverse:
            elements.reverse()
        if scenario["fault"] == "bad_totals":
            total_pages += 1
        response_status = (
            206 if scenario["fault"] == "unexpected_206" else 200
        )
        self._send_json(
            entry,
            response_status,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": len(elements),
                    "totalElements": total_elements,
                    "totalPages": total_pages,
                },
            },
        )

    def _send_json(
        self,
        entry: dict[str, Any],
        status: int,
        payload: Any,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        entry["responseStatus"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = FixtureState(args.contract, args.scenario, args.log)
    server = FixtureServer(("127.0.0.1", 0), Handler)
    server.state = state
    host, port = server.server_address
    args.ready.write_text(
        f"http://{host}:{port}",
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
