#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0206."""

from __future__ import annotations

import ast
import importlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLIENT_PATH = SRC / "vcf_installer" / "client.py"
INIT_PATH = SRC / "vcf_installer" / "__init__.py"
PROJECT_PATH = ROOT / "pyproject.toml"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
OPERATION_IDS = ["updateDepotSettings"]
TOP_LEVEL_PROPERTIES = ["vmwareAccount", "offlineAccount", "depotConfiguration"]
ACCOUNT_PROPERTIES = [
    "username",
    "password",
    "status",
    "message",
    "downloadToken",
    "downloadActivationCode",
]
UNSET_TOP_LEVEL = ["offlineAccount", "depotConfiguration"]
UNSET_ACCOUNT = [
    "username",
    "password",
    "status",
    "message",
    "downloadActivationCode",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repository") == "vmware/vcf-api-specs", "repository changed")
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "VCF API version changed")

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract must name exactly updateDepotSettings",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations]
        == [("PUT", "/v1/system/settings/depot")],
        "contract route changed",
    )
    operation = operations[0]
    require(operation.get("parameters") == [], "updateDepotSettings has no parameters")
    require(
        operation.get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "DepotSettings",
        },
        "request body projection changed",
    )
    require(
        operation.get("responses")
        == {
            "202": {
                "description": "Accepted",
                "contentType": "application/json",
                "schema": "DepotSettings",
            },
            "400": {
                "description": "Bad Request",
                "contentType": "application/json",
                "schema": "Error",
            },
            "500": {
                "description": "Internal Server Error",
                "contentType": "application/json",
                "schema": "Error",
            },
        },
        "response projection changed",
    )

    schemas = contract.get("schemas", {})
    account = schemas.get("DepotAccount", {})
    require(account.get("type") == "object", "DepotAccount type changed")
    require(account.get("required") == [], "DepotAccount fields are optional in the spec")
    require(
        list(account.get("properties", {})) == ACCOUNT_PROPERTIES,
        "DepotAccount properties changed",
    )
    require(
        account["properties"].get("downloadToken")
        == {"type": "string", "maxLength": 32},
        "downloadToken projection changed",
    )
    settings = schemas.get("DepotSettings", {})
    require(settings.get("type") == "object", "DepotSettings type changed")
    require(settings.get("required") == [], "DepotSettings properties are optional")
    require(
        list(settings.get("properties", {})) == TOP_LEVEL_PROPERTIES,
        "DepotSettings properties changed",
    )
    configuration = schemas.get("DepotConfiguration", {})
    require(
        configuration.get("required") == ["isOfflineDepot"],
        "DepotConfiguration required fields changed",
    )
    require(
        configuration.get("properties", {}).get("port")
        == {"type": "integer", "format": "int32"},
        "DepotConfiguration port projection changed",
    )
    require(sources.get("repository") == "vmware/vcf-api-specs", "source repository changed")
    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source spec path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
        "official spec URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("specJsonPointer"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            (
                "updateDepotSettings",
                "PUT",
                "/v1/system/settings/depot",
                "/paths/~1v1~1system~1settings~1depot/put/operationId",
                COMMIT,
                SPEC_PATH,
            )
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "a documentation page must not be the contract source",
    )


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def verify_package_shape() -> None:
    require(CLIENT_PATH.is_file(), "src/vcf_installer/client.py is missing")
    require(INIT_PATH.is_file(), "protected package initializer is missing")
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    require(project.get("project", {}).get("dependencies") == [], "dependencies must be empty")
    require(
        project.get("tool", {}).get("moonshiner", {}).get("stdlib-only") is True,
        "package must remain stdlib-only",
    )

    source = CLIENT_PATH.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(CLIENT_PATH))
    except SyntaxError as error:
        raise VerificationError(f"client.py is not valid Python: {error.msg}") from None
    roots = imported_roots(tree)
    third_party = roots - set(sys.stdlib_module_names) - {"__future__"}
    require(
        not third_party,
        "client imports non-stdlib modules: " + ", ".join(sorted(third_party)),
    )
    require("subprocess" not in roots, "client must not invoke external programs")
    require(
        "notimplementederror" not in source.casefold(),
        "client workflow is still a stub",
    )

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix.casefold()
        in {".whl", ".egg", ".zip", ".so", ".dll", ".dylib", ".pyc"}
    ]
    require(not vendored, "the package must not vendor dependencies or binary artifacts")


def wait_for_ready(ready_path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before readiness: " + (stderr or stdout or "no diagnostics")
            )
        if ready_path.is_file():
            try:
                ready = load_json(ready_path)
                require(ready.get("host") == "127.0.0.1", "mock is not loopback-only")
                require(ready.get("operationIds") == OPERATION_IDS, "mock operation set changed")
                port = ready.get("port")
                if isinstance(port, int) and 0 < port < 65536:
                    return port
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def run_mock_calls(
    calls_builder: Callable[[dict[str, str]], list[dict[str, Any]]],
    scenario_builder: Callable[[dict[str, str]], dict[str, Any]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    list[tuple[object | None, Exception | None]],
    Any,
]:
    runtime = {
        "accessToken": "access-" + secrets.token_urlsafe(24),
        "downloadToken": "døwnload-" + secrets.token_hex(10),
        "activationCode": "activation-Δ-" + secrets.token_hex(12),
    }
    scenario: dict[str, Any] = {"accessToken": runtime["accessToken"]}
    if scenario_builder is not None:
        scenario.update(scenario_builder(runtime))
    calls = calls_builder(runtime)
    with tempfile.TemporaryDirectory(prefix="vcf-installer-verify-") as temporary:
        temp = Path(temporary)
        scenario_path = temp / "scenario.json"
        request_log = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--scenario",
                str(scenario_path),
                "--request-log",
                str(request_log),
                "--ready",
                str(ready_path),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_ready(ready_path, process)
            if str(SRC) not in sys.path:
                sys.path.insert(0, str(SRC))
            module = importlib.import_module("vcf_installer")
            client = module.VcfInstallerClient(
                f"http://127.0.0.1:{port}",
                runtime["accessToken"],
                timeout=3.0,
            )
            outcomes: list[tuple[object | None, Exception | None]] = []
            for arguments in calls:
                try:
                    result = client.update_depot_settings(**arguments)
                except Exception as error:
                    outcomes.append((None, error))
                else:
                    outcomes.append((result, None))
        except Exception as error:
            if isinstance(error, VerificationError):
                raise
            raise VerificationError(f"could not exercise client: {type(error).__name__}") from None
        finally:
            stop_process(process)

        lines = request_log.read_text(encoding="utf-8").splitlines()
        requests = [json.loads(line) for line in lines if line.strip()]
    return requests, runtime, outcomes, module


def require_success(
    outcome: tuple[object | None, Exception | None],
    expected: dict[str, object],
) -> None:
    result, error = outcome
    require(error is None, f"client unexpectedly failed: {type(error).__name__}")
    require(isinstance(result, dict), "update_depot_settings must return a dictionary")
    require(result == expected, "successful response was not returned exactly")
    require(result is not expected, "response must be a freshly decoded dictionary")


def require_client_error(
    outcome: tuple[object | None, Exception | None],
    module: Any,
    secrets_to_hide: list[str],
) -> None:
    result, error = outcome
    require(result is None, "a failing call returned a value")
    require(
        isinstance(error, module.VcfInstallerError),
        "failures must raise VcfInstallerError",
    )
    message = str(error)
    require(
        all(secret not in message for secret in secrets_to_hide),
        "a credential was exposed in an error",
    )


def verify_exact_requests(
    requests: list[dict[str, Any]],
    runtime: dict[str, str],
    expected: dict[str, object],
    statuses: list[int | None],
    effects: list[bool],
    effect_counts: list[int],
) -> None:
    require(len(requests) == len(statuses), "unexpected request count")
    expected_body = json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    for index, request in enumerate(requests):
        require(request.get("sequence") == index + 1, "request sequence is not contiguous")
        require(
            request.get("operationId") == "updateDepotSettings",
            "an unapproved operation was called",
        )
        require(request.get("method") == "PUT", "updateDepotSettings must use PUT")
        require(
            request.get("path") == "/v1/system/settings/depot",
            "updateDepotSettings path changed",
        )
        require(
            request.get("rawTarget") == "/v1/system/settings/depot",
            "raw target contains a query or delimiter",
        )
        require(request.get("rawQuery") == "", "updateDepotSettings has no query")
        require(request.get("body") == expected_body, "request body bytes changed")
        require(
            request.get("bodyLength") == len(expected_body.encode("utf-8")),
            "request body length changed",
        )
        require(request.get("validAttempt") == index + 1, "mock rejected an attempt")

        headers = request.get("headerValues", {})
        require(
            headers.get("authorization") == [f"Bearer {runtime['accessToken']}"],
            "Authorization header value or multiplicity changed",
        )
        require(
            headers.get("accept") == ["application/json"],
            "Accept header value or multiplicity changed",
        )
        require(
            headers.get("content-type") == ["application/json"],
            "Content-Type header value or multiplicity changed",
        )
        require(
            headers.get("content-length") == [str(len(expected_body.encode("utf-8")))],
            "Content-Length header value or multiplicity changed",
        )
        require(
            "transfer-encoding" not in headers,
            "request must use fixed length rather than chunked transfer",
        )

        decoded = json.loads(request["body"])
        require(set(decoded) == {"vmwareAccount"}, "top-level optional fields were sent")
        account = decoded["vmwareAccount"]
        require(
            isinstance(account, dict)
            and set(account) == set(expected["vmwareAccount"]),
            "unset DepotAccount fields were sent",
        )
        require(
            all(name not in decoded for name in UNSET_TOP_LEVEL),
            "an unset top-level member was transmitted",
        )
        require(
            all(
                name not in account
                for name in UNSET_ACCOUNT
                if name not in expected["vmwareAccount"]
            ),
            "an unset account member was transmitted",
        )

    require(
        [request.get("body") for request in requests]
        == [expected_body] * len(requests),
        "retry representation was not byte-identical",
    )
    if requests:
        require(
            [request.get("headerValues") for request in requests]
            == [requests[0].get("headerValues")] * len(requests),
            "retry header values changed between attempts",
        )
    require(
        [request.get("responseStatus") for request in requests] == statuses,
        "scripted response sequence changed",
    )
    require(
        [request.get("effectApplied") for request in requests] == effects,
        "retry duplicated the replacement effect",
    )
    require(
        [request.get("effectCount") for request in requests] == effect_counts,
        "semantic mutation count changed",
    )


def verify_primary_replacement() -> None:
    requests, runtime, outcomes, _module = run_mock_calls(
        lambda values: [
            {"download_token": values["downloadToken"], "max_retries": 1}
        ]
    )
    expected: dict[str, object] = {
        "vmwareAccount": {"downloadToken": runtime["downloadToken"]}
    }
    require_success(outcomes[0], expected)
    verify_exact_requests(
        requests,
        runtime,
        expected,
        statuses=[500, 202],
        effects=[True, False],
        effect_counts=[1, 1],
    )


def verify_activation_code() -> None:
    requests, runtime, outcomes, _module = run_mock_calls(
        lambda values: [
            {
                "download_token": values["downloadToken"],
                "download_activation_code": values["activationCode"],
                "max_retries": 1,
            }
        ]
    )
    expected: dict[str, object] = {
        "vmwareAccount": {
            "downloadToken": runtime["downloadToken"],
            "downloadActivationCode": runtime["activationCode"],
        }
    }
    require_success(outcomes[0], expected)
    verify_exact_requests(
        requests,
        runtime,
        expected,
        statuses=[500, 202],
        effects=[True, False],
        effect_counts=[1, 1],
    )


def verify_success_response_is_returned() -> None:
    def response_scenario(values: dict[str, str]) -> dict[str, Any]:
        return {
            "responsePlan": [202],
            "successResponse": {
                "vmwareAccount": {
                    "downloadToken": values["downloadToken"],
                    "status": "READY",
                    "message": "configured",
                },
                "depotConfiguration": {"isOfflineDepot": False},
            },
        }

    requests, runtime, outcomes, _module = run_mock_calls(
        lambda values: [
            {"download_token": values["downloadToken"], "max_retries": 0}
        ],
        response_scenario,
    )
    request_value: dict[str, object] = {
        "vmwareAccount": {"downloadToken": runtime["downloadToken"]}
    }
    response_value: dict[str, object] = response_scenario(runtime)["successResponse"]
    require_success(outcomes[0], response_value)
    verify_exact_requests(
        requests,
        runtime,
        request_value,
        statuses=[202],
        effects=[True],
        effect_counts=[1],
    )


def verify_input_validation() -> None:
    def invalid_calls(values: dict[str, str]) -> list[dict[str, Any]]:
        valid_token = values["downloadToken"]
        return [
            {"download_token": None},
            {"download_token": True},
            {"download_token": ""},
            {"download_token": " \t"},
            {"download_token": "x" * 33},
            {"download_token": valid_token, "download_activation_code": ""},
            {"download_token": valid_token, "download_activation_code": " \n"},
            {"download_token": valid_token, "download_activation_code": 1},
            {"download_token": valid_token, "max_retries": True},
            {"download_token": valid_token, "max_retries": -1},
            {"download_token": valid_token, "max_retries": 6},
            {"download_token": valid_token, "max_retries": 1.0},
            {"download_token": valid_token, "max_retries": "1"},
        ]

    requests, runtime, outcomes, module = run_mock_calls(invalid_calls)
    require(not requests, "invalid input must be rejected before making a request")
    require(len(outcomes) == 13, "input validation cases did not all run")
    for outcome in outcomes:
        require_client_error(outcome, module, list(runtime.values()))


def verify_http_retry_policy() -> None:
    for status in (400, 503, 201):
        requests, runtime, outcomes, module = run_mock_calls(
            lambda values: [
                {"download_token": values["downloadToken"], "max_retries": 5}
            ],
            lambda _values, value=status: {"responsePlan": [value, 202]},
        )
        require_client_error(outcomes[0], module, list(runtime.values()))
        verify_exact_requests(
            requests,
            runtime,
            {"vmwareAccount": {"downloadToken": runtime["downloadToken"]}},
            statuses=[status],
            effects=[False],
            effect_counts=[0],
        )

    requests, runtime, outcomes, module = run_mock_calls(
        lambda values: [
            {"download_token": values["downloadToken"], "max_retries": 0}
        ],
        lambda _values: {"responsePlan": [500, 202]},
    )
    require_client_error(outcomes[0], module, list(runtime.values()))
    verify_exact_requests(
        requests,
        runtime,
        {"vmwareAccount": {"downloadToken": runtime["downloadToken"]}},
        statuses=[500],
        effects=[True],
        effect_counts=[1],
    )

    requests, runtime, outcomes, module = run_mock_calls(
        lambda values: [
            {"download_token": values["downloadToken"], "max_retries": 2}
        ],
        lambda _values: {"responsePlan": [500]},
    )
    require_client_error(outcomes[0], module, list(runtime.values()))
    verify_exact_requests(
        requests,
        runtime,
        {"vmwareAccount": {"downloadToken": runtime["downloadToken"]}},
        statuses=[500, 500, 500],
        effects=[True, False, False],
        effect_counts=[1, 1, 1],
    )


def verify_transport_retry_policy() -> None:
    for first_response, first_status in (("disconnect", None), ("partial-202", 202)):
        requests, runtime, outcomes, _module = run_mock_calls(
            lambda values: [
                {"download_token": values["downloadToken"], "max_retries": 1}
            ],
            lambda _values, response=first_response: {
                "responsePlan": [response, 202]
            },
        )
        expected: dict[str, object] = {
            "vmwareAccount": {"downloadToken": runtime["downloadToken"]}
        }
        require_success(outcomes[0], expected)
        verify_exact_requests(
            requests,
            runtime,
            expected,
            statuses=[first_status, 202],
            effects=[True, False],
            effect_counts=[1, 1],
        )

    requests, runtime, outcomes, module = run_mock_calls(
        lambda values: [
            {"download_token": values["downloadToken"], "max_retries": 0}
        ],
        lambda _values: {"responsePlan": ["disconnect", 202]},
    )
    require_client_error(outcomes[0], module, list(runtime.values()))
    verify_exact_requests(
        requests,
        runtime,
        {"vmwareAccount": {"downloadToken": runtime["downloadToken"]}},
        statuses=[None],
        effects=[True],
        effect_counts=[1],
    )


def verify_success_response_validation() -> None:
    scenarios: list[Callable[[dict[str, str]], dict[str, Any]]] = [
        lambda _values: {
            "responsePlan": [202],
            "successRawBody": "{not-json",
        },
        lambda _values: {
            "responsePlan": [202],
            "successContentType": "text/plain",
        },
        lambda _values: {"responsePlan": [202], "successResponse": []},
        lambda _values: {"responsePlan": [202], "successResponse": {}},
        lambda _values: {
            "responsePlan": [202],
            "successResponse": {"vmwareAccount": {"downloadToken": "different"}},
        },
        lambda values: {
            "responsePlan": [202],
            "successResponse": {
                "vmwareAccount": {
                    "downloadToken": values["downloadToken"],
                    "downloadActivationCode": values["activationCode"],
                }
            },
        },
    ]
    for scenario_builder in scenarios:
        requests, runtime, outcomes, module = run_mock_calls(
            lambda values: [
                {"download_token": values["downloadToken"], "max_retries": 5}
            ],
            scenario_builder,
        )
        require_client_error(outcomes[0], module, list(runtime.values()))
        verify_exact_requests(
            requests,
            runtime,
            {"vmwareAccount": {"downloadToken": runtime["downloadToken"]}},
            statuses=[202],
            effects=[True],
            effect_counts=[1],
        )

    requests, runtime, outcomes, module = run_mock_calls(
        lambda values: [
            {
                "download_token": values["downloadToken"],
                "download_activation_code": values["activationCode"],
                "max_retries": 5,
            }
        ],
        lambda values: {
            "responsePlan": [202],
            "successResponse": {
                "vmwareAccount": {
                    "downloadToken": values["downloadToken"],
                    "downloadActivationCode": "different",
                }
            },
        },
    )
    require_client_error(outcomes[0], module, list(runtime.values()))
    verify_exact_requests(
        requests,
        runtime,
        {
            "vmwareAccount": {
                "downloadToken": runtime["downloadToken"],
                "downloadActivationCode": runtime["activationCode"],
            }
        },
        statuses=[202],
        effects=[True],
        effect_counts=[1],
    )


def main() -> int:
    try:
        verify_contract()
        verify_package_shape()
        verify_input_validation()
        verify_primary_replacement()
        verify_activation_code()
        verify_success_response_is_returned()
        verify_http_retry_policy()
        verify_transport_retry_policy()
        verify_success_response_validation()
    except VerificationError as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print(
        "VERIFICATION PASSED: exact updateDepotSettings retry with one semantic effect"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
