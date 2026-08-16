"""Strict JSON helpers for durable local contracts."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def json_object(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    _require_string_keys(value, name=name)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain JSON-compatible values") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")
    return decoded


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain JSON-compatible values") from exc


def _require_string_keys(value: object, *, name: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} must use string keys")
            _require_string_keys(child, name=name)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _require_string_keys(child, name=name)
