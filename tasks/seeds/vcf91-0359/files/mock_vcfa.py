#!/usr/bin/env python3
"""Loopback fixture that serves only the two operations in docs/contract.json.

Request identifiers, non-terminal poll depths, terminal statuses and detail
strings are supplied at runtime by verify.py, so nothing about the served
responses can be hardcoded inside the client under test.
"""
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ACTIONS = [
    {"actionId": "resize-cpu", "inputs": {"cpuCount": 8}, "reason": "quarterly capacity change"},
    {"actionId": "resize-memory", "inputs": {"memoryInMB": 32768}, "reason": "quarterly capacity change"},
    {
        "actionId": "extend-lease",
        "inputs": {"expirationDate": "2026-12-31T23:59:59Z"},
        "reason": "quarterly capacity change",
    },
]

NONTERMINAL_CYCLE = [
    "PENDING",
    "INITIALIZATION",
    "CHECKING_APPROVAL",
    "INPROGRESS",
    "COMPLETION",
]


def request_id(nonce, index):
    return f"req-{nonce}-{index + 1}"


def details_for(action_id, status, detail_nonce):
    # detail_nonce is independent of the request-ID nonce, so the terminal
    # details string cannot be reconstructed from the returned request ID.
    return f"{action_id} reached {status} [{detail_nonce}]"


def build_plans(nonce, detail_nonce, nonterminal_counts, cycle_starts, terminal_statuses):
    plans = []
    for index, action in enumerate(ACTIONS):
        statuses = []
        for step in range(nonterminal_counts[index]):
            status = NONTERMINAL_CYCLE[(cycle_starts[index] + step) % len(NONTERMINAL_CYCLE)]
            statuses.append((status, details_for(action["actionId"], status, detail_nonce)))
        terminal = terminal_statuses[index]
        statuses.append((terminal, details_for(action["actionId"], terminal, detail_nonce)))
        plans.append({
            "actionId": action["actionId"],
            "inputs": action["inputs"],
            "reason": action["reason"],
            "requestId": request_id(nonce, index),
            "statuses": statuses,
        })
    return plans


# A correct client makes at most one POST plus a bounded number of polls per
# planned action. This cap keeps a runaway client from flooding the request log
# and makes such a run fail fast and deterministically.
MAX_EVENTS = 200


class ContractState:
    def __init__(self, log_path: Path, token: str, resource_id: str, plans):
        self.log_path = log_path
        self.token = token
        self.resource_id = resource_id
        self.plans = plans
        self.next_plan = 0
        self.requests = {}
        self.events = 0
        self.lock = threading.Lock()

    def exhausted(self):
        return self.events >= MAX_EVENTS

    def log(self, event):
        self.events += 1
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()


def make_handler(state: ContractState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VCFAContractMock/1.0"

        def log_message(self, _format, *_args):
            return

        def send_json(self, status, payload):
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            return self.headers.get("Authorization") == f"Bearer {state.token}"

        def do_POST(self):
            path = urlsplit(self.path).path
            match = re.fullmatch(r"/deployment/api/resources/([^/]+)/requests", path)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None

            event = {"method": "POST", "path": path, "body": body}
            with state.lock:
                if state.exhausted():
                    self.send_json(429, {"error": "request budget exhausted"})
                    return
                if not match:
                    event["responseStatus"] = 404
                    state.log(event)
                    self.send_json(404, {"error": "operation not in contract"})
                    return
                if not self.authorized():
                    event["responseStatus"] = 401
                    state.log(event)
                    self.send_json(401, {"error": "unauthorized"})
                    return
                if self.headers.get_content_type() != "application/json":
                    event["responseStatus"] = 415
                    state.log(event)
                    self.send_json(415, {"error": "application/json required"})
                    return
                if unquote(match.group(1)) != state.resource_id:
                    event["responseStatus"] = 404
                    state.log(event)
                    self.send_json(404, {"error": "resource not found"})
                    return
                if state.next_plan >= len(state.plans):
                    event["responseStatus"] = 409
                    state.log(event)
                    self.send_json(409, {"error": "no further planned action"})
                    return

                plan = state.plans[state.next_plan]
                expected = {
                    "actionId": plan["actionId"],
                    "inputs": plan["inputs"],
                    "reason": plan["reason"],
                }
                if body != expected:
                    event["responseStatus"] = 400
                    state.log(event)
                    self.send_json(400, {"error": "request body does not match change plan"})
                    return

                created = plan["requestId"]
                state.requests[created] = {"plan": plan, "poll": 0}
                state.next_plan += 1
                event.update({
                    "operation": "Submit Resource Action Request",
                    "requestId": created,
                    "servedStatus": "CREATED",
                    "responseStatus": 200,
                })
                state.log(event)
                self.send_json(200, {
                    "actionId": plan["actionId"],
                    "details": "Request accepted",
                    "id": created,
                    "status": "CREATED",
                })

        def do_GET(self):
            path = urlsplit(self.path).path
            match = re.fullmatch(r"/deployment/api/requests/([^/]+)", path)
            event = {"method": "GET", "path": path}
            with state.lock:
                if state.exhausted():
                    self.send_json(429, {"error": "request budget exhausted"})
                    return
                if not match:
                    event["responseStatus"] = 404
                    state.log(event)
                    self.send_json(404, {"error": "operation not in contract"})
                    return
                if not self.authorized():
                    event["responseStatus"] = 401
                    state.log(event)
                    self.send_json(401, {"error": "unauthorized"})
                    return

                polled = unquote(match.group(1))
                request = state.requests.get(polled)
                if request is None:
                    event["responseStatus"] = 404
                    state.log(event)
                    self.send_json(404, {"error": "request not found"})
                    return

                statuses = request["plan"]["statuses"]
                index = min(request["poll"], len(statuses) - 1)
                status, details = statuses[index]
                request["poll"] += 1
                event.update({
                    "operation": "Get Request",
                    "requestId": polled,
                    "servedStatus": status,
                    "responseStatus": 200,
                })
                state.log(event)
                self.send_json(200, {
                    "actionId": request["plan"]["actionId"],
                    "details": details,
                    "id": polled,
                    "status": status,
                })

    return Handler


def comma_list(value):
    return [item for item in value.split(",") if item]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--resource-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--detail-nonce", required=True)
    parser.add_argument("--nonterminal-counts", required=True, type=comma_list)
    parser.add_argument("--cycle-starts", required=True, type=comma_list)
    parser.add_argument("--terminal-statuses", required=True, type=comma_list)
    args = parser.parse_args()

    counts = [int(item) for item in args.nonterminal_counts]
    starts = [int(item) for item in args.cycle_starts]
    if (
        len(counts) != len(ACTIONS)
        or len(starts) != len(ACTIONS)
        or len(args.terminal_statuses) != len(ACTIONS)
    ):
        parser.error(
            f"expected {len(ACTIONS)} non-terminal counts, cycle starts and terminal statuses"
        )

    plans = build_plans(args.nonce, args.detail_nonce, counts, starts, args.terminal_statuses)
    args.log.write_text("", encoding="utf-8")
    state = ContractState(args.log, args.token, args.resource_id, plans)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    print(f"http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
