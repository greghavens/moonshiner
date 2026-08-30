"""Loopback mock of the VCF Operations suite-api, pinned to docs/contract.json.

The mock refuses to serve anything the contract does not name: its route table is
built at start-up from ``contract["operations"]``, so an operation that is not in
the contract is a 404 no matter how plausible it looks.

State lives in memory and starts empty; every response is computed from the
requests the client actually made. Each received request is appended to a JSONL
request log so a test can assert the exact wire shape after the fact.

Run standalone:

    python3 mock/vcfops_mock.py --port 8443 --request-log ./requests.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT = REPO_ROOT / "docs" / "contract.json"

def _is_empty(value: object) -> bool:
    """True for the values an unset optional field must never be sent as.

    ``False`` is a real value a caller can choose, so booleans are never empty.
    """
    if isinstance(value, bool):
        return False
    if value is None:
        return True
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return len(value) == 0
    return False


class ContractViolation(Exception):
    """Raised when a request does not match the contract; becomes a 4xx."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class OperationsState:
    """In-memory suite-api state. Starts empty."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.groups: dict[str, dict] = {}
        self.tokens: set[str] = set()
        self._token_seq = 0
        self._uuid_seq = 0

    def issue_token(self, username: str) -> str:
        self._token_seq += 1
        digest = hashlib.sha256(f"{username}:{self._token_seq}".encode()).hexdigest()
        token = f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"
        self.tokens.add(token)
        return token

    def next_group_id(self) -> str:
        self._uuid_seq += 1
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"vcfops-mock-group/{self._uuid_seq}"))

    @staticmethod
    def identity(group: dict) -> tuple[str, str, str]:
        key = group.get("resourceKey") or {}
        return (
            key.get("adapterKindKey", ""),
            key.get("resourceKindKey", ""),
            key.get("name", ""),
        )


def _require_no_empty_optionals(obj: dict, path: str, optional_names: set[str]) -> None:
    for name in optional_names:
        if name in obj and _is_empty(obj[name]):
            raise ContractViolation(
                400,
                f"optional field '{path}{name}' was sent empty ({obj[name]!r}); "
                "unset optional fields must be omitted",
            )


def _validate_custom_group(body: object, *, expect_id: bool) -> dict:
    if not isinstance(body, dict):
        raise ContractViolation(400, "custom-group body must be a JSON object")

    allowed = {
        "autoResolveMembership",
        "id",
        "links",
        "membershipDefinition",
        "policy",
        "resourceKey",
    }
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ContractViolation(400, f"custom-group has fields not in the schema: {unknown}")

    for required in ("membershipDefinition", "resourceKey"):
        if required not in body:
            raise ContractViolation(400, f"custom-group is missing required field '{required}'")

    if "links" in body:
        raise ContractViolation(400, "'links' is server-generated and must not be sent by a client")

    if expect_id and "id" not in body:
        raise ContractViolation(400, "modifyCustomGroup requires the existing custom group 'id'")
    if not expect_id and "id" in body:
        raise ContractViolation(400, "createCustomGroup must not send 'id'; the server assigns it")

    _require_no_empty_optionals(body, "", {"autoResolveMembership", "id", "policy"})

    key = body["resourceKey"]
    if not isinstance(key, dict):
        raise ContractViolation(400, "resourceKey must be a JSON object")
    key_allowed = {"adapterKindKey", "extension", "links", "name", "resourceIdentifiers", "resourceKindKey"}
    key_unknown = sorted(set(key) - key_allowed)
    if key_unknown:
        raise ContractViolation(400, f"resourceKey has fields not in the schema: {key_unknown}")
    for required in ("adapterKindKey", "name", "resourceKindKey"):
        if not key.get(required):
            raise ContractViolation(400, f"resourceKey is missing required field '{required}'")
    _require_no_empty_optionals(key, "resourceKey.", {"extension", "links", "resourceIdentifiers"})

    membership = body["membershipDefinition"]
    if not isinstance(membership, dict):
        raise ContractViolation(400, "membershipDefinition must be a JSON object")
    membership_allowed = {"custom-group-properties", "excludedResources", "includedResources", "rules"}
    membership_unknown = sorted(set(membership) - membership_allowed)
    if membership_unknown:
        raise ContractViolation(
            400, f"membershipDefinition has fields not in the schema: {membership_unknown}"
        )
    _require_no_empty_optionals(membership, "membershipDefinition.", membership_allowed)

    return body


def _validate_username_password(body: object) -> dict:
    if not isinstance(body, dict):
        raise ContractViolation(400, "username-password body must be a JSON object")
    allowed = {"authSource", "password", "username"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ContractViolation(400, f"username-password has fields not in the schema: {unknown}")
    for required in ("username", "password"):
        if not body.get(required):
            raise ContractViolation(400, f"username-password is missing required field '{required}'")
    _require_no_empty_optionals(body, "", {"authSource"})
    return body


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfops-mock/1.0"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        pass

    @property
    def _mock(self) -> "MockServer":
        return self.server.mock  # type: ignore[attr-defined]

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int, payload: object) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw_body = self._read_body()
        record = {
            "method": method,
            "path": split.path,
            "raw_query": split.query,
            "query": [list(pair) for pair in parse_qsl(split.query, keep_blank_values=True)],
            "headers": {k: v for k, v in self.headers.items()},
            "body_raw": raw_body.decode("utf-8", "replace"),
            "body": None,
            "operation_id": None,
            "status": None,
        }
        if raw_body:
            try:
                record["body"] = json.loads(raw_body)
            except ValueError:
                record["body"] = None

        try:
            operation_id = self._mock.route(method, split.path)
            record["operation_id"] = operation_id
            status, payload = self._dispatch(operation_id, split.query, record["body"], record)
        except ContractViolation as exc:
            status, payload = exc.status, {"message": exc.message}
        except Exception as exc:  # pragma: no cover - defensive
            status, payload = 500, {"message": f"mock failure: {exc!r}"}

        record["status"] = status
        self._mock.log(record)
        self._send_json(status, payload)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    # -- contract enforcement --------------------------------------------
    def _require_accept_json(self) -> None:
        accept = self.headers.get("Accept")
        if not accept or "application/json" not in accept:
            raise ContractViolation(
                406, "the contract requires 'Accept: application/json' on every request"
            )

    def _require_json_content_type(self) -> None:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ContractViolation(415, f"expected Content-Type application/json, got {ctype!r}")

    def _require_token(self) -> None:
        header = self.headers.get("Authorization")
        if not header:
            raise ContractViolation(401, "missing Authorization header")
        prefix = "OpsToken "
        if not header.startswith(prefix):
            raise ContractViolation(401, "Authorization header must use the 'OpsToken <token>' scheme")
        token = header[len(prefix):].strip()
        with self._mock.state.lock:
            known = token in self._mock.state.tokens
        if not known:
            raise ContractViolation(401, "unknown or expired token")

    # -- operations -------------------------------------------------------
    def _dispatch(self, operation_id: str, raw_query: str, body: object, record: dict):
        self._require_accept_json()
        handler = getattr(self, f"_op_{operation_id}")
        if self._mock.contract["operations"][operation_id]["secured"]:
            self._require_token()
        return handler(raw_query, body, record)

    def _op_acquireToken(self, raw_query: str, body: object, record: dict):
        self._require_json_content_type()
        payload = _validate_username_password(body)
        state = self._mock.state
        with state.lock:
            token = state.issue_token(payload["username"])
        record["issued_token"] = token
        return 200, {
            "token": token,
            "validity": 21600000,
            "expiresAt": "6 hours from issue",
            "roles": ["ContentAdmin"],
        }

    def _op_getCustomGroups(self, raw_query: str, body: object, record: dict):
        pairs = parse_qsl(raw_query, keep_blank_values=True)
        names = {name for name, _ in pairs}
        unknown = sorted(names - {"groupId", "includePolicy"})
        if unknown:
            raise ContractViolation(400, f"unknown query parameters: {unknown}")
        for name, value in pairs:
            if value == "":
                raise ContractViolation(
                    400, f"query parameter '{name}' was sent empty; unset parameters must be omitted"
                )
        include_policy = False
        for name, value in pairs:
            if name == "includePolicy":
                if value not in ("true", "false"):
                    raise ContractViolation(400, f"includePolicy must be 'true' or 'false', got {value!r}")
                include_policy = value == "true"
        wanted = [value for name, value in pairs if name == "groupId"]

        state = self._mock.state
        with state.lock:
            groups = [dict(g) for g in state.groups.values()]
        if wanted:
            groups = [g for g in groups if g.get("id") in wanted]
        if not include_policy:
            for group in groups:
                group.pop("policy", None)
        return 200, {"groups": groups}

    def _op_createCustomGroup(self, raw_query: str, body: object, record: dict):
        self._require_json_content_type()
        group = _validate_custom_group(body, expect_id=False)
        state = self._mock.state
        identity = OperationsState.identity(group)
        with state.lock:
            for existing in state.groups.values():
                if OperationsState.identity(existing) == identity:
                    raise ContractViolation(
                        409,
                        "a custom group with resourceKey "
                        f"{identity} already exists (id={existing['id']})",
                    )
            stored = json.loads(json.dumps(group))
            stored["id"] = state.next_group_id()
            state.groups[stored["id"]] = stored
        return 201, json.loads(json.dumps(stored))

    def _op_modifyCustomGroup(self, raw_query: str, body: object, record: dict):
        self._require_json_content_type()
        group = _validate_custom_group(body, expect_id=True)
        state = self._mock.state
        group_id = group["id"]
        identity = OperationsState.identity(group)
        with state.lock:
            if group_id not in state.groups:
                raise ContractViolation(404, f"no custom group with id {group_id}")
            for other_id, existing in state.groups.items():
                if other_id != group_id and OperationsState.identity(existing) == identity:
                    raise ContractViolation(
                        409, f"resourceKey {identity} already belongs to custom group {other_id}"
                    )
            stored = json.loads(json.dumps(group))
            state.groups[group_id] = stored
        return 200, json.loads(json.dumps(stored))


class MockServer:
    """Threaded loopback HTTP server whose routes come from the contract."""

    def __init__(self, contract_path: Path | str = DEFAULT_CONTRACT, request_log: Path | str | None = None,
                 host: str = "127.0.0.1", port: int = 0) -> None:
        self.contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        self.request_log = Path(request_log) if request_log else None
        self.state = OperationsState()
        self._log_lock = threading.Lock()
        self._routes: dict[tuple[str, str], str] = {}
        base_path = self.contract["api"]["base_path"]
        for operation_id, operation in self.contract["operations"].items():
            full_path = operation.get("full_path") or base_path + operation["path"]
            self._routes[(operation["method"], full_path)] = operation_id

        if self.request_log:
            self.request_log.parent.mkdir(parents=True, exist_ok=True)
            self.request_log.write_text("", encoding="utf-8")

        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.mock = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f"http://{host}:{port}"

    def route(self, method: str, path: str) -> str:
        try:
            return self._routes[(method, path)]
        except KeyError:
            raise ContractViolation(
                404,
                f"{method} {path} is not one of the operations named by the contract "
                f"({', '.join(sorted(self.contract['operations']))})",
            ) from None

    def log(self, record: dict) -> None:
        if not self.request_log:
            return
        with self._log_lock:
            with self.request_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    def read_log(self) -> list[dict]:
        if not self.request_log or not self.request_log.exists():
            return []
        with self._log_lock:
            text = self.request_log.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def truncate_log(self) -> None:
        if self.request_log:
            with self._log_lock:
                self.request_log.write_text("", encoding="utf-8")

    def start(self) -> "MockServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "MockServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--request-log", default=None)
    args = parser.parse_args()

    server = MockServer(args.contract, args.request_log, args.host, args.port)
    print(f"vcfops mock listening on {server.base_url} (contract: {args.contract})", flush=True)
    server.start()
    try:
        while True:
            server._thread.join(1)  # noqa: SLF001
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
