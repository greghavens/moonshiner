"""Contract-pinned loopback SDDC Manager for VMware Cloud Foundation 9.0.0.0.

This process is a fixture, not a VMware product. It binds an ephemeral port on
127.0.0.1 only and never reaches the network. Its routing table is built from
``docs/contract.json``, so it serves exactly the five operationIds that the
pinned 9.0.0.0 specification projection names and nothing else. Any other
method or target is refused with 404 and recorded as an off-contract request so
the acceptance harness can fail the run.

The fixture models one access token that expires part way through the run: the
token minted by ``createToken`` stops being accepted after the first credential
rotation task finishes. The next host's rotation is therefore rejected and
must be replayed after refresh. Expiry is triggered by task state rather than
wall-clock time or a raw request count, so the run is deterministic.

The optional mode argument is used by focused polling checks in verify.ps1:
``terminal:<status>`` makes every task immediately report that terminal state,
and ``never-terminal`` keeps every task in progress.

Usage: python3 mock_sddc.py <log-path> <port-path> [mode]
"""
from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text())

SDDC_MANAGER_USERNAME = "administrator@vsphere.local"
SDDC_MANAGER_PASSWORD = "VMw@re1!VMw@re1!"
INITIAL_ACCESS_TOKEN = "vcf90-access-token-initial"
REFRESHED_ACCESS_TOKEN = "vcf90-access-token-refreshed"
REFRESH_TOKEN_ID = "vcf90-refresh-token-0001"

# Four ESXi hosts carry a rotatable SSH credential. esxi-05 deliberately has no
# SSH credential. Its API credential otherwise resembles the SSH entries, so
# filtering by username or account type instead of credentialType is caught.
HOSTS = [
    ("host-01", "esxi-01.vcf.local", "10.0.0.11", True),
    ("host-02", "esxi-02.vcf.local", "10.0.0.12", True),
    ("host-03", "esxi-03.vcf.local", "10.0.0.13", True),
    ("host-04", "esxi-04.vcf.local", "10.0.0.14", True),
    ("host-05", "esxi-05.vcf.local", "10.0.0.15", False),
]
ROTATABLE_RESOURCE_IDS = [host[0] for host in HOSTS if host[3]]


def build_credentials() -> list[dict]:
    """Build the PageOfCredential elements this fixture serves."""
    elements = []
    for index, (resource_id, fqdn, ip_address, has_ssh) in enumerate(HOSTS, 1):
        resource = {
            "resourceId": resource_id,
            "resourceName": fqdn,
            "fqdn": fqdn,
            "resourceIp": ip_address,
            "resourceType": "ESXI",
        }
        if has_ssh:
            elements.append({
                "id": f"cred-ssh-{index:02d}",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "resource": dict(resource),
                "creationTimestamp": "2026-01-05T09:00:00.000Z",
                "modificationTimestamp": "2026-01-05T09:00:00.000Z",
            })
        elements.append({
            "id": f"cred-api-{index:02d}",
            "credentialType": "API",
            "accountType": "USER" if not has_ssh else "SYSTEM",
            "username": "root" if not has_ssh else f"svc-vcf-{index:02d}",
            "resource": dict(resource),
            "creationTimestamp": "2026-01-05T09:00:00.000Z",
            "modificationTimestamp": "2026-01-05T09:00:00.000Z",
        })
    return elements


CREDENTIALS = build_credentials()


def compile_routes() -> list[tuple[str, re.Pattern, str]]:
    """Derive the served routes from the protected contract projection."""
    routes = []
    for operation in CONTRACT["operations"].values():
        pattern = re.sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", operation["path"])
        routes.append((operation["method"],
                       re.compile(f"^{pattern}$"),
                       operation["operationId"]))
    return routes


ROUTES = compile_routes()


class FixtureState:
    """All mutable fixture state, guarded for the threading HTTP server."""

    def __init__(self, log_path: Path, mode: str = "normal"):
        self.lock = threading.Lock()
        self.log_path = log_path
        self.entries: list[dict] = []
        self.access_token: str | None = None
        self.expired_tokens: set[str] = set()
        self.token_sequence = 0
        self.rotation_tasks: dict[str, dict] = {}
        self.accepted_rotations: list[str] = []
        self.task_sequence = 0
        self.mode = mode

    def mint_access_token(self) -> str:
        """Issue an access token. A real appliance hands out a usable token on
        every login, so re-authenticating is possible here; it is simply not
        what this task asks for."""
        self.token_sequence += 1
        token = (INITIAL_ACCESS_TOKEN if self.token_sequence == 1
                 else f"vcf90-access-token-relogin-{self.token_sequence - 1:02d}")
        self.access_token = token
        return token

    def append(self, entry: dict) -> None:
        self.entries.append(entry)
        with self.log_path.open("w") as handle:
            json.dump(self.entries, handle, indent=1)
            handle.write("\n")

    def match(self, method: str, path: str):
        for route_method, pattern, operation_id in ROUTES:
            found = pattern.match(path)
            if found and route_method == method:
                return operation_id, found.groupdict()
        return None, {}


def error_body(code: str, message: str) -> dict:
    return {"errorCode": code, "errorType": "VALIDATION",
            "message": message, "referenceToken": "fixture"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MoonshinerVcfFixture/9.0.0.0"

    def log_message(self, *args) -> None:  # keep the fixture quiet
        pass

    # -- helpers ----------------------------------------------------------
    def _bearer(self) -> str | None:
        value = self.headers.get("Authorization") or ""
        return value[7:].strip() if value.startswith("Bearer ") else None

    def _respond(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorize(self, state: FixtureState):
        """Return an error tuple when the presented token is not usable."""
        token = self._bearer()
        if not token:
            return 401, error_body("UNAUTHENTICATED",
                                   "Authorization: Bearer <accessToken> required")
        if token in state.expired_tokens:
            return 401, error_body("TOKEN_EXPIRED",
                                   "The access token has expired. Refresh it "
                                   "with the refresh token.")
        if token != state.access_token:
            return 401, error_body("TOKEN_INVALID", "Unknown access token")
        return None

    # -- operations -------------------------------------------------------
    def _create_token(self, state: FixtureState, parsed):
        if not isinstance(parsed, dict):
            return 400, error_body("BAD_REQUEST", "TokenCreationSpec expected")
        if (parsed.get("username") != SDDC_MANAGER_USERNAME
                or parsed.get("password") != SDDC_MANAGER_PASSWORD):
            return 401, error_body("UNAUTHENTICATED", "Invalid credentials")
        return 201, {"accessToken": state.mint_access_token(),
                     "refreshToken": {"id": REFRESH_TOKEN_ID}}

    def _refresh_access_token(self, state: FixtureState, parsed):
        # The pinned contract types this body as a bare JSON string.
        if not isinstance(parsed, str):
            return 400, error_body(
                "BAD_REQUEST",
                "refreshAccessToken expects a bare JSON string holding the "
                "refresh token id")
        if parsed != REFRESH_TOKEN_ID:
            return 404, error_body("NOT_FOUND", "Unknown refresh token")
        state.access_token = REFRESHED_ACCESS_TOKEN
        return 200, REFRESHED_ACCESS_TOKEN

    def _get_credentials(self, state: FixtureState, query):
        matching = list(CREDENTIALS)
        resource_type = query.get("resourceType")
        if resource_type:
            matching = [item for item in matching
                        if item["resource"]["resourceType"] == resource_type]
        account_type = query.get("accountType")
        if account_type:
            matching = [item for item in matching
                        if item["accountType"] == account_type]

        total = len(matching)
        try:
            page_size = int(query["pageSize"]) if "pageSize" in query else total
            page_number = int(query["pageNumber"]) if "pageNumber" in query else 0
        except ValueError:
            return 400, error_body("BAD_REQUEST", "pageNumber/pageSize must be integers")
        if page_size <= 0 or page_number < 0:
            return 400, error_body("BAD_REQUEST", "pageNumber/pageSize out of range")

        total_pages = max(1, (total + page_size - 1) // page_size)
        start = page_number * page_size
        return 200, {
            "elements": matching[start:start + page_size],
            "pageMetadata": {"pageNumber": page_number, "pageSize": page_size,
                             "totalElements": total, "totalPages": total_pages},
        }

    def _update_or_rotate_passwords(self, state: FixtureState, parsed):
        if not isinstance(parsed, dict):
            return 400, error_body("BAD_REQUEST", "CredentialsUpdateSpec expected")
        if parsed.get("operationType") != "ROTATE":
            return 400, error_body("BAD_REQUEST", "operationType must be ROTATE")
        elements = parsed.get("elements")
        if not isinstance(elements, list) or not elements:
            return 400, error_body("BAD_REQUEST", "elements must be a non-empty array")

        resource_ids = []
        for element in elements:
            if not isinstance(element, dict):
                return 400, error_body("BAD_REQUEST", "element must be an object")
            resource_id = element.get("resourceId")
            known = {host[0] for host in HOSTS}
            if resource_id not in known:
                return 400, error_body("BAD_REQUEST", f"unknown resourceId {resource_id!r}")
            resource_ids.append(resource_id)

        state.task_sequence += 1
        task_id = f"credentials-task-{state.task_sequence:02d}"
        state.rotation_tasks[task_id] = {"polls": 0, "resourceIds": resource_ids}
        state.accepted_rotations.extend(resource_ids)
        return 202, {"id": task_id, "name": "Rotate Passwords", "type": "ROTATE",
                     "status": "IN_PROGRESS",
                     "creationTimestamp": "2026-01-05T09:10:00.000Z"}

    def _get_credentials_task(self, state: FixtureState, parameters):
        task_id = parameters.get("id")
        task = state.rotation_tasks.get(task_id)
        if task is None:
            return 404, error_body("NOT_FOUND", f"unknown task {task_id!r}")
        task["polls"] += 1
        if state.mode == "never-terminal":
            task_status = "IN_PROGRESS"
        elif state.mode.startswith("terminal:"):
            task_status = state.mode.partition(":")[2]
        else:
            task_status = "SUCCESSFUL" if task["polls"] > 1 else "IN_PROGRESS"
        payload = {"id": task_id, "name": "Rotate Passwords", "type": "ROTATE",
                   "status": task_status,
                   "creationTimestamp": "2026-01-05T09:10:00.000Z",
                   "isAutoRotate": False}
        if task_status != "IN_PROGRESS":
            payload["completionTimestamp"] = "2026-01-05T09:11:00.000Z"
            # Expire only after one task reaches its terminal response. The
            # following host rotation receives 401 and has to be replayed.
            state.expired_tokens.add(INITIAL_ACCESS_TOKEN)
        return 200, payload

    # -- dispatch ---------------------------------------------------------
    def _dispatch(self) -> None:
        state: FixtureState = self.server.state
        target = urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        query = {key: values[0] for key, values in
                 parse_qs(target.query, keep_blank_values=True).items()}

        parsed_body = None
        body_parse_error = False
        if raw_body:
            try:
                parsed_body = json.loads(raw_body)
            except json.JSONDecodeError:
                body_parse_error = True

        with state.lock:
            operation_id, parameters = state.match(self.command, target.path)
            if operation_id is None:
                status, payload = 404, error_body(
                    "NOT_FOUND",
                    "The pinned 9.0.0.0 contract does not serve "
                    f"{self.command} {target.path}")
            elif body_parse_error:
                status, payload = 400, error_body("BAD_REQUEST", "body is not valid JSON")
            elif operation_id == "createToken":
                status, payload = self._create_token(state, parsed_body)
            elif operation_id == "refreshAccessToken":
                status, payload = self._refresh_access_token(state, parsed_body)
            else:
                denied = self._authorize(state)
                if denied is not None:
                    status, payload = denied
                elif operation_id == "getCredentials":
                    status, payload = self._get_credentials(state, query)
                elif operation_id == "updateOrRotatePasswords":
                    status, payload = self._update_or_rotate_passwords(state, parsed_body)
                else:
                    status, payload = self._get_credentials_task(state, parameters)

            state.append({
                "operationId": operation_id,
                "offContract": operation_id is None,
                "method": self.command,
                "path": target.path,
                "rawTarget": self.path,
                "rawQuery": target.query,
                "query": query,
                "headers": {name.lower(): value.strip()
                            for name, value in self.headers.items()},
                "bearer": self._bearer(),
                "rawBody": raw_body,
                "body": parsed_body,
                "status": status,
                "acceptedRotations": list(state.accepted_rotations),
            })

        self._respond(status, payload)

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = _dispatch


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit("usage: mock_sddc.py <log-path> <port-path> [mode]")
    log_path, port_path = Path(sys.argv[1]), Path(sys.argv[2])
    mode = sys.argv[3] if len(sys.argv) == 4 else "normal"
    valid_modes = {"normal", "never-terminal"}
    valid_terminal_states = {
        "FAILED", "USER_CANCELLED", "INCONSISTENT"
    }
    if (mode not in valid_modes
            and not (mode.startswith("terminal:")
                     and mode.partition(":")[2] in valid_terminal_states)):
        raise SystemExit(f"unsupported fixture mode: {mode}")
    log_path.write_text("[]\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.state = FixtureState(log_path, mode)
    port_path.write_text(str(server.server_address[1]))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
