"""Small, dependency-free structured-output contract for OMD AI adapters."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field


_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_KEYS = frozenset({"type", "properties", "required", "additionalProperties", "items", "enum"})
_MAX_SCHEMA_BYTES = 64 * 1024
_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_DEPTH = 8


class StructuredOutputError(ValueError):
    """Raised when a provider result does not satisfy the frozen output schema."""


@dataclass(frozen=True, init=False)
class AIOutputSchema:
    name: str
    _schema_json: str = field(repr=False)

    def __init__(self, *, name: str, schema: Mapping[str, object]) -> None:
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise ValueError("schema name must use 1-64 letters, digits, underscores, or hyphens")
        if not isinstance(schema, Mapping):
            raise TypeError("schema must be a mapping")
        try:
            schema_json = json.dumps(
                dict(schema),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            normalized = json.loads(schema_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("schema must contain JSON-compatible values") from exc
        if len(schema_json.encode("utf-8")) > _MAX_SCHEMA_BYTES:
            raise ValueError("schema is too large")
        _validate_schema(normalized, path="$", depth=0)
        if normalized.get("type") != "object":
            raise ValueError("structured output must use an object schema")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "_schema_json", schema_json)

    @property
    def schema(self) -> dict[str, object]:
        return json.loads(self._schema_json)


def parse_structured_output(text: str, contract: AIOutputSchema) -> dict[str, object]:
    if not isinstance(text, str):
        raise TypeError("structured output text must be a string")
    if not isinstance(contract, AIOutputSchema):
        raise TypeError("contract must be an AIOutputSchema")
    if len(text.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise StructuredOutputError("structured output is too large")
    try:
        value = json.loads(
            text,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise StructuredOutputError("structured output is not valid JSON") from exc
    _validate_value(value, contract.schema, path="$", depth=0)
    if not isinstance(value, dict):
        raise StructuredOutputError("structured output must be an object")
    return value


def _validate_schema(schema: object, *, path: str, depth: int) -> None:
    if depth > _MAX_DEPTH or not isinstance(schema, dict):
        raise ValueError(f"unsupported schema at {path}")
    unsupported = set(schema) - _KEYS
    if unsupported:
        raise ValueError(f"unsupported schema keyword at {path}: {sorted(unsupported)[0]}")
    kind = schema.get("type")
    if kind not in _TYPES:
        raise ValueError(f"unsupported schema type at {path}")
    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum or any(
            isinstance(item, (dict, list)) for item in enum
        ):
            raise ValueError(f"unsupported enum at {path}")
    if kind == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError(f"object schema requires properties and required at {path}")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"object schema must disable additional properties at {path}")
        if any(not isinstance(name, str) or not _PROPERTY_RE.fullmatch(name) for name in properties):
            raise ValueError(f"unsupported property name at {path}")
        if any(not isinstance(name, str) for name in required) or set(required) != set(properties):
            raise ValueError(f"all object properties must be required at {path}")
        for name, child in properties.items():
            _validate_schema(child, path=f"{path}.{name}", depth=depth + 1)
    elif kind == "array":
        if "items" not in schema:
            raise ValueError(f"array schema requires items at {path}")
        _validate_schema(schema["items"], path=f"{path}[]", depth=depth + 1)
    elif set(schema) - {"type", "enum"}:
        raise ValueError(f"unsupported schema keyword for {kind} at {path}")


def _validate_value(value: object, schema: dict[str, object], *, path: str, depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise StructuredOutputError(f"structured output exceeds maximum depth at {path}")
    kind = schema["type"]
    if not _matches_type(value, kind):
        raise StructuredOutputError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise StructuredOutputError(f"{path} is not an allowed enum value")
    if kind == "object":
        assert isinstance(value, dict)
        properties = schema["properties"]
        required = schema["required"]
        missing = [name for name in required if name not in value]
        if missing:
            raise StructuredOutputError(f"{path} is missing required property {missing[0]}")
        extra = set(value) - set(properties)
        if extra:
            raise StructuredOutputError(f"{path} contains additional property {sorted(extra)[0]}")
        for name, child_schema in properties.items():
            _validate_value(value[name], child_schema, path=f"{path}.{name}", depth=depth + 1)
    elif kind == "array":
        assert isinstance(value, list)
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], path=f"{path}[{index}]", depth=depth + 1)


def _matches_type(value: object, kind: object) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if kind == "boolean":
        return isinstance(value, bool)
    return value is None
