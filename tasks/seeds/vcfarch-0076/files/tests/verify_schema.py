#!/usr/bin/env python3
"""Validate the migration artifact as an SddcSpec using only the stdlib.

This script is intentionally the first executable acceptance check.  It reads
no inventory, compatibility snapshot, research record, package source, or plan
schema.  The whole artifact is checked against the SddcSpec component in the
pinned vendor OpenAPI document; SddcSpec permits the migration extension fields.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = ROOT / "specifications/vcf-installer/vcf-installer-openapi.json"
PLAN_PATH = ROOT / "architecture/migration-plan.json"


class SchemaError(AssertionError):
    pass


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _pointer(root: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise SchemaError(f"external schema reference is not supported: {ref}")
    value = root
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, ValueError) as exc:
            raise SchemaError(f"unresolvable schema reference: {ref}") from exc
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise SchemaError(f"unsupported schema type {expected!r}")


def validate(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate(value, _pointer(root, schema["$ref"]), root, path)
        return

    for part in schema.get("allOf", []):
        validate(value, part, root, path)

    if "anyOf" in schema:
        if not any(_valid(value, part, root, path) for part in schema["anyOf"]):
            raise SchemaError(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        count = sum(_valid(value, part, root, path) for part in schema["oneOf"])
        if count != 1:
            raise SchemaError(f"{path}: satisfies {count} oneOf branches, expected exactly one")

    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not in {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in choices):
            raise SchemaError(f"{path}: expected type {choices!r}, got {_kind(value)}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise SchemaError(f"{path}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], root, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise SchemaError(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(item, schema["additionalProperties"], root, f"{path}.{name}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaError(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise SchemaError(f"{path}: {value!r} does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: {value} is above maximum {schema['maximum']}")


def _valid(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str) -> bool:
    try:
        validate(value, schema, root, path)
    except SchemaError:
        return False
    return True


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaError(f"required artifact is missing: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise SchemaError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def main() -> int:
    try:
        openapi = load_json(OPENAPI_PATH)
        artifact = load_json(PLAN_PATH)
        schema = openapi["components"]["schemas"]["SddcSpec"]
        validate(artifact, schema, openapi)
    except (SchemaError, KeyError) as exc:
        print(f"FAIL: installer SddcSpec validation: {exc}", file=sys.stderr)
        return 1
    print("PASS: artifact validates against installer SddcSpec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
