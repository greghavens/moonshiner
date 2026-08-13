#!/usr/bin/env python3
"""Contract-pinned loopback VCF Operations for networks node for vcf91-0286.

Routes are derived from docs/contract.json. Any method/path outside that
projection is refused, and every request is appended to a flushed JSONL log.

The node deliberately mirrors the specification's behaviour for
addApplicationWithTiers: no conflict response is documented, so a repeated POST
creates a second application with the same name. Retry safety therefore has to
come from the caller.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


EXPECTED_OPERATION_IDS = {
    "create",
    "getSavedApplicationsSummaries",
    "addApplicationWithTiers",
    "delete",
}

TOKEN_PREFIX = "NetworkInsight "


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def compile_path_template(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"[^/]+")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


def load_routes(path: Path) -> tuple[list[dict[str, Any]], str]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if {item["operationId"] for item in operations} != EXPECTED_OPERATION_IDS:
        raise ValueError("unexpected focused operationId set")
    if len(operations) != len(EXPECTED_OPERATION_IDS):
        raise ValueError("contract repeats an operationId")
    if contract["securitySchemes"]["ApiKeyAuth"]["valuePrefix"] != TOKEN_PREFIX:
        raise ValueError("contract authorization prefix changed")

    base_path = contract["source"]["basePath"]
    routes = [
        {
            "operationId": item["operationId"],
            "method": item["method"],
            "path": item["path"],
            "pathPattern": compile_path_template(base_path + item["path"]),
        }
        for item in operations
    ]
    return routes, base_path


def api_error(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "details": []}


def encode_cursor(index: int) -> str:
    return base64.b64encode(str(index).encode("ascii")).decode("ascii")


def decode_cursor(cursor: str) -> int | None:
    try:
        return int(base64.b64decode(cursor.encode("ascii"), validate=True))
    except Exception:  # noqa: BLE001 - any malformed cursor is a client error
        return None


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, Any]],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()
        self.sequence = 0
        self.tokens_issued = 0
        self.active_tokens: set[str] = set()
        self.applications: list[dict[str, Any]] = [
            self._application(item, index)
            for index, item in enumerate(config["existing_applications"])
        ]
        self.created: list[dict[str, Any]] = []

    # -- fixtures ---------------------------------------------------------

    def _application(self, item: dict[str, Any], index: int) -> dict[str, Any]:
        base = self.config["start_time"]
        return {
            "entity_id": item["entity_id"],
            "name": item["name"],
            "entity_type": "Application",
            "create_time": base + index * 1_000,
            "created_by": item["created_by"],
            "last_modified_time": base + index * 1_000 + 500,
            "last_modified_by": item["created_by"],
            "last_modified_by_service": "Ensemble",
            "tier_count": item["tier_count"],
            "member_count": item["member_count"],
            "update_status": "NO_CHANGE",
        }

    def find_route(self, method: str, path: str) -> dict[str, Any] | None:
        for route in self.routes:
            if route["method"] == method and route["pathPattern"].fullmatch(path):
                return route
        return None

    def append_log(self, item: dict[str, Any]) -> None:
        # Member order is part of the contract under test, so the log must not
        # sort keys anywhere in the record.
        encoded = json.dumps(item, separators=(",", ":"))
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")

    def next_sequence(self) -> int:
        with self.lock:
            value = self.sequence
            self.sequence += 1
            return value

    # -- operations -------------------------------------------------------

    def _create_token(self, body: Any) -> tuple[int, Any]:
        config = self.config
        if not isinstance(body, dict):
            return 400, api_error(400, "a JSON user credential is required")
        if body.get("username") != config["username"]:
            return 401, api_error(401, "unknown user")
        if body.get("password") != config["password"]:
            return 401, api_error(401, "invalid credentials")
        domain = body.get("domain")
        if not isinstance(domain, dict):
            return 400, api_error(400, "domain is required")
        domain_type = domain.get("domain_type")
        if domain_type not in ("LOCAL", "LDAP"):
            return 400, api_error(400, "domain_type must be LOCAL or LDAP")
        if domain_type == "LDAP" and not domain.get("value"):
            return 400, api_error(400, "an LDAP domain requires a value")
        if domain_type == "LOCAL" and "value" in domain:
            return 400, api_error(400, "a LOCAL domain must not carry a value")
        with self.lock:
            self.tokens_issued += 1
            token = "%s-%d" % (config["token_prefix"], self.tokens_issued)
            self.active_tokens.add(token)
        return 200, {"token": token, "expiry": config["start_time"] + 3_600_000}

    def _delete_token(self, token: str) -> tuple[int, Any]:
        with self.lock:
            self.active_tokens.discard(token)
        return 204, None

    def _summaries(self, query: str) -> tuple[int, Any]:
        pairs = parse_qsl(query, keep_blank_values=True)
        names = [key for key, _ in pairs]
        if "size" not in names:
            return 400, api_error(400, "size is required")
        values = dict(pairs)
        try:
            size = int(values["size"])
        except ValueError:
            return 400, api_error(400, "size must be a number")
        if size < 1:
            return 400, api_error(400, "size must be positive")

        start = 0
        if "cursor" in values:
            decoded = decode_cursor(values["cursor"])
            if decoded is None:
                return 400, api_error(400, "cursor is not a cursor this node issued")
            start = decoded

        with self.lock:
            snapshot = list(self.applications)
        page = snapshot[start : start + size]
        payload: dict[str, Any] = {"results": page, "total_count": len(snapshot)}
        if start + size < len(snapshot):
            payload["cursor"] = encode_cursor(start + size)
        return 200, payload

    def _add_application(self, body: Any, if_match: str | None) -> tuple[int, Any]:
        if not isinstance(body, dict):
            return 400, api_error(400, "a JSON application definition is required")
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return 400, api_error(400, "name is required")
        tiers = body.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            return 400, api_error(400, "at least one tier is required")
        for tier in tiers:
            if not isinstance(tier, dict) or not tier.get("name"):
                return 400, api_error(400, "every tier requires a name")
            criteria = tier.get("group_membership_criteria")
            if not isinstance(criteria, list) or not criteria:
                return 400, api_error(400, "every tier requires membership criteria")
        if if_match is not None and re.fullmatch(r"-?\d+", if_match) is None:
            return 400, api_error(400, "If-Match must be a lastModifiedTimestamp")

        with self.lock:
            index = len(self.applications)
            record = self._application(
                {
                    "entity_id": "%s:%d" % (self.config["created_entity_prefix"], index),
                    "name": name,
                    "created_by": self.config["username"],
                    "tier_count": len(tiers),
                    "member_count": len(tiers) * 4,
                },
                index,
            )
            # The specification documents no conflict response here, so a
            # duplicate name is accepted and produces a second application.
            self.applications.append(record)
            self.created.append(record)
        return 201, record

    def response_for(
        self,
        operation_id: str | None,
        query: str,
        body: Any,
        authorization: str | None,
        if_match: str | None,
    ) -> tuple[int, Any]:
        if operation_id is None:
            return 404, api_error(404, "route is outside the pinned contract")
        if operation_id == "create":
            return self._create_token(body)

        if authorization is None or not authorization.startswith(TOKEN_PREFIX):
            return 401, api_error(401, "an Authorization: NetworkInsight token is required")
        token = authorization[len(TOKEN_PREFIX) :]
        with self.lock:
            active = token in self.active_tokens
        if not active:
            return 401, api_error(401, "token is invalid or expired")

        if operation_id == "delete":
            return self._delete_token(token)
        if operation_id == "getSavedApplicationsSummaries":
            failed_number = self.config.get("fail_summary_token_number")
            failed_token = "%s-%s" % (self.config["token_prefix"], failed_number)
            if failed_number is not None and token == failed_token:
                return 500, api_error(500, "injected summaries failure")
            return self._summaries(query)
        if operation_id == "addApplicationWithTiers":
            return self._add_application(body, if_match)
        return 404, api_error(404, "route is outside the pinned contract")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def _read_body(self) -> tuple[bytes, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length else 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            return raw, {"_malformed": raw.decode("utf-8", errors="replace")}

    def _headers(self) -> tuple[list[list[str]], dict[str, list[str]]]:
        pairs: list[list[str]] = []
        grouped: dict[str, list[str]] = {}
        for key, value in self.headers.raw_items():
            lowered = key.lower()
            pairs.append([lowered, value])
            grouped.setdefault(lowered, []).append(value)
        return pairs, grouped

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw, body = self._read_body()
        route = self.server.find_route(method, split.path)
        operation_id = route["operationId"] if route else None
        pairs, grouped = self._headers()
        status, response = self.server.response_for(
            operation_id,
            split.query,
            body,
            self.headers.get("Authorization"),
            self.headers.get("If-Match"),
        )
        self.server.append_log(
            {
                "sequence": self.server.next_sequence(),
                "method": method,
                "raw_target": self.path,
                "path": split.path,
                "query": split.query,
                "query_pairs": [list(item) for item in parse_qsl(split.query)],
                "header_pairs": pairs,
                "headers": grouped,
                "body": body,
                "body_raw": raw.decode("utf-8", errors="replace"),
                "body_bytes": len(raw),
                "operationId": operation_id,
                "response_status": status,
            }
        )
        self._json(status, response)

    def _json(self, status: int, value: Any) -> None:
        if status == 204 or value is None:
            self.send_response(status)
            if status != 204:
                self.send_header("Content-Length", "0")
            self.end_headers()
            return
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    routes, base_path = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), routes, args.log, config)
    durable_write(
        args.ready,
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": server.server_port,
                "basePath": base_path,
            },
            separators=(",", ":"),
        ),
        "w",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
