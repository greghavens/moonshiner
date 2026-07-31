#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0178."""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = [
    "testLogForwarderConnection",
    "createLogForwarder",
]
ROUTES = [
    ("POST", "/api/v2/logs/forwarders/test"),
    ("POST", "/api/v2/logs/forwarders"),
]
BODY_PROPERTIES = [
    "enabled",
    "host",
    "name",
    "port",
    "protocol",
    "sslEnabled",
    "transportProtocol",
]
UNSET_PROPERTIES = [
    "certificate",
    "connectionRefreshInterval",
    "constraints",
    "forwardComplementaryFields",
    "id",
    "tags",
    "workerCount",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

    require(contract["schemaVersion"] == 1, "contract schema version changed")
    require(source["repository"] == "vmware/vcf-api-specs", "repository changed")
    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract spec blob changed")
    require(source["license"] == "Apache-2.0", "source license changed")
    require(source["openapi"] == "3.0.1", "OpenAPI version changed")
    require(source["apiVersion"] == "9.1.0.0", "API version changed")
    require(
        source["serverUrlInSpecification"] == "http://localhost:8787",
        "specification server projection changed",
    )
    require(
        contract["securitySchemes"]["OPSTokenAuthorization"]
        == {
            "type": "apiKey",
            "in": "header",
            "name": "X-JWT-Token",
        },
        "security projection changed",
    )

    operations = contract["operations"]
    require(
        [item["operationId"] for item in operations] == OPERATION_IDS,
        "focused operationId order changed",
    )
    require(
        [(item["method"], item["path"]) for item in operations] == ROUTES,
        "focused routes changed",
    )
    require(
        all(
            item["security"] == ["OPSTokenAuthorization"]
            and item["parameters"] == []
            and item["requestBody"]
            == {
                "required": True,
                "contentType": "application/json",
                "schema": "LogForwarder",
            }
            for item in operations
        ),
        "operation security, parameters, or request body changed",
    )
    require(
        set(operations[0]["responses"]) == {"200", "400", "403", "502"}
        and operations[0]["responses"]["200"]["schema"] is None
        and operations[0]["responses"]["200"]["contentType"] is None,
        "connection-test response projection changed",
    )
    require(
        set(operations[1]["responses"])
        == {"201", "400", "403", "422", "500", "502"}
        and operations[1]["responses"]["201"]["schema"] == "LogForwarder",
        "create response projection changed",
    )

    schemas = contract["schemas"]
    schema_properties = [
        "certificate",
        "connectionRefreshInterval",
        "constraints",
        "enabled",
        "forwardComplementaryFields",
        "host",
        "id",
        "name",
        "port",
        "protocol",
        "sslEnabled",
        "tags",
        "transportProtocol",
        "workerCount",
    ]
    require(
        list(schemas["LogForwarder"]["properties"]) == schema_properties
        and schemas["LogForwarder"]["required"] == [],
        "LogForwarder projection changed",
    )
    require(
        schemas["LogForwarder"]["properties"]["protocol"]["enum"]
        == ["SYSLOG", "RAW", "RAWPLUS"]
        and schemas["LogForwarder"]["properties"]["transportProtocol"]["enum"]
        == ["TCP", "UDP"]
        and schemas["LogForwarder"]["properties"]["id"]["readOnly"] is True,
        "LogForwarder enum or read-only projection changed",
    )
    require(
        list(schemas["ErrorBody"]["properties"])
        == ["errorCode", "errorDetails", "errorMessage"],
        "ErrorBody projection changed",
    )

    workflow = contract["focusedWorkflow"]
    require(
        workflow["operationOrder"] == OPERATION_IDS,
        "workflow order changed",
    )
    require(
        workflow["precheck"]
        == {
            "operationId": "testLogForwarderConnection",
            "passingStatus": 200,
            "failureBehavior": "stop-before-create",
        },
        "precheck gate changed",
    )
    require(
        workflow["mutation"]
        == {
            "operationId": "createLogForwarder",
            "successStatus": 201,
        },
        "mutation profile changed",
    )
    require(
        workflow["requestBody"]["propertyOrder"] == BODY_PROPERTIES
        and workflow["requestBody"]["unsetProperties"] == UNSET_PROPERTIES
        and workflow["requestBody"]["unsetBehavior"] == "omit"
        and workflow["requestBody"]["reuseBytesForMutation"] is True,
        "focused request-body profile changed",
    )
    require(
        "no mutation occurs" in workflow["failureRule"]
        and "come from the pinned OpenAPI specification"
        in workflow["specificationBoundary"],
        "specification and acceptance-policy boundary is unclear",
    )

    require(sources["schemaVersion"] == 1, "source schema version changed")
    require(sources["repository"] == "vmware/vcf-api-specs", "source repository changed")
    require(sources["repositoryCommitSha"] == COMMIT, "source commit changed")
    require(sources["specPath"] == SPEC_PATH, "source spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "source blob changed")
    require(sources["license"] == "Apache-2.0", "source license changed")
    require(sources["operationIds"] == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources["specUrl"]
        and sources["specUrl"].endswith(SPEC_PATH),
        "specification URL is not immutable",
    )
    require(
        [
            (
                item["operationId"],
                item["method"],
                item["path"],
                item["specLine"],
                item["repositoryCommitSha"],
                item["specPath"],
            )
            for item in sources["operations"]
        ]
        == [
            (
                "testLogForwarderConnection",
                "POST",
                "/api/v2/logs/forwarders/test",
                884,
                COMMIT,
                SPEC_PATH,
            ),
            (
                "createLogForwarder",
                "POST",
                "/api/v2/logs/forwarders",
                805,
                COMMIT,
                SPEC_PATH,
            ),
        ],
        "every operation must carry its exact pinned source",
    )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"] is False,
        "a documentation page must not be the contract source",
    )


def runtime_config(scenario: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "name": f'edge-"雪-{secrets.token_hex(5)}',
        "host": f"sink-{secrets.token_hex(6)}.lab.local",
        "port": 20000 + secrets.randbelow(30000),
        "protocol": secrets.choice(["SYSLOG", "RAW", "RAWPLUS"]),
        "ssl_enabled": False,
        "transport_protocol": secrets.choice(["TCP", "UDP"]),
        "enabled": False,
        "created_id": f"fwd-{secrets.token_hex(10)}",
        "token": f"jwt-{secrets.token_urlsafe(24)}",
        "sensitive_error": f"certificate-detail-{secrets.token_hex(16)}",
    }


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def wait_for_json(path: Path, process: subprocess.Popen[bytes]) -> Any:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise VerificationError(
                f"mock exited before readiness with status {process.returncode}"
            )
        if path.exists():
            try:
                return load_json(path)
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.01)
    raise VerificationError("mock did not become ready")


def single_header(entry: dict[str, Any], name: str) -> str:
    values = entry["headers"].get(name.lower(), [])
    require(len(values) == 1, f"{name} must occur exactly once")
    return values[0]


def expected_document(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": config["enabled"],
        "host": config["host"],
        "name": config["name"],
        "port": config["port"],
        "protocol": config["protocol"],
        "sslEnabled": config["ssl_enabled"],
        "transportProtocol": config["transport_protocol"],
    }


def expected_body(config: dict[str, Any]) -> str:
    return json.dumps(
        expected_document(config),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def assert_request(
    entry: dict[str, Any],
    *,
    operation_id: str,
    target: str,
    config: dict[str, Any],
) -> None:
    raw_body = expected_body(config)
    body_bytes = raw_body.encode("utf-8")

    require(entry["operationId"] == operation_id, "wrong operationId")
    require(entry["method"] == "POST", "operation must use POST")
    require(entry["raw_target"] == target, "raw target changed")
    require(entry["path"] == target, "request path changed")
    require(entry["query"] == "", "query string or bare question mark sent")
    require(entry["body_raw"] == raw_body, "request body bytes or property order changed")
    require(entry["body_bytes"] == len(body_bytes), "request body length changed")
    require(entry["body"] == expected_document(config), "request JSON changed")
    require(list(entry["body"]) == BODY_PROPERTIES, "request property order changed")
    require(
        set(entry["body"]) == set(BODY_PROPERTIES),
        "request has missing or extra properties",
    )
    require(
        all(name not in entry["body"] for name in UNSET_PROPERTIES),
        "an unset optional LogForwarder property was sent",
    )
    require(
        entry["body"]["enabled"] is False
        and entry["body"]["sslEnabled"] is False,
        "explicit false values were not preserved",
    )

    require(single_header(entry, "Accept") == "application/json", "wrong Accept")
    require(
        single_header(entry, "Content-Type") == "application/json",
        "wrong Content-Type",
    )
    require(
        single_header(entry, "X-JWT-Token") == config["token"],
        "wrong log token header",
    )
    require(
        single_header(entry, "Content-Length") == str(len(body_bytes)),
        "wrong Content-Length",
    )
    require("authorization" not in entry["headers"], "Authorization header must be absent")
    require(
        "transfer-encoding" not in entry["headers"],
        "chunked transfer encoding must be absent",
    )


def with_mock(
    config: dict[str, Any],
    callback: Callable[[str, Path, Path], None],
) -> None:
    with tempfile.TemporaryDirectory(prefix="vcf91-0178-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        state_path = temp / "state.json"
        ready_path = temp / "ready.json"
        config_path.write_text(
            json.dumps(config, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind(("127.0.0.1", 0))
            finally:
                probe.close()
            loopback_available = True
        except OSError:
            loopback_available = False

        if not loopback_available:
            from contract_transport import ContractTransport

            with ContractTransport(
                CONTRACT_PATH,
                log_path,
                state_path,
                config,
            ):
                callback("http://127.0.0.1:1", log_path, state_path)
            return

        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--config",
                str(config_path),
                "--log",
                str(log_path),
                "--state",
                str(state_path),
                "--ready",
                str(ready_path),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            ready = wait_for_json(ready_path, process)
            require(ready["host"] == "127.0.0.1", "mock is not loopback-only")
            base_url = f"http://127.0.0.1:{ready['port']}"
            callback(base_url, log_path, state_path)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            if process.returncode not in (0, -15):
                stderr = (process.stderr.read() if process.stderr else b"").decode(
                    "utf-8",
                    errors="replace",
                )
                raise VerificationError(
                    f"mock exited unexpectedly with status {process.returncode}: {stderr}"
                )


def make_forwarder(config: dict[str, Any]) -> Any:
    from vcf_log_forwarder import ForwarderConfig

    return ForwarderConfig(
        name=config["name"],
        host=config["host"],
        port=config["port"],
        protocol=config["protocol"],
        ssl_enabled=config["ssl_enabled"],
        transport_protocol=config["transport_protocol"],
        enabled=config["enabled"],
    )


def verify_validation_before_traffic() -> None:
    from vcf_log_forwarder import ForwarderConfig, LogManagementClient

    invalid_origins = [
        "",
        " http://127.0.0.1",
        "relative/path",
        "ftp://127.0.0.1",
        "http://user@127.0.0.1",
        "http://127.0.0.1/non-root",
        "http://127.0.0.1/?x=1",
        "http://127.0.0.1/#fragment",
    ]
    for origin in invalid_origins:
        try:
            LogManagementClient(origin, "safe-token")
        except (TypeError, ValueError):
            pass
        else:
            raise VerificationError(f"invalid origin accepted: {origin!r}")

    for token in ["", " \t ", "bad\rvalue", "bad\nvalue"]:
        try:
            LogManagementClient("http://127.0.0.1:1", token)
        except (TypeError, ValueError):
            pass
        else:
            raise VerificationError("invalid token accepted")

    for timeout in [True, 0, -1, float("inf"), float("nan"), "10"]:
        try:
            LogManagementClient(
                "http://127.0.0.1:1",
                "safe-token",
                timeout=timeout,
            )
        except (TypeError, ValueError):
            pass
        else:
            raise VerificationError(f"invalid timeout accepted: {timeout!r}")

    config = runtime_config("success")

    def callback(base_url: str, log_path: Path, _state_path: Path) -> None:
        client = LogManagementClient(base_url, config["token"])
        invalid_values = [
            ForwarderConfig(
                " ",
                config["host"],
                config["port"],
                config["protocol"],
                False,
                config["transport_protocol"],
                False,
            ),
            ForwarderConfig(
                config["name"],
                config["host"],
                True,
                config["protocol"],
                False,
                config["transport_protocol"],
                False,
            ),
            ForwarderConfig(
                config["name"],
                config["host"],
                config["port"],
                "OTHER",
                False,
                config["transport_protocol"],
                False,
            ),
        ]
        for value in invalid_values:
            try:
                client.create_after_precheck(value)
            except (TypeError, ValueError):
                pass
            else:
                raise VerificationError("invalid forwarder accepted")
        require(read_log(log_path) == [], "validation performed network traffic")

    with_mock(config, callback)


def verify_precheck_failure() -> None:
    from vcf_log_forwarder import LogManagementClient, PrecheckFailed

    config = runtime_config("precheck_failure")

    def callback(base_url: str, log_path: Path, state_path: Path) -> None:
        client = LogManagementClient(base_url, config["token"], timeout=3.0)
        try:
            client.create_after_precheck(make_forwarder(config))
        except PrecheckFailed as error:
            require(error.operation_id == OPERATION_IDS[0], "wrong precheck operation")
            require(error.status_code == 502, "precheck status was not preserved")
            rendered = f"{error!s} {error!r}"
            for secret in [
                config["token"],
                config["sensitive_error"],
                config["name"],
                config["host"],
            ]:
                require(secret not in rendered, "precheck error leaked sensitive data")
        else:
            raise VerificationError("failed precheck did not raise PrecheckFailed")

        entries = read_log(log_path)
        require(len(entries) == 1, "failed precheck made an extra request")
        assert_request(
            entries[0],
            operation_id="testLogForwarderConnection",
            target="/api/v2/logs/forwarders/test",
            config=config,
        )
        require(entries[0]["response_status"] == 502, "mock precheck status changed")
        require(entries[0]["effect_committed"] is False, "precheck mutated state")
        state = load_json(state_path)
        require(state["createCount"] == 0, "create occurred after failed precheck")
        require(state["forwarders"] == [], "state changed after failed precheck")

    with_mock(config, callback)


def verify_success() -> None:
    from vcf_log_forwarder import CreateResult, LogManagementClient

    config = runtime_config("success")

    def callback(base_url: str, log_path: Path, state_path: Path) -> None:
        client = LogManagementClient(base_url + "/", config["token"], timeout=3)
        result = client.create_after_precheck(make_forwarder(config))
        require(
            result
            == CreateResult(
                created=True,
                operation_id="createLogForwarder",
                id=config["created_id"],
            ),
            "wrong create result",
        )

        entries = read_log(log_path)
        require(len(entries) == 2, "successful workflow made the wrong request count")
        require(
            [entry["operationId"] for entry in entries] == OPERATION_IDS,
            "operation order changed",
        )
        assert_request(
            entries[0],
            operation_id="testLogForwarderConnection",
            target="/api/v2/logs/forwarders/test",
            config=config,
        )
        assert_request(
            entries[1],
            operation_id="createLogForwarder",
            target="/api/v2/logs/forwarders",
            config=config,
        )
        require(
            entries[0]["body_raw"].encode("utf-8")
            == entries[1]["body_raw"].encode("utf-8"),
            "precheck and create request bodies were not byte-identical",
        )
        require(
            entries[0]["effect_committed"] is False
            and entries[1]["effect_committed"] is True,
            "mock effect ledger changed",
        )
        state = load_json(state_path)
        require(state["createCount"] == 1, "mutation count was not exactly one")
        require(
            state["forwarders"] == [expected_document(config)],
            "created state does not match the request",
        )

    with_mock(config, callback)


def verify_create_response_validation() -> None:
    from vcf_log_forwarder import LogManagementClient, ProtocolError

    config = runtime_config("bad_create_response")

    def callback(base_url: str, log_path: Path, state_path: Path) -> None:
        client = LogManagementClient(base_url, config["token"], timeout=3.0)
        try:
            client.create_after_precheck(make_forwarder(config))
        except ProtocolError as error:
            rendered = f"{error!s} {error!r}"
            for secret in [
                config["token"],
                config["name"],
                config["host"],
                config["created_id"],
            ]:
                require(secret not in rendered, "protocol error leaked response data")
        else:
            raise VerificationError("mismatched create response was accepted")

        entries = read_log(log_path)
        require(
            [entry["operationId"] for entry in entries] == OPERATION_IDS,
            "response validation retried or changed operation order",
        )
        state = load_json(state_path)
        require(state["createCount"] == 1, "response validation replayed mutation")

    with_mock(config, callback)


def main() -> int:
    try:
        require(sys.version_info >= (3, 11), "Python 3.11 or later is required")
        require(
            sha256(CONTRACT_PATH)
            == "1b86a4feb432fd2ee7a7e971dc53a1b3cdd281fb91e9cb2c1681c7a2b18684e2",
            "protected contract bytes changed",
        )
        require(
            sha256(SOURCES_PATH)
            == "b8b54449e52f70d14fe6252a4d6e56c094ebcefdfdb2bcdfe83e21c91aeee5a7",
            "protected official source bytes changed",
        )
        verify_contract()
        sys.path.insert(0, str(ROOT))
        verify_validation_before_traffic()
        verify_precheck_failure()
        verify_success()
        verify_create_response_validation()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("ok - contract, precheck gate, wire shape, and mutation ledger verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
