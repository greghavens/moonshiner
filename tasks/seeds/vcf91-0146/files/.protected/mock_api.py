"""Contract-pinned loopback API used only by the protected verifier."""

from __future__ import annotations

import base64
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import unquote


GET_NAMESPACE_OPERATION = "Vcenter.Namespaces.Instances_getV2"
LIST_CLUSTERS_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)
CREATE_BACKUP_OPERATION = (
    "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create"
)
GET_TASK_OPERATION = "Cis.Tasks_get"


def _compile_path(template: str, parameter: str) -> re.Pattern[str]:
    escaped = re.escape(template)
    marker = re.escape("{" + parameter + "}")
    expression = escaped.replace(marker, rf"(?P<{parameter}>[^/?]+)")
    return re.compile(rf"^{expression}$")


class ContractMock(AbstractContextManager["ContractMock"]):
    """Serve exactly the focused contract's four named operations."""

    def __init__(
        self,
        contract_path: Path,
        request_log: Path,
        *,
        namespace: str,
        supervisor: str,
        clusters: list[dict[str, str]],
        task_id: str,
        task_states: list[str],
        task_result: Any,
        task_error: Any = None,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            operation["contractName"]: operation
            for operation in contract["operations"]
        }
        expected_names = {
            "getSupervisorNamespace",
            "listVksClusters",
            "createSupervisorBackup",
            "getTask",
        }
        if set(operations) != expected_names:
            raise ValueError("focused contract operation set does not match mock")
        if len(clusters) != 3:
            raise ValueError("the order-flip fixture requires three clusters")
        if not task_states:
            raise ValueError("task_states must not be empty")

        get_namespace = operations["getSupervisorNamespace"]
        list_clusters = operations["listVksClusters"]
        create_backup = operations["createSupervisorBackup"]
        get_task = operations["getTask"]
        if (
            get_namespace.get("operationId") != GET_NAMESPACE_OPERATION
            or list_clusters.get("operationKey") != LIST_CLUSTERS_OPERATION
            or create_backup.get("operationId") != CREATE_BACKUP_OPERATION
            or get_task.get("operationId") != GET_TASK_OPERATION
        ):
            raise ValueError("focused contract identifiers do not match mock")

        self._operations = operations
        self._paths = {
            "getSupervisorNamespace": _compile_path(
                get_namespace["pathTemplate"], "namespace"
            ),
            "listVksClusters": _compile_path(
                list_clusters["pathTemplate"], "namespace"
            ),
            "createSupervisorBackup": _compile_path(
                create_backup["pathTemplate"], "supervisor"
            ),
            "getTask": _compile_path(get_task["pathTemplate"], "task"),
        }
        self._request_log = request_log
        self._namespace = namespace
        self._supervisor = supervisor
        canonical = sorted(
            (dict(cluster) for cluster in clusters),
            key=lambda item: item["name"],
        )
        self._cluster_base_order = [canonical[1], canonical[0], canonical[2]]
        self._task_id = task_id
        self._task_states = list(task_states)
        self._task_result = task_result
        self._task_error = task_error
        self._poll_index = 0
        self._collection_count = 0
        self._collection_orders: list[list[str]] = []
        self._submitted = False
        self._sequence = 0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def collection_orders(self) -> list[list[str]]:
        with self._lock:
            return [list(order) for order in self._collection_orders]

    def __enter__(self) -> "ContractMock":
        self._request_log.parent.mkdir(parents=True, exist_ok=True)
        self._request_log.write_text("", encoding="utf-8")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf91-0146-contract-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server.server_close()

    def _matches(
        self,
        contract_name: str,
        raw_target: str,
        parameter: str,
        expected_value: str,
    ) -> bool:
        match = self._paths[contract_name].fullmatch(raw_target)
        return (
            match is not None
            and unquote(match.group(parameter), errors="strict") == expected_value
        )

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length_text = handler.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else 0
        except ValueError:
            length = 0
        body = handler.rfile.read(length) if length else b""
        raw_target = handler.path

        contract_name: str | None = None
        operation_id: str | None = None
        operation_key: str | None = None
        status = 404
        payload: Any = {"error": "route is absent from the focused contract"}

        get_namespace = self._operations["getSupervisorNamespace"]
        list_clusters = self._operations["listVksClusters"]
        create_backup = self._operations["createSupervisorBackup"]
        get_task = self._operations["getTask"]

        if (
            handler.command == get_namespace["method"]
            and self._matches(
                "getSupervisorNamespace",
                raw_target,
                "namespace",
                self._namespace,
            )
        ):
            contract_name = "getSupervisorNamespace"
            operation_id = GET_NAMESPACE_OPERATION
            status = int(next(iter(get_namespace["responses"])))
            payload = {
                "access_list": [],
                "config_status": "RUNNING",
                "description": "runtime namespace fixture",
                "messages": [],
                "stats": {},
                "storage_specs": [],
                "supervisor": self._supervisor,
            }
        elif (
            handler.command == list_clusters["method"]
            and self._matches(
                "listVksClusters",
                raw_target,
                "namespace",
                self._namespace,
            )
        ):
            contract_name = "listVksClusters"
            operation_key = LIST_CLUSTERS_OPERATION
            status = list_clusters["successStatuses"][0]
            payload = self._cluster_list_payload()
        elif (
            handler.command == create_backup["method"]
            and self._matches(
                "createSupervisorBackup",
                raw_target,
                "supervisor",
                self._supervisor,
            )
        ):
            contract_name = "createSupervisorBackup"
            operation_id = CREATE_BACKUP_OPERATION
            with self._lock:
                if self._submitted:
                    status = 409
                    payload = {"error": "backup submitted more than once"}
                else:
                    self._submitted = True
                    status = int(next(iter(create_backup["responses"])))
                    payload = self._task_id
        elif (
            handler.command == get_task["method"]
            and self._matches("getTask", raw_target, "task", self._task_id)
        ):
            contract_name = "getTask"
            operation_id = GET_TASK_OPERATION
            with self._lock:
                submitted = self._submitted
                state = self._task_states[
                    min(self._poll_index, len(self._task_states) - 1)
                ]
                if submitted:
                    self._poll_index += 1
            if submitted:
                status = int(next(iter(get_task["responses"])))
                payload = {
                    "cancelable": False,
                    "description": {
                        "args": [],
                        "default_message": "Supervisor backup",
                        "id": "com.vmware.vcenter.supervisor.backup",
                    },
                    "operation": "create",
                    "service": (
                        "com.vmware.vcenter.namespace_management.supervisors."
                        "recovery.backup.jobs"
                    ),
                    "status": state,
                }
                if state == "SUCCEEDED":
                    payload["result"] = self._task_result
                if state == "FAILED":
                    payload["error"] = self._task_error
            else:
                status = 409
                payload = {"error": "task read before backup submission"}

        self._record(
            handler=handler,
            contract_name=contract_name,
            operation_id=operation_id,
            operation_key=operation_key,
            raw_target=raw_target,
            body=body,
        )
        self._respond(handler, status, payload)

    def _cluster_list_payload(self) -> dict[str, Any]:
        with self._lock:
            self._collection_count += 1
            if self._collection_count % 2:
                ordered = list(self._cluster_base_order)
            else:
                ordered = list(reversed(self._cluster_base_order))
            self._collection_orders.append([item["name"] for item in ordered])

        items = [
            {
                "apiVersion": "cluster.x-k8s.io/v1beta2",
                "kind": "Cluster",
                "metadata": {
                    "name": item["name"],
                    "namespace": self._namespace,
                },
                "spec": {
                    "topology": {
                        "version": item["topologyVersion"],
                    }
                },
            }
            for item in ordered
        ]
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "items": items,
            "kind": "ClusterList",
        }

    def _record(
        self,
        *,
        handler: BaseHTTPRequestHandler,
        contract_name: str | None,
        operation_id: str | None,
        operation_key: str | None,
        raw_target: str,
        body: bytes,
    ) -> None:
        with self._lock:
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "contract_name": contract_name,
                "operation_id": operation_id,
                "operation_key": operation_key,
                "method": handler.command,
                "raw_target": raw_target,
                "headers": list(handler.headers.items()),
                "body_length": len(body),
                "body_base64": base64.b64encode(body).decode("ascii"),
            }
            encoded = (
                json.dumps(record, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )
            with self._request_log.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

    @staticmethod
    def _respond(
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: Any,
    ) -> None:
        raw = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        handler.wfile.flush()
