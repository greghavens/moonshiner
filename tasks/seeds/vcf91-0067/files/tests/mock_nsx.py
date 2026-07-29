"""Contract-pinned loopback NSX Policy Traceflow service for acceptance tests."""

from __future__ import annotations

import base64
import contextlib
import copy
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATION_IDS = {
    "OrgsOrgIdProjectsProjectIdInfraUpdateTraceflowConfig",
    "OrgsOrgIdProjectsProjectIdInfraReadTraceflowStatus",
    "OrgsOrgIdProjectsProjectIdInfraListTraceflowObservations",
}
USERNAME = "automation"
PASSWORD = "correct-horse"


def _compile_path(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    position = 0
    for match in re.finditer(r"\{([^{}]+)\}", template):
        pieces.append(re.escape(template[position : match.start()]))
        pieces.append(f"(?P<{match.group(1).replace('-', '_')}>[^/]+)")
        position = match.end()
    pieces.append(re.escape(template[position:]))
    return re.compile("^" + "".join(pieces) + "$")


class MockNsxServer(HTTPServer):
    """HTTPServer whose routes are constructed only from docs/contract.json."""

    allow_reuse_address = True

    def __init__(self, contract_path: Path):
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = self.contract["operations"]
        operation_ids = {operation["operationId"] for operation in operations}
        if operation_ids != EXPECTED_OPERATION_IDS or len(operations) != 3:
            raise ValueError("mock contract must name exactly the three Traceflow operations")

        base_path = self.contract["basePath"].rstrip("/")
        self.routes = [
            {
                "method": operation["method"],
                "path": base_path + operation["path"],
                "pattern": _compile_path(base_path + operation["path"]),
                "operation_id": operation["operationId"],
            }
            for operation in operations
        ]
        self.request_log: list[dict[str, object]] = []
        self.runs: dict[tuple[str, str, str], dict[str, object]] = {}
        self.observation_responses = 0
        super().__init__(("127.0.0.1", 0), MockNsxHandler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def clear_log(self) -> None:
        self.request_log.clear()


class MockNsxHandler(BaseHTTPRequestHandler):
    server: MockNsxServer

    def log_message(self, format: str, *args: object) -> None:
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
        route, match = self._match_route(parsed.path)
        body = self._read_json()
        entry: dict[str, object] = {
            "method": self.command,
            "path": parsed.path,
            "query": parse_qs(parsed.query, keep_blank_values=True),
            "operationId": route["operation_id"] if route else None,
            "body": body,
            "accept": self.headers.get("Accept"),
            "content_type": self.headers.get("Content-Type"),
        }
        self.server.request_log.append(entry)

        if route is None or match is None:
            entry["response_status"] = 404
            self._send(404, {"error_code": 404, "error_message": "operation not in contract"})
            return
        if not self._authenticated():
            entry["response_status"] = 401
            self._send(401, {"error_code": 401, "error_message": "authentication required"})
            return
        if self.headers.get("Accept") != "application/json":
            entry["response_status"] = 406
            self._send(406, {"error_code": 406, "error_message": "Accept must be application/json"})
            return

        identifiers = {
            name: unquote(value) for name, value in match.groupdict().items()
        }
        key = (
            identifiers["org_id"],
            identifiers["project_id"],
            identifiers["traceflow_id"],
        )
        operation_id = route["operation_id"]
        if operation_id.endswith("UpdateTraceflowConfig"):
            status, payload = self._start(key, parsed.query, body)
        elif operation_id.endswith("ReadTraceflowStatus"):
            status, payload = self._status(key, parsed.query)
        else:
            status, payload = self._observations(key, parsed.query)
        entry["response_status"] = status
        self._send(status, payload)

    def _match_route(
        self, path: str
    ) -> tuple[dict[str, object] | None, re.Match[str] | None]:
        for route in self.server.routes:
            match = route["pattern"].match(path)
            if match and route["method"] == self.command:
                return route, match
        return None, None

    def _read_json(self) -> object | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"_malformed": raw.decode("utf-8", errors="replace")}

    def _authenticated(self) -> bool:
        token = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == f"Basic {token}"

    @staticmethod
    def _enforcement_path(query: str) -> str | None:
        values = parse_qs(query).get("enforcement_point_path", [])
        return values[0] if len(values) == 1 else None

    def _start(
        self, key: tuple[str, str, str], query: str, body: object | None
    ) -> tuple[int, object]:
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return 415, {"error_code": 415, "error_message": "JSON body required"}
        if not isinstance(body, dict):
            return 400, {"error_code": 400, "error_message": "TraceflowConfig required"}
        packet = body.get("packet")
        if (
            not isinstance(packet, dict)
            or packet.get("resource_type") != "FieldsPacketData"
        ):
            return 400, {"error_code": 400, "error_message": "invalid TraceflowConfig"}
        self.server.runs[key] = {
            "config": copy.deepcopy(body),
            "enforcement_point_path": self._enforcement_path(query),
            "polls": 0,
        }
        response = copy.deepcopy(body)
        response.update(
            {
                "id": key[2],
                "display_name": key[2],
                "path": (
                    f"/orgs/{key[0]}/projects/{key[1]}/infra/traceflows/{key[2]}"
                ),
                "resource_type": "TraceflowConfig",
            }
        )
        return 200, response

    def _status(
        self, key: tuple[str, str, str], query: str
    ) -> tuple[int, object]:
        run = self.server.runs.get(key)
        if run is None:
            return 404, {"error_code": 404, "error_message": "traceflow not found"}
        if self._enforcement_path(query) != run["enforcement_point_path"]:
            return 400, {"error_code": 400, "error_message": "enforcement path changed"}
        run["polls"] = int(run["polls"]) + 1
        traceflow_id = key[2]
        if traceflow_id == "never-finishes":
            state, request_status = "IN_PROGRESS", "SUCCESS"
        elif traceflow_id == "will-fail" and run["polls"] >= 2:
            state, request_status = "FAILED", "DATA_PATH_NOT_READY"
        elif run["polls"] >= 3:
            state, request_status = "FINISHED", "SUCCESS"
        else:
            state, request_status = "IN_PROGRESS", "SUCCESS"
        return 200, {
            "id": f"/orgs/{key[0]}/projects/{key[1]}/infra/traceflows/{traceflow_id}",
            "operation_state": state,
            "request_status": request_status,
            "result_overflowed": False,
        }

    def _observations(
        self, key: tuple[str, str, str], query: str
    ) -> tuple[int, object]:
        run = self.server.runs.get(key)
        if run is None:
            return 404, {"error_code": 404, "error_message": "traceflow not found"}
        if self._enforcement_path(query) != run["enforcement_point_path"]:
            return 400, {"error_code": 400, "error_message": "enforcement path changed"}
        if int(run["polls"]) < 3 or key[2] in {"will-fail", "never-finishes"}:
            return 409, {"error_code": 409, "error_message": "traceflow is not finished"}

        observations = [
            {
                "resource_type": "TraceflowObservationReceived",
                "sequence_no": 0,
                "timestamp_micro": 1700000000000100,
                "transport_node_id": "tn-c",
                "transport_node_name": "edge-c",
                "transport_node_type": "EDGE",
                "component_type": "PHYSICAL",
                "component_sub_type": "UNKNOWN",
                "component_name": "uplink",
            },
            {
                "resource_type": "TraceflowObservationForwarded",
                "sequence_no": 1,
                "timestamp_micro": 1700000000000300,
                "transport_node_id": "tn-b",
                "transport_node_name": "host-b",
                "transport_node_type": "ESX",
                "component_type": "LS",
                "component_sub_type": "UNKNOWN",
                "component_name": "segment",
            },
            {
                "resource_type": "TraceflowObservationForwarded",
                "sequence_no": 1,
                "timestamp_micro": 1700000000000200,
                "transport_node_id": "tn-a",
                "transport_node_name": "host-a",
                "transport_node_type": "ESX",
                "component_type": "DFW",
                "component_sub_type": "UNKNOWN",
                "component_name": "firewall",
            },
            {
                "resource_type": "TraceflowObservationDelivered",
                "sequence_no": 2,
                "timestamp_micro": 1700000000000400,
                "transport_node_id": "tn-d",
                "transport_node_name": "host-d",
                "transport_node_type": "ESX",
                "component_type": "PHYSICAL",
                "component_sub_type": "UNKNOWN",
                "component_name": "vnic",
            },
        ]
        self.server.observation_responses += 1
        if self.server.observation_responses % 2:
            observations.reverse()
        return 200, {
            "results": observations,
            "result_count": len(observations),
            "sort_by": "sequence_no",
            "sort_ascending": False,
        }

    def _send(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextlib.contextmanager
def running_mock(contract_path: Path):
    server = MockNsxServer(contract_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
