"""Contract-pinned loopback server for the focused VCF Automation scenario."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit


EXPECTED_CONTRACT_FORMAT = "focused-reference-documentation-projection-v1"
EXPECTED_OPERATION_NAMES = ["Get Deployments 1"]
EXPECTED_SOURCE_PAGE = (
    "https://developer.broadcom.com/xapis/vm-apps-org-deployment/9.0/"
    "deployment/api/deployments/get/"
)

DEFAULT_DEPLOYMENTS: list[dict[str, Any]] = [
    {
        "id": "a-2",
        "name": "alpha",
        "projectId": "proj A/B",
        "status": "CREATE_SUCCESSFUL",
    },
    {
        "id": "d-4",
        "name": "delta",
        "projectId": "proj-other",
        "status": "CREATE_SUCCESSFUL",
    },
    {
        "id": "a-1",
        "name": "alpha",
        "projectId": "proj A/B",
        "status": "CREATE_SUCCESSFUL",
    },
    {
        "id": "c-3",
        "name": "charlie",
        "projectId": "proj-other",
        "status": "CREATE_FAILED",
    },
    {
        "id": "b-2",
        "name": "bravo",
        "projectId": "proj A/B",
        "status": "CREATE_SUCCESSFUL",
    },
]


class ContractMockServer:
    """Serve only operations named by the focused reference-derived contract."""

    def __init__(
        self,
        contract_path: str | Path,
        *,
        response_status: int = 200,
        response_payload: Any = None,
    ) -> None:
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        source = contract.get("source", {})
        if (
            contract.get("contractFormat") != EXPECTED_CONTRACT_FORMAT
            or source.get("kind") != "authoritative-reference-documentation"
            or source.get("publishedSpecification") is not False
            or source.get("pageUrls") != [EXPECTED_SOURCE_PAGE]
        ):
            raise RuntimeError("mock contract is not pinned to the reference source")

        operations = contract.get("operations", [])
        if [entry.get("name") for entry in operations] != EXPECTED_OPERATION_NAMES:
            raise RuntimeError("mock contract operation set changed")
        self._routes = {
            entry["name"]: (entry["method"], entry["path"])
            for entry in operations
        }
        self._query_names = {
            parameter["name"]
            for parameter in operations[0].get("queryParameters", [])
        }
        self.request_log: list[dict[str, Any]] = []
        self._response_status = response_status
        self._response_payload = response_payload
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("server is not running")
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMockServer":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _record(self) -> bytes:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                owner.request_log.append(
                    {
                        "method": self.command,
                        "target": self.path,
                        "headers": {
                            key.lower(): value for key, value in self.headers.items()
                        },
                        "body": body,
                    }
                )
                return body

            def _json_response(self, status: int, payload: Any) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _empty_response(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                self._record()
                route = owner._routes.get("Get Deployments 1")
                parsed = urlsplit(self.path)
                if route is None or route[0] != "GET" or parsed.path != route[1]:
                    self._empty_response(404)
                    return

                query = parse_qs(parsed.query, keep_blank_values=True)
                if not set(query) <= owner._query_names:
                    self._empty_response(400)
                    return
                try:
                    page = int(query.get("page", ["0"])[0])
                    size = int(query.get("size", ["20"])[0])
                except (TypeError, ValueError):
                    self._empty_response(400)
                    return
                if page < 0 or size < 1:
                    self._empty_response(400)
                    return

                if owner._response_status != 200:
                    self._json_response(
                        owner._response_status,
                        {"status": owner._response_status},
                    )
                    return
                if owner._response_payload is not None:
                    self._json_response(200, owner._response_payload)
                    return

                deployments = list(DEFAULT_DEPLOYMENTS)
                project_values = query.get("projects")
                if project_values is not None:
                    accepted = {
                        value
                        for group in project_values
                        for value in group.split(",")
                    }
                    deployments = [
                        item for item in deployments if item["projectId"] in accepted
                    ]
                status_values = query.get("status")
                if status_values is not None:
                    accepted = {
                        value
                        for group in status_values
                        for value in group.split(",")
                    }
                    deployments = [
                        item for item in deployments if item["status"] in accepted
                    ]

                sort_values = query.get("sort", ["createdAt,DESC"])
                if sort_values == ["name,ASC"]:
                    # Deliberately preserve source order for equal names. The API
                    # sort drives paging; the client owns the requested id tie-break.
                    deployments.sort(key=lambda item: item["name"])
                elif sort_values != ["createdAt,DESC"]:
                    self._empty_response(400)
                    return

                total_elements = len(deployments)
                total_pages = (total_elements + size - 1) // size
                start = page * size
                content = deployments[start : start + size]
                payload = {
                    "content": content,
                    "empty": not content,
                    "first": page == 0,
                    "last": page + 1 >= total_pages,
                    "number": page,
                    "numberOfElements": len(content),
                    "size": size,
                    "totalElements": total_elements,
                    "totalPages": total_pages,
                }
                self._json_response(200, payload)

            def _reject_other_method(self) -> None:
                self._record()
                self._empty_response(404)

            do_DELETE = _reject_other_method
            do_PATCH = _reject_other_method
            do_POST = _reject_other_method
            do_PUT = _reject_other_method

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        assert self._httpd is not None
        assert self._thread is not None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None
