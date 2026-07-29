#!/usr/bin/env python3
import argparse
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
EXPECTED_OPERATION_IDS = {"PatchSegment", "ReadIntentStatus"}


def load_contract(contract_path):
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    sources = json.loads(
        (Path(contract_path).parent / "official_sources.json").read_text(encoding="utf-8")
    )
    if sources["repository_commit_sha"] != EXPECTED_COMMIT:
        raise RuntimeError("contract is not pinned to the selected VCF 9.1 commit")
    if sources["spec_path"] != EXPECTED_SPEC_PATH:
        raise RuntimeError("unexpected specification path")

    operations = {item["operationId"]: item for item in contract["operations"]}
    if set(operations) != EXPECTED_OPERATION_IDS:
        raise RuntimeError("mock only supports the two task contract operations")
    if set(sources["operationIds"]) != set(operations):
        raise RuntimeError("operation provenance and contract differ")
    return contract, operations


def compile_path(base_path, template):
    names = []
    pattern_parts = []
    cursor = 0
    for match in re.finditer(r"\{([^{}]+)\}", base_path + template):
        pattern_parts.append(re.escape((base_path + template)[cursor : match.start()]))
        pattern_parts.append(r"([^/]+)")
        names.append(match.group(1))
        cursor = match.end()
    pattern_parts.append(re.escape((base_path + template)[cursor:]))
    return re.compile("^" + "".join(pattern_parts) + "$"), names


class State:
    def __init__(self, contract, operations, log_path):
        self.log_path = Path(log_path)
        self.log_lock = threading.Lock()
        self.poll_count = 0
        self.routes = []
        for operation_id, operation in operations.items():
            regex, names = compile_path(contract["base_path"], operation["path"])
            self.routes.append((operation_id, operation, regex, names))

    def append(self, record):
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
                stream.write("\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ContractLoopback/1"
    sys_version = ""

    def do_PATCH(self):
        self.dispatch()

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        split = urllib.parse.urlsplit(self.path)
        route = None
        path_values = {}
        for operation_id, operation, regex, names in self.server.state.routes:
            match = regex.fullmatch(split.path)
            if match and self.command == operation["method"]:
                route = (operation_id, operation)
                path_values = dict(zip(names, match.groups()))
                break

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""
        record = {
            "operationId": route[0] if route else None,
            "method": self.command,
            "target": self.path,
            "raw_path": split.path,
            "raw_query": split.query,
            "path_parameters": path_values,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
        }
        if body:
            try:
                record["body_json"] = json.loads(body)
            except json.JSONDecodeError:
                record["body_json"] = None
        self.server.state.append(record)

        if route is None:
            self.send_json(404, {"error": "operation is not in the pinned contract"})
            return

        operation_id = route[0]
        if operation_id == "PatchSegment":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if operation_id == "ReadIntentStatus":
            query = urllib.parse.parse_qs(split.query, keep_blank_values=True)
            self.server.state.poll_count += 1
            sequence = ["UNREALIZED", "UNAVAILABLE", "REALIZED"]
            index = min(self.server.state.poll_count - 1, len(sequence) - 1)
            intent_path = query.get("intent_path", [""])[0]
            self.send_json(
                200,
                {
                    "intent_path": intent_path,
                    "publish_status": sequence[index],
                },
            )
            return

        self.send_json(500, {"error": "unreachable operation"})

    def send_json(self, status, value):
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    contract, operations = load_contract(args.contract)
    state = State(contract, operations, args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
