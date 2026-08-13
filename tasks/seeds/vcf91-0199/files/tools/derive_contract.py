#!/usr/bin/env python3
"""Reproduce docs/contract.json from a local copy of the pinned OpenAPI file.

The full upstream specification is intentionally not vendored. Pass it as the
only argument. This script never fetches a documentation page or the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
OPERATIONS = {
    "createToken": ("post", "/v1/tokens"),
    "getTasks": ("get", "/v1/tasks"),
}
SCHEMAS = (
    "TokenCreationSpec",
    "RefreshToken",
    "TokenPair",
    "PageMetadata",
    "PageOfTask",
    "Task",
)
SCHEMA_PROPERTIES = {
    "TokenCreationSpec": ("username", "password", "apiKey", "idToken"),
    "RefreshToken": ("id",),
    "TokenPair": ("accessToken", "refreshToken"),
    "PageMetadata": ("pageNumber", "pageSize", "totalElements", "totalPages"),
    "PageOfTask": ("elements", "pageMetadata"),
    "Task": (
        "id", "name", "type", "status", "creationTimestamp",
        "completionTimestamp", "isCancellable", "isRetryable",
    ),
}


def local_ref(value: str) -> str:
    return value.removeprefix("#/components/schemas/")


def simplify_schema(value: object) -> object:
    if isinstance(value, list):
        return [simplify_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    kept: dict[str, object] = {}
    for key in ("type", "format", "default", "required", "readOnly", "properties", "items", "$ref"):
        if key not in value:
            continue
        item = value[key]
        kept[key] = local_ref(item) if key == "$ref" else simplify_schema(item)
    return kept


def operation_contract(operation: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    if operation["operationId"] != "createToken":
        parameters = []
        for parameter in operation.get("parameters", []):
            schema = parameter.get("schema", {})
            record = {
                "name": parameter["name"],
                "in": parameter["in"],
                "required": parameter.get("required", False),
                **simplify_schema(schema),
            }
            parameters.append(record)
        result["parameters"] = parameters
    if "requestBody" in operation:
        media = operation["requestBody"]["content"]["application/json"]
        result["requestBody"] = {
            "required": operation["requestBody"].get("required", False),
            "contentType": "application/json",
            "schema": local_ref(media["schema"]["$ref"]),
        }
    success_code = "201" if operation["operationId"] == "createToken" else "200"
    success = operation["responses"][success_code]["content"]["application/json"]
    result["success"] = {
        "status": int(success_code),
        "contentType": "application/json",
        "schema": local_ref(success["schema"]["$ref"]),
    }
    return result


def selected_schema(name: str, source: dict[str, object]) -> dict[str, object]:
    result = simplify_schema(source)
    properties = result.get("properties", {})
    result["properties"] = {
        key: properties[key] for key in SCHEMA_PROPERTIES[name]
    }
    return result


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: derive_contract.py PATH_TO_vcf-installer-openapi.json")
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    operations: dict[str, object] = {}
    for operation_id, (method, path) in OPERATIONS.items():
        source = spec["paths"][path][method]
        if source["operationId"] != operation_id:
            raise ValueError(f"operationId mismatch at {method.upper()} {path}")
        operations[operation_id] = {
            "method": method.upper(),
            "path": path,
            **operation_contract(source),
        }
    contract = {
        "contractVersion": 1,
        "source": {
            "repository": "https://github.com/vmware/vcf-api-specs",
            "commit": COMMIT,
            "path": SPEC_PATH,
            "openapi": spec["openapi"],
            "apiVersion": spec["info"]["version"],
        },
        "operations": operations,
        "schemas": {
            name: selected_schema(name, spec["components"]["schemas"][name])
            for name in SCHEMAS
        },
    }
    print(json.dumps(contract, indent=2) + "\n", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
