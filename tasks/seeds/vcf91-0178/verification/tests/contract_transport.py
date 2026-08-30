#!/usr/bin/env python3
"""Socketless contract transport for execution sandboxes without loopback."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit

from mock_server import durable_write, load_routes


class Response:
    def __init__(
        self,
        status: int,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> None:
        self.status = status
        self._headers = headers
        self._body = body
        self._offset = 0

    def read(self, amount: int | None = None) -> bytes:
        if amount is None or amount < 0:
            amount = len(self._body) - self._offset
        result = self._body[self._offset : self._offset + amount]
        self._offset += len(result)
        return result

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers)


class Connection:
    transport: "ContractTransport"

    def __init__(
        self,
        host: str,
        port: int | None = None,
        *,
        timeout: float | None = None,
        **_kwargs: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._response: Response | None = None

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        encode_chunked: bool = False,
    ) -> None:
        self._response = self.transport.handle(
            self,
            method,
            url,
            body or b"",
            headers or {},
            encode_chunked,
        )

    def getresponse(self) -> Response:
        if self._response is None:
            raise http.client.ResponseNotReady()
        return self._response

    def close(self) -> None:
        return


class ContractTransport:
    """Patch the application transport while retaining contract-derived logs."""

    def __init__(
        self,
        contract_path: Path,
        log_path: Path,
        state_path: Path,
        config: dict[str, Any],
    ) -> None:
        self.routes = load_routes(contract_path)
        self.log_path = log_path
        self.state_path = state_path
        self.config = config
        self.state: dict[str, Any] = {
            "createCount": 0,
            "forwarders": [],
        }
        self.log_path.write_text("", encoding="utf-8")
        self._write_state()
        Connection.transport = self
        self._patches = [
            patch("http.client.HTTPConnection", Connection),
            patch("http.client.HTTPSConnection", Connection),
        ]

    def __enter__(self) -> "ContractTransport":
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        for item in reversed(self._patches):
            item.stop()

    def _find_route(self, method: str, path: str) -> dict[str, Any] | None:
        for route in self.routes:
            if (
                route["method"] == method
                and route["pathPattern"].fullmatch(path)
            ):
                return route
        return None

    def _write_state(self) -> None:
        durable_write(
            self.state_path,
            json.dumps(
                self.state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            "w",
        )

    def handle(
        self,
        connection: Connection,
        method: str,
        raw_target: str,
        raw_body: bytes,
        supplied_headers: dict[str, str],
        encode_chunked: bool,
    ) -> Response:
        split = urlsplit(raw_target)
        route = self._find_route(method, split.path)
        operation_id = route["operationId"] if route else None
        try:
            body = json.loads(raw_body) if raw_body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"_malformed": raw_body.decode("utf-8", errors="replace")}

        header_pairs = [[key.lower(), value] for key, value in supplied_headers.items()]
        if not any(key == "host" for key, _value in header_pairs):
            authority = connection.host
            if connection.port is not None:
                authority += f":{connection.port}"
            header_pairs.insert(0, ["host", authority])
        if raw_body and not any(key == "content-length" for key, _value in header_pairs):
            header_pairs.append(["content-length", str(len(raw_body))])
        grouped: dict[str, list[str]] = {}
        for key, value in header_pairs:
            grouped.setdefault(key, []).append(value)

        status: int
        response_value: Any
        effect_committed = False
        if operation_id == "testLogForwarderConnection":
            if self.config["scenario"] == "precheck_failure":
                status = 502
                response_value = {
                    "errorCode": "CERTIFICATE_NOT_TRUSTED",
                    "errorMessage": self.config["sensitive_error"],
                }
            else:
                status = 200
                response_value = None
        elif operation_id == "createLogForwarder":
            status = 201
            effect_committed = True
            self.state["createCount"] += 1
            self.state["forwarders"].append(body)
            self._write_state()
            response_value = {
                "id": self.config["created_id"],
                "enabled": self.config["enabled"],
                "host": self.config["host"],
                "name": self.config["name"],
                "port": self.config["port"],
                "protocol": self.config["protocol"],
                "sslEnabled": self.config["ssl_enabled"],
                "transportProtocol": self.config["transport_protocol"],
            }
            if self.config["scenario"] == "bad_create_response":
                response_value["host"] = self.config["host"] + ".mismatch"
        else:
            status = 404
            response_value = {
                "errorCode": "OUTSIDE_FOCUSED_CONTRACT",
                "errorMessage": "route is not in the focused contract",
            }

        entry = {
            "method": method,
            "raw_target": raw_target,
            "path": split.path,
            "query": split.query,
            "header_pairs": header_pairs,
            "headers": grouped,
            "body": body,
            "body_raw": raw_body.decode("utf-8", errors="replace"),
            "body_bytes": len(raw_body),
            "operationId": operation_id,
            "response_status": status,
            "effect_committed": effect_committed,
        }
        durable_write(
            self.log_path,
            json.dumps(
                entry,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            "a",
        )

        if response_value is None:
            response_body = b""
            response_headers = [("Content-Length", "0")]
        else:
            response_body = json.dumps(
                response_value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            response_headers = [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(response_body))),
            ]
        return Response(status, response_headers, response_body)
