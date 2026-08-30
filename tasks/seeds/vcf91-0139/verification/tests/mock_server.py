#!/usr/bin/env python3
"""Contract-pinned loopback service for the protected verifier."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _compile_path(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9_]*)\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


class ContractState:
    def __init__(self, args: argparse.Namespace) -> None:
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        named: list[tuple[str, str, re.Pattern[str]]] = []
        for operation in contract["operations"]:
            named.append(
                (
                    operation["name"],
                    operation["method"],
                    _compile_path(operation["path"]),
                )
            )
        for operation in contract["kubernetesApi"]["operations"]:
            named.append(
                (
                    operation["name"],
                    operation["method"],
                    _compile_path(operation["path"]),
                )
            )
        self.routes = tuple(named)
        self.log_path = Path(args.log_file)
        self.namespace = args.namespace
        self.clusters = tuple(json.loads(args.clusters_json))
        self.before_versions = tuple(json.loads(args.before_versions_json))
        self.after_versions = tuple(json.loads(args.after_versions_json))
        if not (
            len(self.clusters)
            == len(self.before_versions)
            == len(self.after_versions)
            == 2
        ):
            raise ValueError("the verifier fixture requires two Cluster values")
        self.subject_token = args.subject_token
        self.old_token = args.old_access_token
        self.new_token = args.new_access_token
        self.token_issues = 0
        self.successful_patches = 0
        self.old_expired = False
        self.authority = ""
        self.lock = threading.Lock()

    def match(
        self, method: str, path: str
    ) -> tuple[str | None, dict[str, str]]:
        for name, allowed_method, pattern in self.routes:
            if method != allowed_method:
                continue
            match = pattern.fullmatch(path)
            if match is not None:
                values = {
                    key: urllib.parse.unquote(value)
                    for key, value in match.groupdict().items()
                }
                return name, values
        return None, {}

    def append_log(
        self,
        *,
        method: str,
        raw_target: str,
        headers: list[list[str]],
        body: bytes,
        operation: str | None,
        status: int,
    ) -> None:
        entry = {
            "sequence": None,
            "method": method,
            "raw_target": raw_target,
            "headers": headers,
            "body_length": len(body),
            "body_base64": base64.b64encode(body).decode("ascii"),
            "operation": operation,
            "status": status,
        }
        with self.lock:
            if self.log_path.exists():
                with self.log_path.open("rb") as existing:
                    entry["sequence"] = sum(1 for _ in existing)
            else:
                entry["sequence"] = 0
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ContractLoopback"
    sys_version = ""

    @property
    def state(self) -> ContractState:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length) if content_length else b""
        split = urllib.parse.urlsplit(self.path)
        operation, values = self.state.match(self.command, split.path)
        status, response, content_type = self._serve(operation, values, body)
        headers = [[name.lower(), value] for name, value in self.headers.raw_items()]
        self.state.append_log(
            method=self.command,
            raw_target=self.path,
            headers=headers,
            body=body,
            operation=operation,
            status=status,
        )
        self.send_response(status)
        if response:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        if response:
            self.wfile.write(response)

    def _serve(
        self,
        operation: str | None,
        values: dict[str, str],
        body: bytes,
    ) -> tuple[int, bytes, str]:
        if operation is None:
            return self._json(404, {"error": "operation_not_in_contract"})
        if operation == "vcenter.token.issue":
            return self._issue_token()
        if operation == "vcenter.namespace.listAuthorized":
            return self._list_namespaces()
        if operation == "kubernetes.cluster.get":
            return self._get_cluster(values)
        if operation == "kubernetes.cluster.patch":
            return self._patch_cluster(values, body)
        return self._json(404, {"error": "operation_not_in_contract"})

    def _issue_token(self) -> tuple[int, bytes, str]:
        authorization = self.headers.get("Authorization")
        if authorization != f"Bearer {self.state.subject_token}":
            return self._json(400, {"error": "invalid_request"})
        with self.state.lock:
            token = (
                self.state.old_token
                if self.state.token_issues == 0
                else self.state.new_token
            )
            self.state.token_issues += 1
        return self._json(
            200,
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": 30,
            },
        )

    def _list_namespaces(self) -> tuple[int, bytes, str]:
        token = self.headers.get("vmware-api-session-id")
        if token not in (self.state.old_token, self.state.new_token):
            return self._json(401, {"error_type": "UNAUTHENTICATED"})
        with self.state.lock:
            if token == self.state.old_token and self.state.old_expired:
                return self._json(401, {"error_type": "UNAUTHENTICATED"})
        return self._json(
            200,
            [
                {
                    "namespace": f"unrelated-{os.getpid()}",
                    "master_host": self.state.authority,
                },
                {
                    "namespace": self.state.namespace,
                    "master_host": self.state.authority,
                },
            ],
        )

    def _bearer(self) -> str | None:
        value = self.headers.get("Authorization")
        if value is None or not value.startswith("Bearer "):
            return None
        return value[7:]

    def _authorized_for_kubernetes(self) -> bool:
        token = self._bearer()
        if token not in (self.state.old_token, self.state.new_token):
            return False
        with self.state.lock:
            return not (
                token == self.state.old_token and self.state.old_expired
            )

    def _cluster_index(self, values: dict[str, str]) -> int | None:
        if values.get("namespace") != self.state.namespace:
            return None
        try:
            return self.state.clusters.index(values.get("cluster", ""))
        except ValueError:
            return None

    def _get_cluster(
        self, values: dict[str, str]
    ) -> tuple[int, bytes, str]:
        if not self._authorized_for_kubernetes():
            return self._json(401, {"kind": "Status", "reason": "Unauthorized"})
        index = self._cluster_index(values)
        if index is None:
            return self._json(404, {"kind": "Status", "reason": "NotFound"})
        return self._json(
            200,
            self._cluster_resource(
                index=index,
                resource_version=self.state.before_versions[index],
            ),
        )

    def _patch_cluster(
        self, values: dict[str, str], body: bytes
    ) -> tuple[int, bytes, str]:
        if not self._authorized_for_kubernetes():
            return self._json(401, {"kind": "Status", "reason": "Unauthorized"})
        index = self._cluster_index(values)
        if index is None:
            return self._json(404, {"kind": "Status", "reason": "NotFound"})
        try:
            patch = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(422, {"kind": "Status", "reason": "Invalid"})
        with self.state.lock:
            self.state.successful_patches += 1
            if self.state.successful_patches == 1:
                self.state.old_expired = True
        return self._json(
            200,
            self._cluster_resource(
                index=index,
                resource_version=self.state.after_versions[index],
                annotations=patch.get("metadata", {}).get("annotations"),
            ),
        )

    def _cluster_resource(
        self,
        *,
        index: int,
        resource_version: str,
        annotations: object = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "name": self.state.clusters[index],
            "namespace": self.state.namespace,
            "resourceVersion": resource_version,
        }
        if annotations is not None:
            metadata["annotations"] = annotations
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": metadata,
        }

    @staticmethod
    def _json(
        status: int, value: object
    ) -> tuple[int, bytes, str]:
        return (
            status,
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            ),
            "application/json",
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--clusters-json", required=True)
    parser.add_argument("--before-versions-json", required=True)
    parser.add_argument("--after-versions-json", required=True)
    parser.add_argument("--subject-token", required=True)
    parser.add_argument("--old-access-token", required=True)
    parser.add_argument("--new-access-token", required=True)
    args = parser.parse_args()

    state = ContractState(args)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.contract_state = state  # type: ignore[attr-defined]
    state.authority = f"127.0.0.1:{server.server_address[1]}"
    ready_path = Path(args.ready_file)
    with ready_path.open("w", encoding="utf-8") as stream:
        json.dump({"port": server.server_address[1]}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
