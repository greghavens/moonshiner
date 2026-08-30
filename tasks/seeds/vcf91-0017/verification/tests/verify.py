#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 stdlib REST integration task."""

from __future__ import annotations

import ast
import json
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mock_sddc_manager import MockSddcManager  # noqa: E402
from vcf91_change import apply_landing_zone_change  # noqa: E402


PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    {
        "operationId": "createToken",
        "method": "POST",
        "path": "/v1/tokens",
    },
    {
        "operationId": "updateSystemConfiguration",
        "method": "PATCH",
        "path": "/v1/system",
    },
    {
        "operationId": "updateProxyConfiguration",
        "method": "PATCH",
        "path": "/v1/system/proxy-configuration",
    },
]


def fail(message: str) -> None:
    raise AssertionError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"{path.relative_to(ROOT)} is not valid UTF-8 JSON: {error}")


def assert_contract_and_sources() -> None:
    contract = load_json(ROOT / "docs/contract.json")
    sources = load_json(ROOT / "docs/official_sources.json")

    expected_source = {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "repository_commit_sha": PINNED_COMMIT,
        "spec_path": SPEC_PATH,
    }
    if contract.get("openapi") != "3.0.1":
        fail("contract must identify OpenAPI 3.0.1")
    if contract.get("spec_version") != "9.1.0.0":
        fail("contract must identify specification version 9.1.0.0")
    if contract.get("source") != expected_source:
        fail("contract source does not match the commit-pinned official source")

    if sources.get("repository") != expected_source["repository"]:
        fail("official source repository changed")
    if sources.get("license") != "Apache-2.0":
        fail("official source must record the Apache-2.0 repository license")
    if sources.get("repository_commit_sha") != PINNED_COMMIT:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official specification path changed")
    source_url = sources.get("source_url", "")
    if PINNED_COMMIT not in source_url or not source_url.endswith(SPEC_PATH):
        fail("official source URL is not pinned to the recorded commit and path")

    contract_operations = [
        {
            "operationId": item.get("operationId"),
            "method": item.get("method"),
            "path": item.get("path"),
        }
        for item in contract.get("operations", [])
    ]
    source_operations = [
        {
            "operationId": item.get("operationId"),
            "method": item.get("method"),
            "path": item.get("path"),
        }
        for item in sources.get("operationIds", [])
    ]
    if contract_operations != EXPECTED_OPERATIONS:
        fail("contract operation set or order changed")
    if source_operations != EXPECTED_OPERATIONS:
        fail("official_sources.json does not name every scoped operationId")

    schemas = contract.get("schemas", {})
    token_fields = set(
        schemas.get("TokenCreationSpec", {}).get("properties", {})
    )
    if token_fields != {"username", "password", "apiKey", "idToken"}:
        fail("TokenCreationSpec projection differs from the pinned specification")
    system_schema = schemas.get("SystemUpdateSpec", {})
    if system_schema.get("required") != ["maxAllowedDomainsInSubscription"]:
        fail("SystemUpdateSpec required member differs from the pinned specification")
    proxy_fields = schemas.get("ProxyConfiguration", {}).get("properties", {})
    if set(proxy_fields) != {
        "isConfigured",
        "isEnabled",
        "host",
        "port",
        "transferProtocol",
        "username",
        "password",
        "isAuthenticated",
    }:
        fail("ProxyConfiguration projection differs from the pinned specification")
    if proxy_fields["isConfigured"].get("readOnly") is not True:
        fail("ProxyConfiguration.isConfigured must remain read-only")


def assert_stdlib_package() -> None:
    package = ROOT / "vcf91_change"
    if not package.is_dir():
        fail("vcf91_change package is missing")

    forbidden_stdlib_transports = {"socket", "subprocess"}
    for source_path in package.glob("*.py"):
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError) as error:
            fail(f"{source_path.relative_to(ROOT)} cannot be parsed: {error}")

        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    names = [node.module.split(".", 1)[0]]
            for name in names:
                if name not in sys.stdlib_module_names:
                    fail(
                        f"{source_path.relative_to(ROOT)} imports non-stdlib "
                        f"module {name!r}"
                    )
                if name in forbidden_stdlib_transports:
                    fail(
                        f"{source_path.relative_to(ROOT)} uses forbidden "
                        f"transport module {name!r}"
                    )

    forbidden_suffixes = {".whl", ".zip", ".egg", ".nupkg"}
    vendored = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes
    ]
    if vendored:
        fail(f"third-party artifacts must not be vendored: {vendored}")


def parse_body(request: dict[str, Any], label: str) -> dict[str, Any]:
    content_type = request["headers"].get("content-type")
    if content_type != "application/json":
        fail(f"{label} Content-Type must be exactly application/json")
    try:
        body = json.loads(request["body"])
    except json.JSONDecodeError as error:
        fail(f"{label} body is not JSON: {error}")
    if not isinstance(body, dict):
        fail(f"{label} body must be a JSON object")

    declared_length = request["headers"].get("content-length")
    if declared_length != str(len(request["body"].encode("utf-8"))):
        fail(f"{label} Content-Length does not match its UTF-8 body")
    return body


def assert_no_unset_values(value: Any, path: str = "$") -> None:
    if value is None or value == "":
        fail(f"request serialized an unset value at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_no_unset_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_unset_values(child, f"{path}[{index}]")


def read_requests(log_path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        fail(f"mock request log is unreadable: {error}")


def assert_wire(
    requests: list[dict[str, Any]],
    *,
    username: str,
    password: str,
    access_token: str,
    limit: int,
    proxy_host: str,
    proxy_port: int,
) -> None:
    if len(requests) != 3:
        fail(f"expected exactly three requests, observed {len(requests)}")

    actual_sequence = [
        {"method": request["method"], "path": request["target"]}
        for request in requests
    ]
    expected_sequence = [
        {"method": operation["method"], "path": operation["path"]}
        for operation in EXPECTED_OPERATIONS
    ]
    if actual_sequence != expected_sequence:
        fail(f"REST operation sequence differs: {actual_sequence!r}")

    token_body = parse_body(requests[0], "createToken")
    if token_body != {"username": username, "password": password}:
        fail("createToken body has the wrong values or member set")
    if "authorization" in requests[0]["headers"]:
        fail("createToken must not send Authorization")

    system_body = parse_body(requests[1], "updateSystemConfiguration")
    if system_body != {"maxAllowedDomainsInSubscription": limit}:
        fail("updateSystemConfiguration body has the wrong value or member set")

    proxy_body = parse_body(requests[2], "updateProxyConfiguration")
    if proxy_body != {
        "isEnabled": True,
        "host": proxy_host,
        "port": proxy_port,
    }:
        fail("updateProxyConfiguration body has the wrong values or member set")

    for field in ("apiKey", "idToken"):
        if field in token_body:
            fail(f"unset token field was serialized: {field}")
    for field in (
        "isConfigured",
        "transferProtocol",
        "username",
        "password",
        "isAuthenticated",
    ):
        if field in proxy_body:
            fail(f"unset proxy field was serialized: {field}")

    for body in (token_body, system_body, proxy_body):
        assert_no_unset_values(body)

    for request in requests[1:]:
        if request["headers"].get("accept") != "application/json":
            fail(f"{request['target']} must send Accept: application/json")
        if request["headers"].get("authorization") != f"Bearer {access_token}":
            fail(f"{request['target']} has the wrong bearer authorization")


def expected_partial_report() -> dict[str, Any]:
    return {
        "status": "partial_failure",
        "succeeded": 1,
        "failed": 1,
        "steps": [
            {
                "name": "subscription-domain-limit",
                "operationId": "updateSystemConfiguration",
                "status": "succeeded",
            },
            {
                "name": "outbound-proxy",
                "operationId": "updateProxyConfiguration",
                "status": "failed",
            },
        ],
    }


def assert_report(
    report_path: Path,
    returned: dict[str, Any],
    *,
    secrets_to_reject: tuple[str, ...],
) -> None:
    expected = expected_partial_report()
    if returned != expected:
        fail(f"returned partial-failure report differs: {returned!r}")

    try:
        raw = report_path.read_bytes()
    except OSError as error:
        fail(f"report was not written: {error}")
    if raw.startswith(b"\xef\xbb\xbf"):
        fail("report must be UTF-8 without a BOM")
    if b"\r" in raw:
        fail("report must use LF line endings")
    if not raw.endswith(b"\n"):
        fail("report must end with LF")
    try:
        text = raw.decode("utf-8")
        stored = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"report is not valid UTF-8 JSON: {error}")
    if stored != expected:
        fail(f"stored partial-failure report differs: {stored!r}")
    for secret in secrets_to_reject:
        if secret in text:
            fail("report leaked a credential, token, or response value")


def exercise() -> None:
    nonce = secrets.token_hex(8)
    username = f"svc-{nonce}@vsphere.local"
    password = f"pw-{secrets.token_urlsafe(14)}"
    access_token = f"access-{secrets.token_urlsafe(18)}"
    refresh_token = f"refresh-{secrets.token_urlsafe(18)}"
    limit = 7 + secrets.randbelow(80)
    proxy_host = f"proxy-{nonce}.corp.example"
    proxy_port = 12000 + secrets.randbelow(30000)
    plan = {
        "maxAllowedDomainsInSubscription": limit,
        "proxy": {
            "isEnabled": True,
            "host": proxy_host,
            "port": proxy_port,
            "transferProtocol": None,
            "username": None,
            "password": "",
            "isAuthenticated": None,
            "isConfigured": False,
        },
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0017-") as temp_dir:
        temp = Path(temp_dir)
        log_path = temp / "requests.jsonl"
        report_path = temp / "nested" / "change-report.json"
        log_path.touch()

        with MockSddcManager(
            ROOT / "docs/contract.json",
            log_path,
            access_token=access_token,
            refresh_token=refresh_token,
        ) as mock:
            try:
                returned = apply_landing_zone_change(
                    mock.service_root,
                    username,
                    password,
                    plan,
                    report_path,
                    timeout=2.0,
                )
            except Exception as error:  # noqa: BLE001
                fail(
                    "acceptance scenario raised instead of returning its report "
                    f"({type(error).__name__})"
                )

        requests = read_requests(log_path)
        assert_wire(
            requests,
            username=username,
            password=password,
            access_token=access_token,
            limit=limit,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
        )
        assert_report(
            report_path,
            returned,
            secrets_to_reject=(
                username,
                password,
                access_token,
                refresh_token,
            ),
        )


def main() -> int:
    try:
        assert_contract_and_sources()
        assert_stdlib_package()
        exercise()
    except (AssertionError, OSError) as error:
        print(f"VERIFICATION FAILED: {error}", file=sys.stderr)
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
