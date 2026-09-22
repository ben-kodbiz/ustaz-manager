"""Minimal JSON-Schema-subset validator (Python stdlib only).

Supports the keywords used by schemas/*.schema.json:
type, required, properties, items, enum, minLength, minItems,
minimum, pattern, any_array_present (custom: at least one named
array property must be a non-empty array).
"""

from __future__ import annotations

import re
from typing import Any

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


class SchemaValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _type_ok(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPE_MAP.get(type_name)
    if expected is None:
        return True
    if type_name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected)


def _validate(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        return

    type_name = schema.get("type")
    if type_name is not None and not _type_ok(value, type_name):
        errors.append(f"{path}: expected {type_name}, got {type(value).__name__}")
        return

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        errors.append(f"{path}: must be one of {allowed}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            errors.append(f"{path}: must be at least {min_length} character(s)")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            errors.append(f"{path}: does not match pattern {pattern}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{path}: must be >= {minimum}")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            errors.append(f"{path}: must contain at least {min_items} item(s)")
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for i, item in enumerate(value):
                _validate(item, items_schema, f"{path}[{i}]", errors)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                parent = path if path != "." else ""
                errors.append(f"{parent + '.' if parent else ''}{key} is required")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub_schema in properties.items():
                if key in value and isinstance(sub_schema, dict):
                    child = f"{path}.{key}" if path != "." else key
                    _validate(value[key], sub_schema, child, errors)
        any_arrays = schema.get("any_array_present")
        if isinstance(any_arrays, list):
            if not any(
                isinstance(value.get(name), list) and len(value[name]) > 0
                for name in any_arrays
            ):
                names = " or ".join(f"{path}.{n}" for n in any_arrays)
                errors.append(f"{names}: at least one non-empty array is required")


def validate(instance: Any, schema: dict) -> list[str]:
    """Return a list of error strings; empty list means valid."""
    errors: list[str] = []
    _validate(instance, schema, ".", errors)
    return errors


def validate_or_raise(instance: Any, schema: dict, label: str) -> None:
    errors = validate(instance, schema)
    if errors:
        detail = "\n".join(errors)
        raise SchemaValidationError([f"ERROR:\n{label}\n\n{detail}"])
