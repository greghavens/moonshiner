#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
API_VERSION = CONTRACT["apiVersion"]
CONTRACTED = {(item["method"], item["path"]) for item in CONTRACT["operations"]}
EXPECTED_CONTRACTED = {
    ("GET", "/iaas/api/projects"),
    ("GET", "/iaas/api/integrations"),
    ("PATCH", "/iaas/api/projects/{id}"),
}
if CONTRACTED != EXPECTED_CONTRACTED:
    raise RuntimeError(f"mock and contract operations differ: {CONTRACTED!r}")


@dataclass
class RequestRecord:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: Any
    status: int
    returned_ids: tuple[str, ...] = ()


@dataclass
class MockState:
    token: str
    projects: list[dict[str, Any]]
    integrations: list[dict[str, Any]]
    forced_statuses: dict[tuple[str, str], int] = field(default_factory=dict)
    requests: list[RequestRecord] = field(default_factory=list)
    effects: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: MockState):
        super().__init__(("127.0.0.1", 0), ContractHandler)
        self.state = state

    @property
    def base_uri(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/"


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_POST(self) -> None:
        self._reject_uncontracted("POST")

    def do_PUT(self) -> None:
        self._reject_uncontracted("PUT")

    def do_DELETE(self) -> None:
        self._reject_uncontracted("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        body = self._read_json() if method == "PATCH" else None

        forced_status = self.server.state.forced_statuses.get((method, parsed.path))
        if forced_status is not None:
            self._respond_and_record(
                method,
                parsed.path,
                query,
                body,
                forced_status,
                {"message": "forced failure"},
            )
            return

        if not self._authorized() or query.get("apiVersion") != [API_VERSION]:
            self._respond_and_record(method, parsed.path, query, body, 401, {"message": "unauthorized or bad apiVersion"})
            return

        if method == "GET" and parsed.path == "/iaas/api/projects":
            self._collection(method, parsed.path, query, body, self.server.state.projects)
            return
        if method == "GET" and parsed.path == "/iaas/api/integrations":
            self._collection(method, parsed.path, query, body, self.server.state.integrations)
            return

        match = re.fullmatch(r"/iaas/api/projects/([^/]+)", parsed.path)
        if method == "PATCH" and match:
            self._patch_project(method, parsed.path, query, body, unquote(match.group(1)))
            return
        self._respond_and_record(method, parsed.path, query, body, 404, {"message": "operation not in contract"})

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.server.state.token}"

    def _collection(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: Any,
        source: list[dict[str, Any]],
    ) -> None:
        if "application/json" not in self.headers.get("Accept", ""):
            self._respond_and_record(method, path, query, body, 406, {"message": "Accept must request JSON"})
            return
        values = source
        filters = query.get("$filter")
        if filters:
            matched = re.fullmatch(r"name\s+eq\s+'((?:''|[^'])*)'", filters[0])
            if not matched:
                self._respond_and_record(method, path, query, body, 400, {"message": "unsupported filter"})
                return
            expected_name = matched.group(1).replace("''", "'")
            values = [item for item in source if item.get("name") == expected_name]
        payload = {
            "content": copy.deepcopy(values),
            "totalElements": len(values),
            "numberOfElements": len(values),
        }
        self._respond_and_record(
            method,
            path,
            query,
            body,
            200,
            payload,
            tuple(str(item["id"]) for item in values),
        )

    def _patch_project(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: Any,
        project_id: str,
    ) -> None:
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._respond_and_record(method, path, query, body, 415, {"message": "Content-Type must be JSON"})
            return
        project = next((item for item in self.server.state.projects if item["id"] == project_id), None)
        if project is None:
            self._respond_and_record(method, path, query, body, 404, {"message": "project not found"})
            return
        if not isinstance(body, dict) or body.get("name") != project["name"] or not isinstance(body.get("customProperties"), dict):
            self._respond_and_record(method, path, query, body, 400, {"message": "bad project specification"})
            return
        integration_id = body["customProperties"].get("integrationId")
        earlier = list(self.server.state.requests)
        returned_projects = {item for record in earlier if record.path == "/iaas/api/projects" for item in record.returned_ids}
        returned_integrations = {item for record in earlier if record.path == "/iaas/api/integrations" for item in record.returned_ids}
        if project_id not in returned_projects or integration_id not in returned_integrations:
            self._respond_and_record(method, path, query, body, 409, {"message": "identifier was not returned by this client's lookup"})
            return
        with self.server.state.lock:
            if project.get("customProperties") != body["customProperties"]:
                self.server.state.effects += 1
                project["customProperties"] = copy.deepcopy(body["customProperties"])
        self._respond_and_record(method, path, query, body, 200, copy.deepcopy(project))

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _reject_uncontracted(self, method: str) -> None:
        parsed = urlsplit(self.path)
        self._respond_and_record(method, parsed.path, parse_qs(parsed.query), None, 404, {"message": "operation not in contract"})

    def _respond_and_record(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        body: Any,
        status: int,
        payload: Any,
        returned_ids: tuple[str, ...] = (),
    ) -> None:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        record = RequestRecord(
            method=method,
            path=path,
            query=copy.deepcopy(query),
            headers={key.lower(): value for key, value in self.headers.items()},
            body=copy.deepcopy(body),
            status=status,
            returned_ids=returned_ids,
        )
        with self.server.state.lock:
            self.server.state.requests.append(record)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def start_mock(
    *,
    token: str,
    projects: list[dict[str, Any]],
    integrations: list[dict[str, Any]],
    forced_statuses: dict[tuple[str, str], int] | None = None,
) -> tuple[ContractServer, threading.Thread]:
    state = MockState(
        token=token,
        projects=copy.deepcopy(projects),
        integrations=copy.deepcopy(integrations),
        forced_statuses=dict(forced_statuses or {}),
    )
    server = ContractServer(state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
