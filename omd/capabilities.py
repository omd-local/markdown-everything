"""Static machine-readable OMD feature capabilities."""
from __future__ import annotations

import json


def capabilities_payload() -> dict[str, object]:
    return {"enrich_note": {"supported": True, "schema_versions": [1]}}


def capabilities_json() -> str:
    return json.dumps(
        capabilities_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
