#!/usr/bin/env python3
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
REQUEST_ID = "22222222-2222-4222-8222-222222222291"


def load_routes():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = {item["operationId"]: item for item in contract["operations"]}
    if set(operations) != {"submitDeploymentActionRequest", "getRequest"}:
        raise RuntimeError("mock contract must name exactly the two supported operations")
    return contract, operations


CONTRACT, OPERATIONS = load_routes()


def route_pattern(template, parameter):
    marker = re.escape("{" + parameter + "}")
    return re.compile("^" + re.escape(template).replace(marker, "([^/]+)") + "$")


SUBMIT_ROUTE = route_pattern(
    OPERATIONS["submitDeploymentActionRequest"]["pathTemplate"], "deploymentId")
GET_ROUTE = route_pattern(OPERATIONS["getRequest"]["pathTemplate"], "requestId")


def poll_statuses(scenario):
    if scenario == "submit-terminal":
        return ["COMPLETION", "ABORTED"]
    if scenario.startswith("terminal-"):
        terminal = scenario.removeprefix("terminal-")
        if terminal == "SUCCESSFUL":
            return ["PENDING", "INPROGRESS", "COMPLETION", terminal]
        return [terminal]
    if scenario == "unknown-status":
        return ["SUCCESSFUL_ENOUGH", "SUCCESSFUL"]
    return ["FAILED"]


class State:
    def __init__(self, log_path, scenario):
        self.log_path = log_path
        self.scenario = scenario
        self.deployment_id = None
        self.request_body = None
        self.poll_count = 0

    def append(self, record):
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "VCFContractMock/2.0"

    def log_message(self, *_args):
        pass

    @property
    def state(self):
        return self.server.state

    def send_json(self, status_code, payload):
        self.send_raw(status_code, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def send_raw(self, status_code, body):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def auth_ok(self):
        return self.headers.get("Authorization") == "Bearer test-token-91"

    def request_payload(self, status):
        return {
            "id": REQUEST_ID,
            "deploymentId": self.state.deployment_id,
            "actionId": self.state.request_body.get("actionId"),
            "name": "Deployment action",
            "requestedBy": "contract-test",
            "status": status,
            "completedTasks": 2 if status in CONTRACT["polling"]["terminalStatuses"] else 1,
            "totalTasks": 2,
        }

    def do_POST(self):
        raw_path = urlsplit(self.path).path
        match = SUBMIT_ROUTE.fullmatch(raw_path)
        if match is None:
            self.state.append({"method": "POST", "path": raw_path, "statusCode": 404})
            self.send_json(404, {"error": "operation not in contract"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            body = None

        deployment_id = unquote(match.group(1))
        record = {
            "method": "POST",
            "path": raw_path,
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "body": body,
        }

        if not self.auth_ok():
            record["statusCode"] = 401
            self.state.append(record)
            self.send_json(401, {"error": "unauthorized"})
            return
        if not isinstance(body, dict) or not isinstance(body.get("inputs"), dict):
            record["statusCode"] = 400
            self.state.append(record)
            self.send_json(400, {"error": "invalid ResourceActionRequest"})
            return

        self.state.deployment_id = deployment_id
        self.state.request_body = body
        if self.state.scenario == "submit-error":
            record["statusCode"] = 503
            self.state.append(record)
            # The body is otherwise a valid Request, so status handling is what is graded.
            self.send_json(503, self.request_payload("CREATED"))
            return

        status = "SUCCESSFUL" if self.state.scenario == "submit-terminal" else "CREATED"
        response = self.request_payload(status)
        if self.state.scenario == "missing-id-submit":
            response.pop("id")
        elif self.state.scenario == "wrong-type-id-submit":
            response["id"] = 91
        record.update({"statusCode": 200, "responseStatus": status})
        self.state.append(record)
        self.send_json(200, response)

    def do_GET(self):
        raw_path = urlsplit(self.path).path
        match = GET_ROUTE.fullmatch(raw_path)
        if match is None:
            self.state.append({"method": "GET", "path": raw_path, "statusCode": 404})
            self.send_json(404, {"error": "operation not in contract"})
            return

        request_id = unquote(match.group(1))
        record = {
            "method": "GET",
            "path": raw_path,
            "authorization": self.headers.get("Authorization"),
        }
        if not self.auth_ok():
            record["statusCode"] = 401
            self.state.append(record)
            self.send_json(401, {"error": "unauthorized"})
            return
        expected_request_id = "91" if self.state.scenario == "wrong-type-id-submit" else REQUEST_ID
        if request_id != expected_request_id or self.state.deployment_id is None:
            record["statusCode"] = 404
            self.state.append(record)
            self.send_json(404, {"error": "request not found"})
            return
        if self.state.scenario == "poll-error":
            record["statusCode"] = 502
            self.state.append(record)
            # The body is otherwise terminal and valid; it must not hide the HTTP error.
            self.send_json(502, self.request_payload("FAILED"))
            return

        statuses = poll_statuses(self.state.scenario)
        status = statuses[min(self.state.poll_count, len(statuses) - 1)]
        self.state.poll_count += 1
        response = self.request_payload(status)
        if self.state.scenario == "missing-deployment-poll":
            response.pop("deploymentId")
        elif self.state.scenario == "missing-status-poll":
            response.pop("status")
            response["metadata"] = {"status": "FAILED"}

        record.update({"statusCode": 200, "responseStatus": status})
        self.state.append(record)
        if self.state.scenario == "malformed-json-poll":
            # It deliberately contains all three field spellings so regex extraction is insufficient.
            invalid = (json.dumps(response, separators=(",", ":"))[:-1]).encode("utf-8")
            self.send_raw(200, invalid)
        else:
            self.send_json(200, response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    args.log_file.write_text("", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = State(args.log_file, args.scenario)
    args.port_file.write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
