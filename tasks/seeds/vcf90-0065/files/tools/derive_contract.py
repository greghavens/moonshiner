#!/usr/bin/env python3
"""Reproduce docs/contract.json from a local copy of the pinned OpenAPI file.

The upstream specification is intentionally not vendored. Pass a local copy of

    specifications/vcf-operations/vcf-operations-openapi.json

taken from tag 9.0.0.0 (commit 85151f6b1bb58f13b6ac0304bfec53904bea085f) of
https://github.com/vmware/vcf-api-specs as the only argument. This script reads
that file and nothing else; it never fetches a documentation page or the
network.

    python3 tools/derive_contract.py /path/to/vcf-operations-openapi.json

The 9.0.0.0 revision of this file carries an empty ``info.version``; the 9.1.0.0
revision of the same path sets it to "9.1.0.0" and adds the
``exchangeOpsTokenWithJwtToken`` operation. Both facts are asserted here so that
pointing this tool at the wrong revision fails loudly instead of silently
producing a 9.1 contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_TAG = "9.0.0.0"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"

# Operations the module drives, keyed by operationId.
OPERATIONS = {
    "acquireToken": ("post", "/api/auth/token/acquire"),
    "releaseToken": ("post", "/api/auth/token/release"),
    "getCurrentVersionOfServer": ("get", "/api/versions/current"),
    "createAlertPlugin": ("post", "/api/alertplugins"),
    "updateAlertPlugin": ("put", "/api/alertplugins"),
    "createNotificationPluginRule": ("post", "/api/notifications/rules"),
}

# Present only in the 9.1.0.0 revision of the same file.
ABSENT_IN_9_0 = ("exchangeOpsTokenWithJwtToken",)

SCHEMAS = (
    "username-password",
    "auth-token",
    "version",
    "notification-plugin",
    "notification-rule",
    "name-value",
    "certificate",
    "str-values",
)


def local_ref(value: str) -> str:
    return value.removeprefix("#/components/schemas/")


def simplify(value: object) -> object:
    """Keep the wire-relevant parts of a schema fragment, drop the prose."""
    if isinstance(value, list):
        return [simplify(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, object] = {}
    for key, item in value.items():
        if key in ("description", "example", "xml", "externalDocs", "title"):
            continue
        if key == "$ref" and isinstance(item, str):
            result["$ref"] = local_ref(item)
            continue
        result[key] = simplify(item)
    return result


def simplify_schema(spec: dict, name: str) -> dict:
    raw = spec["components"]["schemas"][name]
    properties = {
        key: simplify(value) for key, value in (raw.get("properties") or {}).items()
    }
    return {
        "required": sorted(raw.get("required") or []),
        "properties": properties,
        "optional": sorted(set(properties) - set(raw.get("required") or [])),
    }


def build_operation(spec: dict, operation_id: str, method: str, path: str) -> dict:
    item = spec["paths"][path][method]
    if item.get("operationId") != operation_id:
        raise SystemExit(f"{method.upper()} {path} is not {operation_id}")

    entry: dict[str, object] = {
        "method": method.upper(),
        "path": path,
        "jsonPointer": "#/paths/" + path.replace("~", "~0").replace("/", "~1") + f"/{method}",
    }

    body = item.get("requestBody")
    if body is not None:
        schema = body["content"]["application/json"]["schema"]
        entry["requestBody"] = {
            "required": bool(body.get("required")),
            "contentType": "application/json",
            "schema": local_ref(schema["$ref"]),
        }

    responses: dict[str, object] = {}
    for status, response in sorted(item.get("responses", {}).items()):
        content = (response.get("content") or {}).get("application/json")
        responses[status] = {
            "contentType": "application/json" if content else None,
            "schema": local_ref(content["schema"]["$ref"]) if content else None,
        }
    entry["responses"] = responses
    return entry


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))

    info_version = spec.get("info", {}).get("version", "")
    if info_version:
        raise SystemExit(
            f"info.version is {info_version!r}; the 9.0.0.0 revision leaves it empty. "
            "This looks like a different revision of the specification."
        )

    present = {
        operation.get("operationId")
        for path_item in spec["paths"].values()
        for key, operation in path_item.items()
        if key in ("get", "put", "post", "delete", "patch")
    }
    for operation_id in ABSENT_IN_9_0:
        if operation_id in present:
            raise SystemExit(
                f"{operation_id} is present; it only exists from 9.1.0.0 onwards."
            )

    servers = spec.get("servers") or []
    base_path = servers[0]["url"] if servers else ""

    contract = {
        "contractVersion": 1,
        "source": {
            "repository": "https://github.com/vmware/vcf-api-specs",
            "commit": COMMIT,
            "tag": SPEC_TAG,
            "path": SPEC_PATH,
            "openapi": spec["openapi"],
            "apiVersion": SPEC_TAG,
            "specInfoVersion": info_version,
            "basePath": base_path,
        },
        "operations": {
            operation_id: build_operation(spec, operation_id, method, path)
            for operation_id, (method, path) in OPERATIONS.items()
        },
        "schemas": {name: simplify_schema(spec, name) for name in SCHEMAS},
    }

    output = Path(__file__).resolve().parents[1] / "docs" / "contract.json"
    output.write_text(json.dumps(contract, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
