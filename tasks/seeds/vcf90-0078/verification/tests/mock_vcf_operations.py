#!/usr/bin/env python3
import argparse
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_TAG = "9.0.0.0"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATION_IDS = {"getSymptomDefinitions"}


def load_contract(contract_path):
    contract_path = Path(contract_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    sources = json.loads(
        (contract_path.parent / "official_sources.json").read_text(encoding="utf-8")
    )
    if sources["repository_commit_sha"] != EXPECTED_COMMIT:
        raise RuntimeError("contract is not pinned to the selected VCF 9.0 commit")
    if sources["repository_tag"] != EXPECTED_TAG:
        raise RuntimeError("contract is not pinned to tag 9.0.0.0")
    if sources["spec_path"] != EXPECTED_SPEC_PATH:
        raise RuntimeError("unexpected specification path")
    operations = {item["operationId"]: item for item in contract["operations"]}
    if set(operations) != EXPECTED_OPERATION_IDS:
        raise RuntimeError("mock supports only the task contract operation")
    if set(sources["operationIds"]) != set(operations):
        raise RuntimeError("operation provenance and contract differ")
    return contract, operations


class State:
    def __init__(self, contract, operations, log_path, scenario):
        operation = operations["getSymptomDefinitions"]
        self.route_path = contract["base_path"] + operation["path"]
        self.route_method = operation["method"]
        self.log_path = Path(log_path)
        self.log_lock = threading.Lock()
        self.scenario = scenario
        self.pages = [
            [
                definition("sym-30", "Zulu", "VirtualMachine"),
                definition("sym-10", "Beta", "VirtualMachine"),
            ],
            [
                definition("sym-20", 'Alpha "CPU"', "VirtualMachine"),
                definition("sym-10", "Alpha", "HostSystem"),
            ],
            [
                definition("sym-40", "Métric \\ memory", "HostSystem"),
                definition("sym-50", "Escapes /\b\f\n\r\t", "VirtualMachine"),
            ],
        ]

    def append(self, record):
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())


def definition(identifier, name, resource_kind):
    return {
        "id": identifier,
        "name": name,
        "adapterKindKey": "VMWARE",
        "resourceKindKey": resource_kind,
        "state": {
            "severity": "WARNING",
            "condition": {
                "type": "CONDITION_HT",
                "key": "cpu|usage_average",
                "operator": "GT_EQ",
                "value": "80",
                "valueType": "NUMERIC",
                "instanced": False,
                "thresholdType": "STATIC",
            },
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOperationsContractLoopback/1"
    sys_version = ""

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_PATCH(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        split = urllib.parse.urlsplit(self.path)
        state = self.server.state
        route_matches = (
            self.command == state.route_method and split.path == state.route_path
        )
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8") if content_length else ""
        state.append(
            {
                "operationId": "getSymptomDefinitions" if route_matches else None,
                "method": self.command,
                "target": self.path,
                "raw_path": split.path,
                "raw_query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )
        if not route_matches:
            self.send_json(404, {"error": "operation is not in the pinned contract"})
            return

        query = urllib.parse.parse_qs(split.query, keep_blank_values=True)
        try:
            page = int(query.get("page", ["0"])[0])
            page_size = int(query.get("pageSize", ["1000"])[0])
        except ValueError:
            self.send_json(400, {"error": "invalid page"})
            return
        if page < 0 or page_size < 1:
            self.send_json(400, {"error": "invalid page"})
            return

        if state.scenario == "http-error-page-1":
            if page == 0:
                self.send_page(page, page_size, 2, [state.pages[0][0]])
            else:
                self.send_json(503, {"error": "contract fixture failure"})
            return
        if state.scenario == "malformed-json":
            self.send_raw(200, b'{"pageInfo":{"totalCount":1},')
            return
        if state.scenario == "missing-item-field":
            item = definition("sym-bad", "Missing resource kind", "VirtualMachine")
            del item["resourceKindKey"]
            self.send_page(page, page_size, 1, [item])
            return
        if state.scenario == "wrong-total-type":
            self.send_page(page, page_size, "1", [state.pages[0][0]])
            return
        if state.scenario == "premature-empty-page":
            items = [state.pages[0][0]] if page == 0 else []
            self.send_page(page, page_size, 2, items)
            return

        items = state.pages[page] if page < len(state.pages) else []
        self.send_page(
            page,
            page_size,
            sum(len(group) for group in state.pages),
            items,
        )

    def send_page(self, page, page_size, total_count, items):
        self.send_json(
            200,
            {
                "pageInfo": {
                    "totalCount": total_count,
                    "page": page,
                    "pageSize": page_size,
                },
                "links": [],
                "symptomDefinitions": items,
            },
        )

    def send_json(self, status, value):
        encoded = json.dumps(value, separators=(",", ":"))
        if self.server.state.scenario == "complete":
            encoded = encoded.replace("/", "\\/")
        payload = encoded.encode("utf-8")
        self.send_raw(status, payload)

    def send_raw(self, status, payload):
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
    parser.add_argument(
        "--scenario",
        choices=(
            "complete",
            "http-error-page-1",
            "malformed-json",
            "missing-item-field",
            "wrong-total-type",
            "premature-empty-page",
        ),
        default="complete",
    )
    args = parser.parse_args()

    contract, operations = load_contract(args.contract)
    state = State(contract, operations, args.log, args.scenario)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
