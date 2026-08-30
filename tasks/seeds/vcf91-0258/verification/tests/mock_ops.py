#!/usr/bin/env python3
"""Contract-pinned loopback VCF Operations node for vcf91-0258.

Routes are derived from docs/contract.json. Any method/path outside that
projection is refused, and every request is appended to a flushed JSONL log.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


EXPECTED_OPERATION_IDS = {
    "acquireToken",
    "getCurrentVersionOfServer",
    "getMatchingResources",
    "queryAlert",
    "getAlertContributingSymptoms",
    "querySymptoms",
    "releaseToken",
}

CAUSE_STAT_KEY = "System Attributes|last_collection_time_diff"
DECOY_STAT_KEY = "System Attributes|health"
EXTRA_STAT_KEY = "System Attributes|total_alarm_count"


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def compile_path_template(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"[^/]+")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


def load_routes(path: Path) -> tuple[list[dict[str, Any]], str]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if {item["operationId"] for item in operations} != EXPECTED_OPERATION_IDS:
        raise ValueError("unexpected focused operationId set")
    if len(operations) != len(EXPECTED_OPERATION_IDS):
        raise ValueError("contract repeats an operationId")

    base_path = contract["source"]["basePath"]
    routes = [
        {
            "operationId": item["operationId"],
            "method": item["method"],
            "path": item["path"],
            "pathPattern": compile_path_template(base_path + item["path"]),
        }
        for item in operations
    ]
    return routes, base_path


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"httpStatusCode": code, "message": message}


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, Any]],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()
        self.sequence = 0

    def find_route(self, method: str, path: str) -> dict[str, Any] | None:
        for route in self.routes:
            if route["method"] == method and route["pathPattern"].fullmatch(path):
                return route
        return None

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")

    def next_sequence(self) -> int:
        with self.lock:
            value = self.sequence
            self.sequence += 1
            return value

    # -- fixtures ---------------------------------------------------------

    def symptom_catalog(self) -> dict[str, dict[str, Any]]:
        config = self.config
        adapter = config["failing_adapter_instance_id"]
        start = config["start_time"]
        return {
            config["decoy_symptom_id"]: {
                "id": config["decoy_symptom_id"],
                "resourceId": adapter,
                "symptomDefinitionId": "SymptomDefinition-adapter-health",
                "statKey": DECOY_STAT_KEY,
                "message": config["decoy_symptom_message"],
                "symptomCriticality": "CRITICAL",
                "alarmInfo": config["decoy_alert_name"],
                "startTimeUTC": start + 60_000,
                "updateTimeUTC": start + 120_000,
                "cancelTimeUTC": 0,
                "kpi": False,
            },
            config["cause_symptom_id"]: {
                "id": config["cause_symptom_id"],
                "resourceId": adapter,
                "symptomDefinitionId": "SymptomDefinition-collection-age",
                "statKey": CAUSE_STAT_KEY,
                "message": config["cause_symptom_message"],
                "symptomCriticality": config["cause_symptom_criticality"],
                "alarmInfo": config["cause_alert_name"],
                "startTimeUTC": start + 30_000,
                "updateTimeUTC": start + 150_000,
                "cancelTimeUTC": 0,
                "kpi": True,
            },
            config["extra_symptom_id"]: {
                "id": config["extra_symptom_id"],
                "resourceId": adapter,
                "symptomDefinitionId": "SymptomDefinition-alarm-count",
                "statKey": EXTRA_STAT_KEY,
                "message": config["extra_symptom_message"],
                "symptomCriticality": "WARNING",
                "alarmInfo": config["cause_alert_name"],
                "startTimeUTC": start + 45_000,
                "updateTimeUTC": start + 140_000,
                "cancelTimeUTC": 0,
                "kpi": False,
            },
        }

    def alert_symptom_map(self) -> dict[str, list[str]]:
        config = self.config
        return {
            config["decoy_alert_id"]: [
                config["decoy_symptom_id"],
                config["extra_symptom_id"],
            ],
            config["cause_alert_id"]: [
                config["cause_symptom_id"],
                config["extra_symptom_id"],
            ],
        }

    def resources_response(self, body: Any) -> tuple[int, Any]:
        config = self.config
        if not isinstance(body, dict):
            return 400, error_body("BAD_REQUEST", "resource-query is required")
        if body.get("name") != [config["object_name"]]:
            return 200, {
                "resourceList": [],
                "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": 0},
                "links": [],
            }
        resource = {
            "creationTime": config["start_time"] - 86_400_000,
            "identifier": config["resource_id"],
            "resourceKey": {
                "adapterKindKey": config["adapter_kind"],
                "name": config["object_name"],
                "resourceKindKey": config["resource_kind"],
                "resourceIdentifiers": [],
            },
            "resourceHealth": "RED",
            "resourceHealthValue": 12.5,
            "dtEnabled": True,
            "monitoringInterval": 5,
            "resourceStatusStates": [
                {
                    "adapterInstanceId": config["healthy_adapter_instance_id"],
                    "resourceStatus": "DATA_RECEIVING",
                    "resourceState": "STARTED",
                    "statusMessage": "",
                },
                {
                    "adapterInstanceId": config["failing_adapter_instance_id"],
                    "resourceStatus": "NO_DATA_RECEIVING",
                    "resourceState": "FAILED",
                    "statusMessage": config["status_message"],
                },
            ],
        }
        return 200, {
            "resourceList": [resource],
            "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": 1},
            "links": [],
        }

    def alerts_response(self, body: Any) -> tuple[int, Any]:
        config = self.config
        if not isinstance(body, dict):
            return 400, error_body("BAD_REQUEST", "alert-query is required")
        nested = body.get("resource-query")
        if not isinstance(nested, dict):
            return 400, error_body(
                "BAD_REQUEST",
                "alert-query must carry the resource-query member",
            )
        if nested.get("resourceId") != [config["failing_adapter_instance_id"]]:
            return 200, {
                "alerts": [],
                "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": 0},
                "links": [],
            }
        start = config["start_time"]
        adapter = config["failing_adapter_instance_id"]
        alerts = [
            {
                "alertId": config["decoy_alert_id"],
                "resourceId": adapter,
                "alertLevel": "CRITICAL",
                "status": "ACTIVE",
                "controlState": "OPEN",
                "alertDefinitionId": "AlertDefinition-adapter-health",
                "alertDefinitionName": config["decoy_alert_name"],
                "type": "Application",
                "subType": "Availability",
                "startTimeUTC": start + 60_000,
                "updateTimeUTC": start + 120_000,
                "cancelTimeUTC": 0,
            },
            {
                "alertId": config["cause_alert_id"],
                "resourceId": adapter,
                "alertLevel": "CRITICAL",
                "status": "ACTIVE",
                "controlState": "OPEN",
                "alertDefinitionId": "AlertDefinition-collection-age",
                "alertDefinitionName": config["cause_alert_name"],
                "type": "Application",
                "subType": "Availability",
                "startTimeUTC": start + 30_000,
                "updateTimeUTC": start + 150_000,
                "cancelTimeUTC": 0,
            },
        ]
        if body.get("activeOnly") is not True:
            alerts.append(
                {
                    "alertId": config["cancelled_alert_id"],
                    "resourceId": adapter,
                    "alertLevel": "CRITICAL",
                    "status": "CANCELED",
                    "controlState": "CLOSED",
                    "alertDefinitionId": "AlertDefinition-stale",
                    "alertDefinitionName": config["cancelled_alert_name"],
                    "type": "Application",
                    "subType": "Availability",
                    "startTimeUTC": start - 900_000,
                    "updateTimeUTC": start - 600_000,
                    "cancelTimeUTC": start - 600_000,
                }
            )
        return 200, {
            "alerts": alerts,
            "pageInfo": {
                "page": 0,
                "pageSize": 1000,
                "totalCount": len(alerts),
            },
            "links": [],
        }

    def contributing_symptoms_response(self, query: str) -> tuple[int, Any]:
        requested = [value for key, value in parse_qsl(query) if key == "id"]
        if not requested:
            return 400, error_body("BAD_REQUEST", "id is required")
        mapping = self.alert_symptom_map()
        entries = []
        for alert_id in requested:
            if alert_id not in mapping:
                return 404, error_body("NOT_FOUND", "unknown alert identifier")
            entries.append(
                {
                    "alertId": alert_id,
                    "contributingSymptoms": {
                        "contributingSymptoms": [
                            {
                                "symptomId": symptom_id,
                                "symptomSetId": "SymptomSet-1",
                                "symptomDefinitionsIds": [
                                    self.symptom_catalog()[symptom_id][
                                        "symptomDefinitionId"
                                    ]
                                ],
                            }
                            for symptom_id in mapping[alert_id]
                        ]
                    },
                }
            )
        return 200, {"contributingSymptoms": entries}

    def symptoms_response(self, body: Any) -> tuple[int, Any]:
        if not isinstance(body, dict):
            return 400, error_body("BAD_REQUEST", "symptom-query is required")
        requested = body.get("symptomId")
        if not isinstance(requested, list) or not requested:
            return 400, error_body(
                "BAD_REQUEST", "symptom-query must carry symptomId"
            )
        catalog = self.symptom_catalog()
        records = []
        for symptom_id in requested:
            if symptom_id not in catalog:
                return 404, error_body("NOT_FOUND", "unknown symptom identifier")
            record = dict(catalog[symptom_id])
            if body.get("includeAlarmInfo") is not True:
                record.pop("alarmInfo", None)
            records.append(record)
        return 200, {
            "symptom": records,
            "pageInfo": {
                "page": 0,
                "pageSize": 1000,
                "totalCount": len(records),
            },
            "links": [],
        }

    def response_for(
        self,
        operation_id: str | None,
        query: str,
        body: Any,
        authorization: str | None,
    ) -> tuple[int, Any]:
        config = self.config
        if operation_id is None:
            return 404, error_body("NOT_FOUND", "outside the focused contract")

        if operation_id == "acquireToken":
            if not isinstance(body, dict):
                return 400, error_body("BAD_REQUEST", "username-password required")
            matches = (
                body.get("username") == config["username"]
                and body.get("password") == config["password"]
                and body.get("authSource") == config["auth_source"]
            )
            if not matches:
                return 401, error_body("UNAUTHORIZED", "authentication failed")
            return 200, {
                "token": config["token"],
                "validity": config["token_validity"],
                "expiresAt": config["token_expires_at"],
                "roles": ["ContentAdmin", "PowerUser"],
            }

        expected = "OpsToken " + config["token"]
        if authorization != expected:
            return 401, error_body("UNAUTHORIZED", "missing or stale session")

        if operation_id == "getCurrentVersionOfServer":
            return 200, {
                "releaseName": config["release_name"],
                "major": 9,
                "minor": 1,
                "minorMinor": 0,
                "patch": 0,
                "releasedDate": config["start_time"] - 5_000_000_000,
                "humanlyReadableReleaseDate": config["release_date"],
            }
        if operation_id == "getMatchingResources":
            return self.resources_response(body)
        if operation_id == "queryAlert":
            return self.alerts_response(body)
        if operation_id == "getAlertContributingSymptoms":
            return self.contributing_symptoms_response(query)
        if operation_id == "querySymptoms":
            return self.symptoms_response(body)
        if operation_id == "releaseToken":
            return 200, None
        return 404, error_body("NOT_FOUND", "outside the focused contract")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def _read_body(self) -> tuple[bytes, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length else 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            return raw, {"_malformed": raw.decode("utf-8", errors="replace")}

    def _headers(self) -> tuple[list[list[str]], dict[str, list[str]]]:
        pairs: list[list[str]] = []
        grouped: dict[str, list[str]] = {}
        for key, value in self.headers.raw_items():
            lowered = key.lower()
            pairs.append([lowered, value])
            grouped.setdefault(lowered, []).append(value)
        return pairs, grouped

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw, body = self._read_body()
        route = self.server.find_route(method, split.path)
        operation_id = route["operationId"] if route else None
        pairs, grouped = self._headers()
        authorization = self.headers.get("Authorization")
        status, response = self.server.response_for(
            operation_id, split.query, body, authorization
        )
        self.server.append_log(
            {
                "sequence": self.server.next_sequence(),
                "method": method,
                "raw_target": self.path,
                "path": split.path,
                "query": split.query,
                "query_pairs": [list(item) for item in parse_qsl(split.query)],
                "header_pairs": pairs,
                "headers": grouped,
                "body": body,
                "body_raw": raw.decode("utf-8", errors="replace"),
                "body_bytes": len(raw),
                "operationId": operation_id,
                "response_status": status,
            }
        )
        self._json(status, response)

    def _json(self, status: int, value: Any) -> None:
        raw = b"" if value is None else json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        if raw:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    routes, base_path = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), routes, args.log, config)
    durable_write(
        args.ready,
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": server.server_port,
                "basePath": base_path,
            },
            separators=(",", ":"),
        ),
        "w",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
