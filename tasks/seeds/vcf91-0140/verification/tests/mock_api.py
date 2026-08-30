#!/usr/bin/env python3
"""Contract-pinned loopback vCenter and VKS Cluster API mock."""

from __future__ import annotations

import argparse
import copy
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit


def compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class State:
    """Runtime data and contract-derived route allow-list."""

    def __init__(
        self,
        contract_path: Path,
        config_path: Path,
        log_path: Path,
    ) -> None:
        self.contract = json.loads(
            contract_path.read_text(encoding="utf-8")
        )
        self.config = json.loads(
            config_path.read_text(encoding="utf-8")
        )
        self.log_path = log_path
        self.lock = threading.Lock()
        self.discovery_count = 0
        self.collection_index = -1
        self.active_reverse = False

        operations = self.contract["operations"]
        if len(operations) != 1:
            raise ValueError(
                "focused contract must contain one VMware operation"
            )
        discovery = operations[0]
        self.discovery_operation = discovery["operationId"]
        self.discovery_method = discovery["method"]
        self.discovery_path = discovery["wirePath"]

        kubernetes_operations = self.contract[
            "kubernetesIntegration"
        ]["operations"]
        if len(kubernetes_operations) != 1:
            raise ValueError(
                "focused contract must contain one Kubernetes operation"
            )
        clusters = kubernetes_operations[0]
        self.cluster_operation = clusters["operationKey"]
        self.cluster_method = clusters["method"]
        self.cluster_path_template = clusters["pathTemplate"]
        self.allowed_operations = {
            self.discovery_operation,
            self.cluster_operation,
        }

    def append_log(self, record: dict[str, Any]) -> None:
        line = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

    def discovery_payload(
        self,
        authority: str,
    ) -> list[dict[str, str]]:
        selected = {
            "namespace": self.config["namespace"],
            "master_host": authority,
        }
        distractor = {
            "namespace": self.config["distractor_namespace"],
            "master_host": authority,
        }
        with self.lock:
            reverse = self.discovery_count % 2 == 1
            self.discovery_count += 1
        summaries = [distractor, selected]
        if reverse:
            summaries.reverse()
        return summaries

    def cluster_payload(
        self,
        query_pairs: list[tuple[str, str]],
    ) -> tuple[int, dict[str, Any]]:
        expected_limit = str(self.config["page_size"])
        if not query_pairs or query_pairs[0] != (
            "limit",
            expected_limit,
        ):
            return 400, {"error": "invalid pagination limit"}

        supplied_continue: str | None = None
        if len(query_pairs) == 2 and query_pairs[1][0] == "continue":
            supplied_continue = query_pairs[1][1]
        elif len(query_pairs) != 1:
            return 400, {"error": "invalid query shape"}

        markers: list[str] = self.config["markers"]
        if supplied_continue is None:
            with self.lock:
                self.collection_index += 1
                self.active_reverse = self.collection_index % 2 == 1
                reverse = self.active_reverse
            page_number = 0
        else:
            try:
                page_number = markers.index(supplied_continue) + 1
            except ValueError:
                return 400, {"error": "unknown continuation"}
            with self.lock:
                reverse = self.active_reverse
        with self.lock:
            collection_index = self.collection_index

        pages: list[list[dict[str, Any]]] = self.config["pages"]
        if page_number >= len(pages):
            return 400, {"error": "continuation beyond final page"}

        items = list(pages[page_number])
        if reverse:
            items.reverse()
        if (
            self.config.get("fault") == "wrong_item_namespace"
            and self.config.get("fault_collection")
            == collection_index
            and page_number == 0
            and items
        ):
            items = copy.deepcopy(items)
            items[0]["metadata"]["namespace"] = self.config[
                "distractor_namespace"
            ]
        metadata: dict[str, Any] = {
            "resourceVersion": self.config["resource_version"],
        }
        if page_number < len(markers):
            metadata["continue"] = markers[page_number]
            metadata["remainingItemCount"] = sum(
                len(page) for page in pages[page_number + 1 :]
            )

        return 200, {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "ClusterList",
            "metadata": metadata,
            "items": items,
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MoonshinerContractMock/1.0"
    sys_version = ""

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            return b""
        return self.rfile.read(max(0, length))

    def _operation_for_path(self, path: str) -> str | None:
        if path == self.state.discovery_path:
            return self.state.discovery_operation
        expected = self.state.cluster_path_template.replace(
            "{namespace}",
            self.state.config["namespace"],
        )
        if unquote(path) == expected:
            return self.state.cluster_operation
        return None

    def _record(
        self,
        operation: str | None,
        body: bytes,
    ) -> None:
        split = urlsplit(self.path)
        self.state.append_log(
            {
                "operation": operation,
                "method": self.command,
                "raw_target": self.path,
                "path": split.path,
                "query_pairs": parse_qsl(
                    split.query,
                    keep_blank_values=True,
                    strict_parsing=False,
                ),
                "headers": [
                    {"name": name, "value": value}
                    for name, value in self.headers.raw_items()
                ],
                "body_length": len(body),
                "body_utf8": body.decode(
                    "utf-8",
                    errors="replace",
                ),
            }
        )

    def _send_json(self, status: int, value: Any) -> None:
        payload = compact_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _one_header(self, name: str) -> str | None:
        values = self.headers.get_all(name, failobj=[])
        if len(values) != 1:
            return None
        return values[0]

    def do_GET(self) -> None:  # noqa: N802
        body = self._read_body()
        split = urlsplit(self.path)
        operation = self._operation_for_path(split.path)
        self._record(operation, body)

        if operation not in self.state.allowed_operations:
            self._send_json(
                404,
                {"error": "operation not present in focused contract"},
            )
            return
        if operation == self.state.discovery_operation:
            if self.command != self.state.discovery_method:
                self._send_json(405, {"error": "method not allowed"})
                return
            if split.query:
                self._send_json(
                    400,
                    {"error": "namespace discovery query is forbidden"},
                )
                return
            if (
                self._one_header("vmware-api-session-id")
                != self.state.config["vcenter_session_id"]
            ):
                self._send_json(
                    401,
                    {"error": "invalid vCenter session"},
                )
                return
            authority = f"127.0.0.1:{self.server.server_port}"
            self._send_json(
                200,
                self.state.discovery_payload(authority),
            )
            return

        if self.command != self.state.cluster_method:
            self._send_json(405, {"error": "method not allowed"})
            return
        if (
            self._one_header("Authorization")
            != "Bearer " + self.state.config["kubernetes_token"]
        ):
            self._send_json(
                401,
                {"error": "invalid Kubernetes bearer token"},
            )
            return
        status, payload = self.state.cluster_payload(
            parse_qsl(
                split.query,
                keep_blank_values=True,
                strict_parsing=False,
            )
        )
        self._send_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method()

    def _reject_method(self) -> None:
        body = self._read_body()
        path = urlsplit(self.path).path
        self._record(self._operation_for_path(path), body)
        self._send_json(405, {"error": "method not allowed"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.log.write_text("", encoding="utf-8")
    state = State(args.contract, args.config, args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state  # type: ignore[attr-defined]

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(
            target=server.shutdown,
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    args.port_file.write_text(
        f"{server.server_port}\n",
        encoding="ascii",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
