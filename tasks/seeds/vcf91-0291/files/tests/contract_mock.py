"""Loopback mock pinned to ``docs/contract.json``.

The mock refuses to serve anything the contract does not name: the routable
surface, the accepted HTTP methods, the accepted query parameter names and the
required authorization header shape are all read out of the contract document
at start-up. Every accepted or rejected request is appended to a synchronized
request log so a verifier can assert the exact wire shape a client produced.

Nothing here talks to a VMware appliance; the server binds an ephemeral port on
127.0.0.1.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "contract.json"
)

#: Hard stop so a client that never terminates its pagination loop fails the
#: verifier quickly instead of hanging it.
REQUEST_CAP = 200


def load_contract(path=CONTRACT_PATH):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


class _Route:
    def __init__(self, operation):
        self.operation_id = operation["operationId"]
        self.method = operation["method"]
        self.path = operation["fullPath"]
        self.query_names = frozenset(
            param["name"]
            for param in operation.get("parameters", [])
            if param.get("in") == "query"
        )
        request_body = operation.get("requestBody")
        self.request_content_type = (
            request_body.get("contentType") if request_body else None
        )


class ContractMock:
    """A contract-pinned VCF Operations for Networks stand-in.

    Two serving modes are available and can be combined:

    * dataset mode -- ``pods`` is paginated with real cursors that honour the
      ``size`` query parameter, and ``names`` resolves entity ids to display
      names. Entity ids missing from ``names`` are simply not returned, exactly
      as the appliance behaves for entities whose name is unknown.
    * script mode -- ``list_script`` / ``names_script`` supply explicit
      ``(status, body)`` responses that are consumed in order, which lets a
      verifier exercise error and malformed-response handling.
    """

    def __init__(
        self,
        pods=(),
        names=None,
        token="netins-token",
        list_script=None,
        names_script=None,
        page_overlap=0,
    ):
        self.contract = load_contract()
        self.token = token
        self.expected_authorization = "NetworkInsight " + token
        self.routes = {}
        for operation in self.contract["operations"]:
            route = _Route(operation)
            self.routes.setdefault(route.path, {})[route.method] = route

        self.pods = [dict(pod) for pod in pods]
        self.names = dict(names or {})
        self.page_overlap = page_overlap
        self.list_script = list(list_script) if list_script is not None else None
        self.names_script = list(names_script) if names_script is not None else None

        self._lock = threading.Lock()
        self._requests = []
        self._served = 0
        self._server = None
        self._thread = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence stderr noise
                pass

            def do_GET(self):
                mock._handle(self, "GET")

            def do_POST(self):
                mock._handle(self, "POST")

            def do_PUT(self):
                mock._handle(self, "PUT")

            def do_DELETE(self):
                mock._handle(self, "DELETE")

            def do_PATCH(self):
                mock._handle(self, "PATCH")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return "http://%s:%d" % (host, port)

    # -- request log -------------------------------------------------------
    @property
    def requests(self):
        with self._lock:
            return list(self._requests)

    def requests_for(self, operation_id):
        return [item for item in self.requests if item["operation_id"] == operation_id]

    # -- serving -----------------------------------------------------------
    def _handle(self, handler, method):
        split = urlsplit(handler.path)
        raw_query = split.query
        length = int(handler.headers.get("Content-Length") or 0)
        body_bytes = handler.rfile.read(length) if length else b""
        headers = [(key, value) for key, value in handler.headers.items()]

        try:
            body_text = body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body_text = None
        try:
            body_json = json.loads(body_text) if body_text else None
        except ValueError:
            body_json = None

        route = (self.routes.get(split.path) or {}).get(method)
        entry = {
            "operation_id": route.operation_id if route else None,
            "method": method,
            "path": split.path,
            "raw_query": raw_query,
            "query_pairs": parse_qsl(raw_query, keep_blank_values=True),
            "headers": headers,
            "header_names": [key.lower() for key, _ in headers],
            "body_bytes": body_bytes,
            "body_text": body_text,
            "body_json": body_json,
        }
        with self._lock:
            self._requests.append(entry)
            self._served += 1
            over_cap = self._served > REQUEST_CAP

        if over_cap:
            return self._respond(handler, 500, self._error(500, "mock request cap reached"))
        if split.path not in self.routes:
            return self._respond(handler, 404, self._error(404, "route not in contract"))
        if route is None:
            return self._respond(handler, 405, self._error(405, "method not in contract"))
        if handler.headers.get("Authorization") != self.expected_authorization:
            return self._respond(handler, 401, self._error(401, "invalid authorization header"))

        supplied = [name for name, _ in entry["query_pairs"]]
        unknown = [name for name in supplied if name not in route.query_names]
        if unknown:
            return self._respond(
                handler, 400, self._error(400, "query parameter not in contract: %s" % unknown[0])
            )

        if route.request_content_type is not None:
            content_type = (handler.headers.get("Content-Type") or "").split(";")[0].strip()
            if content_type != route.request_content_type:
                return self._respond(handler, 400, self._error(400, "unsupported content type"))
            if not isinstance(body_json, dict):
                return self._respond(handler, 400, self._error(400, "body is not a JSON object"))

        if route.operation_id == "listKubernetesPods":
            return self._serve_list(handler, entry)
        return self._serve_names(handler, entry)

    def _serve_list(self, handler, entry):
        if self.list_script is not None:
            with self._lock:
                if not self.list_script:
                    return self._respond(handler, 500, self._error(500, "list script exhausted"))
                status, body = self.list_script.pop(0)
            return self._respond(handler, status, body)

        query = dict(entry["query_pairs"])
        try:
            size = int(float(query.get("size", "10")))
        except ValueError:
            return self._respond(handler, 400, self._error(400, "size is not a number"))
        if size < 1:
            return self._respond(handler, 400, self._error(400, "size must be positive"))

        cursor = query.get("cursor")
        if cursor is None:
            offset = 0
        else:
            try:
                offset = int(cursor)
            except ValueError:
                return self._respond(handler, 400, self._error(400, "unknown cursor"))
            if offset < 0 or offset > len(self.pods):
                return self._respond(handler, 400, self._error(400, "unknown cursor"))
            offset = max(0, offset - self.page_overlap)

        window = self.pods[offset : offset + size]
        body = {"results": [dict(pod) for pod in window], "total_count": len(self.pods)}
        next_offset = offset + size
        if next_offset < len(self.pods):
            body["cursor"] = str(next_offset)
        return self._respond(handler, 200, body)

    def _serve_names(self, handler, entry):
        if self.names_script is not None:
            with self._lock:
                if not self.names_script:
                    return self._respond(handler, 500, self._error(500, "names script exhausted"))
                status, body = self.names_script.pop(0)
            return self._respond(handler, status, body)

        requested = entry["body_json"].get("entities")
        if not isinstance(requested, list):
            return self._respond(handler, 400, self._error(400, "entities must be a list"))
        if len(requested) > 1000:
            return self._respond(handler, 400, self._error(400, "batch larger than 1000"))

        resolved = []
        for item in requested:
            if not isinstance(item, dict):
                return self._respond(handler, 400, self._error(400, "entity is not an object"))
            entity_id = item.get("entity_id")
            if entity_id in self.names:
                answer = {"entity_id": entity_id, "name": self.names[entity_id]}
                for pod in self.pods:
                    if pod.get("entity_id") == entity_id:
                        if "entity_type" in pod:
                            answer["entity_type"] = pod["entity_type"]
                        if "time" in pod:
                            answer["time"] = pod["time"]
                        break
                resolved.append(answer)
        return self._respond(handler, 200, {"entities": resolved})

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _error(code, message):
        return {"code": code, "message": message}

    @staticmethod
    def _respond(handler, status, body):
        payload = json.dumps(body).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)
