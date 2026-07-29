"""Contract-pinned loopback SDDC Manager used only by the acceptance verifier."""

from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit


CONTRACT = json.loads(
    (Path(__file__).parent / "docs" / "contract.json").read_text(encoding="utf-8")
)


def _operation_index():
    found = {}
    for path, path_item in CONTRACT["paths"].items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            found[operation["operationId"]] = {
                "method": method.upper(),
                "path": path,
                "operation": operation,
            }
    return found


OPERATIONS = _operation_index()


def _operation_for(method, path):
    for operation_id, entry in OPERATIONS.items():
        if method != entry["method"]:
            continue
        template = entry["path"]
        if template == path:
            return operation_id
        if template.endswith("/{id}") and path.startswith(template[:-4]):
            value = path[len(template) - 4 :]
            if value and "/" not in value:
                return operation_id
    return None


def _success_status(operation_id):
    responses = OPERATIONS[operation_id]["operation"]["responses"]
    return min(int(status) for status in responses if status.isdigit() and status[0] == "2")


class _Handler(BaseHTTPRequestHandler):
    server_version = "ContractPinnedSddc/1"

    def log_message(self, format, *args):
        return

    def _capture(self):
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        operation_id = _operation_for(self.command, split.path)
        record = {
            "operationId": operation_id,
            "method": self.command,
            "path": split.path,
            "query": split.query,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "raw_body": raw,
            "json": json.loads(raw.decode("utf-8")) if raw else None,
        }
        self.server.request_log.append(record)
        return operation_id

    def _json(self, status, body):
        raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _dispatch(self):
        operation_id = self._capture()
        if operation_id is None:
            self._json(404, {"errorCode": "NOT_FOUND", "message": "operation not served"})
            return
        if operation_id == self.server.error_operation_id:
            self._json(
                self.server.error_status,
                {
                    "errorCode": "VCF_HOST_VALIDATION_REJECTED",
                    "message": "validation request rejected",
                    "referenceToken": "reference-001",
                },
            )
            return
        response_status = self.server.status_overrides.get(
            operation_id, _success_status(operation_id)
        )

        if operation_id == "validateHostCommissionSpec":
            self._json(
                response_status,
                {
                    "id": self.server.validation_id,
                    "description": "Host commission validation",
                    "executionStatus": "IN_PROGRESS",
                    "resultStatus": "UNKNOWN",
                },
            )
            return

        if operation_id == "getHostCommissionValidationByID":
            self.server.poll_count += 1
            if self.server.poll_count <= self.server.in_progress_polls:
                execution_status = "IN_PROGRESS"
                result_status = "UNKNOWN"
                checks = []
            else:
                execution_status = self.server.final_execution_status
                result_status = self.server.final_result_status
                checks = self.server.validation_checks
            self._json(
                response_status,
                {
                    "id": self.server.validation_id,
                    "description": "Host commission validation",
                    "executionStatus": execution_status,
                    "resultStatus": result_status,
                    "validationChecks": checks,
                },
            )
            return

        self.server.commission_count += 1
        self._json(
            response_status,
            {
                "id": "task-001",
                "name": "Commission hosts",
                "status": "IN_PROGRESS",
                "creationTimestamp": "2026-07-28T00:00:00Z",
            },
        )

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch


class MockSddc(AbstractContextManager):
    """Run the three contract operations on an ephemeral loopback port."""

    def __init__(
        self,
        *,
        final_execution_status="COMPLETED",
        final_result_status="SUCCEEDED",
        in_progress_polls=1,
        validation_checks=None,
        error_operation_id=None,
        error_status=400,
        status_overrides=None,
    ):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.request_log = []
        self._server.validation_id = "validation-001"
        self._server.poll_count = 0
        self._server.commission_count = 0
        self._server.final_execution_status = final_execution_status
        self._server.final_result_status = final_result_status
        self._server.in_progress_polls = in_progress_polls
        self._server.validation_checks = list(validation_checks or [])
        self._server.error_operation_id = error_operation_id
        self._server.error_status = error_status
        self._server.status_overrides = dict(status_overrides or {})
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def request_log(self):
        return self._server.request_log

    @property
    def commission_count(self):
        return self._server.commission_count

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        return False
