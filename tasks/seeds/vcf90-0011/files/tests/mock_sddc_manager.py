"""Contract-pinned loopback SDDC Manager used only by verification.

The route table, the accepted query-parameter names and the accepted
``resourceType`` values are all derived from ``docs/contract.json``, which is a
projection of the VCF 9.0.0.0 SDDC Manager OpenAPI file. Only the two
operationIds named by that contract are served; every other request is a 404.

The server is deliberately strict about the wire shape so that a client which
sends an optional field it was never given fails loudly instead of silently
"working":

* a query parameter present with an empty value is a 400, not an ignored no-op;
* a query parameter the contract does not declare is a 400;
* a ``TokenCreationSpec`` property the caller never supplied must be absent,
  not ``null`` and not ``""``.

Every request is appended to a JSONL request log so the verifier can assert the
exact bytes that were sent.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

EXPECTED_OPERATION_IDS = {"createToken", "getCredentials"}

ACCESS_TOKEN = "vcf90-access-token-for-verification"
REFRESH_TOKEN_ID = "vcf90-refresh-token-for-verification"
VALID_USERNAME = "administrator@vsphere.local"
VALID_PASSWORD = "VMw@re1!SddcManager"

SCENARIOS = {
    "primary", "aligned", "ties", "empty",
    "token-rejected", "credentials-rejected",
}

_NON_NEGATIVE_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")


def _credential(
    identifier: str,
    credential_type: str,
    account_type: str,
    username: str,
    password: str,
    resource_id: str,
    resource_name: str,
    resource_type: str,
    domain_name: str,
) -> dict:
    """Build a Credential exactly as the 9.0.0.0 schema shapes it."""
    return {
        "id": identifier,
        "credentialType": credential_type,
        "accountType": account_type,
        "username": username,
        "password": password,
        "creationTimestamp": "2025-03-04T09:14:22.000Z",
        "modificationTimestamp": "2025-05-19T17:02:41.000Z",
        "expiry": {
            "expiryDate": "2026-03-04T09:14:22.000Z",
            "lastCheckedDate": "2025-11-02T02:00:00.000Z",
            "connectivityStatus": "ACTIVE",
            "status": "ACTIVE",
        },
        "resource": {
            "resourceId": resource_id,
            "resourceName": resource_name,
            "resourceIp": "10.0.0.1",
            "resourceType": resource_type,
            "domainNames": [domain_name],
            "domainName": domain_name,
        },
    }


# Stored in an order that is neither sorted nor grouped, so a client that simply
# concatenates the pages it receives cannot produce the required stable order.
_PRIMARY = [
    _credential(
        "6f2d1e40-7a53-4d0b-9c11-2a8f5b31c904", "SSO", "USER",
        "administrator@vsphere.local", "Vc3nt3r!Sso#01",
        "a1c0f8d2-3b47-4e91-8f65-0d2e7c419a33",
        "vcenter-mgmt.vcf.local", "VCENTER", "mgmt-domain"),
    _credential(
        "b83c5a17-9e26-4f88-a30d-71c4e6b25f10", "SSH", "USER",
        "root", "Esxi!Root#11",
        "d4e91b60-52a8-4c37-bf19-8a03d7e15c62",
        "esxi-11.vcf.local", "ESXI", "wld-domain-01"),
    _credential(
        "1d47e8b3-0c95-42a6-8e74-59f1a2d3b806", "FTP", "SERVICE",
        "backup-admin", "B@ckup#Svc01",
        "7c25a4f8-16d3-4b09-9e58-3f0c6d81ba47",
        "backup-01.vcf.local", "BACKUP", "mgmt-domain"),
    _credential(
        "9a06c2f5-4d81-4739-b2ce-6e85f03a17d9", "API", "SERVICE",
        "svc-vcf-esxi02", "Svc!Esxi#02",
        "e0b73d19-8f42-4a56-91c7-24d5b6e08f31",
        "esxi-02.vcf.local", "ESXI", "wld-domain-01"),
    _credential(
        "3e5b9d74-a218-4c60-8f93-0b7e1c4a2568", "API", "USER",
        "admin", "Nsx!Mgr#01",
        "5f1a8c03-7e64-4d29-b085-9c37e2a416db",
        "nsxt-mgmt.vcf.local", "NSXT_MANAGER", "mgmt-domain"),
    _credential(
        "c21f7a86-5b39-4e02-97d4-8a60b3f1e547", "SSH", "USER",
        "root", "Esxi!Root#04",
        "82d4e670-1c95-4f38-a6b2-70e91d5c83a4",
        "esxi-04.vcf.local", "ESXI", "wld-domain-01"),
    _credential(
        "48ba0c93-6f17-4d85-b3ea-1c92f7580d36", "SSH", "SYSTEM",
        "root", "Esxi!Root#02",
        "e0b73d19-8f42-4a56-91c7-24d5b6e08f31",
        "esxi-02.vcf.local", "ESXI", "wld-domain-01"),
]

# Exactly six elements, so a page size of three divides evenly and the final
# page is full. A client that stops when a short page arrives instead of
# honouring totalPages asks for one page too many here.
_ALIGNED = [
    _credential(
        "f37c1b05-8d42-4e96-a071-53b8e2c4f9a6", "SSH", "USER",
        "root", "Esxi!Root#23",
        "0c94a7e3-6b18-4d52-8f07-e13c5a9b2764",
        "esxi-23.vcf.local", "ESXI", "wld-domain-02"),
    _credential(
        "5b90d1e7-4c26-48f3-b105-9a7e34d06c82", "SSH", "USER",
        "root", "Esxi!Root#21",
        "3a7f2e18-9c05-4b63-a48d-1e6082c5f73b",
        "esxi-21.vcf.local", "ESXI", "wld-domain-02"),
    _credential(
        "a6e48f21-3c07-4b95-8d1f-62a09e75c3b8", "SSO", "USER",
        "administrator@vsphere.local", "Vc3nt3r!Sso#02",
        "b158c04d-7e92-4a36-91f8-4d072b6ea5c1",
        "vcenter-wld01.vcf.local", "VCENTER", "wld-domain-02"),
    _credential(
        "0d29b7c4-1f68-4a03-95e7-8b4c6d10f2a5", "FTP", "SERVICE",
        "backup-admin", "B@ckup#Svc02",
        "6e83f5a0-2d47-4c19-b76a-95201f8de4c3",
        "backup-02.vcf.local", "BACKUP", "wld-domain-02"),
    _credential(
        "7c14e6a9-b503-4d27-8fa1-30e95b2c8746", "API", "USER",
        "admin", "Nsx!Mgr#02",
        "94c60d7b-5a13-4e08-82f6-c7b491e3a0d5",
        "nsxt-wld01.vcf.local", "NSXT_MANAGER", "wld-domain-02"),
    _credential(
        "2f8a03d6-4e71-49bc-a5c8-6b230e94f17d", "SSH", "USER",
        "root", "Esxi!Root#22",
        "17e5b93c-8046-4f2a-95d1-b3c78e604a29",
        "esxi-22.vcf.local", "ESXI", "wld-domain-02"),
]

# Both credentials deliberately have identical primary and secondary sort
# keys, and arrive with the larger id first. This makes the required tertiary
# id ordering observable.
_TIES = [
    _credential(
        "f6b14728-9d30-4c5a-8e21-73a0b4d962cf", "SSH", "USER",
        "root", "Tie!Root#02",
        "f1c8437a-25d9-4b60-ae18-7c0392d54f6b",
        "esxi-tie.vcf.local", "ESXI", "tie-domain"),
    _credential(
        "06a81e4d-2c75-49b3-90f6-1d8e37a5c4b2", "SSH", "SYSTEM",
        "root", "Tie!Root#01",
        "02d9c4a7-6e31-48b5-af20-9c7531e6d842",
        "esxi-tie.vcf.local", "ESXI", "tie-domain"),
]

DATASETS = {
    "primary": _PRIMARY,
    "aligned": _ALIGNED,
    "ties": _TIES,
    "empty": [],
    "token-rejected": _PRIMARY,
    "credentials-rejected": _PRIMARY,
}

_FILTER_FIELDS = {
    "resourceName": lambda item: item["resource"]["resourceName"],
    "resourceIp": lambda item: item["resource"]["resourceIp"],
    "resourceType": lambda item: item["resource"]["resourceType"],
    "domainName": lambda item: item["resource"]["domainName"],
    "accountType": lambda item: item["accountType"],
}


def load_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operation_ids = {item["operationId"] for item in contract["operations"]}
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise ValueError(f"unexpected operation set: {sorted(operation_ids)}")
    return contract


class ContractServer(ThreadingHTTPServer):
    # server_close() must wait for every handler to finish writing its request
    # log before verification reads that log.
    daemon_threads = False

    def __init__(self, contract: dict, log_path: Path, scenario: str):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown mock scenario: {scenario}")
        super().__init__(("127.0.0.1", 0), ContractHandler)
        self.log_path = log_path
        self.scenario = scenario
        self.dataset = DATASETS[scenario]
        self.routes = {
            (operation["method"].upper(), operation["path"]): operation
            for operation in contract["operations"]
        }
        credentials = next(
            item for item in contract["operations"]
            if item["operationId"] == "getCredentials"
        )
        self.query_parameters = {
            parameter["name"] for parameter in credentials["parameters"]
        }
        self.resource_types = set(
            next(
                parameter for parameter in credentials["parameters"]
                if parameter["name"] == "resourceType"
            )["documentedValues"]
        )
        self.token_properties = set(
            contract["schemas"]["TokenCreationSpec"]["properties"]
        )
        self.lock = threading.Lock()
        self.sequence = 0

    @property
    def uri(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def reserve_sequence(self) -> int:
        with self.lock:
            sequence = self.sequence
            self.sequence += 1
            return sequence

    def append_log(self, record: dict) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                stream.write("\n")


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockSddcManager/9.0.0.0"

    def log_message(self, fmt, *args):  # noqa: A003 - silence stderr access log
        return

    # -- plumbing ---------------------------------------------------------

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def _send(self, status: int, payload=None) -> int:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    @staticmethod
    def _error(message: str, code: str) -> dict:
        return {
            "errorCode": code,
            "errorType": "VALIDATION_FAILED",
            "message": message,
            "referenceToken": "MOCK",
        }

    def _dispatch(self, method: str) -> None:
        split = urlsplit(self.path)
        body = self._read_body()
        record = {
            # Reserve request order before sending the response. A subsequent
            # request may be dispatched as soon as the client reads that
            # response, before this handler reaches its finally block.
            "seq": self.server.reserve_sequence(),
            "method": method,
            "path": split.path,
            "raw_query": split.query,
            "query": [list(pair) for pair in parse_qsl(split.query, keep_blank_values=True)],
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body.decode("utf-8", "replace"),
        }
        operation = self.server.routes.get((method, split.path))
        record["operationId"] = operation["operationId"] if operation else None
        record["status"] = None
        try:
            if operation is None:
                record["status"] = self._send(404, self._error(
                    f"no operation {method} {split.path} in the pinned contract",
                    "NOT_FOUND"))
            elif operation["operationId"] == "createToken":
                record["status"] = self._create_token(body)
            else:
                record["status"] = self._get_credentials(record["query"])
        finally:
            self.server.append_log(record)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # -- operations -------------------------------------------------------

    def _create_token(self, body: bytes) -> int:
        media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if media_type != "application/json":
            return self._send(400, self._error(
                "createToken requires Content-Type application/json",
                "UNSUPPORTED_MEDIA_TYPE"))
        try:
            spec = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._send(400, self._error(
                "createToken body is not JSON", "MALFORMED_BODY"))
        if not isinstance(spec, dict):
            return self._send(400, self._error(
                "TokenCreationSpec must be a JSON object", "MALFORMED_BODY"))

        unknown = sorted(set(spec) - self.server.token_properties)
        if unknown:
            return self._send(400, self._error(
                f"TokenCreationSpec does not define {unknown}", "UNKNOWN_PROPERTY"))
        missing = sorted({"username", "password"} - set(spec))
        if missing:
            return self._send(400, self._error(
                f"password authentication requires {missing}", "MISSING_PROPERTY"))
        for name in ("apiKey", "idToken"):
            if name in spec:
                return self._send(400, self._error(
                    f"optional TokenCreationSpec property {name!r} was not supplied by "
                    "the caller and must be omitted, not sent as "
                    f"{spec[name]!r}", "EMPTY_OPTIONAL_PROPERTY"))
        for name in ("username", "password"):
            if not isinstance(spec[name], str) or not spec[name]:
                return self._send(400, self._error(
                    f"{name} must be a non-empty string", "MALFORMED_PROPERTY"))

        if self.server.scenario == "token-rejected":
            return self._send(401, self._error(
                f"the supplied credential {VALID_PASSWORD!r} was rejected", "UNAUTHORIZED"))
        if spec["username"] != VALID_USERNAME or spec["password"] != VALID_PASSWORD:
            return self._send(401, self._error(
                "the supplied credentials were rejected", "UNAUTHORIZED"))
        return self._send(201, {
            "accessToken": ACCESS_TOKEN,
            "refreshToken": {"id": REFRESH_TOKEN_ID},
        })

    def _get_credentials(self, query: list) -> int:
        if self.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return self._send(401, self._error(
                "getCredentials requires the bearer access token", "UNAUTHORIZED"))
        if self.server.scenario == "credentials-rejected":
            return self._send(500, self._error(
                f"credential inventory for token {ACCESS_TOKEN!r} is unavailable",
                "INTERNAL_SERVER_ERROR"))

        seen: dict[str, str] = {}
        for name, value in query:
            if name not in self.server.query_parameters:
                return self._send(400, self._error(
                    f"getCredentials does not define query parameter {name!r}",
                    "UNKNOWN_PARAMETER"))
            if name in seen:
                return self._send(400, self._error(
                    f"query parameter {name!r} was sent more than once",
                    "REPEATED_PARAMETER"))
            if value == "":
                return self._send(400, self._error(
                    f"optional query parameter {name!r} was not supplied by the caller "
                    "and must be omitted from the URL, not sent empty",
                    "EMPTY_PARAMETER"))
            seen[name] = value

        for name in ("pageNumber", "pageSize"):
            raw = seen.get(name, "0")
            if not _NON_NEGATIVE_INTEGER.match(raw):
                return self._send(400, self._error(
                    f"{name} must be a non-negative decimal integer, got {raw!r}",
                    "MALFORMED_PARAMETER"))
        page_number = int(seen.get("pageNumber", "0"))
        page_size = int(seen.get("pageSize", "0"))

        resource_type = seen.get("resourceType")
        if resource_type is not None and resource_type not in self.server.resource_types:
            return self._send(400, self._error(
                f"resourceType {resource_type!r} is not one of "
                f"{sorted(self.server.resource_types)} in this API version",
                "UNSUPPORTED_RESOURCE_TYPE"))

        matches = [
            item for item in self.server.dataset
            if all(accessor(item) == seen[name]
                   for name, accessor in _FILTER_FIELDS.items() if name in seen)
        ]

        total_elements = len(matches)
        if total_elements == 0:
            total_pages = 0
        elif page_size == 0:
            # The specification documents 0 as "return all records in one page".
            total_pages = 1
        else:
            total_pages = -(-total_elements // page_size)

        if page_size == 0:
            page = matches if page_number == 0 else []
        else:
            start = page_number * page_size
            page = matches[start:start + page_size]

        payload = {
            "pageMetadata": {
                "pageNumber": page_number,
                # The 9.0.0.0 schema documents pageMetadata.pageSize as "the
                # number of elements in the current page", not the size that was
                # requested. Report it that way.
                "pageSize": len(page),
                "totalElements": total_elements,
                "totalPages": total_pages,
            },
        }
        if page:
            payload["elements"] = page
        return self._send(200, payload)


def start_contract_server(contract: dict, log_path: Path, scenario: str) -> ContractServer:
    server = ContractServer(contract, log_path, scenario)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
