"""Static machine-readable OMD feature capabilities."""
from __future__ import annotations

import json
import os
from datetime import date

from omd import __version__
from omd.provider_disclosure import disclosure_readiness

try:
    from omd._build_info import BUILD_REVISION
except ImportError:
    BUILD_REVISION = None

PROTOCOL_VERSION = 1


def _build_revision() -> str | None:
    value = os.environ.get("OMD_BUILD_REVISION", "").strip() or BUILD_REVISION or ""
    printable = "".join(character for character in value if character.isprintable())
    return printable[:128] or None


def capabilities_payload(*, as_of: date | None = None) -> dict[str, object]:
    return {
        "package_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "build_revision": _build_revision(),
        "browser_ui": {"supported": False, "reason": "dependency_security_update_pending"},
        "enrich_note": {"supported": True, "schema_versions": [1]},
        "hosted_ai_disclosure": disclosure_readiness(as_of=as_of),
    }


def capabilities_json(*, as_of: date | None = None) -> str:
    return json.dumps(
        capabilities_payload(as_of=as_of),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
