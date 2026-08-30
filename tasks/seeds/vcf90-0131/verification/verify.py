from __future__ import annotations

import ast
import inspect
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from mock_vcf_networks import MockVcfOperationsForNetworks


ROOT = Path(__file__).resolve().parent
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_OPERATIONS = {
    "updateCertificate": ("PUT", "/settings/certificates/{id}"),
    "fetchCertificateUpdateStatusForUpdateId": (
        "GET",
        "/settings/certificates/status/{id}",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def header(entry: dict[str, object], name: str) -> str | None:
    headers = entry["headers"]
    require(isinstance(headers, dict), "mock log did not capture headers")
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return None


def require_encoded_segment(path: object, prefix: str, decoded: str) -> None:
    parsed = urlsplit(str(path))
    encoded = parsed.path[len(prefix) :] if parsed.path.startswith(prefix) else ""
    require(
        not parsed.query
        and bool(encoded)
        and "/" not in encoded
        and unquote(encoded) == decoded,
        f"{decoded!r} was not encoded as one path segment",
    )


def verify_provenance_and_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8")
    )
    require(sources["spec_path"] == EXPECTED_SPEC_PATH, "official spec path drifted")
    require(sources["commit_sha"] == EXPECTED_SHA, "official revision SHA drifted")
    require(sources["tag"] == "9.0.0.0", "official tag drifted")
    require(sources["license"] == "Apache-2.0", "official license drifted")
    source_ops = {
        item["operationId"]: (item["method"], item["path"])
        for item in sources["operations"]
    }
    require(source_ops == EXPECTED_OPERATIONS, "official operation provenance drifted")
    contract_ops = {
        operation_id: (item["method"], item["path"])
        for operation_id, item in contract["operations"].items()
    }
    require(contract_ops == EXPECTED_OPERATIONS, "derived contract operation set drifted")
    require(
        all(
            item["operationId"] == operation_id
            for operation_id, item in contract["operations"].items()
        ),
        "derived contract must name each exact operationId",
    )
    require(contract["server_url"] == "/api/ni", "OpenAPI server URL drifted")
    require(
        contract["authentication"] == {
            "scheme": "ApiKeyAuth",
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "API Key - NetworkInsight {token}",
        },
        "authentication contract drifted",
    )
    status_enum = contract["schemas"]["CertificateUpdateStatus"]["properties"][
        "status"
    ]["enum"]
    require(
        status_enum == ["SUBMITTED", "IN_PROGRESS", "SUCCESS", "FAILED"],
        "certificate status enum drifted",
    )
    request_properties = contract["schemas"]["CertificateUpdateRequest"]["properties"]
    require(
        set(request_properties) == {"certificate", "private_key", "chain"},
        "certificate request fields drifted",
    )
    require(
        MockVcfOperationsForNetworks.operation_ids == frozenset(EXPECTED_OPERATIONS),
        "mock serves an operation outside the pinned contract",
    )


def import_client():
    try:
        from vcf_operations_networks import OperationsForNetworksClient
    except ImportError as exc:
        raise AssertionError(
            "vcf_operations_networks must export OperationsForNetworksClient"
        ) from exc
    return OperationsForNetworksClient


def verify_stdlib_only() -> None:
    package = ROOT / "vcf_operations_networks"
    require(package.is_dir(), "vcf_operations_networks package is missing")
    python_files = sorted(package.rglob("*.py"))
    require(bool(python_files), "vcf_operations_networks contains no Python modules")
    local_roots = {"vcf_operations_networks"}
    stdlib = set(sys.stdlib_module_names)
    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                roots = {str(node.module).split(".", 1)[0]}
            else:
                continue
            require(
                roots <= stdlib | local_roots,
                f"third-party import in {path.relative_to(ROOT)}: {sorted(roots - stdlib - local_roots)}",
            )


def verify_public_api() -> None:
    OperationsForNetworksClient = import_client()

    constructor = inspect.signature(OperationsForNetworksClient).parameters
    require(
        "base_url" in constructor and "token" in constructor,
        "constructor must accept base_url and token",
    )
    timeout = constructor.get("timeout")
    require(
        timeout is not None
        and timeout.kind is inspect.Parameter.KEYWORD_ONLY
        and timeout.default == 10.0,
        "constructor timeout must be keyword-only and default to 10.0",
    )

    method = inspect.signature(
        OperationsForNetworksClient.update_certificate_and_wait
    ).parameters
    require(
        all(
            name in method
            for name in ("certificate_id", "certificate", "private_key")
        ),
        "update_certificate_and_wait is missing a required certificate argument",
    )
    chain = method.get("chain")
    poll_interval = method.get("poll_interval")
    require(
        chain is not None
        and chain.kind is inspect.Parameter.KEYWORD_ONLY
        and chain.default is None,
        "chain must be keyword-only and default to None",
    )
    require(
        poll_interval is not None
        and poll_interval.kind is inspect.Parameter.KEYWORD_ONLY
        and poll_interval.default == 0.0,
        "poll_interval must be keyword-only and default to 0.0",
    )


def verify_wire(
    terminal_status: str,
    chain: str | None,
    *,
    trailing_slash: bool,
) -> None:
    OperationsForNetworksClient = import_client()
    with MockVcfOperationsForNetworks(terminal_status=terminal_status) as mock:
        base_url = mock.base_url + "/" if trailing_slash else mock.base_url
        client = OperationsForNetworksClient(base_url, "fixture-token", timeout=2.0)
        result = client.update_certificate_and_wait(
            "proxy register/client.crt",
            "leaf-certificate",
            "private-key",
            chain=chain,
            poll_interval=0.0,
        )

        expected_result = {
            "id": "update 73/9",
            "name": "proxy register/client.crt",
            "status": terminal_status,
        }
        if terminal_status == "FAILED":
            expected_result["error_message"] = "certificate update failed"
        require(
            result == expected_result,
            "decoded terminal status object was not returned intact",
        )
        log = list(mock.request_log)

    require(len(log) == 3, "workflow must submit once and poll through the terminal state")
    require(
        [entry["method"] for entry in log] == ["PUT", "GET", "GET"],
        "wrong operation or polling sequence",
    )
    require_encoded_segment(
        log[0]["path"],
        "/api/ni/settings/certificates/",
        "proxy register/client.crt",
    )
    for entry in log[1:]:
        require_encoded_segment(
            entry["path"],
            "/api/ni/settings/certificates/status/",
            "update 73/9",
        )
    for entry in log:
        require(
            header(entry, "Authorization") == "NetworkInsight fixture-token",
            "wrong NetworkInsight authorization wire value",
        )
    content_type = header(log[0], "Content-Type")
    require(
        content_type is not None
        and content_type.split(";", 1)[0].strip().lower() == "application/json",
        "updateCertificate must send the contract JSON media type",
    )
    require(log[1]["body"] == b"" and log[2]["body"] == b"", "GET polls sent a body")
    body = log[0]["body"]
    require(isinstance(body, bytes), "mock did not capture raw request bytes")
    decoded = json.loads(body.decode("utf-8"))
    expected = {
        "certificate": "leaf-certificate",
        "private_key": "private-key",
    }
    if chain is not None:
        expected["chain"] = chain
    require(decoded == expected, "updateCertificate JSON wire object is not exact")
    if chain is None:
        require("chain" not in decoded, "unset optional chain must be omitted")


def main() -> None:
    verify_provenance_and_contract()
    verify_stdlib_only()
    verify_public_api()
    verify_wire("SUCCESS", None, trailing_slash=False)
    verify_wire("FAILED", "trusted-chain", trailing_slash=True)
    print("verification passed")


if __name__ == "__main__":
    main()
