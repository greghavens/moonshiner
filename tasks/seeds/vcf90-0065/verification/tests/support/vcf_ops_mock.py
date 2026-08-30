"""TLS loopback VCF Operations mock constrained by docs/contract.json.

The route table is built from the pinned contract, so the service answers only
the operations the contract names. Anything else is recorded and refused. Every
request is appended to a JSONL log that the verifier reads back.
"""

from __future__ import annotations

import json
import ssl
import threading
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


EXPECTED_OPERATIONS = {
    "acquireToken": ("POST", "/api/auth/token/acquire"),
    "releaseToken": ("POST", "/api/auth/token/release"),
    "getCurrentVersionOfServer": ("GET", "/api/versions/current"),
    "createAlertPlugin": ("POST", "/api/alertplugins"),
    "updateAlertPlugin": ("PUT", "/api/alertplugins"),
    "createNotificationPluginRule": ("POST", "/api/notifications/rules"),
}

BASE_PATH = "/suite-api"
OPS_TOKEN = "moonshiner-loopback-ops-token"
SEED_USER = "seed-user"
SEED_PASSWORD = "seed-password"
PRODUCT_VERSION = "VCF Operations 9.0.0.0"

# Deterministic identifiers handed out by createAlertPlugin, in call order.
PLUGIN_IDS = (
    "6f0f9d9a-1f6f-4a1e-9a0f-2c7a5d8b3e10",
    "b3c1a77e-5d42-4f3b-8c19-7a2e4d6f9b21",
    "0d5e2c48-9a63-4e57-b2d8-1f4c6a9e3d32",
)
RULE_IDS = (
    "9a7c1e35-2b48-4d61-8f03-5c9e7a2b1d40",
    "4e8b6d12-7c39-4a58-b1e6-3d0f9c5a7e51",
)

# The 9.0.0.0 contract has no token-exchange operation; 9.1.0.0 adds
# exchangeOpsTokenWithJwtToken at this target. Kept here only so the verifier
# can prove the service refuses it.
OFF_CONTRACT_PROBE = ("POST", "/api/auth/token/exchange")


class _ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, contract: dict, request_log: Path):
        super().__init__(address, handler)
        self.contract = contract
        self.request_log = request_log
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.plugins: dict[str, dict] = {}
        self.plugin_calls = 0
        self.rule_calls = 0
        self.routes = {
            (operation["method"], BASE_PATH + operation["path"]): operation_id
            for operation_id, operation in contract["operations"].items()
        }

    def append_request(self, record: dict) -> None:
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")

    def next_plugin_id(self) -> str:
        with self.state_lock:
            index = self.plugin_calls
            self.plugin_calls += 1
        if index >= len(PLUGIN_IDS):
            raise IndexError("createAlertPlugin was called more times than expected")
        return PLUGIN_IDS[index]

    def next_rule_id(self) -> str:
        with self.state_lock:
            index = self.rule_calls
            self.rule_calls += 1
        if index >= len(RULE_IDS):
            raise IndexError("createNotificationPluginRule succeeded more than expected")
        return RULE_IDS[index]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOpsContractMock/1.0"

    def _handle(self) -> None:
        split = urlsplit(self.path)
        operation_id = self.server.routes.get((self.command, split.path))
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None

        self.server.append_request(
            {
                "method": self.command,
                "rawTarget": self.path,
                "path": split.path,
                "query": parse_qs(split.query, keep_blank_values=True),
                "headers": {key.casefold(): value for key, value in self.headers.items()},
                "body": text,
                "json": parsed,
                "operationId": operation_id,
                "offContract": operation_id is None,
            }
        )

        if operation_id is None:
            self._json(404, {"message": "operation is not present in the pinned contract"})
            return

        handler = {
            "acquireToken": self._acquire_token,
            "releaseToken": self._release_token,
            "getCurrentVersionOfServer": self._current_version,
            "createAlertPlugin": self._create_alert_plugin,
            "updateAlertPlugin": self._update_alert_plugin,
            "createNotificationPluginRule": self._create_rule,
        }[operation_id]
        handler(parsed)

    # -- operations ------------------------------------------------------

    def _acquire_token(self, body: object) -> None:
        if not isinstance(body, dict):
            self._json(401, {"message": "a username-password body is required"})
            return
        if body.get("username") != SEED_USER or body.get("password") != SEED_PASSWORD:
            self._json(401, {"message": "authentication failed"})
            return
        self._json(
            200,
            {
                "token": OPS_TOKEN,
                "validity": 4102444799000,
                "expiresAt": "Thursday, December 31, 2099 11:59:59 PM UTC",
                "roles": ["ContentAdmin"],
            },
        )

    def _release_token(self, _body: object) -> None:
        self._json(200, {"message": "session terminated"})

    def _current_version(self, _body: object) -> None:
        self._json(
            200,
            {
                "releaseName": PRODUCT_VERSION,
                "major": 9,
                "minor": 0,
                "minorMinor": 0,
                "releasedDate": 1746057600000,
                "humanlyReadableReleaseDate": "May 01, 2025",
            },
        )

    def _create_alert_plugin(self, body: object) -> None:
        if not isinstance(body, dict) or not body.get("name") or not body.get("pluginTypeId"):
            self._json(422, {"message": "name and pluginTypeId are required"})
            return
        plugin_id = self.server.next_plugin_id()
        stored = dict(body)
        stored["pluginId"] = plugin_id
        stored.setdefault("enabled", False)
        with self.server.state_lock:
            self.server.plugins[plugin_id] = stored
        self._json(201, stored)

    def _update_alert_plugin(self, body: object) -> None:
        if not isinstance(body, dict):
            self._json(422, {"message": "a notification-plugin body is required"})
            return
        plugin_id = body.get("pluginId")
        with self.server.state_lock:
            known = self.server.plugins.get(plugin_id)
        if known is None:
            self._json(404, {"message": f"unknown plugin instance {plugin_id!r}"})
            return
        if not body.get("name") or not body.get("pluginTypeId"):
            self._json(422, {"message": "name and pluginTypeId are required"})
            return
        merged = dict(body)
        with self.server.state_lock:
            self.server.plugins[plugin_id] = merged
        self._json(200, merged)

    def _create_rule(self, body: object) -> None:
        if not isinstance(body, dict) or not body.get("name") or not body.get("pluginId"):
            self._json(422, {"message": "name and pluginId are required"})
            return
        plugin_id = body.get("pluginId")
        with self.server.state_lock:
            known = self.server.plugins.get(plugin_id)
        if known is None:
            self._json(404, {"message": f"unknown plugin instance {plugin_id!r}"})
            return
        if not known.get("enabled"):
            self._json(
                422,
                {
                    "message": "the referenced notification plugin instance is disabled",
                    "httpStatusCode": 422,
                    "apiErrorCode": 10022,
                },
            )
            return
        # A rule is only accepted when it names a notification template that
        # exists on this server.
        template_id = body.get("templateId")
        if not template_id:
            self._json(
                422,
                {
                    "message": (
                        "notification rule requires an existing notification template"
                    ),
                    "httpStatusCode": 422,
                    "apiErrorCode": 10041,
                },
            )
            return
        if template_id != KNOWN_TEMPLATE_ID:
            self._json(
                422,
                {
                    "message": f"unknown notification template {template_id!r}",
                    "httpStatusCode": 422,
                    "apiErrorCode": 10042,
                },
            )
            return
        created = dict(body)
        created["id"] = self.server.next_rule_id()
        self._json(201, created)

    # -- plumbing --------------------------------------------------------

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, _format: str, *args: object) -> None:
        return


KNOWN_TEMPLATE_ID = "tmpl-critical-webhook"


class ContractPinnedVcfOps(AbstractContextManager):
    """Run the contract-pinned mock and retain its JSONL request log."""

    def __init__(self, contract_path: Path, request_log: Path, cert: Path, key: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            operation_id: (operation["method"], operation["path"])
            for operation_id, operation in contract["operations"].items()
        }
        if actual != EXPECTED_OPERATIONS:
            raise ValueError(f"contract operation mismatch: {actual!r}")
        if contract["source"]["basePath"] != BASE_PATH:
            raise ValueError("contract base path is not /suite-api")
        request_log.write_text("", encoding="utf-8")
        self.server = _ContractServer(
            ("127.0.0.1", 0), _Handler, contract=contract, request_log=request_log
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.request_log = request_log

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False

    def requests(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.request_log.read_text(encoding="utf-8").splitlines()
            if line
        ]
