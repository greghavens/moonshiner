#!/usr/bin/env python3
import argparse
import base64
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def operation_from_contract(contract):
    found = []
    for api_path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                found.append((operation["operationId"], method.upper(), api_path, operation))
    if len(found) != 1 or found[0][0] != "updateSymptomDefinition":
        raise ValueError("mock contract must contain only updateSymptomDefinition")
    return found[0]


def make_handler(contract, log_path):
    operation_id, method, api_path, operation = operation_from_contract(contract)
    request_path = contract["servers"][0]["url"] + api_path
    schemas = contract["components"]["schemas"]
    entities = {}
    attempts = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *args):
            return

        def _not_found(self):
            payload = b'{"error":"operation not served"}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._not_found()

        def do_POST(self):
            self._not_found()

        def do_DELETE(self):
            self._not_found()

        def do_PATCH(self):
            self._not_found()

        def do_PUT(self):
            if method != "PUT" or self.path != request_path:
                self._not_found()
                return

            transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
            if transfer_encoding == "chunked":
                chunks = []
                while True:
                    size_line = self.rfile.readline().split(b";", 1)[0].strip()
                    size = int(size_line, 16)
                    if size == 0:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        break
                    chunks.append(self.rfile.read(size))
                    if self.rfile.read(2) != b"\r\n":
                        raise ValueError("malformed chunked request")
                body_bytes = b"".join(chunks)
            else:
                length = int(self.headers.get("Content-Length", "0"))
                body_bytes = self.rfile.read(length)
            validation_error = None
            document = None
            try:
                document = json.loads(body_bytes.decode("utf-8"))
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise ValueError("Content-Type must be application/json")
                if not self.headers.get("Authorization"):
                    raise ValueError("Authorization is required")

                symptom_schema = schemas["symptom-definition"]
                missing = set(symptom_schema["required"]) - set(document)
                unknown = set(document) - set(symptom_schema["properties"])
                if missing or unknown:
                    raise ValueError(f"symptom keys missing={sorted(missing)} unknown={sorted(unknown)}")

                state = document["state"]
                state_schema = schemas["symptom-state"]
                missing = set(state_schema["required"]) - set(state)
                unknown = set(state) - set(state_schema["properties"])
                if missing or unknown:
                    raise ValueError(f"state keys missing={sorted(missing)} unknown={sorted(unknown)}")

                condition = state["condition"]
                ht_properties = schemas["HT-condition"]["allOf"][1]["properties"]
                missing = set(schemas["HT-condition"]["required"]) - set(condition)
                unknown = set(condition) - (set(ht_properties) | {"type"})
                if missing or unknown or condition.get("type") != "CONDITION_HT":
                    raise ValueError(f"condition keys/type missing={sorted(missing)} unknown={sorted(unknown)}")
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                validation_error = str(error)

            if validation_error is None:
                entity_key = document.get("id", json.dumps(document, sort_keys=True))
                entities[entity_key] = document
                attempts[entity_key] = attempts.get(entity_key, 0) + 1

            record = {
                "operationId": operation_id,
                "requestLine": self.requestline,
                "method": self.command,
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body_bytes.decode("utf-8", errors="replace"),
                "bodyBase64": base64.b64encode(body_bytes).decode("ascii"),
                "valid": validation_error is None,
                "validationError": validation_error,
                "entityCount": len(entities)
            }
            with log_path.open("a", encoding="utf-8") as request_log:
                request_log.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                request_log.flush()

            if validation_error is not None:
                payload = json.dumps({"error": validation_error}, separators=(",", ":")).encode("utf-8")
                status = 400
            elif attempts[entity_key] == 1:
                payload = b'{"error":"transient response after apply"}'
                status = 503
            else:
                response_document = dict(document)
                response_document["waitCycles"] = len(entities) + 1
                payload = json.dumps(
                    response_document,
                    ensure_ascii=False,
                    separators=(",", ":")
                ).encode("utf-8")
                status = 200

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = HTTPServer(("127.0.0.1", 0), make_handler(contract, args.log))
    print(json.dumps({"host": "127.0.0.1", "port": server.server_port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
