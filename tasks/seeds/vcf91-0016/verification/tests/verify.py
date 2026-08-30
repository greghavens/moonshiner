#!/usr/bin/env python3
"""Protected acceptance verifier for vcf91-0016."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = Path(__file__).resolve().parent / "mock_sddc_manager.py"
SRC_PATH = ROOT / "src"
EXPECTED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
EXPECTED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_ID = "updateDepotSettings"
EXPECTED_SPEC_URL = (
    "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
    f"{EXPECTED_COMMIT}/{EXPECTED_SPEC_PATH}"
)


class VerificationFailure(AssertionError):
    """Acceptance contract failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def operation_index(contract: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    found: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            operation_id = operation["operationId"]
            require(operation_id not in found, f"duplicate operationId in contract: {operation_id}")
            found[operation_id] = (method.upper(), path, operation)
    return found


def verify_provenance() -> tuple[bytes, dict[str, Any]]:
    contract_bytes = CONTRACT_PATH.read_bytes()
    contract = json.loads(contract_bytes)
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    operations = operation_index(contract)

    require(contract.get("openapi") == "3.0.1", "contract must retain OpenAPI 3.0.1")
    require(
        contract.get("info", {}).get("version") == "9.1.0.0",
        "contract must identify SDDC Manager 9.1.0.0",
    )
    require(
        set(operations) == {EXPECTED_OPERATION_ID},
        "contract must contain only the named updateDepotSettings operation",
    )
    method, path, operation = operations[EXPECTED_OPERATION_ID]
    require(method == "PUT", "updateDepotSettings must be PUT")
    require(path == "/v1/system/settings/depot", "updateDepotSettings path drifted")
    request_schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    require(
        request_schema == {"$ref": "#/components/schemas/DepotSettings"},
        "updateDepotSettings request schema drifted",
    )

    require(
        sources.get("source_type") == "OpenAPI specification",
        "official source must be the specification",
    )
    require(
        sources.get("repository") == "https://github.com/vmware/vcf-api-specs",
        "official repository drifted",
    )
    require(
        sources.get("repository_commit_sha") == EXPECTED_COMMIT,
        "official source commit drifted",
    )
    require(sources.get("spec_path") == EXPECTED_SPEC_PATH, "official spec path drifted")
    require(sources.get("spec_url") == EXPECTED_SPEC_URL, "pinned spec URL drifted")
    require(sources.get("repository_license") == "Apache-2.0", "source license drifted")
    recorded_operations = sources.get("operations")
    require(isinstance(recorded_operations, list), "official operations must be a list")
    require(
        recorded_operations
        == [
            {
                "operationId": EXPECTED_OPERATION_ID,
                "method": method,
                "path": path,
            }
        ],
        "official_sources.json must record every selected operationId",
    )
    return contract_bytes, contract


def verify_stdlib_only() -> None:
    allowed_local = {"vcf_depot"}
    for source_path in sorted((SRC_PATH / "vcf_depot").glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            roots: list[str] = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    roots = [node.module.split(".", 1)[0]]
            for root in roots:
                require(
                    root in sys.stdlib_module_names or root in allowed_local,
                    f"non-stdlib import {root!r} in {source_path.relative_to(ROOT)}",
                )


def wait_for_mock(process: subprocess.Popen[bytes], ready_file: Path) -> str:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ready_file.exists():
            ready = json.loads(ready_file.read_text(encoding="utf-8"))
            base_url = ready.get("base_url")
            require(
                isinstance(base_url, str) and base_url.startswith("http://127.0.0.1:"),
                "mock did not bind to IPv4 loopback",
            )
            return base_url
        if process.poll() is not None:
            raise VerificationFailure(f"mock exited early with status {process.returncode}")
        time.sleep(0.02)
    raise VerificationFailure("mock did not become ready")


def header_map(items: Any) -> dict[str, list[str]]:
    require(isinstance(items, list), "request log headers are malformed")
    result: dict[str, list[str]] = {}
    for item in items:
        require(
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) for part in item),
            "request log contains a malformed header",
        )
        result.setdefault(item[0].lower(), []).append(item[1])
    return result


def verify_constructor_contract(client_type: type[Any], error_type: type[BaseException]) -> None:
    bad_constructors = [
        ("", "access-token", 1.0, 2),
        ("http://127.0.0.1", "", 1.0, 2),
        ("http://127.0.0.1", "access-token", 0.0, 2),
        ("http://127.0.0.1", "access-token", 1.0, 0),
    ]
    for base_url, access_token, timeout, max_attempts in bad_constructors:
        try:
            client_type(
                base_url,
                access_token,
                timeout=timeout,
                max_attempts=max_attempts,
            )
        except ValueError:
            pass
        else:
            raise VerificationFailure("invalid constructor input must raise ValueError")
    require(
        issubclass(error_type, RuntimeError),
        "SddcManagerError must remain a RuntimeError",
    )


def run_retry_scenario(
    contract_bytes: bytes,
    client_type: type[Any],
) -> None:
    seed = hashlib.sha256(contract_bytes + SOURCES_PATH.read_bytes()).hexdigest()
    access_token = "access-" + seed[:20]
    download_token = seed[20:48]
    expected_body = {"vmwareAccount": {"downloadToken": download_token}}
    optional_download_token = seed[8:36]
    optional_values = {
        "username": "svc-" + seed[48:56],
        "password": "pwd-" + seed[56:64],
        "download_activation_code": "activation-" + seed[:12],
    }
    expected_optional_body = {
        "vmwareAccount": {
            "downloadToken": optional_download_token,
            "username": optional_values["username"],
            "password": optional_values["password"],
            "downloadActivationCode": optional_values["download_activation_code"],
        }
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0016-") as temp_name:
        temp = Path(temp_name)
        request_log = temp / "requests.jsonl"
        ready_file = temp / "ready.json"
        process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--request-log",
                str(request_log),
                "--ready-file",
                str(ready_file),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            base_url = wait_for_mock(process, ready_file)
            client = client_type(
                base_url + "/",
                access_token,
                timeout=2.0,
                max_attempts=2,
            )
            try:
                client.update_depot_settings("")
            except ValueError:
                pass
            else:
                raise VerificationFailure("empty download_token must raise ValueError")
            for name in optional_values:
                try:
                    client.update_depot_settings(download_token, **{name: ""})
                except ValueError:
                    pass
                else:
                    raise VerificationFailure(
                        f"empty optional argument {name} must raise ValueError"
                    )
            result = client.update_depot_settings(download_token)
            optional_result = client.update_depot_settings(
                optional_download_token, **optional_values
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        require(result == expected_body, "202 response body was not returned intact")
        require(
            optional_result == expected_optional_body,
            "supplied optional fields were not returned intact",
        )
        lines = [
            line
            for line in request_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        require(len(lines) == 3, f"expected exactly three sends, observed {len(lines)}")
        entries = [json.loads(line) for line in lines]

        raw_bodies: list[bytes] = []
        for sequence, entry in enumerate(entries, start=1):
            require(entry.get("sequence") == sequence, "request sequence is not stable")
            require(
                entry.get("operationId") == EXPECTED_OPERATION_ID,
                "wrong contract operation was called",
            )
            require(entry.get("method") == "PUT", "wire method must be PUT")
            require(
                entry.get("target") == "/v1/system/settings/depot",
                "wire target must have the exact path and no query",
            )
            headers = header_map(entry.get("headers"))
            require(
                headers.get("authorization") == [f"Bearer {access_token}"],
                "Authorization header is missing or malformed",
            )
            require(
                headers.get("accept") == ["application/json"],
                "Accept header must be exactly application/json",
            )
            require(
                headers.get("content-type") == ["application/json"],
                "Content-Type header must be exactly application/json",
            )
            try:
                raw_body = bytes.fromhex(entry["body_hex"])
            except (KeyError, TypeError, ValueError) as exc:
                raise VerificationFailure("request log body is malformed") from exc
            raw_bodies.append(raw_body)
            try:
                decoded_body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VerificationFailure("request body is not UTF-8 JSON") from exc
            expected_for_send = expected_body if sequence <= 2 else expected_optional_body
            require(
                decoded_body == expected_for_send,
                "request JSON has the wrong exact shape",
            )
            require(
                entry.get("body_json") == expected_for_send,
                "mock-observed request JSON has the wrong exact shape",
            )
            if sequence <= 2:
                require(
                    set(decoded_body) == {"vmwareAccount"}
                    and set(decoded_body["vmwareAccount"]) == {"downloadToken"},
                    "unset optional fields must be omitted, not serialized empty",
                )

        require(raw_bodies[0] == raw_bodies[1], "retry must replay identical body bytes")
        require(
            [entry.get("response_status") for entry in entries] == [500, 202, 202],
            "mock did not exercise transient retry followed by a normal update",
        )
        require(
            [entry.get("effect_applied") for entry in entries] == [True, False, True],
            "retry duplicated the mutation effect",
        )
        require(
            [entry.get("effect_count") for entry in entries] == [1, 1, 2],
            "retry must not add an effect; a distinct later update must add one",
        )


def main() -> int:
    try:
        contract_bytes, _contract = verify_provenance()
        verify_stdlib_only()
        sys.path.insert(0, str(SRC_PATH))
        from vcf_depot import SddcManagerClient, SddcManagerError

        verify_constructor_contract(SddcManagerClient, SddcManagerError)
        run_retry_scenario(contract_bytes, SddcManagerClient)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        if not isinstance(exc, VerificationFailure):
            traceback.print_exc()
        return 1
    print(
        "PASS: updateDepotSettings used the exact wire contract, omitted unset "
        "fields, and applied one effect across the identical PUT retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
