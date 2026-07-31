#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "contract_mock.py"

PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC = "specifications/vcf-operations/log-management-openapi.json"
OPERATION_IDS = [
    "createAgentSecret",
    "createAgentSession",
    "revokeAgentSecret",
]

# Filled with hashes of every protected fixture other than this verifier.
PROTECTED_SHA256 = {
    "docs/contract.json": "d52f0d41b0b1b7e063cbb4fa64134224718c2e5986b3ae7d044a7799041e4946",
    "docs/official_sources.json": "e289b22f021b32153032384f0306495b20f62e0cfa2906e6383a277cf4938abd",
    "tests/__init__.py": "b0a1070df83ae922063f99664a1126d69ba0fcd4b4c7ccc2bc0bcf67a717e9b5",
    "tools/contract_mock.py": "e817ebdd9c7497a6711c1f04a093da38e9afa729dcde80d8c274a8543b3a5f75",
    "vcf_log_rotation/__init__.py": "02ccacb4a5499d74ceb14dbc236f8711b796849e4ac21dbea88941fa9ad5ba10",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        require(path.is_file(), f"missing protected file: {relative}")
        require(expected != "TO_BE_FILLED", "verifier hashes were not finalized")
        require(sha256(path) == expected, f"protected file changed: {relative}")


def verify_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    source = contract.get("source", {})
    for item in (source, sources):
        require(
            item.get("repositoryCommitSha") == PINNED_COMMIT,
            "wrong source commit",
        )
        require(item.get("specPath") == PINNED_SPEC, "wrong spec path")
        require(item.get("license") == "Apache-2.0", "wrong source license")
    require(
        source.get("openapi") == "3.0.1"
        and source.get("apiVersion") == "9.1.0.0",
        "wrong OpenAPI identity",
    )
    require(
        sources.get("operationIds") == OPERATION_IDS,
        "official operationId list changed",
    )
    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations missing")
    require(
        [item.get("contractName") for item in operations] == OPERATION_IDS,
        "contract route allow-list changed",
    )
    expected_wire = [
        ("createAgentSecret", "POST", "/api/v2/agent/secrets", 201),
        (
            "createAgentSession",
            "POST",
            "/api/v2/agent/secrets/exchange",
            200,
        ),
        (
            "revokeAgentSecret",
            "POST",
            "/api/v2/agent/secrets/{secretName}/revoke",
            200,
        ),
    ]
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("pathTemplate"),
                item.get("successStatus"),
            )
            for item in operations
        ]
        == expected_wire,
        "focused wire projection changed",
    )
    for item in operations:
        require(
            item.get("queryParameters") == [],
            "focused operations must not gain query parameters",
        )
        require(
            item.get("security") == ["OPSTokenAuthorization"],
            "operation security changed",
        )
    auth = contract["securitySchemes"]["OPSTokenAuthorization"]
    require(
        auth
        == {"type": "apiKey", "in": "header", "name": "X-JWT-Token"},
        "security header changed",
    )
    request_schema = contract["schemas"]["AgentAuthenticationRequest"]
    require(
        request_schema.get("required") == ["secret"],
        "exchange required fields changed",
    )
    require(
        request_schema["properties"]["ttl"].get("unsetBehavior") == "omit",
        "unset ttl behavior changed",
    )
    response_schema = contract["schemas"]["AgentAuthenticationResponse"]
    require(
        response_schema.get("required")
        == ["access_token", "name", "new_secret", "ttl"],
        "exchange response requirements changed",
    )


def read_log(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            entries.append(json.loads(line))
    return entries


def wait_for(
    predicate: Callable[[], bool], message: str, timeout: float = 4.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise VerificationError(message)


def header_values(entry: dict[str, object], name: str) -> list[str]:
    wanted = name.casefold()
    return [
        value
        for key, value in entry["headers"]
        if str(key).casefold() == wanted
    ]


def body_bytes(entry: dict[str, object]) -> bytes:
    return base64.b64decode(str(entry["bodyBase64"]), validate=True)


def compact(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def assert_headers(
    entry: dict[str, object], ops_token: str, has_json_body: bool
) -> None:
    require(
        header_values(entry, "Accept") == ["application/json"],
        "Accept header mismatch or duplicate",
    )
    require(
        header_values(entry, "X-JWT-Token") == [ops_token],
        "X-JWT-Token mismatch or duplicate",
    )
    expected_content = ["application/json"] if has_json_body else []
    require(
        header_values(entry, "Content-Type") == expected_content,
        "Content-Type presence or multiplicity mismatch",
    )
    require(not header_values(entry, "Authorization"), "unexpected Authorization")
    require(not header_values(entry, "Cookie"), "unexpected Cookie")


def compile_template(template: str) -> re.Pattern[str]:
    parts = ["^"]
    cursor = 0
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"([^/]+)")
        cursor = match.end()
    parts.extend((re.escape(template[cursor:]), "$"))
    return re.compile("".join(parts))


class FallbackResponse:
    def __init__(self, status: int, value: object) -> None:
        self.status = status
        self._body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._offset = 0

    def __enter__(self) -> "FallbackResponse":
        return self

    def __exit__(self, *ignored: object) -> None:
        self.close()

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        result = self._body[self._offset : self._offset + size]
        self._offset += len(result)
        return result

    def close(self) -> None:
        return


class ContractFallbackOpener:
    """Request-level equivalent used only when the sandbox denies sockets."""

    def __init__(
        self, contract_path: Path, log_path: Path, state_path: Path
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.routes = [
            (
                item["contractName"],
                item["method"],
                compile_template(item["pathTemplate"]),
            )
            for item in contract["operations"]
        ]
        require(
            [item[0] for item in self.routes] == OPERATION_IDS,
            "fallback route allow-list changed",
        )
        self.log_path = log_path
        self.state_path = state_path
        self.lock = threading.Lock()
        self.secrets_by_name: dict[str, str] = {}
        self.names_by_secret: dict[str, str] = {}
        self.creation_order: list[str] = []
        self.revoked: set[str] = set()
        self.active: dict[str, int] = {}
        self.sessions: dict[str, list[dict[str, object]]] = {}
        self.early_revocations: list[str] = []
        self._write_state_locked()

    def open(
        self, request: object, timeout: float | None = None
    ) -> FallbackResponse:
        del timeout
        method = request.get_method()
        split = urllib.parse.urlsplit(request.full_url)
        raw_target = split.path + (("?" + split.query) if split.query else "")
        operation = None
        captures: list[str] = []
        for name, expected_method, pattern in self.routes:
            match = pattern.fullmatch(split.path)
            if method == expected_method and match is not None:
                operation = name
                captures = [urllib.parse.unquote(value) for value in match.groups()]
                break
        body = request.data or b""
        entry = {
            "operation": operation,
            "method": method,
            "rawTarget": raw_target,
            "headers": [[key, value] for key, value in request.header_items()],
            "bodyBase64": base64.b64encode(body).decode("ascii"),
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
        if operation == "createAgentSecret":
            return self._create_secret(body)
        if operation == "createAgentSession":
            return self._create_session(body)
        if operation == "revokeAgentSecret":
            return self._revoke(captures, body)
        return FallbackResponse(404, {"error": "route not in contract"})

    def _create_secret(self, body: bytes) -> FallbackResponse:
        value = json.loads(body.decode("utf-8"))
        name = value.get("name")
        if not isinstance(name, str) or not name:
            return FallbackResponse(400, {"error": "name required"})
        with self.lock:
            secret = "secret_" + secrets.token_urlsafe(24)
            self.secrets_by_name[name] = secret
            self.names_by_secret[secret] = name
            self.creation_order.append(name)
            self.active[name] = 0
            self.sessions[name] = []
            self._write_state_locked()
        return FallbackResponse(
            201,
            {
                "id": "id_" + secrets.token_hex(8),
                "name": name,
                "secret": secret,
                "status": "ACTIVE",
            },
        )

    def _create_session(self, body: bytes) -> FallbackResponse:
        value = json.loads(body.decode("utf-8"))
        secret = value.get("secret")
        ttl = value.get("ttl", 1_800_000)
        with self.lock:
            name = self.names_by_secret.get(secret)
            if name is None or name in self.revoked:
                return FallbackResponse(400, {"error": "invalid secret"})
            self.active[name] += 1
            is_old = name == self.creation_order[0]
            self._write_state_locked()
        try:
            if is_old:
                time.sleep(1.2)
            with self.lock:
                if name in self.revoked:
                    return FallbackResponse(
                        400, {"error": "secret revoked in flight"}
                    )
                session = {
                    "access_token": "access_" + secrets.token_urlsafe(24),
                    "name": name,
                    "new_secret": "next_" + secrets.token_urlsafe(24),
                    "ttl": ttl,
                }
                self.sessions[name].append(session)
                self._write_state_locked()
            return FallbackResponse(200, session)
        finally:
            with self.lock:
                self.active[name] -= 1
                self._write_state_locked()

    def _revoke(
        self, captures: list[str], body: bytes
    ) -> FallbackResponse:
        if body or len(captures) != 1:
            return FallbackResponse(400, {"error": "invalid revoke"})
        name = captures[0]
        with self.lock:
            if self.active.get(name, 0):
                self.early_revocations.append(name)
            self.revoked.add(name)
            self._write_state_locked()
        return FallbackResponse(
            200,
            {
                "id": "id_" + secrets.token_hex(8),
                "name": name,
                "status": "REVOKED",
            },
        )

    def _write_state_locked(self) -> None:
        self.state_path.write_text(
            json.dumps(
                {
                    "secrets": self.secrets_by_name,
                    "creationOrder": self.creation_order,
                    "revoked": sorted(self.revoked),
                    "activeExchanges": self.active,
                    "sessions": self.sessions,
                    "earlyRevocations": self.early_revocations,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )


def run_scenario() -> None:
    sys.path.insert(0, str(ROOT))
    sys.dont_write_bytecode = True
    from vcf_log_rotation import RotatingAgentSessionClient

    suffix = secrets.token_hex(5)
    old_name = f"legacy/agent Ω {suffix}"
    new_name = f"replacement-{suffix}"
    ops_token = "ops_" + secrets.token_urlsafe(27)

    with tempfile.TemporaryDirectory(prefix="vcf-log-rotation-") as temporary:
        directory = Path(temporary)
        log_path = directory / "requests.jsonl"
        state_path = directory / "state.json"
        port_path = directory / "port.json"
        process: subprocess.Popen[str] | None = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--log",
                str(log_path),
                "--state",
                str(state_path),
                "--port-file",
                str(port_path),
                "--old-exchange-delay-ms",
                "1200",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_for(
                lambda: port_path.exists()
                or (process is not None and process.poll() is not None),
                "mock did not start",
            )
            if process.poll() is None:
                port = json.loads(port_path.read_text(encoding="utf-8"))["port"]
                fallback = None
            else:
                stderr = process.stderr.read() if process.stderr else ""
                require(
                    "Operation not permitted" in stderr
                    or "PermissionError" in stderr,
                    "mock exited for a reason other than sandbox socket policy",
                )
                process = None
                port = 1
                fallback = ContractFallbackOpener(
                    CONTRACT_PATH, log_path, state_path
                )
            client = RotatingAgentSessionClient(
                f"http://127.0.0.1:{port}", ops_token, timeout=3.0
            )
            if fallback is not None:
                client._opener = fallback

            client.bootstrap(old_name)
            require(
                client.current_secret_name == old_name,
                "bootstrap did not publish the initial secret",
            )

            old_result: list[object] = []
            old_error: list[BaseException] = []

            def exchange_old() -> None:
                try:
                    old_result.append(client.create_session())
                except BaseException as error:
                    old_error.append(error)

            old_thread = threading.Thread(target=exchange_old, daemon=True)
            old_thread.start()
            wait_for(
                lambda: len(read_log(log_path)) >= 2,
                "old exchange never reached the mock",
            )

            rotation_error: list[BaseException] = []

            def rotate() -> None:
                try:
                    client.rotate(new_name)
                except BaseException as error:
                    rotation_error.append(error)

            rotation_thread = threading.Thread(target=rotate, daemon=True)
            rotation_thread.start()
            wait_for(
                lambda: len(read_log(log_path)) >= 3,
                "replacement creation never reached the mock",
            )
            wait_for(
                lambda: client.current_secret_name == new_name,
                "replacement was not published while the old exchange drained",
            )

            new_session = client.create_session(ttl_ms=120_000)

            old_thread.join(4.0)
            rotation_thread.join(4.0)
            require(not old_thread.is_alive(), "old exchange was stranded")
            require(not rotation_thread.is_alive(), "rotation did not finish")
            require(not old_error, "old in-flight exchange failed")
            require(not rotation_error, "rotation failed")
            require(len(old_result) == 1, "old exchange returned no session")

            entries = read_log(log_path)
            require(len(entries) == 5, f"expected 5 requests, got {len(entries)}")
            require(
                [entry["operation"] for entry in entries]
                == [
                    "createAgentSecret",
                    "createAgentSession",
                    "createAgentSecret",
                    "createAgentSession",
                    "revokeAgentSecret",
                ],
                "operation order changed",
            )
            require(
                all(entry["method"] == "POST" for entry in entries),
                "HTTP method mismatch",
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            old_secret = state["secrets"][old_name]
            new_secret = state["secrets"][new_name]
            expected_targets = [
                "/api/v2/agent/secrets",
                "/api/v2/agent/secrets/exchange",
                "/api/v2/agent/secrets",
                "/api/v2/agent/secrets/exchange",
                (
                    "/api/v2/agent/secrets/"
                    + urllib.parse.quote(
                        old_name,
                        safe="-._~",
                        encoding="utf-8",
                        errors="strict",
                    )
                    + "/revoke"
                ),
            ]
            require(
                [entry["rawTarget"] for entry in entries] == expected_targets,
                "raw request target mismatch",
            )
            require(
                all("?" not in str(entry["rawTarget"]) for entry in entries),
                "unexpected query delimiter",
            )
            expected_bodies = [
                compact({"name": old_name}),
                compact({"secret": old_secret}),
                compact({"name": new_name}),
                compact({"secret": new_secret, "ttl": 120_000}),
                b"",
            ]
            require(
                [body_bytes(entry) for entry in entries] == expected_bodies,
                "exact request body bytes mismatch",
            )
            for index, entry in enumerate(entries):
                assert_headers(entry, ops_token, index != 4)

            old_exchange = json.loads(body_bytes(entries[1]).decode("utf-8"))
            require(
                set(old_exchange) == {"secret"},
                "unset ttl must be omitted, not sent empty",
            )
            new_exchange = json.loads(body_bytes(entries[3]).decode("utf-8"))
            require(
                new_exchange.get("ttl") == 120_000
                and isinstance(new_exchange["ttl"], int),
                "explicit ttl was not preserved as an integer",
            )
            require(
                state["earlyRevocations"] == [],
                "old secret was revoked while its exchange was active",
            )
            require(state["revoked"] == [old_name], "wrong secret revoked")
            require(
                old_result[0].access_token
                == state["sessions"][old_name][0]["access_token"],
                "old session response was not validated or returned",
            )
            require(
                new_session.access_token
                == state["sessions"][new_name][0]["access_token"],
                "new session response was not validated or returned",
            )
        finally:
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)


def main() -> int:
    try:
        verify_protected_files()
        verify_contract()
        run_scenario()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: contract, exact wire shape, omission, and rotation drain verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
