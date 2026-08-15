#!/usr/bin/env python3
"""Validate one JSON instance with the self-contained installer schema subset."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    pass


def fail(path: str, message: str) -> None:
    raise SchemaError(f"{path}: {message}")


def resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise SchemaError(f"unsupported non-local $ref: {reference}")
    value: Any = root
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or token not in value:
            raise SchemaError(f"unresolvable $ref: {reference}")
        value = value[token]
    if not isinstance(value, dict):
        raise SchemaError(f"$ref does not select an object: {reference}")
    return value


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate(instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        validate(instance, resolve_ref(root, schema["$ref"]), root, path)
        return

    if "const" in schema and instance != schema["const"]:
        fail(path, f"must equal {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail(path, f"must be one of {schema['enum']!r}")

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            fail(path, "must be an object")
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            fail(path, f"missing required properties {missing!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(instance) - set(properties))
            if extras:
                fail(path, f"unexpected properties {extras!r}")
        for name, value in instance.items():
            if name in properties:
                validate(value, properties[name], root, f"{path}.{name}")
        return

    if expected == "array":
        if not isinstance(instance, list):
            fail(path, "must be an array")
        if len(instance) < schema.get("minItems", 0):
            fail(path, f"must contain at least {schema['minItems']} items")
        if schema.get("uniqueItems"):
            rendered = [canonical(item) for item in instance]
            if len(rendered) != len(set(rendered)):
                fail(path, "must contain unique items")
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(instance):
                validate(value, item_schema, root, f"{path}[{index}]")
        return

    if expected == "string":
        if not isinstance(instance, str):
            fail(path, "must be a string")
        if len(instance) < schema.get("minLength", 0):
            fail(path, f"must contain at least {schema['minLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            fail(path, f"does not match {schema['pattern']!r}")
    elif expected == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            fail(path, "must be an integer")
    elif expected == "number":
        if not is_number(instance):
            fail(path, "must be a finite number")
    elif expected == "boolean":
        if not isinstance(instance, bool):
            fail(path, "must be a boolean")

    if expected in {"integer", "number"}:
        if "minimum" in schema and instance < schema["minimum"]:
            fail(path, f"must be >= {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            fail(path, f"must be <= {schema['maximum']}")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: verify_schema.py SCHEMA INSTANCE", file=sys.stderr)
        return 2
    try:
        schema = json.loads(Path(argv[1]).read_text())
        instance = json.loads(Path(argv[2]).read_text())
        if not isinstance(schema, dict):
            raise SchemaError("schema root must be an object")
        validate(instance, schema, schema)
    except (OSError, json.JSONDecodeError, SchemaError) as error:
        print(f"schema validation failed: {error}", file=sys.stderr)
        return 1
    print(f"schema validation passed: {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
