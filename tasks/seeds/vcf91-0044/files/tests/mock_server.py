#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager fixture for TestMain."""

from __future__ import annotations

import argparse
import io
import json
import secrets
import signal
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


EXPECTED_OPERATIONS = {
    "getTask": ("GET", "/v1/tasks/{id}"),
    "getNotifications": ("GET", "/v1/notifications"),
    "startSupportBundle": ("POST", "/v1/system/support-bundles"),
    "getSupportBundleStatus": ("GET", "/v1/system/support-bundles/{id}"),
    "exportSupportBundleByID": (
        "GET",
        "/v1/system/support-bundles/{id}/data",
    ),
}


def runtime_value(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


def load_contract(path: Path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    actual = {
        operation["operationId"]: (operation["method"], operation["path"])
        for operation in document["operations"]
    }
    if actual != EXPECTED_OPERATIONS:
        raise RuntimeError("focused contract operation set changed")


def make_archive(values: dict[str, str]) -> bytes:
    records = [
        {
            "taskId": values["task_id"],
            "referenceToken": "wrong-reference",
            "eventId": values["event_id"],
            "cause": "uncorrelated reference token",
        },
        {
            "taskId": values["task_id"],
            "referenceToken": values["reference_token"],
            "eventId": "unrelated-event",
            "cause": "uncorrelated resource event",
        },
        {
            "taskId": values["task_id"],
            "referenceToken": values["reference_token"],
            "eventId": values["event_id"],
            "cause": values["cause"],
        },
    ]
    log_data = (
        "\n".join(
            json.dumps(record, separators=(",", ":"), ensure_ascii=False)
            for record in records
        )
        + "\n"
    ).encode("utf-8")

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.USTAR_FORMAT) as archive:
        manifest = b"generated loopback support bundle\n"
        manifest_info = tarfile.TarInfo("manifest.txt")
        manifest_info.size = len(manifest)
        manifest_info.mtime = 0
        archive.addfile(manifest_info, io.BytesIO(manifest))

        log_info = tarfile.TarInfo("logs/api/vcf-api.log")
        log_info.size = len(log_data)
        log_info.mtime = 0
        archive.addfile(log_info, io.BytesIO(log_data))
    return output.getvalue()


class State:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.polls = 0
        nonce = secrets.token_hex(5)
        self.values = {
            "access_token": runtime_value("access"),
            "task_id": f"failed task/{runtime_value('task')}",
            "resource_id": runtime_value("resource"),
            "event_id": runtime_value("event"),
            "reference_token": runtime_value("reference"),
            "bundle_id": runtime_value("bundle"),
            "cause": f"certificate thumbprint mismatch {nonce}",
        }
        self.archive = make_archive(self.values)

    def record(
        self,
        operation_id: str | None,
        method: str,
        target: str,
        path: str,
        query: str,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        with self.lock:
            self.sequence += 1
            entry = {
                "sequence": self.sequence,
                "operationId": operation_id,
                "method": method,
                "target": target,
                "path": path,
                "query": query,
                "headers": headers,
                "body": body.decode("utf-8", errors="replace"),
                "bodyLength": len(body),
            }
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def handler_type(state: State):
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

        def _dispatch(self) -> None:
            parsed = urlsplit(self.path)
            task_path = "/v1/tasks/" + quote(state.values["task_id"], safe="")
            bundle_path = (
                "/v1/system/support-bundles/"
                + quote(state.values["bundle_id"], safe="")
            )
            routes = {
                ("GET", task_path): "getTask",
                ("GET", "/v1/notifications"): "getNotifications",
                ("POST", "/v1/system/support-bundles"): "startSupportBundle",
                ("GET", bundle_path): "getSupportBundleStatus",
                ("GET", bundle_path + "/data"): "exportSupportBundleByID",
            }
            operation_id = routes.get((self.command, parsed.path))
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            headers = {
                name.lower(): value
                for name, value in self.headers.items()
            }
            state.record(
                operation_id,
                self.command,
                self.path,
                parsed.path,
                parsed.query,
                headers,
                body,
            )

            if operation_id is None:
                self._reply(404, "application/json", b'{"errorCode":"NOT_FOUND"}')
                return
            expected_accept = (
                "application/octet-stream"
                if operation_id == "exportSupportBundleByID"
                else "application/json"
            )
            if (
                headers.get("authorization")
                != "Bearer " + state.values["access_token"]
                or headers.get("accept") != expected_accept
            ):
                self._reply(401, "application/json", b'{"errorCode":"UNAUTHORIZED"}')
                return

            if operation_id == "getTask":
                payload = {
                    "id": state.values["task_id"],
                    "name": "Expand workload domain",
                    "type": "DOMAIN_EXPANSION",
                    "status": "FAILED",
                    "creationTimestamp": "2026-07-29T09:00:00Z",
                    "errors": [
                        {
                            "errorCode": "VCF_DIAGNOSTIC_REQUIRED",
                            "message": "The operation failed; inspect correlated evidence.",
                            "referenceToken": state.values["reference_token"],
                        }
                    ],
                    "resources": [
                        {
                            "resourceId": state.values["resource_id"],
                            "type": "VCENTER",
                            "name": "runtime-vcenter",
                        }
                    ],
                }
                self._json(200, payload)
            elif operation_id == "getNotifications":
                payload = [
                    {
                        "type": "RESOURCE_HEALTH",
                        "severity": "WARNING",
                        "message": {
                            "id": "unrelated-event",
                            "localizedMessage": "An unrelated resource changed state.",
                        },
                        "resources": [
                            {
                                "id": "unrelated-resource",
                                "type": "VCENTER",
                                "name": "other-vcenter",
                            }
                        ],
                    },
                    {
                        "type": "TASK_FAILURE",
                        "severity": "ERROR",
                        "message": {
                            "id": state.values["event_id"],
                            "localizedMessage": "A managed resource rejected the operation.",
                        },
                        "creationTimestamp": "2026-07-29T09:01:00Z",
                        "resources": [
                            {
                                "id": state.values["resource_id"],
                                "type": "VCENTER",
                                "name": "runtime-vcenter",
                            }
                        ],
                    },
                ]
                self._json(200, payload)
            elif operation_id == "startSupportBundle":
                self._json(
                    202,
                    {"status": "PENDING", "id": state.values["bundle_id"]},
                )
            elif operation_id == "getSupportBundleStatus":
                with state.lock:
                    state.polls += 1
                    status = (
                        "IN_PROGRESS"
                        if state.polls == 1
                        else "COMPLETED_WITH_SUCCESS"
                    )
                self._json(
                    200,
                    {"status": status, "id": state.values["bundle_id"]},
                )
            else:
                self._reply(200, "application/octet-stream", state.archive)

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self._reply(status, "application/json", body)

        def _reply(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    args = parser.parse_args()

    load_contract(args.contract)
    state = State(args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_type(state))
    port_document = dict(state.values)
    port_document["port"] = server.server_address[1]
    pending_port_file = args.port_file.with_name(args.port_file.name + ".tmp")
    pending_port_file.write_text(
        json.dumps(port_document, separators=(",", ":")),
        encoding="utf-8",
    )
    pending_port_file.replace(args.port_file)

    signal.signal(
        signal.SIGTERM,
        lambda *_args: threading.Thread(target=server.shutdown, daemon=True).start(),
    )
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
