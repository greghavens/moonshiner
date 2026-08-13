#!/usr/bin/env python3
"""Loopback VCF Installer service for the contract and pinned SDK handshake."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getApplianceInfo": ("GET", "/v1/system/appliance-info"),
    "updateDepotSettings": ("PUT", "/v1/system/settings/depot"),
}

PRIMARY_TOKEN = "0123456789abcdef0123456789abcdef"
CLIENT_ERROR_TOKEN = "fedcba9876543210fedcba9876543210"
REQUEST_TIMEOUT_TOKEN = "11111111111111111111111111111111"
THROTTLED_TOKEN = "22222222222222222222222222222222"
SERVER_ERROR_TOKEN = "33333333333333333333333333333333"
TRANSPORT_ERROR_TOKEN = "44444444444444444444444444444444"
EXHAUSTED_TOKEN = "55555555555555555555555555555555"

TOKEN_SCENARIOS = {
    PRIMARY_TOKEN: "primary",
    CLIENT_ERROR_TOKEN: "clientError",
    REQUEST_TIMEOUT_TOKEN: "requestTimeout",
    THROTTLED_TOKEN: "throttled",
    SERVER_ERROR_TOKEN: "serverError",
    TRANSPORT_ERROR_TOKEN: "transportError",
    EXHAUSTED_TOKEN: "exhausted",
}


class State:
    def __init__(self, contract_path: Path, log_path: Path) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            operation_id: (definition["method"], definition["path"])
            for operation_id, definition in self.contract["operations"].items()
        }
        if actual != EXPECTED_OPERATIONS:
            raise ValueError("mock routes differ from docs/contract.json")
        if self.contract["operationIds"] != list(EXPECTED_OPERATIONS):
            raise ValueError("mock operationIds differ from docs/contract.json")

        self.log_path = log_path
        self.lock = threading.Lock()
        self.put_count = 0
        self.effect_count = 0
        self.scenario_attempts: dict[str, int] = {}
        self.depot_representation: dict[str, object] | None = None

    def append_request(self, record: dict[str, object]) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server_version = "VcfInstallerContractMock/9.1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _request_record(self, body: bytes) -> dict[str, object]:
        split = urlsplit(self.path)
        return {
            "method": self.command,
            "path": split.path,
            "query": split.query,
            "headers": {
                name.lower(): value for name, value in self.headers.items()
            },
            "body": body.decode("utf-8"),
        }

    def _json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def _not_found(self) -> None:
        self._json(
            404,
            {
                "errorCode": "VCF_CONTRACT_ROUTE_NOT_FOUND",
                "message": "No operation in the pinned contract matches this request",
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        record = self._request_record(body)
        self.state.append_request(record)
        if record["path"] != "/v1/tokens" or record["query"]:
            self._not_found()
            return
        self._json(
            201,
            {
                "accessToken": "loopback-access-token",
                "refreshToken": {"id": "loopback-refresh-token"},
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        record = self._request_record(body)
        self.state.append_request(record)
        # VMware.Sdk.Vcf.Installer 13.5 performs this internal version probe
        # while Connect-VcfInstallerServer creates its genuine connection.
        # The probe is SDK plumbing rather than an operation projected for the
        # candidate, but the response model and path are pinned by the exact
        # prerequisite version used by this verifier.
        if (
            record["path"] == "/v1/sddc-manager"
            and not record["query"]
            and not body
        ):
            self._json(200, {"version": "9.1.0.0.25380678"})
            return
        if (
            record["path"] != "/v1/system/appliance-info"
            or record["query"]
            or body
        ):
            self._not_found()
            return
        self._json(
            200,
            {
                "role": "VcfInstaller",
                "version": "9.1.0.0.25380678",
                "dnsDomain": "example.com",
            },
        )

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        record = self._request_record(body)
        if record["path"] != "/v1/system/settings/depot" or record["query"]:
            self.state.append_request(record)
            self._not_found()
            return

        try:
            representation = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            record["effectApplied"] = False
            self.state.append_request(record)
            self._json(
                400,
                {
                    "errorCode": "VCF_INVALID_JSON",
                    "message": "Request body must be a JSON object",
                },
            )
            return
        if not isinstance(representation, dict):
            record["effectApplied"] = False
            self.state.append_request(record)
            self._json(
                400,
                {
                    "errorCode": "VCF_INVALID_DEPOT_SETTINGS",
                    "message": "Depot settings must be a JSON object",
                },
            )
            return

        account = representation.get("vmwareAccount")
        token = account.get("downloadToken") if isinstance(account, dict) else None
        scenario = TOKEN_SCENARIOS.get(token, "unknown")

        with self.state.lock:
            self.state.put_count += 1
            sequence = self.state.put_count
            attempt = self.state.scenario_attempts.get(scenario, 0) + 1
            self.state.scenario_attempts[scenario] = attempt

            if scenario == "primary":
                outcome: int | str = 503 if attempt == 1 else 202
            elif scenario == "clientError":
                outcome = 400
            elif scenario == "requestTimeout":
                outcome = 408 if attempt == 1 else 202
            elif scenario == "throttled":
                outcome = 429 if attempt == 1 else 202
            elif scenario == "serverError":
                outcome = 502 if attempt == 1 else 202
            elif scenario == "transportError":
                outcome = "disconnect" if attempt == 1 else 202
            elif scenario == "exhausted":
                outcome = 503
            else:
                outcome = 400

            apply_representation = outcome == 202 or (
                attempt == 1
                and scenario in {"primary", "transportError", "exhausted"}
            )
            changed = (
                apply_representation
                and representation != self.state.depot_representation
            )
            if changed:
                self.state.depot_representation = representation
                self.state.effect_count += 1
            effect_count = self.state.effect_count

        record["putSequence"] = sequence
        record["scenario"] = scenario
        record["scenarioAttempt"] = attempt
        record["plannedStatus"] = outcome
        record["effectApplied"] = changed
        record["effectCount"] = effect_count
        self.state.append_request(record)

        if outcome == "disconnect":
            # Close after recording and applying the representation but before
            # sending any status line. The SDK must surface an ambiguous
            # transport failure, and retrying the same PUT must be idempotent.
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        if outcome == 503:
            self._json(
                503,
                {
                    "errorCode": "VCF_RESPONSE_LOST_AFTER_APPLY",
                    "message": "The representation was applied but confirmation failed",
                },
            )
            return
        if outcome == 400:
            self._json(
                400,
                {
                    "errorCode": "VCF_DEPOT_TOKEN_REJECTED",
                    "message": "The supplied depot credentials were rejected",
                },
            )
            return
        if outcome == 408:
            self._json(
                408,
                {
                    "errorCode": "VCF_REQUEST_TIMEOUT",
                    "message": "The depot update request timed out",
                },
            )
            return
        if outcome == 429:
            self._json(
                429,
                {
                    "errorCode": "VCF_RATE_LIMITED",
                    "message": "The depot update request was throttled",
                },
            )
            return
        if outcome == 502:
            self._json(
                502,
                {
                    "errorCode": "VCF_UPSTREAM_FAILURE",
                    "message": "The depot upstream was temporarily unavailable",
                },
            )
            return

        assert outcome == 202
        assert self.state.depot_representation is not None
        self._json(202, self.state.depot_representation)

    def _unsupported(self) -> None:
        body = self._read_body()
        self.state.append_request(self._request_record(body))
        self._not_found()

    do_DELETE = _unsupported  # type: ignore[assignment]
    do_PATCH = _unsupported  # type: ignore[assignment]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    args.log.write_text("", encoding="utf-8")
    state = State(args.contract, args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
