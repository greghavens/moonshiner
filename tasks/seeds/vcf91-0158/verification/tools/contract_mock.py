"""Contract-pinned loopback vCenter and VKS mock for the protected verifier."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


class ContractMock:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / "docs" / "contract.json").read_text())
        operations = contract.get("operations")
        kubernetes = contract.get("kubernetesApi", {}).get("operations")
        if not isinstance(operations, list) or len(operations) != 1:
            raise RuntimeError("contract must name exactly one VMware operation")
        if not isinstance(kubernetes, list) or len(kubernetes) != 1:
            raise RuntimeError("contract must name exactly one Kubernetes operation")

        vmware = operations[0]
        native = kubernetes[0]
        if vmware.get("operationId") != (
                "Vcenter.Namespaces.User.Instances_list"):
            raise RuntimeError("unexpected VMware operation")
        if native.get("operationKey") != (
                "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"):
            raise RuntimeError("unexpected Kubernetes operation")
        if vmware.get("method") != "GET" or native.get("method") != "GET":
            raise RuntimeError("mock supports only the contract GET operations")

        self._namespace_path = vmware["wirePath"]
        template = native["pathTemplate"]
        marker = "{namespace}"
        if template.count(marker) != 1:
            raise RuntimeError("invalid Kubernetes path template")
        self._cluster_prefix, self._cluster_suffix = template.split(marker)

        self._lock = threading.Lock()
        self._requests: list[dict[str, object]] = []
        self._failures: list[str] = []
        self._round = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.contract_mock = self
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf-vks-contract-mock",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def clear_log(self) -> None:
        with self._lock:
            self._requests.clear()
            self._failures.clear()

    def request_log(self) -> list[dict[str, object]]:
        with self._lock:
            return deepcopy(self._requests)

    def failures(self) -> list[str]:
        with self._lock:
            return list(self._failures)

    def _record(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        with self._lock:
            self._requests.append({
                "method": handler.command,
                "target": handler.path,
                "headers": list(handler.headers.raw_items()),
                "body": body,
            })

    def _failure(self, text: str) -> None:
        with self._lock:
            self._failures.append(text)

    def _dispatch(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        self._record(handler, body)
        target = urlsplit(handler.path)

        if handler.command != "GET":
            self._failure("non-GET request")
            self._json(handler, 405, {"error": "method not allowed"})
            return

        if target.path == self._namespace_path:
            if target.query:
                self._failure("vCenter request included a query")
                self._json(handler, 400, {"error": "query not allowed"})
                return
            with self._lock:
                self._round += 1
                round_number = self._round
            summaries = [
                {"namespace": "zeta-team", "master_host": self.base_url},
                {"namespace": "alpha-team", "master_host": self.base_url},
            ]
            if round_number % 2 == 0:
                summaries.reverse()
            self._json(handler, 200, summaries)
            return

        if (target.path.startswith(self._cluster_prefix)
                and target.path.endswith(self._cluster_suffix)):
            encoded = target.path[
                len(self._cluster_prefix):-len(self._cluster_suffix)]
            if not encoded or "/" in encoded:
                self._json(handler, 404, {"error": "route not found"})
                return
            namespace = unquote(encoded)
            if namespace not in {"alpha-team", "zeta-team"}:
                self._json(handler, 404, {"error": "route not found"})
                return
            self._serve_cluster_page(handler, namespace, target.query)
            return

        self._json(handler, 404, {"error": "route not found"})

    def _serve_cluster_page(
            self,
            handler: BaseHTTPRequestHandler,
            namespace: str,
            query: str) -> None:
        parsed = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        expected_token = f"{namespace}+next/=?&two"
        if set(parsed) == {"limit"} and parsed["limit"] == ["2"]:
            page = 1
        elif (set(parsed) == {"continue", "limit"}
              and parsed["limit"] == ["2"]
              and parsed["continue"] == [expected_token]):
            page = 2
        else:
            self._failure(f"invalid Kubernetes pagination query for {namespace}")
            self._json(handler, 400, {"error": "invalid pagination"})
            return

        with self._lock:
            round_number = self._round
        pages = self._pages(namespace, round_number)
        metadata: dict[str, str] = {
            "resourceVersion": f"rv-{round_number}-{namespace}"
        }
        if page == 1:
            metadata["continue"] = expected_token
        document = {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "ClusterList",
            "metadata": metadata,
            "items": pages[page - 1],
        }
        self._json(handler, 200, document)

    @staticmethod
    def _cluster(
            namespace: str,
            name: str,
            uid: str,
            version: str,
            phase: str) -> dict[str, object]:
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": {
                "namespace": namespace,
                "name": name,
                "uid": uid,
            },
            "spec": {"topology": {"version": version}},
            "status": {"phase": phase},
        }

    def _pages(
            self,
            namespace: str,
            round_number: int) -> list[list[dict[str, object]]]:
        if namespace == "alpha-team":
            rows = [
                self._cluster(
                    namespace, "cobalt", "uid-alpha-cobalt",
                    "v1.31.2+vmware.1-vks.1", "Provisioned"),
                self._cluster(
                    namespace, "amber", "uid-alpha-amber",
                    "v1.30.6+vmware.1-vks.1", "Provisioned"),
                self._cluster(
                    namespace, "birch", "uid-alpha-birch",
                    "v1.31.2+vmware.1-vks.1", "ScalingUp"),
            ]
        else:
            rows = [
                self._cluster(
                    namespace, "zenith", "uid-zeta-zenith",
                    "v1.31.2+vmware.1-vks.1", "Provisioned"),
                self._cluster(
                    namespace, "cedar", "uid-zeta-cedar",
                    "v1.30.6+vmware.1-vks.1", "Provisioned"),
                self._cluster(
                    namespace, "maple", "uid-zeta-maple",
                    "v1.31.2+vmware.1-vks.1", "ScalingUp"),
            ]
        if round_number % 2:
            return [rows[:2], rows[2:]]
        return [[rows[2]], list(reversed(rows[:2]))]

    @staticmethod
    def _json(
            handler: BaseHTTPRequestHandler,
            status: int,
            value: object) -> None:
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.contract_mock._dispatch(self, body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.server.contract_mock._dispatch(self, body)

    def log_message(self, _format: str, *_args: object) -> None:
        return
