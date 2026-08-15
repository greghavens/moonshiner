"""A small JSON Schema/OpenAPI 3 validator implemented with the Python stdlib.

It intentionally implements only assertion keywords used by the pinned installer
schema and the seed's brownfield extension schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    """Raised when an instance violates a supported schema assertion."""


def _pointer(document: Any, fragment: str) -> Any:
    if fragment in ("", "#"):
        return document
    if fragment.startswith("#"):
        fragment = fragment[1:]
    if not fragment.startswith("/"):
        raise SchemaError(f"unsupported JSON pointer fragment: {fragment!r}")
    value = document
    for token in fragment[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(token)]
        else:
            value = value[token]
    return value


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise SchemaError(f"unsupported schema type: {expected}")


class Validator:
    """Validate JSON values with local-file and JSON-pointer reference support."""

    def __init__(self) -> None:
        self._documents: dict[Path, Any] = {}

    def load(self, path: Path) -> Any:
        path = path.resolve()
        if path not in self._documents:
            with path.open("r", encoding="utf-8") as handle:
                self._documents[path] = json.load(handle)
        return self._documents[path]

    def validate(self, value: Any, schema: dict[str, Any], document: Any, base: Path, where: str = "$") -> None:
        if "$ref" in schema:
            reference = schema["$ref"]
            filename, separator, fragment = reference.partition("#")
            if filename:
                referenced_path = (base.parent / filename).resolve()
                referenced_document = self.load(referenced_path)
                referenced_schema = _pointer(referenced_document, f"#{fragment}" if separator else "#")
                self.validate(value, referenced_schema, referenced_document, referenced_path, where)
            else:
                self.validate(value, _pointer(document, f"#{fragment}"), document, base, where)

        for subschema in schema.get("allOf", []):
            self.validate(value, subschema, document, base, where)

        if "const" in schema and value != schema["const"]:
            raise SchemaError(f"{where}: expected constant {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            raise SchemaError(f"{where}: {value!r} is not one of {schema['enum']!r}")

        expected = schema.get("type")
        if expected is not None:
            expected_types = [expected] if isinstance(expected, str) else expected
            if not any(_matches_type(value, item) for item in expected_types):
                raise SchemaError(f"{where}: expected type {expected!r}, got {type(value).__name__}")

        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [key for key in required if key not in value]
            if missing:
                raise SchemaError(f"{where}: missing required properties {missing!r}")
            properties = schema.get("properties", {})
            for key, subschema in properties.items():
                if key in value:
                    self.validate(value[key], subschema, document, base, f"{where}.{key}")
            if schema.get("additionalProperties") is False:
                extras = sorted(set(value) - set(properties))
                if extras:
                    raise SchemaError(f"{where}: unexpected properties {extras!r}")
            if "minProperties" in schema and len(value) < schema["minProperties"]:
                raise SchemaError(f"{where}: has fewer than {schema['minProperties']} properties")

        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise SchemaError(f"{where}: has fewer than {schema['minItems']} items")
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                raise SchemaError(f"{where}: has more than {schema['maxItems']} items")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
                if len(encoded) != len(set(encoded)):
                    raise SchemaError(f"{where}: array items are not unique")
            items = schema.get("items")
            if isinstance(items, dict):
                for index, item in enumerate(value):
                    self.validate(item, items, document, base, f"{where}[{index}]")

        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise SchemaError(f"{where}: string is shorter than {schema['minLength']}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise SchemaError(f"{where}: string is longer than {schema['maxLength']}")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                raise SchemaError(f"{where}: value does not match pattern {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise SchemaError(f"{where}: value is below minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                raise SchemaError(f"{where}: value is above maximum {schema['maximum']}")


def validate_file(instance: Any, schema_path: Path, schema_pointer: str = "#") -> None:
    """Validate *instance* against a schema document and optional pointer."""

    validator = Validator()
    document = validator.load(schema_path)
    schema = _pointer(document, schema_pointer)
    validator.validate(instance, schema, document, schema_path.resolve())
