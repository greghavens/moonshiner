"""Contract-pinned loopback Snapshot Appliance used by the protected verifier.

The server has no built-in knowledge of the REST surface: it reads
``docs/contract.json`` and refuses to start unless that projection names exactly
the two vSAN Data Protection operations in scope. Routes, the accepted query
field vocabulary and the field kinds all come from the contract, so a contract
that drifts from the pinned specification produces a server that no longer
answers. Every request is appended to a JSON Lines log for wire assertions.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


EXPECTED_OPERATION_IDS = {
    "Snapservice.Sessions_create",
    "Snapservice.VirtualMachines.Snapshots_list",
}

SESSION_HEADER = "vmware-api-session-id"


def _error(error_type: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build a Vapi.Std.Errors.Error shaped body as projected in the contract."""

    body: dict[str, Any] = {
        "error_type": error_type,
        "messages": [
            {
                "id": "com.vmware.snapservice.mock",
                "default_message": message,
                "args": [],
            }
        ],
    }
    body.update(extra)
    return body


class MockSnapservice:
    """Serve only the operation set projected into the protected contract."""

    def __init__(
        self,
        contract_path: Path,
        log_path: Path,
        *,
        dataset: list[dict[str, Any]],
        username: str,
        password: str,
        session_token: str,
        max_page_size: int = 20,
        default_page_size: int = 10,
    ) -> None:
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        operations = contract["operations"]
        operation_ids = {item["operationId"] for item in operations}
        if operation_ids != EXPECTED_OPERATION_IDS or len(operations) != 2:
            raise AssertionError(
                f"unexpected mock contract operations: {sorted(operation_ids)}"
            )

        base_path = contract["server"]["base_path"]
        self.routes: dict[tuple[str, str], str] = {}
        for item in operations:
            wire_path = base_path + item["path"]
            if item.get("wire_path") != wire_path:
                raise AssertionError(
                    f"contract wire_path disagrees with base_path + path for "
                    f"{item['operationId']}"
                )
            self.routes[(item["method"], wire_path)] = item["operationId"]
        if len(self.routes) != len(operations):
            raise AssertionError("contract contains duplicate method/path routes")

        list_operation = next(
            item
            for item in operations
            if item["operationId"] == "Snapservice.VirtualMachines.Snapshots_list"
        )
        self.query_kinds = {
            field["name"]: field["kind"]
            for field in list_operation["query_parameters"]
        }
        if len(self.query_kinds) != len(list_operation["query_parameters"]):
            raise AssertionError("contract declares a duplicate query field")

        self.log_path = Path(log_path)
        self.dataset = dataset
        self.username = username
        self.password = password
        self.session_token = session_token
        self.max_page_size = max_page_size
        self.default_page_size = default_page_size
        self._log_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def service_root(self) -> str:
        if self._server is None:
            raise RuntimeError("mock server is not running")
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    # -- request handling ------------------------------------------------

    def _expected_basic(self) -> str:
        raw = f"{self.username}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _select(self, fields: dict[str, list[str]]) -> tuple[list[dict], int]:
        """Apply the contract's filter fields, then the appliance ordering."""

        rows = list(self.dataset)
        clusters = fields.get("clusters")
        if clusters:
            rows = [row for row in rows if row["cluster"] in clusters]
        bios_uuids = fields.get("vm_bios_uuids")
        if bios_uuids:
            rows = [
                row for row in rows if row["item"].get("vm_bios_uuid") in bios_uuids
            ]
        snapshot_ids = fields.get("snapshots")
        if snapshot_ids:
            rows = [row for row in rows if row["item"]["snapshot"] in snapshot_ids]
        created_after = fields.get("created_after")
        if created_after:
            rows = [
                row
                for row in rows
                if row["item"]["creation_time"] >= created_after[0]
            ]
        created_before = fields.get("created_before")
        if created_before:
            rows = [
                row
                for row in rows
                if row["item"]["creation_time"] <= created_before[0]
            ]

        # The specification only promises creation_time ordering, so ties come
        # back in an order the client must not depend on.
        rows.sort(key=lambda row: row["item"]["snapshot"], reverse=True)
        rows.sort(key=lambda row: row["item"]["creation_time"])

        per_vm = fields.get("snapshots_per_vm")
        if per_vm:
            limit = int(per_vm[0])
            seen: dict[str, int] = {}
            kept = []
            for row in rows:
                vm = row["item"]["vm"]
                seen[vm] = seen.get(vm, 0) + 1
                if seen[vm] <= limit:
                    kept.append(row)
            rows = kept

        return rows, len(rows)

    def _list_snapshots(self, query: str) -> tuple[int, dict[str, Any]]:
        pairs = parse_qsl(query, keep_blank_values=True)
        fields: dict[str, list[str]] = {}
        for name, value in pairs:
            kind = self.query_kinds.get(name)
            if kind is None:
                return 400, _error(
                    "INVALID_ARGUMENT",
                    f"unknown query field {name!r} for "
                    "Snapservice.VirtualMachines.Snapshots_list",
                )
            if value == "":
                return 400, _error(
                    "INVALID_ARGUMENT",
                    f"query field {name!r} was sent with an empty value; unset "
                    "fields must be omitted",
                )
            if kind == "scalar" and name in fields:
                return 400, _error(
                    "INVALID_ARGUMENT",
                    f"query field {name!r} is not repeatable",
                )
            fields.setdefault(name, []).append(value)

        page_size = self.default_page_size
        if "page_size" in fields:
            try:
                page_size = int(fields["page_size"][0])
            except ValueError:
                return 400, _error("INVALID_ARGUMENT", "page_size is not an integer")
            if page_size < 1:
                return 400, _error("INVALID_ARGUMENT", "page_size must be positive")
            page_size = min(page_size, self.max_page_size)

        offset = 0
        if "offset" in fields:
            try:
                offset = int(fields["offset"][0])
            except ValueError:
                return 400, _error("INVALID_ARGUMENT", "offset is not an integer")
            if offset < 0:
                return 400, _error(
                    "INVALID_ARGUMENT", "offset must not be negative"
                )

        if "snapshots_per_vm" in fields:
            try:
                int(fields["snapshots_per_vm"][0])
            except ValueError:
                return 400, _error(
                    "INVALID_ARGUMENT", "snapshots_per_vm is not an integer"
                )

        rows, total = self._select(fields)
        window = rows[offset : offset + page_size]
        return 200, {
            "snapshots": [row["item"] for row in window],
            "total_count": total,
        }

    def __enter__(self) -> "MockSnapservice":
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def do_PUT(self) -> None:  # noqa: N802
                self._handle()

            def do_PATCH(self) -> None:  # noqa: N802
                self._handle()

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle()

            def _handle(self) -> None:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                body = self.rfile.read(max(content_length, 0))
                split = urlsplit(self.path)
                record = {
                    "method": self.command,
                    "target": self.path,
                    "path": split.path,
                    "query": split.query,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                    "body": body.decode("utf-8", errors="replace"),
                }
                with parent._log_lock:
                    with parent.log_path.open(
                        "a", encoding="utf-8", newline="\n"
                    ) as log_file:
                        log_file.write(
                            json.dumps(
                                record, sort_keys=True, separators=(",", ":")
                            )
                            + "\n"
                        )

                operation_id = parent.routes.get((self.command, split.path))
                if operation_id == "Snapservice.Sessions_create":
                    self._create_session(split.query)
                elif operation_id == "Snapservice.VirtualMachines.Snapshots_list":
                    self._list(split.query)
                else:
                    self._send_json(
                        404,
                        _error(
                            "NOT_FOUND",
                            "this appliance serves only the operations named by "
                            "the pinned contract",
                        ),
                    )

            def _create_session(self, query: str) -> None:
                if query:
                    self._send_json(
                        400,
                        _error(
                            "INVALID_ARGUMENT",
                            "Snapservice.Sessions_create declares no parameters",
                        ),
                    )
                    return
                if self.headers.get("Authorization") != parent._expected_basic():
                    self._send_json(
                        401,
                        _error(
                            "UNAUTHENTICATED",
                            "credentials were rejected",
                            challenge='Basic realm="snapservice"',
                        ),
                    )
                    return
                self._send_json(201, parent.session_token)

            def _list(self, query: str) -> None:
                if self.headers.get(SESSION_HEADER) != parent.session_token:
                    self._send_json(
                        401,
                        _error(
                            "UNAUTHENTICATED",
                            "a valid session token is required",
                            challenge='SIGN realm="snapservice"',
                        ),
                    )
                    return
                response = parent._list_snapshots(query)
                status, payload = response[:2]
                headers = response[2] if len(response) == 3 else None
                self._send_json(status, payload, headers=headers)

            def _send_json(
                self,
                status: int,
                payload: Any,
                *,
                headers: dict[str, str] | None = None,
            ) -> None:
                encoded = json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf91-snapservice-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
